"""Member request flow: dates, stock warnings, status link, accept/deny,
committee edits and collection of a reduced approval."""
from datetime import date, timedelta

import pytest
from conftest import get_csrf_token, login

import app as app_module
from models import Category, ItemRequest, KitItem, SignOut, Subcategory, db

ITEM = "Requests Test Item"


@pytest.fixture()
def item_id(app):
    with app.app_context():
        category = Category(name="Requests Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Requests Test Sub", category_id=category.id)
        db.session.add(sub)
        db.session.flush()
        item = KitItem(name=ITEM, subcategory_id=sub.id, quantity_total=3, quantity_available=2)
        extra = KitItem(name="Requests Extra Item", subcategory_id=sub.id, quantity_total=5, quantity_available=5)
        db.session.add_all([item, extra])
        db.session.commit()
        ids = item.id, extra.id
        sub_id, category_id = sub.id, category.id
    previous_inbox = app.config.get("REQUESTS_EMAIL")
    app.config["REQUESTS_EMAIL"] = "kit@example.org, gearsec@example.org"
    app_module.OUTBOX.clear()

    yield ids[0]

    app.config["REQUESTS_EMAIL"] = previous_inbox

    with app.app_context():
        ItemRequest.query.filter(ItemRequest.requester_name == "Robin").delete()
        SignOut.query.filter(SignOut.item_id.in_(ids)).delete()
        KitItem.query.filter(KitItem.id.in_(ids)).delete()
        Subcategory.query.filter_by(id=sub_id).delete()
        Category.query.filter_by(id=category_id).delete()
        db.session.commit()


def _add(client, item_id, quantity):
    token = get_csrf_token(client, "/request")
    return client.post(
        "/basket/add",
        data={"item_id": item_id, "quantity": quantity, "csrf_token": token},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).get_json()


def _checkout(client, item_id, quantity=5, **overrides):
    _add(client, item_id, quantity)
    token = get_csrf_token(client, "/basket")
    data = {
        "requester_name": "Robin",
        "requester_email": "robin@example.org",
        "needed_from": date.today().isoformat(),
        "needed_to": (date.today() + timedelta(days=4)).isoformat(),
        "csrf_token": token,
        **overrides,
    }
    return client.post("/basket", data=data)


def _group(app):
    with app.app_context():
        line = ItemRequest.query.filter_by(requester_name="Robin").first()
        return line.request_group, line.status_token


def test_adding_more_than_stock_warns(client, item_id):
    data = _add(client, item_id, 5)
    assert data["ok"]
    assert "Only 2 available" in data["message"]
    assert "Only 2 available" in client.get("/basket").get_data(as_text=True)


def test_checkout_needs_dates_in_order(client, app, item_id):
    response = _checkout(client, item_id, needed_to=(date.today() - timedelta(days=1)).isoformat())
    assert response.status_code == 200
    with app.app_context():
        assert ItemRequest.query.filter_by(requester_name="Robin").count() == 0


def test_checkout_emails_member_and_inbox_and_shows_status(client, app, item_id):
    response = _checkout(client, item_id)
    _, token = _group(app)
    assert response.headers["Location"] == f"/r/{token}?sent=1"
    sent_to = sorted(mail["to"] for mail in app_module.OUTBOX)
    assert sent_to == ["gearsec@example.org", "kit@example.org", "robin@example.org"]
    inbox = next(m for m in app_module.OUTBOX if m["to"] == "kit@example.org")
    assert inbox["reply_to"] == "robin@example.org"
    assert "only 2 available" in inbox["html"]
    page = client.get(f"/r/{token}?sent=1").get_data(as_text=True)
    assert "Request sent" in page and "Waiting for committee" in page


def test_member_can_cancel_only_while_pending(client, app, item_id):
    _checkout(client, item_id)
    group, token = _group(app)
    csrf = get_csrf_token(client, f"/r/{token}")
    client.post(f"/r/{token}/cancel", data={"csrf_token": csrf})
    with app.app_context():
        assert {r.status for r in ItemRequest.query.filter_by(request_group=group)} == {"cancelled"}


