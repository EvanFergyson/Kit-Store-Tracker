"""Colour-coded racks: all-or-nothing sign-out, blockers, whole-rack sign-in,
and rack pieces never being lent or requested on their own."""
from datetime import date, timedelta

import pytest
from conftest import get_csrf_token, login

import app as app_module
from models import Category, KitItem, Rack, RackLoan, SignOut, Subcategory, db


@pytest.fixture()
def rack(app):
    """A Red rack holding 1 x cam and 2 x nut, plus one loose item in the same category."""
    with app.app_context():
        category = Category(name="Rack Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Rack Test Sub", category_id=category.id)
        db.session.add(sub)
        db.session.flush()
        red = Rack(name="Red Test Rack", colour="Red", colour_hex="#cc0000")
        db.session.add(red)
        db.session.flush()
        cam = KitItem(name="Red Cam", subcategory_id=sub.id, quantity_total=1, quantity_available=1, rack_id=red.id)
        nuts = KitItem(name="Red Nut", subcategory_id=sub.id, quantity_total=2, quantity_available=2, rack_id=red.id)
        loose = KitItem(name="Loose Sling", subcategory_id=sub.id, quantity_total=3, quantity_available=3)
        db.session.add_all([cam, nuts, loose])
        db.session.commit()
        ids = {"rack": red.id, "cam": cam.id, "nuts": nuts.id, "loose": loose.id, "category": category.id}

        yield ids

        item_ids = [cam.id, nuts.id, loose.id]
        SignOut.query.filter(SignOut.item_id.in_(item_ids)).delete()
        RackLoan.query.filter_by(rack_id=red.id).delete()
        KitItem.query.filter(KitItem.id.in_(item_ids)).delete()
        Rack.query.filter_by(id=red.id).delete()
        Subcategory.query.filter_by(id=sub.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()


def _token(client):
    login(client)
    return get_csrf_token(client, "/admin/categories")


def _sign_out_rack(client, token, rack_id, **overrides):
    data = {
        "borrower_name": "Alex",
        "due_date": (date.today() + timedelta(days=7)).isoformat(),
        "csrf_token": token,
    } | overrides
    return client.post(f"/racks/{rack_id}/sign-out", data=data, follow_redirects=True)


def _available(app, *item_ids):
    with app.app_context():
        return [db.session.get(KitItem, i).quantity_available for i in item_ids]


def test_rack_signs_out_every_piece(client, app, rack):
    token = _token(client)
    response = _sign_out_rack(client, token, rack["rack"])
    assert b"Signed out the Red Test Rack rack to Alex" in response.data
    assert _available(app, rack["cam"], rack["nuts"]) == [0, 0]
    with app.app_context():
        loan = RackLoan.query.filter_by(rack_id=rack["rack"]).one()
        assert len(loan.sign_outs) == 2


def test_rack_blocked_when_a_piece_is_out(client, app, rack):
    with app.app_context():
        app_module._create_signout(db.session.get(KitItem, rack["nuts"]), 1, "Someone", "", "Tester")
        db.session.commit()
    token = _token(client)
    response = _sign_out_rack(client, token, rack["rack"])
    assert b"can&#39;t go out" in response.data
    assert _available(app, rack["cam"]) == [1]
    with app.app_context():
        assert RackLoan.query.filter_by(rack_id=rack["rack"]).count() == 0


def test_rack_sign_out_rolls_back_if_a_piece_goes_mid_request(client, app, rack, monkeypatch):
    """The blocker check passes on stale data, then one piece turns out to be
    gone: nothing may leave, and no loan may be recorded."""
    with app.app_context():
        app_module._create_signout(db.session.get(KitItem, rack["nuts"]), 1, "Someone", "", "Tester")
        db.session.commit()
    monkeypatch.setattr(app_module, "_rack_blockers", lambda r: [])
    token = _token(client)
    _sign_out_rack(client, token, rack["rack"])
    assert _available(app, rack["cam"], rack["nuts"]) == [1, 1]
    with app.app_context():
        assert RackLoan.query.filter_by(rack_id=rack["rack"]).count() == 0


def test_rack_rejects_past_due_date(client, app, rack):
    token = _token(client)
    response = _sign_out_rack(client, token, rack["rack"], due_date=(date.today() - timedelta(days=1)).isoformat())
    assert b"return date" in response.data
    assert _available(app, rack["cam"]) == [1]


def test_whole_rack_sign_in_only_returns_stock_once(client, app, rack):
    token = _token(client)
    _sign_out_rack(client, token, rack["rack"])
    with app.app_context():
        loan_id = RackLoan.query.filter_by(rack_id=rack["rack"]).one().id
    client.post(f"/racks/loans/{loan_id}/sign-in", data={"csrf_token": token})
    response = client.post(f"/racks/loans/{loan_id}/sign-in", data={"csrf_token": token}, follow_redirects=True)
    assert b"already signed in" in response.data
    assert _available(app, rack["cam"], rack["nuts"]) == [1, 2]


def test_rack_piece_cannot_be_signed_in_alone(client, app, rack):
    token = _token(client)
    _sign_out_rack(client, token, rack["rack"])
    with app.app_context():
        piece_signout = SignOut.query.filter_by(item_id=rack["cam"]).one()
    response = client.post(f"/sign-in/{piece_signout.id}", data={"csrf_token": token}, follow_redirects=True)
    assert b"sign the whole rack in" in response.data
    assert _available(app, rack["cam"]) == [0]


def test_rack_piece_cannot_be_signed_out_alone(client, app, rack):
    token = _token(client)
    response = client.post(
        "/sign-out",
        data={"item_id": rack["cam"], "quantity": "1", "borrower_name": "Alex", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"sign out the whole rack" in response.data
    assert _available(app, rack["cam"]) == [1]


def test_rack_pieces_hidden_from_members(client, rack):
    html = client.get(f"/request/category/{rack['category']}").get_data(as_text=True)
    assert "Loose Sling" in html
    assert "Red Cam" not in html.split("Club Trad Racks")[0]
    token = get_csrf_token(client, "/login")
    response = client.post(
        "/basket/add", data={"item_id": rack["cam"], "quantity": "1", "csrf_token": token}, follow_redirects=True
    )
    assert b"can&#39;t be requested on its own" in response.data


def test_request_page_shows_rack_contents(client, rack):
    html = client.get("/request").get_data(as_text=True)
    assert "Red Test Rack" in html and "Red Cam" in html


def test_rack_with_history_cannot_be_deleted(client, app, rack):
    token = _token(client)
    _sign_out_rack(client, token, rack["rack"])
    response = client.post(f"/admin/racks/{rack['rack']}/delete", data={"csrf_token": token}, follow_redirects=True)
    assert b"loan history" in response.data
    with app.app_context():
        assert db.session.get(Rack, rack["rack"]) is not None


def test_admin_can_add_and_remove_rack_items(client, app, rack):
    token = _token(client)
    client.post(f"/admin/racks/{rack['rack']}/items/add", data={"item_id": rack["loose"], "csrf_token": token})
    with app.app_context():
        assert db.session.get(KitItem, rack["loose"]).rack_id == rack["rack"]
    client.post(f"/admin/racks/{rack['rack']}/items/{rack['loose']}/remove", data={"csrf_token": token})
    with app.app_context():
        assert db.session.get(KitItem, rack["loose"]).rack_id is None
