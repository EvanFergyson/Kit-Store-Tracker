"""Gear register: separate password, serial-date decoding, inspections and
retirement, CSV import/export, and damaged pieces blocking their rack."""
import io
from datetime import date

import pytest
from conftest import get_csrf_token, login

import app as app_module
import serial_dates
from models import Category, GearUnit, Inspection, KitItem, Rack, SignOut, Subcategory, db


@pytest.fixture()
def harness(app):
    """A tracked 'Harness' subcategory (10-year lifespan) with one item of 2."""
    with app.app_context():
        category = Category(name="Gear Test Category")
        db.session.add(category)
        db.session.flush()
        sub = Subcategory(name="Gear Test Harnesses", category_id=category.id, track_serials=True, max_age_years=10)
        db.session.add(sub)
        db.session.flush()
        item = KitItem(name="Gear Test Harness", subcategory_id=sub.id, quantity_total=2, quantity_available=2)
        db.session.add(item)
        db.session.commit()
        ids = {"item": item.id, "sub": sub.id}

        yield ids

        unit_ids = [u.id for u in GearUnit.query.filter_by(item_id=item.id)]
        Inspection.query.filter(Inspection.unit_id.in_(unit_ids)).delete()
        GearUnit.query.filter_by(item_id=item.id).delete()
        SignOut.query.filter_by(item_id=item.id).delete()
        KitItem.query.filter_by(id=item.id).delete()
        Rack.query.filter_by(name="Gear Test Rack").delete()
        Subcategory.query.filter_by(id=sub.id).delete()
        Category.query.filter_by(id=category.id).delete()
        db.session.commit()


def gear_login(client):
    token = get_csrf_token(client, "/gear/login")
    response = client.post(
        "/gear/login", data={"password": "test-only-gear-password", "gear_name": "Sam", "csrf_token": token}
    )
    assert response.status_code == 302
    return token


def _add_unit(client, token, item_id, serial, **extra):
    data = {"item_id": item_id, "serial": serial, "brand": "Petzl", "csrf_token": token} | extra
    return client.post("/gear/units/add", data=data, follow_redirects=True)


def _unit(app, serial):
    with app.app_context():
        unit = GearUnit.query.filter_by(serial=serial).one()
        return unit.id, unit.manufacture_date, unit.manufacture_date_source, unit.status


# --- access ---------------------------------------------------------------

def test_committee_login_does_not_open_register(client):
    login(client)
    response = client.get("/gear")
    assert response.status_code == 302 and "/gear/login" in response.headers["Location"]


def test_wrong_gear_password_rejected(client):
    token = get_csrf_token(client, "/gear/login")
    client.post("/gear/login", data={"password": "test-only-password", "csrf_token": token})
    assert client.get("/gear").status_code == 302


def test_gear_password_opens_register(client):
    gear_login(client)
    assert client.get("/gear").status_code == 200


# --- serial date decoding --------------------------------------------------
# Real serials from the club's kit plus the manufacturers' own published
# examples. Month/year-only codes decode to the 1st of the month / 1 Jan.

@pytest.mark.parametrize(
    "brand, serial, expected, rule",
    [
        # Petzl, current format: YY + month letter + batch + counter
        ("Petzl", "25F0655055501", date(2025, 6, 1), "petzl-yy-month"),  # club kit, bought 2025
        ("Petzl", "25F0660619322", date(2025, 6, 1), "petzl-yy-month"),  # club kit, bought 2025
        ("Petzl", "17H0093785262", date(2017, 8, 1), "petzl-yy-month"),
        ("Petzl", "21 C 1234567 123", date(2021, 3, 1), "petzl-yy-month"),
        # Petzl, pre-2016/17 format: YY + day of year + inspector + number
        ("Petzl", "06 035 AB 3261", date(2006, 2, 4), "petzl-yy-day"),
        ("Petzl", "14125AB1234", date(2014, 5, 5), "petzl-yy-day"),
        ("Petzl", "20366AB0001", date(2020, 12, 31), "petzl-yy-day"),  # leap year
        # DMM: club samples, then dmmwales.com examples
        ("DMM", "251702322", date(2025, 6, 19), "dmm-yy-day"),
        ("DMM", "251402608", date(2025, 5, 20), "dmm-yy-day"),
        ("DMM", "241001450A", date(2024, 4, 9), "dmm-yy-day"),
        ("DMM", "24223060307 R", date(2024, 8, 10), "dmm-yy-day"),
        ("DMM", "230623904 / 2024", date(2024, 1, 1), "dmm-pn-year"),
        ("DMM", "S-2024-0085-017-034", date(2024, 3, 25), "dmm-rope"),
        ("DMM", "24 B", date(2024, 1, 1), "dmm-passive-year"),
        # Wild Country: club samples, then the 2016 Syncro recall code
        ("Wild Country", "05A0725", date(2025, 7, 1), "wildcountry-mm-yy"),
        ("Wild Country", "06B1124", date(2024, 11, 1), "wildcountry-mm-yy"),
        ("Wild Country", "01 A 01 16", date(2016, 1, 1), "wildcountry-mm-yy"),
        # Mammut: club sample
        ("Mammut", "08 2025 2399821", date(2025, 8, 1), "mammut-mm-yyyy"),
    ],
)
def test_serials_decode(brand, serial, expected, rule):
    assert serial_dates.decode(brand, serial) == (expected, rule)


