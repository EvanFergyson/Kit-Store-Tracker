"""Due dates and reminder emails: each kind sent once per rule, grouped per
borrower, the Monday digest, the cron endpoint's secret, and the Resend call."""
import io
import json
from datetime import date, datetime, timedelta

import pytest
from conftest import get_csrf_token, login

import app as app_module
import config as config_module
from models import Category, EmailLog, ItemRequest, KitItem, SignOut, Subcategory, db

TODAY = date(2030, 6, 5)  # a Wednesday
MONDAY = date(2030, 6, 10)


@pytest.fixture()
def item(app):
    with app.app_context():
        category = Category(name="Reminder Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Reminder Test Sub", category_id=category.id)
        db.session.add(sub)
        db.session.flush()
        kit = KitItem(name="Reminder Test Rope", subcategory_id=sub.id, quantity_total=10, quantity_available=10)
        db.session.add(kit)
        db.session.commit()
        item_id = kit.id
        app_module.OUTBOX.clear()

        yield item_id

        loan_ids = [s.id for s in SignOut.query.filter_by(item_id=item_id)]
        EmailLog.query.filter(EmailLog.signout_id.in_(loan_ids)).delete()
        EmailLog.query.filter_by(kind="digest").delete()
        ItemRequest.query.filter_by(item_name="Reminder Test Rope").delete()
        SignOut.query.filter_by(item_id=item_id).delete()
        KitItem.query.filter_by(id=item_id).delete()
        Subcategory.query.filter_by(id=sub.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()
        app_module.OUTBOX.clear()


def _loan(app, item_id, due, email="sam@example.com", name="Sam", signed_out=None):
    with app.app_context():
        loan = app_module._create_signout(
            db.session.get(KitItem, item_id), 1, name, "", "Tester", due_date=due, borrower_email=email
        )
        db.session.flush()
        loan.signed_out_at = signed_out or datetime.combine(TODAY, datetime.min.time())
        db.session.commit()
        return loan.id


def _run(app, today):
    with app.test_request_context():
        return app_module.run_reminders(today)


def _sent(to=None, subject=None):
    return [
        m for m in app_module.OUTBOX if (to is None or m["to"] == to) and (subject is None or subject in m["subject"])
    ]


def test_receipt_groups_loans_and_sends_once(app, item):
    _loan(app, item, TODAY + timedelta(days=7))
    _loan(app, item, TODAY + timedelta(days=7))
    _run(app, TODAY)
    receipts = _sent("sam@example.com", "Your CUMC kit loan")
    assert len(receipts) == 1 and receipts[0]["html"].count("Reminder Test Rope") == 2
    _run(app, TODAY)
    assert len(_sent("sam@example.com", "Your CUMC kit loan")) == 1


def test_no_receipt_for_old_loans(app, item):
    _loan(app, item, TODAY + timedelta(days=7), signed_out=datetime(2030, 5, 1))
    _run(app, TODAY)
    assert _sent(subject="Your CUMC kit loan") == []


def test_day_before_reminder_once(app, item):
    _loan(app, item, TODAY + timedelta(days=1))
    _run(app, TODAY)
    _run(app, TODAY)
    assert len(_sent("sam@example.com", "due back tomorrow")) == 1


def test_overdue_on_due_date_then_every_three_days(app, item):
    _loan(app, item, TODAY)
    _run(app, TODAY)
    _run(app, TODAY + timedelta(days=1))
    _run(app, TODAY + timedelta(days=2))
    assert len(_sent("sam@example.com", "CUMC kit due back")) == 1
    _run(app, TODAY + timedelta(days=3))
    assert len(_sent("sam@example.com", "CUMC kit due back")) == 2


def test_returned_kit_gets_no_reminders(app, item):
    loan_id = _loan(app, item, TODAY)
    with app.app_context():
        app_module._return_signout(db.session.get(SignOut, loan_id))
        db.session.commit()
    _run(app, TODAY)
    assert _sent("sam@example.com") == []


def test_loans_without_email_only_appear_in_monday_digest(app, item, monkeypatch):
    monkeypatch.setitem(app.config, "GEAR_SEC_EMAIL", "gear@example.com")
    _loan(app, item, TODAY - timedelta(days=4), email=None, name="Walk-in Wes")
    _run(app, TODAY)
    assert _sent("gear@example.com") == []  # not Monday
    _run(app, MONDAY)
    _run(app, MONDAY)
    digests = _sent("gear@example.com", "Overdue kit")
    assert len(digests) == 1 and "Walk-in Wes" in digests[0]["html"] and "(no email)" in digests[0]["html"]
    assert len(app_module.OUTBOX) == 1


def test_invalid_address_is_logged_once_not_retried_hourly(app, item):
    loan_id = _loan(app, item, TODAY + timedelta(days=7), email="sam@example")
    _run(app, TODAY)
    _run(app, TODAY)
    assert _sent("sam@example") == []
    with app.app_context():
        logs = EmailLog.query.filter_by(signout_id=loan_id).all()
        assert [(log.kind, log.status) for log in logs] == [("receipt", "rejected")]


# --- talking to Resend ----------------------------------------------------------

class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def resend(app, monkeypatch):
    """Point send_email at a fake Resend; returns a dict of what was sent."""
    monkeypatch.setitem(app.config, "RESEND_API_KEY", "re_test")
    monkeypatch.setitem(app.config, "MAIL_FROM", "CUMC Kit Store <reminders@cumc.co.uk>")
    captured = {"respond": lambda req: FakeResponse()}

    def fake_urlopen(req, timeout):
        captured["url"], captured["headers"] = req.full_url, dict(req.header_items())
        captured["body"] = json.loads(req.data)
        return captured["respond"](req)

    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
    return captured


def _send(app):
    with app.app_context():
        return app_module.send_email("sam@example.com", "Hello", "<p>Hi</p>")


def _http_error(req, code, body):
    return app_module.urllib.error.HTTPError(req.full_url, code, "error", {}, io.BytesIO(body))


def test_resend_request_shape(app, resend, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_REPLY_TO", "gear@example.com")
    assert _send(app) == app_module.SENT
    assert resend["url"] == "https://api.resend.com/emails"
    assert resend["headers"]["Authorization"] == "Bearer re_test"
    assert resend["body"]["from"] == "CUMC Kit Store <reminders@cumc.co.uk>"
    assert resend["body"]["to"] == ["sam@example.com"] and resend["body"]["subject"] == "Hello"
    assert resend["body"]["reply_to"] == "gear@example.com"


def test_recipient_rejection_is_permanent_and_logged(app, resend, caplog):
    def reject(req):
        raise _http_error(req, 422, b'{"message":"Invalid `to` field."}')

    resend["respond"] = reject
    assert _send(app) == app_module.REJECTED
    assert "422" in caplog.text and "Invalid `to` field" in caplog.text


def test_sender_rejection_is_retried(app, resend):
    def reject(req):
        raise _http_error(req, 422, b'{"message":"Invalid `from` field."}')

    resend["respond"] = reject
    assert _send(app) == app_module.RETRY  # our config is wrong, not the recipient


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("reset"),
        TimeoutError("timed out"),
        app_module.http.client.RemoteDisconnected("gone"),
        app_module.http.client.IncompleteRead(b""),
        ValueError("Invalid header value"),
    ],
)
def test_network_and_header_errors_never_escape(app, resend, error):
    def fail(req):
        raise error

    resend["respond"] = fail
    assert _send(app) == app_module.RETRY


