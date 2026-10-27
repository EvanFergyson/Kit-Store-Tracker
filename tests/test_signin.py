"""Committee loan handling: multi-item sign-out, partial and bulk sign-in,
undo, and the optional serial at sign-out."""
import pytest
from conftest import get_csrf_token, login

from models import Category, GearUnit, KitItem, SignOut, Subcategory, db


@pytest.fixture()
def kit(app):
    with app.app_context():
        category = Category(name="Signin Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Signin Test Sub", category_id=category.id, track_serials=True)
        db.session.add(sub)
        db.session.flush()
        rope = KitItem(name="Signin Rope", subcategory_id=sub.id, quantity_total=4, quantity_available=4)
        draw = KitItem(name="Signin Draw", subcategory_id=sub.id, quantity_total=2, quantity_available=2)
        db.session.add_all([rope, draw])
        db.session.flush()
        db.session.add(GearUnit(item_id=rope.id, serial="SR-1"))
        db.session.commit()
        ids = {"rope": rope.id, "draw": draw.id, "sub": sub.id, "category": category.id}

    yield ids

    with app.app_context():
        item_ids = [ids["rope"], ids["draw"]]
        SignOut.query.filter(SignOut.item_id.in_(item_ids)).delete()
        GearUnit.query.filter(GearUnit.item_id.in_(item_ids)).delete()
        KitItem.query.filter(KitItem.id.in_(item_ids)).delete()
        Subcategory.query.filter_by(id=ids["sub"]).delete()
        Category.query.filter_by(id=ids["category"]).delete()
        db.session.commit()


def _available(app, item_id):
    with app.app_context():
        return db.session.get(KitItem, item_id).quantity_available


def _multi(client, rows, **extra):
    token = get_csrf_token(client, "/sign-out/multi")
    data = {"borrower_name": "Ali", "borrower_email": "ali@example.org", "due_date": "", "csrf_token": token} | extra
    data["item_id"] = [str(item_id) for item_id, _ in rows]
    data["quantity"] = [str(quantity) for _, quantity in rows]
    return client.post("/sign-out/multi", data=data)


def test_multi_sign_out_is_all_or_nothing(client, app, kit):
    login(client)
    response = _multi(client, [(kit["rope"], 2), (kit["draw"], 3)])
    assert b"nothing was signed out" in response.data
    assert (_available(app, kit["rope"]), _available(app, kit["draw"])) == (4, 2)

    _multi(client, [(kit["rope"], 1), (kit["draw"], 2), (kit["rope"], 1)])
    assert (_available(app, kit["rope"]), _available(app, kit["draw"])) == (2, 0)
    with app.app_context():
        loans = SignOut.query.filter(SignOut.item_id.in_([kit["rope"], kit["draw"]])).all()
        assert sorted(loan.quantity for loan in loans) == [2, 2]
        assert {loan.borrower_email for loan in loans} == {"ali@example.org"}


def test_partial_sign_in_then_undo(client, app, kit):
    login(client)
    _multi(client, [(kit["rope"], 3)])
    with app.app_context():
        loan_id = SignOut.query.filter_by(item_id=kit["rope"]).one().id
    token = get_csrf_token(client, "/sign-in-out")
    client.post(f"/sign-in/{loan_id}", data={"quantity": "2", "csrf_token": token})
    assert _available(app, kit["rope"]) == 3
    with app.app_context():
        assert db.session.get(SignOut, loan_id).quantity == 1
        returned = SignOut.query.filter(SignOut.item_id == kit["rope"], SignOut.signed_in_at.isnot(None)).one()
        assert returned.quantity == 2
        returned_id = returned.id
    page = client.get("/sign-in-out").get_data(as_text=True)
    assert f"/sign-in/{returned_id}/undo" in page
    client.post(f"/sign-in/{returned_id}/undo", data={"csrf_token": token})
    assert _available(app, kit["rope"]) == 1
    response = client.post(f"/sign-in/{returned_id}/undo", data={"csrf_token": token}, follow_redirects=True)
    assert b"Couldn" in response.data
    assert _available(app, kit["rope"]) == 1


def test_sign_in_all_for_a_borrower(client, app, kit):
    login(client)
    _multi(client, [(kit["rope"], 1), (kit["draw"], 1)])
    page = client.get("/sign-in-out").get_data(as_text=True)
    assert "Sign In All (2)" in page
    with app.app_context():
        ids = [str(loan.id) for loan in SignOut.query.filter(SignOut.item_id.in_([kit["rope"], kit["draw"]]))]
    token = get_csrf_token(client, "/sign-in-out")
    client.post("/sign-in/many", data={"signout_id": ids, "csrf_token": token})
    assert (_available(app, kit["rope"]), _available(app, kit["draw"])) == (4, 2)


def test_serial_recorded_at_sign_out(client, app, kit):
    login(client)
    token = get_csrf_token(client, "/sign-out/multi")
    data = {"item_id": kit["rope"], "quantity": "1", "borrower_name": "Ali", "csrf_token": token}
    response = client.post("/sign-out", data=data | {"serial": "NOPE"}, follow_redirects=True)
    assert b"isn&#39;t registered" in response.data
    client.post("/sign-out", data=data | {"serial": "sr-1"})
    with app.app_context():
        unit = GearUnit.query.filter_by(serial="SR-1").one()
        assert unit.open_loan.borrower_name == "Ali"
    response = client.post("/sign-out", data=data | {"serial": "SR-1"}, follow_redirects=True)
    assert b"already signed out to Ali" in response.data
