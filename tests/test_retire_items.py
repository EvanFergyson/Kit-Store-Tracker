"""Retiring units of a kit item: partial retirements lower stock, retiring
the lot archives it, and every retirement is logged."""
import pytest
from conftest import get_csrf_token, login

from models import Category, KitItem, RetiredKit, SignOut, Subcategory, db


@pytest.fixture()
def cams(app):
    with app.app_context():
        category = Category(name="Retire Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Retire Test Sub", category_id=category.id)
        db.session.add(sub)
        db.session.flush()
        item = KitItem(name="Retire Test Cam", subcategory_id=sub.id, quantity_total=3, quantity_available=2)
        db.session.add(item)
        db.session.commit()
        ids = item.id, sub.id, category.id

    yield ids[0]

    with app.app_context():
        RetiredKit.query.filter_by(item_id=ids[0]).delete()
        SignOut.query.filter_by(item_id=ids[0]).delete()
        KitItem.query.filter_by(id=ids[0]).delete()
        Subcategory.query.filter_by(id=ids[1]).delete()
        Category.query.filter_by(id=ids[2]).delete()
        db.session.commit()


def _retire(client, item_id, quantity, reason="Trigger wire snapped"):
    token = get_csrf_token(client, "/admin/categories")
    return client.post(
        f"/admin/items/{item_id}/retire",
        data={"quantity": quantity, "reason": reason, "csrf_token": token},
        follow_redirects=True,
    )


def _stock(app, item_id):
    with app.app_context():
        item = db.session.get(KitItem, item_id)
        return item.quantity_available, item.quantity_total, item.active


def test_retire_some_units_lowers_stock_and_logs_it(client, app, cams):
    login(client, committee_name="Sam")
    assert b"Say why" in _retire(client, cams, 1, reason="").data
    assert b"Only 2" in _retire(client, cams, 3).data  # one is out with someone
    _retire(client, cams, 1)
    assert _stock(app, cams) == (1, 2, True)
    with app.app_context():
        log = RetiredKit.query.filter_by(item_id=cams).one()
        assert (log.quantity, log.reason, log.retired_by) == (1, "Trigger wire snapped", "Sam")
    page = client.get("/admin/archived").get_data(as_text=True)
    assert "1 &times; Retire Test Cam" in page and "Trigger wire snapped" in page


def test_retiring_every_unit_archives_the_item(client, app, cams):
    with app.app_context():
        db.session.get(KitItem, cams).quantity_available = 3
        db.session.commit()
    login(client)
    _retire(client, cams, 3, reason="Whole set recalled")
    assert _stock(app, cams) == (3, 3, False)
    token = get_csrf_token(client, "/admin/archived")
    client.post(f"/admin/items/{cams}/restore", data={"csrf_token": token})
    assert _stock(app, cams) == (3, 3, True)