@pytest.mark.parametrize(
    "brand, serial",
    [
        ("Petzl", "21400AB1234"),  # day 400
        ("Petzl", "21000AB1234"),  # day 0
        ("Petzl", "99001AB1234"),  # 2099 - in the future
        ("Petzl", "21M1234567"),  # no 13th month
        ("Petzl", "ABC123"),
        ("DMM", "25400123"),
        ("Wild Country", "05A1325"),  # month 13
        ("Mammut", "13 2025 123"),
        ("Mammut", "", ),
    ],
)
def test_impossible_serials_rejected(brand, serial):
    assert serial_dates.decode(brand, serial) == (None, None)


@pytest.mark.parametrize("brand", ["Black Diamond", "Beal", "Edelrid", "Other", None])
def test_brands_without_a_decoder(brand):
    assert serial_dates.decode(brand, "21045AB1234") == (None, None)


# --- adding and inspecting units ------------------------------------------------

def test_add_unit_decodes_blank_date(client, app, harness):
    token = gear_login(client)
    response = _add_unit(client, token, harness["item"], "21045AB1234")
    assert b"check it against the label" in response.data
    _, made, source, _ = _unit(app, "21045AB1234")
    assert (made, source) == (date(2021, 2, 14), "decoded")


def test_typed_date_that_differs_is_manual(client, app, harness):
    token = gear_login(client)
    _add_unit(client, token, harness["item"], "21045AB1234", manufacture_date="2021-03-01")
    _, made, source, _ = _unit(app, "21045AB1234")
    assert (made, source) == (date(2021, 3, 1), "manual")


def test_duplicate_serial_rejected_case_insensitively(client, app, harness):
    token = gear_login(client)
    _add_unit(client, token, harness["item"], "abc-1")
    response = _add_unit(client, token, harness["item"], "ABC-1")
    assert b"already in the register" in response.data
    with app.app_context():
        assert GearUnit.query.filter_by(item_id=harness["item"]).count() == 1