def test_unreadable_error_body_never_escapes(app, resend):
    class BrokenBody(io.BytesIO):
        def read(self, *args):
            raise ConnectionResetError("reset mid-read")

    def fail(req):
        raise app_module.urllib.error.HTTPError(req.full_url, 500, "error", {}, BrokenBody())

    resend["respond"] = fail
    assert _send(app) == app_module.RETRY


# --- cleaning pasted settings ------------------------------------------------------

@pytest.mark.parametrize(
    "raw, cleaned",
    [
        ('"CUMC Kit Store <reminders@cumc.co.uk>"', "CUMC Kit Store <reminders@cumc.co.uk>"),
        ('"CUMC Kit Store" <reminders@cumc.co.uk>', '"CUMC Kit Store" <reminders@cumc.co.uk>'),  # valid, kept
        ("re_abc123\n", "re_abc123"),
        ("  'gear@cumc.co.uk' ", "gear@cumc.co.uk"),
        ('""', ""),
        (None, ""),
    ],
)
def test_clean_value(raw, cleaned):
    assert config_module.clean_value(raw) == cleaned


def test_blank_or_quotes_only_setting_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", '""')
    assert config_module._env("MAIL_FROM", "default") == "default"
    monkeypatch.setenv("MAIL_FROM", "   ")
    assert config_module._env("MAIL_FROM", "default") == "default"


