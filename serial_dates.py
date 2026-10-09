"""Decode a manufacture date from a manufacturer's serial or batch number.

Each rule is based on the manufacturer's published marking scheme, checked
against real serials (see tests/test_gear.py). A decoded date only ever
pre-fills a field for the Gear Secretary to confirm against the label - it is
never trusted silently. Where a code only gives the month or year, the date
returned is the 1st of that month / 1 January, i.e. the oldest it could be,
so lifespan warnings err on the safe side.

Sources:
- Petzl: technical notices show the individual number as "YY M 0000000 0000"
  (year, month letter A-L, batch, counter); pre-2016/17 products used
  "YY DDD" (year, day of year) followed by inspector initials.
- DMM (dmmwales.com product markings): most metal and textile products
  "YY DDD XXXX" (year, day of year, serial); harness PN labels
  "XXXXXXXXX / YYYY"; ropes "S-YYYY-0DDD-..."; passive pro "YY <batch letter>".
- Wild Country: batch labels end in month + year, e.g. 05A0725 = July 2025
  (consistent with the 2016 Syncro recall code 01 A 01 16).
- Mammut: labels give the production date as MM YYYY before the serial,
  e.g. "08 2025 2399821" = August 2025.
- Black Diamond, Beal and Edelrid print the date on the label; no decoding.

To add a brand: write a rule, add it to RULES, and add real sample serials
with their known dates to tests/test_gear.py.
"""
import re
from datetime import date, timedelta

BRANDS = ["Petzl", "Black Diamond", "DMM", "Wild Country", "Mammut", "Beal", "Edelrid", "Other"]


def _day_of_year(year, day):
    if not 1 <= day <= 366:
        return None
    made = date(year, 1, 1) + timedelta(days=day - 1)
    return made if made.year == year else None


def _month(year, month):
    return date(year, month, 1) if 1 <= month <= 12 else None


_PETZL_NEW = re.compile(r"^(\d{2})\s*([A-L])\s*\d{7}")
_PETZL_OLD = re.compile(r"^(\d{2})\s*(\d{3})\s*[A-Z]{1,2}\s*\d{3,}")


def _petzl(serial):
    match = _PETZL_NEW.match(serial)
    if match:
        return _month(2000 + int(match.group(1)), ord(match.group(2)) - ord("A") + 1), "petzl-yy-month"
    match = _PETZL_OLD.match(serial)
    if match:
        return _day_of_year(2000 + int(match.group(1)), int(match.group(2))), "petzl-yy-day"
    return None, None


_DMM_PN = re.compile(r"^\d{6,10}\s*/\s*((?:19|20)\d{2})$")
_DMM_ROPE = re.compile(r"^[A-Z]\s*-\s*((?:19|20)\d{2})\s*-\s*(\d{3,4})\b")
_DMM_DAY = re.compile(r"^(\d{2})\s*(\d{3})\s*\d{4,7}\s*[A-Z]?$")
_DMM_PASSIVE = re.compile(r"^(\d{2})\s*[A-Z]$")


def _dmm(serial):
    # The PN check must come first: "230623904 / 2024" also looks like
    # YY DDD but its year is the one after the slash.
    match = _DMM_PN.match(serial)
    if match:
        return date(int(match.group(1)), 1, 1), "dmm-pn-year"
    match = _DMM_ROPE.match(serial)
    if match:
        return _day_of_year(int(match.group(1)), int(match.group(2))), "dmm-rope"
    match = _DMM_DAY.match(serial)
    if match:
        return _day_of_year(2000 + int(match.group(1)), int(match.group(2))), "dmm-yy-day"
    match = _DMM_PASSIVE.match(serial)
    if match:
        return date(2000 + int(match.group(1)), 1, 1), "dmm-passive-year"
    return None, None


_WILD_COUNTRY = re.compile(r"^\d{2}\s*[A-Z]\s*(\d{2})\s*(\d{2})$")


def _wild_country(serial):
    match = _WILD_COUNTRY.match(serial)
    if match:
        return _month(2000 + int(match.group(2)), int(match.group(1))), "wildcountry-mm-yy"
    return None, None


_MAMMUT = re.compile(r"^(\d{2})\s*[./-]?\s*((?:19|20)\d{2})\b")


def _mammut(serial):
    match = _MAMMUT.match(serial)
    if match:
        return _month(int(match.group(2)), int(match.group(1))), "mammut-mm-yyyy"
    return None, None


RULES = {
    "petzl": _petzl,
    "dmm": _dmm,
    "wild country": _wild_country,
    "mammut": _mammut,
}


def normalise_brand(raw):
    """Match free-text brand input to a known brand name, case-insensitively."""
    raw = (raw or "").strip()
    for brand in BRANDS:
        if brand.lower() == raw.lower():
            return brand
    return raw or None


def decode(brand, serial):
    """(manufacture_date, rule_name), or (None, None) if this brand/serial
    can't be decoded (unknown brand, unrecognised format, impossible or
    future date)."""
    rule = RULES.get((brand or "").strip().lower())
    serial = (serial or "").strip().upper()
    if not rule or not serial:
        return None, None
    made, name = rule(serial)
    if not made or made > date.today():
        return None, None
    return made, name