def test_retire_reduces_stock(client, app, harness):
    token = gear_login(client)
    _add_unit(client, token, harness["item"], "H-1")
    unit_id, *_ = _unit(app, "H-1")
    response = client.post(
        f"/gear/units/{unit_id}/inspect",
        data={"grade": "retire", "notes": "Frayed tie-in", "reduce_stock": "1", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"stock reduced to 1" in response.data
    assert _unit(app, "H-1")[3] == "retired"
    with app.app_context():
        item = db.session.get(KitItem, harness["item"])
        assert (item.quantity_total, item.quantity_available) == (1, 1)


def test_retire_does_not_reduce_stock_below_loans(client, app, harness):
    with app.app_context():
        app_module._create_signout(db.session.get(KitItem, harness["item"]), 2, "Jo", "", "Tester")
        db.session.commit()
    token = gear_login(client)
    _add_unit(client, token, harness["item"], "H-2")
    unit_id, *_ = _unit(app, "H-2")
    response = client.post(
        f"/gear/units/{unit_id}/inspect",
        data={"grade": "retire", "reduce_stock": "1", "csrf_token": token},
        follow_redirects=True,
    )
    assert b"wasn&#39;t reduced" in response.data
    with app.app_context():
        item = db.session.get(KitItem, harness["item"])
        assert (item.quantity_total, item.quantity_available) == (2, 0)


def test_flags_past_lifespan_and_condition(app, harness):
    with app.app_context():
        unit = GearUnit(item_id=harness["item"], serial="OLD-1", manufacture_date=date(2010, 1, 1))
        db.session.add(unit)
        db.session.flush()
        assert unit.flags() == ["Never inspected", "Past lifespan"]
        db.session.add(Inspection(unit_id=unit.id, grade="needs_attention"))
        db.session.flush()
        db.session.expire(unit, ["inspections"])
        assert unit.flags() == ["Needs attention", "Past lifespan"]
        db.session.rollback()


def test_damaged_piece_blocks_its_rack(client, app, harness):
    with app.app_context():
        rack = Rack(name="Gear Test Rack", colour="Green", colour_hex="#00aa00")
        db.session.add(rack)
        db.session.flush()
        db.session.get(KitItem, harness["item"]).rack_id = rack.id
        unit = GearUnit(item_id=harness["item"], serial="R-1")
        db.session.add(unit)
        db.session.flush()
        db.session.add(Inspection(unit_id=unit.id, grade="needs_attention"))
        db.session.commit()
        blockers = app_module._rack_blockers(rack)
        assert any("R-1" in b and "Needs attention" in b for b in blockers)
        db.session.get(KitItem, harness["item"]).rack_id = None
        db.session.commit()


# --- CSV ------------------------------------------------------------------------

def _upload(client, token, text):
    return client.post(
        "/gear/import",
        data={"file": (io.BytesIO(text.encode("utf-8-sig")), "register.csv"), "csrf_token": token},
        content_type="multipart/form-data",
    )


def test_csv_preview_shows_errors_and_saves_nothing(client, app, harness):
    token = gear_login(client)
    text = (
        "item_name,serial,brand,manufacture_date,purchase_date,condition,notes\n"
        "Gear Test Harness,C-1,Petzl,14/02/2021,,good,\n"
        "No Such Item,C-2,,,,,\n"
        "Gear Test Harness,C-1,,,,,\n"
        "Gear Test Harness,C-3,,31/02/2021,,,\n"
        "Gear Test Harness,C-4,,,,shiny,\n"
    )
    html = _upload(client, token, text).get_data(as_text=True)
    assert "4 rows need fixing" in html
    assert "Line 3" in html and "Line 4" in html and "Line 5" in html and "Line 6" in html
    with app.app_context():
        assert GearUnit.query.filter_by(item_id=harness["item"]).count() == 0


def test_csv_imports_only_on_confirm(client, app, harness):
    token = gear_login(client)
    text = (
        "item_name,serial,brand,manufacture_date,purchase_date,condition,notes\n"
        "gear test harness,C-10,Petzl,,2022-05-01,Minor wear,From the old sheet\n"
        "Gear Test Harness,21045AB9999,petzl,,,,\n"
    )
    html = _upload(client, token, text).get_data(as_text=True)
    assert "2 pieces ready to import" in html
    with app.app_context():
        assert GearUnit.query.filter_by(item_id=harness["item"]).count() == 0

    client.post("/gear/import", data={"confirm": "1", "csv_text": text, "csrf_token": token})
    with app.app_context():
        units = {u.serial: u for u in GearUnit.query.filter_by(item_id=harness["item"])}
        assert set(units) == {"C-10", "21045AB9999"}
        assert units["C-10"].latest_inspection.grade == "minor_wear"
        assert units["C-10"].purchase_date == date(2022, 5, 1)
        assert units["21045AB9999"].manufacture_date == date(2021, 2, 14)
        assert units["21045AB9999"].brand == "Petzl"


def test_csv_missing_columns_rejected(client, harness):
    token = gear_login(client)
    html = _upload(client, token, "name,code\nx,y\n").get_data(as_text=True)
    assert "Missing column(s): item_name, serial" in html


def test_export_neutralises_formulas(client, app, harness):
    with app.app_context():
        db.session.add(GearUnit(item_id=harness["item"], serial="=HYPERLINK(1)", notes="+evil"))
        db.session.commit()
    gear_login(client)
    body = client.get("/gear/export.csv").get_data(as_text=True)
    assert "'=HYPERLINK(1)" in body and "'+evil" in body


def test_settings_save(client, app, harness):
    token = gear_login(client)
    data = {"csrf_token": token, f"max_age_{harness['sub']}": "12"}  # track checkbox omitted = off
    client.post("/gear/settings", data=data)
    with app.app_context():
        sub = db.session.get(Subcategory, harness["sub"])
        assert (sub.track_serials, sub.max_age_years) == (False, 12)