# --- endpoint ----------------------------------------------------------------

def test_cron_endpoint_disabled_without_secret(client):
    assert client.post("/tasks/send-reminders").status_code == 404


def test_cron_endpoint_checks_secret(client, app, item, monkeypatch):
    monkeypatch.setitem(app.config, "CRON_SECRET", "s3cret")
    assert client.post("/tasks/send-reminders", headers={"X-Cron-Secret": "wrong"}).status_code == 403
    assert client.post("/tasks/send-reminders").status_code == 403
    response = client.post("/tasks/send-reminders", headers={"X-Cron-Secret": "s3cret"})
    assert response.status_code == 200 and set(response.get_json()) == {"receipt", "day_before", "overdue", "digest"}


@pytest.mark.parametrize("sent", [" s3cret ", '"s3cret"', "'s3cret'"])
def test_cron_secret_header_tolerates_paste_mistakes(client, app, item, monkeypatch, sent):
    monkeypatch.setitem(app.config, "CRON_SECRET", "s3cret")
    assert client.post("/tasks/send-reminders", headers={"X-Cron-Secret": sent}).status_code == 200


# --- due dates on forms ----------------------------------------------------------

def test_collect_copies_email_and_due_date(client, app, item):
    with app.app_context():
        req = ItemRequest(
            requester_name="Sam", requester_email="sam@example.com", item_name="Reminder Test Rope", quantity=1,
            status="approved",
        )
        db.session.add(req)
        db.session.commit()
        request_id = req.id
    login(client)
    token = get_csrf_token(client, "/admin/categories")
    due = date.today() + timedelta(days=5)
    client.post(f"/admin/requests/{request_id}/collect", data={"due_date": due.isoformat(), "csrf_token": token})
    with app.app_context():
        loan = SignOut.query.filter_by(item_id=item).one()
        assert (loan.borrower_email, loan.due_date) == ("sam@example.com", due)


def test_manual_sign_out_defaults_due_date_and_rejects_past(client, app, item):
    login(client)
    token = get_csrf_token(client, "/admin/categories")
    base = {"item_id": item, "quantity": "1", "borrower_name": "Jo", "csrf_token": token}
    response = client.post(
        "/sign-out", data=base | {"due_date": (date.today() - timedelta(days=1)).isoformat()}, follow_redirects=True
    )
    assert b"isn&#39;t in the past" in response.data
    client.post("/sign-out", data=base)
    with app.app_context():
        loan = SignOut.query.filter_by(item_id=item).one()
        assert loan.due_date == date.today() + timedelta(days=app.config["DEFAULT_LOAN_DAYS"])


def test_request_requires_email(client, item):
    token = get_csrf_token(client, "/login")
    client.post("/basket/add", data={"item_id": item, "quantity": "1", "csrf_token": token})
    response = client.post(
        "/basket",
        data={"requester_name": "Sam", "pickup_slot": "Taf Monday", "return_slot": "Taf Monday", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"Please enter your email" in response.data
