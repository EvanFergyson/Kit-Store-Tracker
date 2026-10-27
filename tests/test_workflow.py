"""Regression tests for the backend review fixes: login redirect, request
resolution/collection guards, proxy-aware lockout, basket key parsing and
the atomic item-total edit."""
import pytest
from conftest import get_csrf_token, login

import app as app_module
from models import Category, ItemRequest, KitItem, SignOut, Subcategory, db


@pytest.fixture()
def item_with_stock(app):
    """One KitItem with 3 units, cleaned up (with its loans/requests) afterwards."""
    with app.app_context():
        category = Category(name="Workflow Test Category")
        db.session.add(category)
        db.session.flush()
        subcategory = Subcategory(name="Workflow Test Sub", category_id=category.id)
        db.session.add(subcategory)
        db.session.flush()
        item = KitItem(
            name="Workflow Test Item", subcategory_id=subcategory.id, quantity_total=3, quantity_available=3
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        yield item_id

        ItemRequest.query.filter_by(item_name="Workflow Test Item").delete()
        SignOut.query.filter_by(item_id=item_id).delete()
        KitItem.query.filter_by(id=item_id).delete()
        Subcategory.query.filter_by(id=subcategory.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()


def _approved_request(app, quantity=1):
    with app.app_context():
        req = ItemRequest(requester_name="Sam", item_name="Workflow Test Item", quantity=quantity, status="approved")
        db.session.add(req)
        db.session.commit()
        return req.id


@pytest.mark.parametrize("next_url", ["https://evil.example/", "//evil.example/", "/\\evil.example/"])
def test_login_ignores_offsite_next(client, next_url):
    token = get_csrf_token(client, "/login")
    response = client.post(
        f"/login?next={next_url}",
        data={"password": "test-only-password", "committee_name": "Sam", "csrf_token": token},
    )
    assert response.headers["Location"] == "/sign-in-out"


def test_login_follows_local_next(client):
    token = get_csrf_token(client, "/login")
    response = client.post(
        "/login?next=/admin", data={"password": "test-only-password", "committee_name": "Sam", "csrf_token": token}
    )
    assert response.headers["Location"] == "/admin"


def test_lockout_is_per_client_behind_proxy(client):
    """Behind Render's proxy every request shares one remote address; the
    lockout must key on the forwarded client IP instead."""
    token = get_csrf_token(client, "/login")
    attacker = {"X-Forwarded-For": "203.0.113.1"}
    for _ in range(app_module.LOGIN_MAX_ATTEMPTS):
        client.post("/login", data={"password": "wrong", "committee_name": "X", "csrf_token": token}, headers=attacker)
    response = client.post(
        "/login",
        data={"password": "test-only-password", "committee_name": "Sam", "csrf_token": token},
        headers={"X-Forwarded-For": "203.0.113.2"},
        follow_redirects=True,
    )
    assert b"Logged in" in response.data


def test_resolved_request_cannot_be_flipped(client, app, item_with_stock):
    request_id = _approved_request(app)
    with app.app_context():
        group = db.session.get(ItemRequest, request_id).request_group
    login(client)
    token = get_csrf_token(client, "/admin/categories")
    response = client.post(
        f"/admin/requests/{group}/deny", data={"deny_reason": "No", "csrf_token": token}, follow_redirects=True
    )
    assert b"already dealt with" in response.data
    with app.app_context():
        assert db.session.get(ItemRequest, request_id).status == "approved"


def test_collect_claim_refuses_second_collection(app, item_with_stock):
    """Two Collects racing past the route's own "already collected?" check:
    the second claim must fail so its sign-out is rolled back."""
    request_id = _approved_request(app)
    with app.app_context():
        item = db.session.get(KitItem, item_with_stock)
        stale_request = db.session.get(ItemRequest, request_id)

        first = app_module._create_signout(item, 1, "Sam", "", "Tester")
        db.session.flush()
        assert app_module._claim_request_collection(stale_request, first.id)
        db.session.commit()

        second = app_module._create_signout(item, 1, "Sam", "", "Tester")
        db.session.flush()
        assert not app_module._claim_request_collection(stale_request, second.id)
        db.session.rollback()

        assert db.session.get(KitItem, item_with_stock).quantity_available == 2
        assert SignOut.query.filter_by(item_id=item_with_stock).count() == 1


def test_item_total_edit_keeps_concurrent_signout(app, item_with_stock):
    """An edit started before a sign-out must not overwrite it: the stale
    in-memory item still says 3 available, but 1 is now out."""
    with app.app_context():
        stale_item = db.session.get(KitItem, item_with_stock)
        _ = stale_item.quantity_available  # loaded as 3
        db.session.execute(
            KitItem.__table__.update().where(KitItem.id == item_with_stock).values(quantity_available=2)
        )
        assert app_module._set_item_total(stale_item, 5)
        db.session.commit()
        fresh = db.session.get(KitItem, item_with_stock)
        assert (fresh.quantity_total, fresh.quantity_available) == (5, 4)


def test_item_total_cannot_drop_below_on_loan(client, app, item_with_stock):
    with app.app_context():
        app_module._create_signout(db.session.get(KitItem, item_with_stock), 2, "Jo", "", "Tester")
        db.session.commit()
    login(client)
    token = get_csrf_token(client, f"/admin/items/{item_with_stock}/edit")
    response = client.post(
        f"/admin/items/{item_with_stock}/edit",
        data={"name": "Workflow Test Item", "quantity_total": "1", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"currently" in response.data and b"signed out" in response.data
    with app.app_context():
        item = db.session.get(KitItem, item_with_stock)
        assert (item.quantity_total, item.quantity_available) == (3, 1)


@pytest.mark.parametrize("path", ["/basket/remove/other:abc", "/basket/update/other:abc", "/basket/remove/other:-1"])
def test_malformed_basket_key_does_not_crash(client, path):
    token = get_csrf_token(client, "/login")
    response = client.post(path, data={"quantity": "1", "csrf_token": token})
    assert response.status_code == 302


def test_sign_out_history_is_capped(client, app, item_with_stock):
    limit = app_module.SIGN_OUT_HISTORY_LIMIT
    with app.app_context():
        for i in range(limit + 5):
            db.session.add(SignOut(item_id=item_with_stock, borrower_name=f"B{i}", quantity=1))
        db.session.commit()
    login(client)
    html = client.get("/sign-in-out").get_data(as_text=True)
    history = html.split("Sign-Out History", 1)[1]
    assert history.count("<tr>") - 1 == limit  # minus the header row
