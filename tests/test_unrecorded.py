"""Signing in stock that's marked out with no loan record (e.g. imported
from the old spreadsheet with available < total and no borrower)."""
import pytest
from conftest import get_csrf_token, login

import app as app_module
from models import Category, KitItem, SignOut, Subcategory, db


@pytest.fixture()
def item(app):
    """5 slings: 2 available, 1 out on a real loan to Jo, so 2 unrecorded."""
    with app.app_context():
        category = Category(name="Unrecorded Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Unrecorded Test Sub", category_id=category.id)
        db.session.add(sub)
        db.session.flush()
        sling = KitItem(name="Unrecorded Test Sling", subcategory_id=sub.id, quantity_total=5, quantity_available=3)
        db.session.add(sling)
        db.session.flush()
        app_module._create_signout(sling, 1, "Jo", "", "Tester")
        db.session.commit()
        item_id = sling.id

        yield item_id

        SignOut.query.filter_by(item_id=item_id).delete()
        KitItem.query.filter_by(id=item_id).delete()
        Subcategory.query.filter_by(id=sub.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()


def _sign_in(client, item_id, quantity):
    login(client)
    token = get_csrf_token(client, "/admin/categories")
    return client.post(
        f"/sign-in/unrecorded/{item_id}", data={"quantity": str(quantity), "csrf_token": token}, follow_redirects=True
    )


def _state(app, item_id):
    with app.app_context():
        item = db.session.get(KitItem, item_id)
        open_loans = SignOut.query.filter_by(item_id=item_id, signed_in_at=None).count()
        return item.quantity_available, open_loans


def test_unrecorded_units_exclude_real_loans(app, item):
    with app.app_context():
        assert [(i.id, units) for i, units in app_module._unrecorded_out() if i.id == item] == [(item, 2)]


def test_listed_on_sign_in_out_page(client, item):
    login(client)
    html = client.get("/sign-in-out").get_data(as_text=True)
    section = html.split('id="unrecorded"', 1)[1].split("</details>", 1)[0]
    assert "Unrecorded Test Sling" in section and "2 of 5" in section


def test_partial_sign_in_and_audit_row(client, app, item):
    response = _sign_in(client, item, 1)
    assert b"Signed in 1 x Unrecorded Test Sling" in response.data
    assert _state(app, item) == (3, 1)  # Jo's loan still open
    with app.app_context():
        audit = SignOut.query.filter_by(item_id=item, borrower_name="Unrecorded loan").one()
        assert audit.quantity == 1 and audit.signed_in_at is not None and audit.checked_out_by == "Test Committee"


def test_cannot_sign_in_more_than_unrecorded(client, app, item):
    response = _sign_in(client, item, 3)  # 3 are out, but 1 of those is Jo's loan
    assert b"more than is out without a loan record" in response.data
    assert _state(app, item) == (2, 1)


def test_double_submit_only_returns_stock_once(client, app, item):
    _sign_in(client, item, 2)
    response = _sign_in(client, item, 2)
    assert b"more than is out without a loan record" in response.data
    assert _state(app, item) == (4, 1)
    with app.app_context():
        assert item not in [i.id for i, _ in app_module._unrecorded_out()]


def test_stock_never_exceeds_total(app, item):
    """Sign in every unrecorded unit, then the real loan comes back: stock
    lands exactly on the total, and nothing more can be signed in."""
    with app.app_context():
        sling = db.session.get(KitItem, item)
        assert app_module._return_unrecorded(sling, 2, "Tester")
        db.session.commit()
        jo = SignOut.query.filter_by(item_id=item, borrower_name="Jo").one()
        assert app_module._return_signout(jo)
        db.session.commit()
        assert not app_module._return_unrecorded(sling, 1, "Tester")
        db.session.rollback()
        fresh = db.session.get(KitItem, item)
        assert fresh.quantity_available == fresh.quantity_total == 5


def test_requires_committee_login(client, item):
    token = get_csrf_token(client, "/login")
    response = client.post(f"/sign-in/unrecorded/{item}", data={"quantity": "1", "csrf_token": token})
    assert response.status_code == 302 and "/login" in response.headers["Location"]
