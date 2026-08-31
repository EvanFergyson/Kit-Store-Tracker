"""Regression tests for the two race conditions fixed in app.py:

1. Stock decrement: _create_signout used to do `item.quantity_available -=
   quantity` in Python, trusting whatever value was last read into that
   object - two requests both starting from the same read could both
   "succeed" and drive stock negative.
2. Name uniqueness: every add/edit route did a SELECT then a separate
   INSERT/UPDATE with no DB-level backing, so two near-simultaneous
   submissions could both pass and create case-variant duplicates.

Both are hard to catch by clicking around manually (the failure only shows
up under real concurrent timing), so they're tested directly rather than
through two sequential HTTP requests, which wouldn't actually exercise the
race - each request re-reads fresh state, so calling the route twice in a
row (nothing overlapping) would look fine on both the old and new code.
"""
import pytest
from conftest import get_csrf_token, login
from sqlalchemy.exc import IntegrityError

import app as app_module
from models import Category, KitItem, Subcategory, SignOut, db


@pytest.fixture()
def stocked_item(app):
    """One real KitItem with exactly 1 unit available, under a throwaway
    category/subcategory, cleaned up after the test."""
    with app.app_context():
        category = Category(name="Concurrency Test Category")
        db.session.add(category)
        db.session.flush()
        subcategory = Subcategory(name="Concurrency Test Sub", category_id=category.id)
        db.session.add(subcategory)
        db.session.flush()
        item = KitItem(
            name="Concurrency Test Item",
            subcategory_id=subcategory.id,
            quantity_total=1,
            quantity_available=1,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        yield item_id

        SignOut.query.filter_by(item_id=item_id).delete()
        KitItem.query.filter_by(id=item_id).delete()
        Subcategory.query.filter_by(id=subcategory.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()


def test_create_signout_rejects_stock_already_taken_underneath_it(app, stocked_item):
    """Two calls sharing the same (deliberately not re-queried) item object,
    simulating two requests that both read "1 available" before either had
    written back. The fix must reject the second one because its atomic
    UPDATE re-checks the real database value, not item.quantity_available
    in Python."""
    with app.app_context():
        item = db.session.get(KitItem, stocked_item)

        app_module._create_signout(item, 1, "Racer A", "", "Tester")
        db.session.commit()

        with pytest.raises(app_module.InsufficientStockError) as exc_info:
            app_module._create_signout(item, 1, "Racer B", "", "Tester")
        db.session.rollback()
        assert exc_info.value.item.quantity_available == 0

        fresh = db.session.get(KitItem, stocked_item)
        assert fresh.quantity_available == 0  # never negative
        assert SignOut.query.filter_by(item_id=stocked_item).count() == 1  # only Racer A's


def test_sign_out_route_surfaces_race_as_flash_not_500(client, stocked_item):
    """Same race, exercised through the real /sign-out route and
    _create_signout's caller-side try/except, to prove the wiring (not just
    the helper function) handles it cleanly."""
    login(client)

    with client.application.app_context():
        item = db.session.get(KitItem, stocked_item)
        # Take the one unit "out from under" the route, after whatever
        # pre-check it's about to do would have seen it as available.
        app_module._create_signout(item, 1, "Already Took It", "", "Someone Else")
        db.session.commit()

    token = get_csrf_token(client, "/sign-in-out")
    response = client.post(
        "/sign-out",
        data={"item_id": stocked_item, "quantity": "1", "borrower_name": "Racer B", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"available" in response.data.lower()
    assert b"Signed out 1" not in response.data


def test_db_unique_index_blocks_case_variant_duplicate(app):
    """Direct proof the new DB-level index (not just the app-level
    _name_exists check) actually enforces case-insensitive uniqueness."""
    with app.app_context():
        db.session.execute(Category.__table__.insert().values(name="Unique Race Category", member_visible=True))
        db.session.commit()

        with pytest.raises(IntegrityError):
            db.session.execute(
                Category.__table__.insert().values(name="unique race category", member_visible=True)
            )
            db.session.commit()
        db.session.rollback()

        Category.query.filter_by(name="Unique Race Category").delete()
        db.session.commit()


def test_duplicate_name_race_through_route_is_handled_gracefully(client, monkeypatch):
    """Widens the app-level check's race window deterministically (real
    concurrent requests would only hit this occasionally) by forcing
    _name_exists to report "no clash" even though a case-variant already
    exists, so add_category proceeds to the real INSERT and hits the DB
    constraint - exercising the actual IntegrityError handler end-to-end
    instead of a raw 500."""
    login(client)

    with client.application.app_context():
        db.session.execute(
            Category.__table__.insert().values(name="Handled Race Category", member_visible=True)
        )
        db.session.commit()

    monkeypatch.setattr(app_module, "_name_exists", lambda *a, **k: False)

    token = get_csrf_token(client, "/admin/categories")
    response = client.post(
        "/admin/categories/add",
        data={"name": "handled race category", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200  # redirected + rendered, not a raw 500
    assert b"clashes with something else" in response.data

    with client.application.app_context():
        # Only the original row exists - the racing insert was rolled back.
        assert Category.query.filter_by(name="Handled Race Category").count() == 1
        Category.query.filter_by(name="Handled Race Category").delete()
        db.session.commit()