def test_accept_reduced_quantity_then_collect(client, app, item_id):
    _checkout(client, item_id)
    group, token = _group(app)
    with app.app_context():
        line_id = ItemRequest.query.filter_by(request_group=group).first().id
    login(client, committee_name="Sam")
    csrf = get_csrf_token(client, "/admin")

    missing_note = client.post(
        f"/admin/requests/{group}/accept", data={f"approved_{line_id}": "2", "csrf_token": csrf}, follow_redirects=True
    )
    assert b"handover note" in missing_note.data

    app_module.OUTBOX.clear()
    client.post(
        f"/admin/requests/{group}/accept",
        data={f"approved_{line_id}": "2", "handover_note": "Cage, Thu 6pm", "csrf_token": csrf},
    )
    with app.app_context():
        line = db.session.get(ItemRequest, line_id)
        assert (line.status, line.approved_quantity, line.claimed_by) == ("approved", 2, "Sam")
    (mail,) = app_module.OUTBOX
    assert mail["to"] == "robin@example.org" and "Cage, Thu 6pm" in mail["html"] and "2 approved" in mail["html"]

    status = client.get(f"/r/{token}").get_data(as_text=True)
    assert "Cage, Thu 6pm" in status and "Cancel Request" not in status

    csrf = get_csrf_token(client, "/sign-in-out")
    client.post(f"/admin/requests/{line_id}/collect", data={"due_date": "", "csrf_token": csrf})
    with app.app_context():
        assert db.session.get(KitItem, item_id).quantity_available == 0
        signout = SignOut.query.filter_by(item_id=item_id).one()
        assert signout.quantity == 2


def test_deny_needs_reason_and_emails_it(client, app, item_id):
    _checkout(client, item_id)
    group, _ = _group(app)
    login(client)
    csrf = get_csrf_token(client, "/admin")
    client.post(f"/admin/requests/{group}/deny", data={"deny_reason": "", "csrf_token": csrf})
    with app.app_context():
        assert ItemRequest.query.filter_by(request_group=group).first().status == "pending"
    app_module.OUTBOX.clear()
    client.post(f"/admin/requests/{group}/deny", data={"deny_reason": "Out on a club trip", "csrf_token": csrf})
    with app.app_context():
        assert ItemRequest.query.filter_by(request_group=group).first().status == "denied"
    assert "Out on a club trip" in app_module.OUTBOX[0]["html"]


def test_committee_edits_add_item_dates_and_cancel(client, app, item_id):
    _checkout(client, item_id)
    group, token = _group(app)
    with app.app_context():
        extra_id = KitItem.query.filter_by(name="Requests Extra Item").one().id
    login(client)
    csrf = get_csrf_token(client, "/admin")
    client.post(
        f"/admin/requests/{group}/add-item", data={"entry": f"item:{extra_id}", "quantity": "1", "csrf_token": csrf}
    )
    new_from = date.today() + timedelta(days=2)
    client.post(
        f"/admin/requests/{group}/dates",
        data={"needed_from": new_from.isoformat(), "needed_to": (new_from + timedelta(days=3)).isoformat(), "csrf_token": csrf},
    )
    with app.app_context():
        lines = ItemRequest.query.filter_by(request_group=group).order_by(ItemRequest.id).all()
        assert [line.item_name for line in lines] == [ITEM, "Requests Extra Item"]
        assert {line.status_token for line in lines} == {token}
        assert {line.needed_from for line in lines} == {new_from}
        ids = [line.id for line in lines]
    client.post(
        f"/admin/requests/{group}/accept",
        data={f"approved_{ids[0]}": "1", f"approved_{ids[1]}": "1", "handover_note": "Bus", "csrf_token": csrf},
    )
    client.post(f"/admin/requests/{group}/cancel", data={"csrf_token": csrf})
    with app.app_context():
        assert {r.status for r in ItemRequest.query.filter_by(request_group=group)} == {"cancelled"}
    assert "Cancelled" in client.get(f"/r/{token}").get_data(as_text=True)


def test_login_requires_a_name(client):
    token = get_csrf_token(client, "/login")
    response = client.post("/login", data={"password": "test-only-password", "committee_name": " ", "csrf_token": token})
    assert b"Enter your name" in response.data
    response = client.post("/login", data={"password": "test-only-password", "committee_name": "Sam", "csrf_token": token})
    assert "committee_name=Sam" in response.headers["Set-Cookie"]


def test_pending_count_badge(client, app, item_id):
    _checkout(client, item_id)
    login(client)
    assert b'class="subnav-count">1<' in client.get("/sign-in-out").data


def test_unclaimed_request_nudges_inbox_once(client, app, item_id):
    _checkout(client, item_id)
    group, _ = _group(app)
    with app.app_context():
        db.session.execute(
            ItemRequest.__table__.update()
            .where(ItemRequest.request_group == group)
            .values(created_at=app_module.utcnow() - timedelta(hours=49))
        )
        db.session.commit()
    app_module.OUTBOX.clear()
    with app.test_request_context():
        assert app_module.run_reminders(date.today())["unclaimed"] == 1
        assert app_module.run_reminders(date.today())["unclaimed"] == 0
        app_module.EmailLog.query.filter_by(request_group=group).delete()
        db.session.commit()
    assert sorted(m["to"] for m in app_module.OUTBOX) == ["gearsec@example.org", "kit@example.org"]
