import csv
import http.client
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Flask,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from markupsafe import Markup
from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import CreateColumn
from werkzeug.middleware.proxy_fix import ProxyFix

import serial_dates
from config import Config, clean_value
from models import (
    ACTION_GRADES,
    INSPECTION_GRADES,
    Category,
    EmailLog,
    GearUnit,
    Inspection,
    ItemGroup,
    ItemRequest,
    KitBundle,
    KitBundleLine,
    KitItem,
    Rack,
    RackLoan,
    SignOut,
    Subcategory,
    db,
    utcnow,
)

csrf = CSRFProtect()

# Basic brute-force throttle on the shared committee password: after this many
# wrong guesses in a row from the same IP, further attempts from it are
# blocked for a short cooldown. Keyed by IP rather than the session, since a
# session-stored counter resets for free the moment an attacker clears
# cookies. Lives only in this process's memory - fine given the confirmed
# single gunicorn worker (Procfile has no -w flag), and an accepted tradeoff
# for a lightweight nudge on a low-stakes shared password rather than a
# hardened rate-limiter; resets on every deploy/restart.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 30
_login_attempts = {}  # {ip: (attempt_count, locked_until_timestamp)}


def _login_locked_until(ip):
    """Seconds-since-epoch the given IP is locked out until, or 0 if it's
    not currently locked."""
    locked_until = _login_attempts.get(ip, (0, 0))[1]
    return locked_until if locked_until > time.time() else 0


def _record_failed_login(ip):
    attempts = _login_attempts.get(ip, (0, 0))[0] + 1
    if attempts >= LOGIN_MAX_ATTEMPTS:
        _login_attempts[ip] = (0, time.time() + LOGIN_LOCKOUT_SECONDS)
    else:
        _login_attempts[ip] = (attempts, 0)


def _clear_login_attempts(ip):
    _login_attempts.pop(ip, None)

SIGN_OUT_HISTORY_LIMIT = 100

# Deliberately loose (just "something@something.something") - this only
# exists to catch typos/garbage before it's stored against a request, not to
# fully validate deliverability. The field itself stays optional.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEX_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    # Render's proxy sits in front of gunicorn, so without this every request's
    # remote_addr is the proxy's - one person's wrong guesses would lock out
    # the whole committee. x_for=1 trusts only the hop Render itself appends.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)
    db.init_app(app)
    csrf.init_app(app)

    # Every POST form in the templates calls {{ csrf_field() }} once, rather
    # than each one spelling out its own <input type="hidden" name="csrf_token"
    # value="{{ csrf_token() }}">, so there's exactly one place to get the
    # markup right across ~20 forms.
    app.jinja_env.globals["csrf_field"] = lambda: Markup(
        f'<input type="hidden" name="csrf_token" value="{generate_csrf()}">'
    )
    app.jinja_env.filters["relative_time"] = _relative_time

    @app.before_request
    def _relax_secure_cookie_for_debug():
        # SESSION_COOKIE_SECURE is seeded from Config.DEBUG (the FLASK_DEBUG
        # env var) above, but the documented local-dev command in the
        # README (`flask run --debug`) flips app.debug via a CLI flag
        # *after* create_app() already ran - that wouldn't be reflected
        # here otherwise, and a Secure cookie is silently dropped by the
        # browser on plain http://127.0.0.1, breaking login. Recompute it
        # from app.debug on every request so either way of enabling debug
        # mode (the CLI flag or the env var) relaxes it the same way.
        app.config["SESSION_COOKIE_SECURE"] = not app.debug

    with app.app_context():
        db.create_all()
        _ensure_schema()

    register_routes(app)
    return app


def _ensure_schema():
    """Bring an existing database's schema up to date with models.py.
    db.create_all() only creates missing TABLES - it never alters a table
    that already exists - so a genuinely new column (like Category.
    member_visible), or a changed constraint on an existing column (like
    KitBundleLine.item_id becoming nullable), needs its own small step here
    instead of silently doing nothing on every deploy. Keep each step
    idempotent (check before altering) and narrow (exactly the change that
    happened, not a general migration tool) - anything bigger than that
    (renaming a column, changing a type) should still be done by hand against
    the database directly.

    Runs once per worker process at import time (create_app() is called at
    module scope below). Fine with the current single-worker `gunicorn
    app:app` (Procfile has no -w flag) - if that's ever changed to run
    multiple workers, they'd all race to ALTER the same tables concurrently
    on boot. Move this behind a startup lock (or a one-off predeploy step)
    before scaling workers.
    """
    inspector = inspect(db.engine)
    existing_columns = {col["name"] for col in inspector.get_columns("categories")}
    if "member_visible" not in existing_columns:
        column_ddl = CreateColumn(Category.__table__.columns["member_visible"]).compile(dialect=db.engine.dialect)
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE categories ADD COLUMN {column_ddl}"))

    bundle_line_columns = {col["name"]: col for col in inspector.get_columns("kit_bundle_lines")}
    if "group_id" not in bundle_line_columns:
        column_ddl = CreateColumn(KitBundleLine.__table__.columns["group_id"]).compile(dialect=db.engine.dialect)
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE kit_bundle_lines ADD COLUMN {column_ddl}"))
        # Re-read the columns now group_id exists, so the item_id nullability
        # check just below sees a fresh/accurate picture on this same boot.
        bundle_line_columns = {col["name"]: col for col in inspect(db.engine).get_columns("kit_bundle_lines")}

    # item_id also changed from NOT NULL to nullable (a bundle line can now
    # point at a group instead of a specific item). A brand new database
    # created from this version of models.py gets it nullable from the start
    # via db.create_all(), but a database that already existed before this
    # change (like production, mid-deploy, the first time this ran) still has
    # the old constraint at the database level - seed_data.py crashed on
    # exactly this, inserting a group-only line with item_id=None. Postgres
    # can drop a NOT NULL constraint as a quick in-place catalog change, so
    # it's safe to just do it here rather than requiring a manual command.
    item_id_col = bundle_line_columns.get("item_id")
    if item_id_col is not None and not item_id_col["nullable"]:
        if db.engine.dialect.name == "postgresql":
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE kit_bundle_lines ALTER COLUMN item_id DROP NOT NULL"))
        else:
            # SQLite doesn't support ALTER COLUMN at all. Only matters for a
            # local dev database created before this change - delete
            # instance/kitstore.db and let it recreate from scratch instead.
            print(
                "kit_bundle_lines.item_id is still NOT NULL and this isn't Postgres - "
                "delete instance/kitstore.db and restart the app to recreate it from scratch."
            )

    # New pickup/return session columns on item_requests - same "add whatever
    # columns are missing" idea as above, just looped since there are four of
    # them and they're all plain nullable strings.
    request_columns = {col["name"] for col in inspector.get_columns("item_requests")}
    for column_name in ("pickup_slot", "pickup_other", "return_slot", "return_other"):
        if column_name not in request_columns:
            column_ddl = CreateColumn(ItemRequest.__table__.columns[column_name]).compile(dialect=db.engine.dialect)
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE item_requests ADD COLUMN {column_ddl}"))

    _add_missing_columns(KitItem, ["rack_id"])
    _add_missing_columns(Subcategory, ["track_serials", "max_age_years"])
    _add_missing_columns(SignOut, ["rack_loan_id", "due_date", "borrower_email"])
    _add_missing_columns(EmailLog, ["status", "request_group"])
    _add_missing_columns(ItemRequest, ["approved_quantity", "claimed_by", "handover_note", "deny_reason", "status_token"])
    _add_missing_columns(GearUnit, ["retired_reason"])
    _add_missing_columns(SignOut, ["gear_unit_id"])
    _backfill_request_tokens()

    # Case-insensitive uniqueness backstop matching the app-level
    # _name_exists() checks - those do a SELECT then a separate INSERT/
    # UPDATE, so two near-simultaneous submissions could both pass the check
    # and create case-variant duplicates ("Rope" / "rope") without a
    # DB-level constraint behind them. IntegrityError from these is caught
    # by the SQLAlchemyError handler below and turned into a flash message.
    # Plain "IF NOT EXISTS" (rather than the existing_columns-style inspector
    # check used above) because SQLite/Postgres can't reflect an expression
    # index back through inspector.get_indexes() the way a plain column
    # index would, so there's nothing reliable to check first - these
    # statements are just safe to re-run unconditionally every boot.
    unique_ci_indexes = [
        ("uq_categories_name_ci", "categories", "lower(name)"),
        ("uq_kit_items_name_ci", "kit_items", "lower(name)"),
        ("uq_item_groups_name_ci", "item_groups", "lower(name)"),
        ("uq_kit_bundles_name_ci", "kit_bundles", "lower(name)"),
        ("uq_racks_name_ci", "racks", "lower(name)"),
        ("uq_gear_units_serial_ci", "gear_units", "lower(serial)"),
        ("uq_subcategories_name_category_ci", "subcategories", "lower(name), category_id"),
    ]
    with db.engine.begin() as conn:
        for index_name, table_name, expression in unique_ci_indexes:
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({expression})"))


def _backfill_request_tokens():
    """Give every request a request_group and a status_token, so older rows
    work with the per-checkout accept/deny/status flows."""
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE item_requests SET request_group = 'single-' || id WHERE request_group IS NULL"))
        groups = [row[0] for row in conn.execute(
            text("SELECT DISTINCT request_group FROM item_requests WHERE status_token IS NULL")
        )]
        for group in groups:
            conn.execute(
                text("UPDATE item_requests SET status_token = :token WHERE request_group = :group"),
                {"token": secrets.token_urlsafe(24), "group": group},
            )


def _add_missing_columns(model, column_names):
    """Add any of the given nullable columns that an existing table lacks."""
    table = model.__table__
    existing = {col["name"] for col in inspect(db.engine).get_columns(table.name)}
    for name in column_names:
        if name not in existing:
            column_ddl = CreateColumn(table.columns[name]).compile(dialect=db.engine.dialect)
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column_ddl}"))


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_committee"):
            flash("Please log in as a committee member to view that page.", "error")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def gear_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_gear"):
            flash("Log in with the gear password to use the gear register.", "error")
            return redirect(url_for("gear_login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _relative_time(dt):
    """"3 days ago"-style rendering of a past datetime, for an at-a-glance
    sense of how long something's been signed out - registered as the
    `relative_time` Jinja filter in create_app(), alongside every other
    template-facing helper. Shown next to (not instead of) the exact
    timestamp everywhere it's used, so there's still something to cross-check
    against."""
    seconds = (utcnow() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def parse_quantity(raw, default=1):
    """Parse a basket-quantity form field into a whole number >= 1.

    Blank input falls back to `default`. Non-positive numbers are clamped up
    to 1, since a basket "quantity of 0" doesn't mean anything different from
    leaving the item out. Anything that isn't a whole number at all (e.g.
    someone pasted text into a number field) returns None instead of raising,
    so callers can show a friendly error instead of the request crashing."""
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(1, value)


def parse_positive_int(raw):
    """Parse a form field into a strictly positive whole number (used for
    real stock numbers: sign-out quantities, item totals). Unlike
    parse_quantity, zero/negative numbers are treated as invalid rather than
    silently clamped to 1, since "I asked for 0" and "I asked for too many"
    are different mistakes that deserve different error messages. Returns
    None for blank, non-numeric, zero or negative input."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _safe_next_url(raw):
    """Only follow a ?next= that stays on this site (a path, not an absolute
    or protocol-relative URL), so a crafted login link can't send a committee
    member somewhere else after they log in."""
    if raw and raw.startswith("/") and not raw.startswith(("//", "/\\")):
        return raw
    return None


def _basket_other_index(key):
    """Index from an "other:<n>" basket key, or None if it's malformed."""
    raw = key.split(":", 1)[1]
    return int(raw) if raw.isdigit() else None


def form_id_or_404(model, field):
    """Look up a row by an id submitted in a form field, 404ing on a missing
    or non-numeric value instead of passing it raw to the database (Postgres
    raises DataError on e.g. 'abc' for an integer column)."""
    row_id = parse_positive_int(request.form.get(field))
    if row_id is None:
        abort(404)
    return db.get_or_404(model, row_id)


def build_kit_entries(items, groups, available_first=False):
    """Merge ungrouped KitItems and ItemGroups into one alphabetically-sorted
    list for display, so members browsing or searching see a single combined
    list instead of two separate "Kit" / "Individual Items" tables - having
    to check two tables to see everything on offer was the actual source of
    confusion, not the grouping/abstraction itself. Each entry is a plain
    dict rather than the raw model instance since KitItem and ItemGroup
    don't share a base class, but do share every field the templates need
    (name, subcategory, quantity_available/total, id)."""
    entries = [
        {
            "kind": "group",
            "id": group.id,
            "name": group.name,
            "category": group.category,
            "subcategory": group.subcategory,
            "quantity_available": group.quantity_available,
            "quantity_total": group.quantity_total,
            "obj": group,
        }
        for group in groups
    ] + [
        {
            "kind": "item",
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "subcategory": item.subcategory,
            "quantity_available": item.quantity_available,
            "quantity_total": item.quantity_total,
            "obj": item,
        }
        for item in items
    ]
    if available_first:
        entries.sort(key=lambda entry: (entry["quantity_available"] <= 0, entry["name"].lower()))
    else:
        entries.sort(key=lambda entry: entry["name"].lower())
    return entries


# --------------------------------------------------------------- sign-outs
# Shared by sign_out_item/sign_out_group (manual sign-out) and collect_request
# (turning an approved request into a real sign-out) so the "decrement stock,
# record a SignOut" step and the "split a group request across its items"
# step each have exactly one implementation instead of being copy-pasted
# across both flows.

class InsufficientStockError(Exception):
    """Raised by _create_signout when the atomic stock check fails - i.e. a
    concurrent sign-out already used up the remaining stock between this
    route's own "is there enough?" pre-check and this call actually running.
    Callers catch this and flash item.name/item.quantity_available - the
    latter is refreshed from the database before raising (see below), so it
    reflects however much is actually left right now, not whatever stale
    figure the route's own pre-check happened to read earlier."""

    def __init__(self, item):
        self.item = item
        super().__init__(f"Not enough '{item.name}' available.")


def _create_signout(
    item, quantity, borrower_name, notes, checked_out_by, due_date=None, borrower_email=None, gear_unit=None
):
    """Decrement one KitItem's available stock and record a SignOut for it.
    Caller commits (and flushes first if it needs the new row's id before
    commit, e.g. to link it back to an ItemRequest).

    Every route already does its own "is there enough?" check before calling
    this, using whatever quantity_available it last read - but that read and
    this write aren't atomic, so two committee members racing to sign out
    the last unit could both pass that check. The UPDATE below re-checks
    quantity_available >= quantity as part of the same statement that
    decrements it, so only one of two racing requests can actually succeed;
    the other gets rowcount 0 and InsufficientStockError instead of silently
    driving stock negative. Uses Core's update() (not the ORM attribute
    assignment this replaced) specifically so the WHERE clause and the
    decrement happen as one atomic statement at the database - item's
    in-memory quantity_available is intentionally left stale on success
    (expired below) since no caller reads it again after this returns."""
    result = db.session.execute(
        KitItem.__table__.update()
        .where(KitItem.id == item.id, KitItem.quantity_available >= quantity)
        .values(quantity_available=KitItem.quantity_available - quantity)
    )
    if result.rowcount == 0:
        db.session.refresh(item, ["quantity_available"])
        raise InsufficientStockError(item)
    db.session.expire(item, ["quantity_available"])

    signout = SignOut(
        item_id=item.id,
        borrower_name=borrower_name,
        quantity=quantity,
        notes=notes,
        checked_out_by=checked_out_by,
        due_date=due_date,
        borrower_email=borrower_email or None,
        gear_unit_id=gear_unit.id if gear_unit else None,
    )
    db.session.add(signout)
    return signout


def _serial_from_form(item, raw):
    """(unit, error) for an optional serial typed at sign-out: blank is fine,
    otherwise it must be an in-service piece of this item that isn't out."""
    serial = (raw or "").strip()
    if not serial:
        return None, None
    unit = GearUnit.query.filter(func.lower(GearUnit.serial) == serial.lower()).first()
    if not unit or unit.item_id != item.id:
        return None, f"Serial {serial} isn't registered as a {item.name}."
    if not unit.in_service:
        return None, f"{serial} is retired — it can't go out."
    if unit.open_loan:
        return None, f"{serial} is already signed out to {unit.open_loan.borrower_name}."
    return unit, None


def _open_loan_quantity(item_id):
    """SQL expression: units of this item covered by loans still out."""
    return (
        select(func.coalesce(func.sum(SignOut.quantity), 0))
        .where(SignOut.item_id == item_id, SignOut.signed_in_at.is_(None))
        .scalar_subquery()
    )


def _unrecorded_out():
    """[(item, units)] for active items with stock marked out that no open
    loan accounts for - e.g. kit imported from the old spreadsheet as
    "out" without a borrower. Those units can't be signed in through a loan."""
    open_by_item = dict(
        db.session.query(SignOut.item_id, func.sum(SignOut.quantity))
        .filter(SignOut.signed_in_at.is_(None))
        .group_by(SignOut.item_id)
        .all()
    )
    items = (
        KitItem.query.filter(KitItem.active.is_(True), KitItem.quantity_available < KitItem.quantity_total)
        .order_by(KitItem.name)
        .all()
    )
    result = []
    for item in items:
        units = item.quantity_total - item.quantity_available - (open_by_item.get(item.id) or 0)
        if units > 0:
            result.append((item, units))
    return result


def _return_unrecorded(item, quantity, returned_by):
    """Put back up to `quantity` unrecorded units of an item, atomically: the
    UPDATE re-checks that stock out minus open loans still covers it, so a
    double-tap (or a real loan) can never push stock past what's accounted
    for. Records a closed SignOut as the audit trail. Caller commits."""
    result = db.session.execute(
        KitItem.__table__.update()
        .where(
            KitItem.id == item.id,
            KitItem.quantity_total - KitItem.quantity_available - _open_loan_quantity(item.id) >= quantity,
        )
        .values(quantity_available=KitItem.quantity_available + quantity)
    )
    if result.rowcount != 1:
        return False
    now = utcnow()
    db.session.add(
        SignOut(
            item_id=item.id,
            borrower_name="Unrecorded loan",
            quantity=quantity,
            notes="Signed back in manually - marked out with no loan record",
            checked_out_by=returned_by,
            signed_out_at=now,
            signed_in_at=now,
        )
    )
    db.session.expire(item, ["quantity_available"])
    return True


def _return_signout(signout):
    """Mark one loan as returned and put its stock back, atomically - only
    the first of two racing sign-ins succeeds. Caller commits."""
    result = db.session.execute(
        SignOut.__table__.update()
        .where(SignOut.id == signout.id, SignOut.signed_in_at.is_(None))
        .values(signed_in_at=utcnow())
    )
    if result.rowcount != 1:
        return False
    db.session.execute(
        KitItem.__table__.update()
        .where(KitItem.id == signout.item_id)
        .values(quantity_available=KitItem.quantity_available + signout.quantity)
    )
    return True


def _return_part(signout, quantity, returned_by):
    """Sign back in `quantity` of a loan that has more out, leaving the rest
    on the loan. The returned part is recorded as its own closed SignOut.
    Caller commits."""
    result = db.session.execute(
        SignOut.__table__.update()
        .where(SignOut.id == signout.id, SignOut.signed_in_at.is_(None), SignOut.quantity > quantity)
        .values(quantity=SignOut.quantity - quantity)
    )
    if result.rowcount != 1:
        return False
    db.session.add(
        SignOut(
            item_id=signout.item_id,
            borrower_name=signout.borrower_name,
            borrower_email=signout.borrower_email,
            quantity=quantity,
            notes=signout.notes,
            checked_out_by=returned_by,
            due_date=signout.due_date,
            signed_out_at=signout.signed_out_at,
            signed_in_at=utcnow(),
        )
    )
    db.session.execute(
        KitItem.__table__.update()
        .where(KitItem.id == signout.item_id)
        .values(quantity_available=KitItem.quantity_available + quantity)
    )
    db.session.expire(signout)
    return True


def _undo_return(signout):
    """Reopen a loan signed in by mistake, taking its stock back out. Fails
    if the loan isn't signed in, or the stock has gone out again since."""
    reopened = db.session.execute(
        SignOut.__table__.update()
        .where(SignOut.id == signout.id, SignOut.signed_in_at.isnot(None))
        .values(signed_in_at=None)
    )
    if reopened.rowcount != 1:
        return False
    taken = db.session.execute(
        KitItem.__table__.update()
        .where(KitItem.id == signout.item_id, KitItem.quantity_available >= signout.quantity)
        .values(quantity_available=KitItem.quantity_available - signout.quantity)
    )
    return taken.rowcount == 1


def _loans_by_borrower(loans):
    """Open loans grouped per borrower, overdue borrowers first, then by the
    soonest due date."""
    groups = {}
    for loan in loans:
        key = (loan.borrower_name.strip().lower(), (loan.borrower_email or "").lower())
        groups.setdefault(key, {"name": loan.borrower_name, "email": loan.borrower_email, "loans": []})
        groups[key]["loans"].append(loan)
    result = list(groups.values())
    for group in result:
        group["overdue"] = any(loan.is_overdue for loan in group["loans"])
        dues = [loan.due_date for loan in group["loans"] if loan.due_date]
        group["next_due"] = min(dues) if dues else None
        group["search"] = " ".join([group["name"], group["email"] or ""] + [l.item.name for l in group["loans"]]).lower()
    result.sort(key=lambda g: (not g["overdue"], g["next_due"] or date.max, g["name"].lower()))
    return result


def _rack_blockers(rack):
    """Reasons this rack can't be signed out right now (empty list = ready)."""
    if not rack.items:
        return ["No kit has been assigned to this rack yet."]
    blockers = []
    for item in rack.items:
        if not item.active:
            blockers.append(f"{item.name} is archived")
        elif item.quantity_available < item.quantity_total:
            out = item.quantity_total - item.quantity_available
            blockers.append(f"{item.name}: {out} of {item.quantity_total} still out")
        for unit in item.units:
            latest = unit.latest_inspection
            if unit.in_service and latest and latest.grade in ACTION_GRADES:
                blockers.append(f"{item.name} ({unit.serial}) is marked '{latest.grade_label}'")
    return blockers


def _borrower_names():
    """Previously-used borrower names, for autocomplete on sign-out forms
    (cuts down on near-duplicate spellings of the same person)."""
    return [
        row[0] for row in db.session.query(SignOut.borrower_name).distinct().order_by(SignOut.borrower_name).all()
    ]


def _default_due_date():
    return date.today() + timedelta(days=current_app.config["DEFAULT_LOAN_DAYS"])


def _due_date_from_form():
    """(due_date, error_message) from a sign-out form's due_date field,
    falling back to the default loan length when it's left blank."""
    raw = (request.form.get("due_date") or "").strip()
    if not raw:
        return _default_due_date(), None
    due = parse_date(raw)
    if due is None or due < date.today():
        return None, "Choose a return date that isn't in the past."
    return due, None


def _claim_request_collection(item_request, signout_id):
    """Link an approved request to its SignOut, but only if nobody else has
    collected it in the meantime - a double-tapped Collect would otherwise
    sign the same kit out twice. Returns False if it was already claimed;
    the caller rolls back."""
    result = db.session.execute(
        ItemRequest.__table__.update()
        .where(
            ItemRequest.id == item_request.id,
            ItemRequest.status == "approved",
            ItemRequest.fulfilled_signout_id.is_(None),
        )
        .values(fulfilled_signout_id=signout_id)
    )
    db.session.expire(item_request, ["fulfilled_signout_id"])
    return result.rowcount == 1


def _set_item_total(item, new_total):
    """Change an item's total stock, shifting quantity_available by the same
    amount in one UPDATE so a sign-out landing mid-edit isn't overwritten.
    Refuses (returns False) if the new total is below what's currently out."""
    result = db.session.execute(
        KitItem.__table__.update()
        .where(
            KitItem.id == item.id,
            KitItem.quantity_total - KitItem.quantity_available <= new_total,
        )
        .values(
            quantity_available=KitItem.quantity_available + (new_total - KitItem.quantity_total),
            quantity_total=new_total,
        )
    )
    db.session.expire(item, ["quantity_available", "quantity_total"])
    return result.rowcount == 1


def _name_exists(model, name, exclude_id=None, **scope_filters):
    """Case-insensitive "does a row with this name already exist" check,
    shared by every add/edit route for Category/Subcategory/KitItem/
    ItemGroup/KitBundle (each of which has its own uniqueness rule, but the
    same func.lower(...) == name.lower() comparison). `exclude_id` excludes
    the row being edited, so renaming something to its own current name
    doesn't flag itself as a clash. `scope_filters` adds extra equality
    filters (e.g. category_id=category.id for Subcategory) for names that
    only need to be unique within a parent, not globally."""
    query = model.query.filter(func.lower(model.name) == name.lower())
    for column, value in scope_filters.items():
        query = query.filter(getattr(model, column) == value)
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _parse_group_split_quantities(candidates, form):
    """Parse a `quantity_<item_id>` field per candidate KitItem (the members
    of an ItemGroup) out of a submitted form, validating each one against its
    own available stock as we go. Returns (splits, error_message): on success
    `splits` is a list of (item, quantity) tuples for every candidate that had
    a quantity entered and `error_message` is None; on the first invalid
    field, `splits` is None and `error_message` is a ready-to-flash string."""
    splits = []
    for candidate in candidates:
        raw = form.get(f"quantity_{candidate.id}", "").strip()
        if not raw:
            continue
        quantity = parse_positive_int(raw)
        if quantity is None:
            return None, f"Quantity for '{candidate.name}' must be a whole number of at least 1."
        if quantity > candidate.quantity_available:
            return None, f"Only {candidate.quantity_available} of '{candidate.name}' available."
        splits.append((candidate, quantity))
    return splits, None


# ------------------------------------------------------------------ basket
# The basket is stored entirely in the member's session (no login, no DB
# rows) until they check out, at which point it becomes real ItemRequest
# rows. Shape: {"items": {"<kit_item_id>": quantity}, "groups": {"<item_group_id>": quantity},
# "other": [{"name":..., "quantity":...}]}

def get_basket():
    basket = session.get("basket")
    if not basket:
        basket = {"items": {}, "groups": {}, "other": []}
        session["basket"] = basket
    # Sessions created before "groups" existed won't have the key - back-fill
    # it rather than letting basket["groups"] raise a KeyError below.
    basket.setdefault("groups", {})
    return basket


def basket_line_items(basket):
    """Resolve the raw session basket into a list of {key, name, quantity,
    group_id} for display. Deliberately skips items/groups that have since
    been deleted or archived from the catalogue, so a stale reference left
    over from a mid-session change never shows up as a phantom basket line.
    group_id is None for ordinary item/custom lines, and lets the checkout
    step below know to record a group request instead of a specific item."""
    lines = []
    for item_id_str, quantity in basket.get("items", {}).items():
        item = db.session.get(KitItem, int(item_id_str))
        if item and item.active and item.rack_id is None:
            lines.append(
                {
                    "key": f"item:{item_id_str}",
                    "name": item.name,
                    "quantity": quantity,
                    "group_id": None,
                    "available": item.quantity_available,
                }
            )
    for group_id_str, quantity in basket.get("groups", {}).items():
        group = db.session.get(ItemGroup, int(group_id_str))
        if group and group.active_items:
            lines.append(
                {
                    "key": f"group:{group_id_str}",
                    "name": group.name,
                    "quantity": quantity,
                    "group_id": group.id,
                    "available": group.quantity_available,
                }
            )
    for idx, other in enumerate(basket.get("other", [])):
        lines.append(
            {
                "key": f"other:{idx}",
                "name": other["name"],
                "quantity": other["quantity"],
                "group_id": None,
                "available": None,
            }
        )
    return lines


def _stock_note(wanted, available):
    if wanted <= available:
        return ""
    if available == 0:
        return " It's all out right now — committee may offer a substitute."
    return f" Only {available} available — committee may offer fewer."


def basket_line_count(basket):
    """Total items in the basket, for the nav badge. Computed from
    basket_line_items() rather than the raw session dict, so the badge can
    never drift out of sync with what the basket page actually shows (e.g. if
    an item in someone's basket gets deleted or archived mid-session)."""
    return sum(line["quantity"] for line in basket_line_items(basket))


def group_pending_requests(requests):
    """Group flat ItemRequest rows back into one basket-style block per checkout."""
    groups = {}
    order = []
    for r in requests:
        key = r.request_group or f"single-{r.id}"
        if key not in groups:
            groups[key] = {
                "request_group": key,
                "requester_name": r.requester_name,
                "requester_email": r.requester_email,
                "message": r.message,
                "pickup_display": r.pickup_display,
                "return_display": r.return_display,
                "needed_from": r.needed_from,
                "needed_to": r.needed_to,
                "claimed_by": r.claimed_by,
                "handover_note": r.handover_note,
                "created_at": r.created_at,
                "lines": [],  # named "lines" not "items" - dicts already have a built-in .items()
            }
            order.append(key)
        groups[key]["lines"].append(r)
    return [groups[key] for key in order]


def line_available(line):
    """Units on the shelf right now for a request or basket line's item or
    group, or None for a free-text line that matches nothing."""
    if line.group_id:
        group = db.session.get(ItemGroup, line.group_id)
        return group.quantity_available if group else None
    item = line.matching_item
    return item.quantity_available if item else None


def request_lines(request_group):
    lines = ItemRequest.query.filter_by(request_group=request_group).order_by(ItemRequest.id).all()
    if not lines:
        abort(404)
    return lines


def recipients(raw):
    return [address.strip() for address in (raw or "").split(",") if address.strip()]


def send_request_email(kind, lines, **extra):
    """Email about one checkout: "received" to the member, "new" to the
    requests inbox, or "update" to the member after committee act on it."""
    first = lines[0]
    status_url = url_for("request_status", token=first.status_token, _external=True)
    html = render_template(
        f"email/request_{'update' if kind == 'update' else kind}.html",
        lines=lines,
        first=first,
        status_url=status_url,
        admin_url=url_for("admin", _external=True),
        **extra,
    )
    if kind == "new":
        to_list = recipients(current_app.config.get("REQUESTS_EMAIL"))
        subject = extra.get("subject") or f"New kit request from {first.requester_name}"
    else:
        to_list = [first.requester_email] if first.requester_email else []
        subject = extra.get("subject") or "Your CUMC kit request"
    # The inbox replies straight to the member; the member replies to the club.
    reply_to = first.requester_email if kind == "new" else None
    for to in to_list:
        send_email(to, subject, html, reply_to=reply_to)


# ----------------------------------------------------------- gear register
REGISTER_CSV_COLUMNS = ["item_name", "serial", "brand", "manufacture_date", "purchase_date", "condition", "notes"]
REGISTER_CSV_MAX_ROWS = 2000


def _serial_exists(serial, exclude_id=None):
    query = GearUnit.query.filter(func.lower(GearUnit.serial) == serial.lower())
    if exclude_id is not None:
        query = query.filter(GearUnit.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _manufacture_date_and_source(brand, serial, raw_date):
    """The manufacture date to store and where it came from: a typed date
    counts as decoded only if it matches what the serial decodes to."""
    decoded, _ = serial_dates.decode(brand, serial)
    if raw_date:
        return raw_date, "decoded" if raw_date == decoded else "manual"
    return (decoded, "decoded") if decoded else (None, None)


def _parse_csv_date(raw):
    """ISO (2021-02-14) or UK (14/02/2021) date; (date, ok)."""
    raw = (raw or "").strip()
    if not raw:
        return None, True
    iso = parse_date(raw)
    if iso:
        return iso, True
    match = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", raw)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1))), True
        except ValueError:
            pass
    return None, False


# Subcategory name keywords -> lifespan in years (None = tracked, no age limit).
PPE_DEFAULTS = [
    (("harness", "helmet", "sling", "rope", "prussik", "daisy"), 10),
    (("carabiner", "quickdraw", "atc", "belay", "descender", "assisted", "maillon"), None),
]


def apply_ppe_defaults():
    """Track every PPE subcategory, filling in a lifespan only where none is
    set. Returns how many subcategories changed."""
    changed = 0
    for sub in Subcategory.query.all():
        name = sub.name.lower()
        for keywords, lifespan in PPE_DEFAULTS:
            if any(word in name for word in keywords):
                if not sub.track_serials or (lifespan and not sub.max_age_years):
                    sub.track_serials = True
                    sub.max_age_years = sub.max_age_years or lifespan
                    changed += 1
                break
    return changed


INSPECTION_DUE_AFTER = timedelta(days=335)  # about 11 months - a yearly check


def _inspection_queue():
    """In-service pieces without a real condition check in the last ~11
    months, past-lifespan ones first, then by item and serial."""
    cutoff = utcnow() - INSPECTION_DUE_AFTER
    units = GearUnit.query.filter_by(status="in_service").join(KitItem).order_by(KitItem.name, GearUnit.serial).all()
    queue = [
        unit for unit in units
        if not unit.latest_grade_inspection or unit.latest_grade_inspection.inspected_at < cutoff
    ]
    queue.sort(key=lambda unit: not unit.past_lifespan)
    return queue, len(units)


def _parse_grade(raw):
    raw = (raw or "").strip().lower()
    for key, label in INSPECTION_GRADES.items():
        if raw in (key, label.lower(), key.replace("_", " ")):
            return key
    return None


def _validate_register_csv(csv_text):
    """Parse an uploaded register CSV without saving anything. Returns
    (rows, errors): rows are ready-to-create dicts, errors are (line, message)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    missing = [c for c in ("item_name", "serial") if c not in headers]
    if missing:
        return [], [(1, f"Missing column(s): {', '.join(missing)}. Download the template for the expected headers.")]

    items_by_name = {item.name.lower(): item for item in KitItem.query.filter_by(active=True)}
    existing_serials = {s.lower() for (s,) in db.session.query(GearUnit.serial)}
    seen = set()
    rows, errors = [], []
    for line, raw_row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        if not any(row.values()):
            continue
        if len(rows) + len(errors) >= REGISTER_CSV_MAX_ROWS:
            errors.append((line, f"Too many rows - import at most {REGISTER_CSV_MAX_ROWS} at a time."))
            break
        problems = []
        item = items_by_name.get(row.get("item_name", "").lower())
        if not item:
            problems.append(f"no active kit item called '{row.get('item_name', '')}'")
        serial = row.get("serial", "")
        if not serial:
            problems.append("serial is blank")
        elif serial.lower() in existing_serials:
            problems.append(f"serial {serial} is already registered")
        elif serial.lower() in seen:
            problems.append(f"serial {serial} appears twice in this file")
        made, made_ok = _parse_csv_date(row.get("manufacture_date"))
        bought, bought_ok = _parse_csv_date(row.get("purchase_date"))
        if not made_ok:
            problems.append("manufacture_date isn't a date (use 2021-02-14 or 14/02/2021)")
        if not bought_ok:
            problems.append("purchase_date isn't a date (use 2021-02-14 or 14/02/2021)")
        grade = None
        if row.get("condition"):
            grade = _parse_grade(row["condition"])
            if not grade:
                problems.append(f"condition must be one of: {', '.join(INSPECTION_GRADES.values())}")
        if problems:
            errors.append((line, "; ".join(problems)))
            continue
        seen.add(serial.lower())
        brand = serial_dates.normalise_brand(row.get("brand"))
        made, source = _manufacture_date_and_source(brand, serial, made)
        rows.append(
            {
                "item": item,
                "serial": serial,
                "brand": brand,
                "manufacture_date": made,
                "manufacture_date_source": source,
                "purchase_date": bought,
                "grade": grade,
                "notes": row.get("notes") or None,
            }
        )
    return rows, errors


def _csv_safe(value):
    """Stop spreadsheet apps treating a cell as a formula on export."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _retire_unit(unit, reduce_stock, reason=None):
    """Retire a unit; optionally take it out of its item's stock. Returns a
    sentence describing what happened to the stock (or '')."""
    unit.status = "retired"
    unit.retired_at = utcnow()
    unit.retired_reason = reason or None
    if not reduce_stock:
        return ""
    item = unit.item
    new_total = item.quantity_total - 1
    if new_total >= 1:
        if _set_item_total(item, new_total):
            return f" {item.name} stock reduced to {new_total}."
        return f" {item.name} stock wasn't reduced because units are signed out - adjust it once they're back."
    if item.quantity_available == item.quantity_total:
        item.active = False
        return f" That was the last {item.name}, so the item has been archived."
    return f" {item.name} stock wasn't reduced because it's signed out - adjust it once it's back."


# --------------------------------------------------------------- email
# Emails "sent" without a RESEND_API_KEY land here instead (dev and tests).
OUTBOX = []
RESEND_URL = "https://api.resend.com/emails"


# send_email outcomes. "rejected" means Resend refused this recipient, so
# retrying won't help; anything else that fails is worth retrying later.
SENT, RETRY, REJECTED = "sent", "retry", "rejected"


def send_email(to, subject, html, reply_to=None):
    """Send one email via Resend and return SENT, RETRY or REJECTED. Never
    raises: one bad email mustn't abort the rest of a reminder run."""
    config = current_app.config
    if not EMAIL_RE.match(to or ""):
        current_app.logger.warning("Email not sent: %r isn't a valid address", to)
        return REJECTED
    if not config.get("RESEND_API_KEY"):
        OUTBOX.append({"to": to, "subject": subject, "html": html, "reply_to": reply_to})
        current_app.logger.info("Email not sent (no RESEND_API_KEY) to %s: %s", to, subject)
        return SENT
    message = {"from": config["MAIL_FROM"], "to": [to], "subject": subject, "html": html}
    if reply_to or config.get("MAIL_REPLY_TO"):
        message["reply_to"] = reply_to or config["MAIL_REPLY_TO"]
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(message).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {config['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "cumc-kit-store",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return SENT if 200 <= response.status < 300 else RETRY
    except urllib.error.HTTPError as error:
        # Resend explains rejections in the body (e.g. an invalid "from").
        try:
            detail = error.read().decode("utf-8", "replace")[:500]
        except (OSError, http.client.HTTPException):
            detail = "(couldn't read Resend's response)"
        current_app.logger.warning("Email to %s rejected by Resend (%s): %s", to, error.code, detail)
        # Only a complaint about the recipient is permanent; a bad "from",
        # API key or rate limit is fixed on our side, so keep retrying.
        return REJECTED if error.code == 422 and "`to`" in detail else RETRY
    except (OSError, ValueError, http.client.HTTPException) as error:
        # OSError covers URLError, timeouts and dropped connections;
        # ValueError an invalid header value (e.g. a mangled API key).
        current_app.logger.warning("Email to %s failed: %s", to, error)
        return RETRY


def _london_today():
    try:
        return datetime.now(ZoneInfo("Europe/London")).date()
    except ZoneInfoNotFoundError:
        return date.today()


def _send_loan_emails(kind, loans, today, subject, intro):
    """One email per borrower covering all their loans of this kind; logs
    each loan so it isn't emailed about again. Returns emails sent."""
    by_email = {}
    for loan in loans:
        by_email.setdefault(loan.borrower_email.lower(), []).append(loan)
    sent = 0
    for email, borrower_loans in by_email.items():
        html = render_template(
            "email/loans.html", intro=intro, loans=borrower_loans, name=borrower_loans[0].borrower_name, today=today
        )
        outcome = send_email(email, subject, html)
        if outcome == RETRY:
            continue
        sent += outcome == SENT
        for loan in borrower_loans:
            db.session.add(EmailLog(kind=kind, signout_id=loan.id, recipient=email, status=outcome, sent_on=today))
        db.session.commit()
    return sent


def run_reminders(today):
    """Send whatever receipts, reminders and digests are due as of `today`.
    Safe to run as often as you like - EmailLog stops repeats."""
    last_sent = {}
    for log in EmailLog.query.filter(EmailLog.signout_id.isnot(None)):
        key = (log.kind, log.signout_id)
        last_sent[key] = max(last_sent.get(key, log.sent_on), log.sent_on)

    out = SignOut.query.filter(SignOut.signed_in_at.is_(None)).all()
    emailable = [loan for loan in out if loan.borrower_email]
    every = current_app.config["OVERDUE_REMINDER_EVERY_DAYS"]
    # Receipts only for recent loans, so switching emails on doesn't send a
    # burst of receipts for kit that went out weeks ago.
    receipt_cutoff = datetime.combine(today - timedelta(days=2), datetime.min.time())

    counts = {
        "receipt": _send_loan_emails(
            "receipt",
            [l for l in emailable if ("receipt", l.id) not in last_sent and l.signed_out_at >= receipt_cutoff],
            today,
            "Your CUMC kit loan",
            "Here's a record of the kit you've borrowed from the CUMC kit store.",
        ),
        "day_before": _send_loan_emails(
            "day_before",
            [l for l in emailable if l.due_date == today + timedelta(days=1) and ("day_before", l.id) not in last_sent],
            today,
            "CUMC kit due back tomorrow",
            "Just a reminder that this kit is due back tomorrow. Please return it to a committee member at a session.",
        ),
        "overdue": _send_loan_emails(
            "overdue",
            [
                l
                for l in emailable
                if l.due_date is not None
                and l.due_date <= today
                and (("overdue", l.id) not in last_sent or last_sent[("overdue", l.id)] <= today - timedelta(days=every))
            ],
            today,
            "CUMC kit due back",
            "This kit is due back now. Please return it to a committee member at the next session, "
            "or reply to let us know if there's a problem.",
        ),
        "digest": 0,
        "unclaimed": _send_unclaimed_reminder(today),
    }

    digest_to = current_app.config.get("GEAR_SEC_EMAIL")
    already = EmailLog.query.filter_by(kind="digest", sent_on=today).first()
    if today.weekday() == 0 and digest_to and not already:
        overdue_loans = [l for l in out if l.rack_loan_id is None and l.due_date and l.due_date < today]
        overdue_racks = RackLoan.query.filter(
            RackLoan.signed_in_at.is_(None), RackLoan.due_date.isnot(None), RackLoan.due_date < today
        ).all()
        expired = [unit for unit in GearUnit.query.filter_by(status="in_service") if unit.past_lifespan]
        if overdue_loans or overdue_racks or expired:
            html = render_template(
                "email/digest.html", loans=overdue_loans, racks=overdue_racks, expired=expired, today=today
            )
            subject = f"Overdue kit: {len(overdue_loans) + len(overdue_racks)} loan(s)"
            if expired:
                subject += f", {len(expired)} piece(s) past lifespan"
            outcome = send_email(digest_to, subject, html)
            if outcome != RETRY:
                db.session.add(EmailLog(kind="digest", recipient=digest_to, status=outcome, sent_on=today))
                db.session.commit()
                counts["digest"] = int(outcome == SENT)
    return counts


UNCLAIMED_AFTER = timedelta(hours=48)


def _send_unclaimed_reminder(today):
    """Nudge the requests inbox, once per request, about any request nobody
    has accepted or denied within 48 hours."""
    to_list = recipients(current_app.config.get("REQUESTS_EMAIL"))
    if not to_list:
        return 0
    reminded = {g for (g,) in db.session.query(EmailLog.request_group).filter(EmailLog.kind == "unclaimed")}
    stale = ItemRequest.query.filter(
        ItemRequest.status == "pending", ItemRequest.created_at <= utcnow() - UNCLAIMED_AFTER
    ).all()
    groups = [g for g in group_pending_requests(stale) if g["request_group"] not in reminded]
    if not groups:
        return 0
    html = render_template("email/unclaimed.html", groups=groups, admin_url=url_for("admin", _external=True))
    subject = f"{len(groups)} kit request{'s' if len(groups) != 1 else ''} waiting over 48 hours"
    outcomes = [send_email(to, subject, html) for to in to_list]
    if all(outcome == RETRY for outcome in outcomes):
        return 0
    for group in groups:
        db.session.add(
            EmailLog(kind="unclaimed", request_group=group["request_group"], recipient=to_list[0], sent_on=today)
        )
    db.session.commit()
    return len(groups)


def register_routes(app):
    app.jinja_env.globals["line_available"] = line_available

    @app.context_processor
    def inject_basket_count():
        pending_count = 0
        if session.get("is_committee"):
            pending_count = (
                db.session.query(func.count(func.distinct(ItemRequest.request_group)))
                .filter(ItemRequest.status == "pending")
                .scalar()
            )
        return {
            "basket_count": basket_line_count(get_basket()),
            "default_due_date": _default_due_date(),
            "today": date.today(),
            "pending_count": pending_count,
        }

    # No route had ever wrapped a db.session.commit() in a try/except, so any
    # database error (a constraint the app-level checks missed, a dropped
    # connection, disk full, ...) surfaced as a raw unhandled 500 with no
    # rollback - leaving the session's failed transaction stuck for whatever
    # request came next. These two handlers catch anything SQLAlchemy raises
    # anywhere in a view, roll the session back, and send the committee
    # member back to what they were doing with an explanation instead.
    # IntegrityError (registered first/more specific) covers exactly the
    # uniqueness-race case the new DB-level indexes above exist to catch;
    # everything else falls through to the general SQLAlchemyError handler.
    @app.errorhandler(IntegrityError)
    def handle_integrity_error(error):
        db.session.rollback()
        flash(
            "That couldn't be saved because it clashes with something else — "
            "possibly created by someone else at the same moment. Check the "
            "list and try again.",
            "error",
        )
        return redirect(request.referrer or url_for("home"))

    @app.errorhandler(SQLAlchemyError)
    def handle_db_error(error):
        db.session.rollback()
        flash("Something went wrong saving that — please try again.", "error")
        return redirect(request.referrer or url_for("home"))

    # ---------------------------------------------------------------- public
    @app.route("/")
    def home():
        return render_template("home.html")

    # Some browsers/crawlers request /favicon.ico at the site root regardless
    # of the <link rel="icon"> tag in base.html - without this route that
    # request 404s. There's no separate .ico file to serve (logo.svg is the
    # only icon asset we have), so this just serves that same SVG; every
    # browser that still probes /favicon.ico directly accepts an SVG there.
    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            os.path.join(app.static_folder, "img"), "logo.svg", mimetype="image/svg+xml"
        )

    @app.route("/request")
    def request_kit():
        # Only show bundles that still have at least one usable line in them
        # (an active item, or a group with an active item left in it) - an
        # empty or fully-archived bundle would just be a dead end for a
        # member browsing the page.
        bundles = [
            bundle
            for bundle in KitBundle.query.order_by(KitBundle.name).all()
            if any(line.active for line in bundle.lines)
        ]
        # One card per category rather than one long combined list of every
        # item in the store - each card links through to that category's own
        # (much shorter) page. Counts are shown on the card so a member can
        # tell at a glance roughly how much is in there before clicking in.
        # Only categories the committee has left visible to members show up
        # here - see Category.member_visible.
        category_cards = []
        for category in Category.query.filter_by(member_visible=True).order_by(Category.name).all():
            item_count = sum(
                1 for sub in category.subcategories for item in sub.items
                if item.active and item.group_id is None and item.rack_id is None
            )
            group_count = sum(
                1 for sub in category.subcategories
                for group in ItemGroup.query.filter_by(subcategory_id=sub.id)
                if group.active_items
            )
            category_cards.append({"category": category, "item_count": item_count, "group_count": group_count})
        racks = Rack.query.order_by(Rack.name).all()
        return render_template(
            "request.html",
            category_cards=category_cards,
            bundles=bundles,
            racks=[(rack, not _rack_blockers(rack)) for rack in racks if rack.items],
        )

    @app.route("/request/category/<int:category_id>")
    def request_category(category_id):
        category = db.get_or_404(Category, category_id)
        # Also block direct URL access to a hidden category, not just its
        # card on the browse page - otherwise "hidden" would only mean
        # "not linked to", which isn't what the committee is asking for.
        if not category.member_visible:
            abort(404)
        # Items that belong to a group (e.g. individual nut sizes) are folded
        # into that group's single row instead of listed on their own here -
        # a member requests "Nuts", not a specific size.
        items = (
            KitItem.query.filter_by(active=True, group_id=None, rack_id=None)
            .join(Subcategory)
            .filter(Subcategory.category_id == category.id)
            .order_by(KitItem.name)
            .all()
        )
        groups = [
            group
            for group in ItemGroup.query.join(Subcategory)
            .filter(Subcategory.category_id == category.id)
            .order_by(ItemGroup.name)
            .all()
            if group.active_items
        ]
        kit_entries = build_kit_entries(items, groups, available_first=True)
        # Only offer a filter chip for subcategories that actually have
        # something to show (grouped or not) - a subcategory with nothing
        # visible in it would otherwise be a dead-end chip that filters the
        # table down to nothing.
        subcategory_ids_with_entries = {entry["subcategory"].id for entry in kit_entries}
        subcategories = [
            sub
            for sub in Subcategory.query.filter_by(category_id=category.id).order_by(Subcategory.name).all()
            if sub.id in subcategory_ids_with_entries
        ]
        return render_template(
            "request_category.html",
            category=category,
            subcategories=subcategories,
            kit_entries=kit_entries,
            has_groups=bool(groups),
        )

    @app.route("/request/search")
    def search_kit():
        query = request.args.get("q", "").strip()
        items = []
        groups = []
        if query:
            like = f"%{query}%"
            # Matches on item name, item description or subcategory name -
            # e.g. searching "cam" also surfaces items filed under a "Cams"
            # subcategory even if the word "cam" isn't in the item's own
            # name. Restricted to member_visible categories and active,
            # ungrouped items/groups with stock, same as browsing by category.
            items = (
                KitItem.query.filter(KitItem.active.is_(True), KitItem.group_id.is_(None), KitItem.rack_id.is_(None))
                .join(Subcategory)
                .join(Category)
                .filter(Category.member_visible.is_(True))
                .filter(
                    or_(
                        KitItem.name.ilike(like),
                        KitItem.description.ilike(like),
                        Subcategory.name.ilike(like),
                    )
                )
                .order_by(KitItem.name)
                .all()
            )
            groups = [
                group
                for group in ItemGroup.query.join(Subcategory)
                .join(Category)
                .filter(Category.member_visible.is_(True))
                .filter(
                    or_(
                        ItemGroup.name.ilike(like),
                        ItemGroup.description.ilike(like),
                        Subcategory.name.ilike(like),
                    )
                )
                .order_by(ItemGroup.name)
                .all()
                if group.active_items
            ]
        kit_entries = build_kit_entries(items, groups, available_first=True)
        return render_template(
            "search_results.html", query=query, kit_entries=kit_entries, has_groups=bool(groups)
        )

    @app.route("/basket/add", methods=["POST"])
    def add_to_basket():
        basket = get_basket()
        quantity = parse_quantity(request.form.get("quantity"))
        item_id = request.form.get("item_id")
        group_id = request.form.get("group_id")
        custom_name = request.form.get("custom_name", "").strip()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if quantity is None:
            ok = False
            message = "Please enter a whole number for quantity."
        elif item_id:
            item = form_id_or_404(KitItem, "item_id")
            if not item.active or item.rack_id:
                ok = False
                message = "That item can't be requested on its own."
            else:
                ok = True
                key = str(item.id)
                basket["items"][key] = basket["items"].get(key, 0) + quantity
                message = f"Added {quantity} x {item.name} to your request list." + _stock_note(
                    basket["items"][key], item.quantity_available
                )
        elif group_id:
            ok = True
            group = form_id_or_404(ItemGroup, "group_id")
            key = str(group.id)
            basket["groups"][key] = basket["groups"].get(key, 0) + quantity
            message = f"Added {quantity} x {group.name} to your request list." + _stock_note(
                basket["groups"][key], group.quantity_available
            )
        elif custom_name:
            ok = True
            basket["other"].append({"name": custom_name, "quantity": quantity})
            message = f"Added {quantity} x {custom_name} to your request list."
        else:
            ok = False
            message = "Choose an item or describe what you need."

        session["basket"] = basket
        session.modified = True

        if is_ajax:
            return {"ok": ok, "message": message, "basket_count": basket_line_count(basket)}

        flash(message, "success" if ok else "error")
        return redirect(request.referrer or url_for("request_kit"))

    @app.route("/basket/add-bundle/<int:bundle_id>", methods=["POST"])
    def add_bundle_to_basket(bundle_id):
        bundle = db.get_or_404(KitBundle, bundle_id)
        basket = get_basket()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        added = []
        for line in bundle.lines:
            if not line.active:
                continue  # skip anything archived/emptied-out since it was added to this bundle
            if line.group_id:
                key = str(line.group_id)
                basket["groups"][key] = basket["groups"].get(key, 0) + line.quantity
            else:
                key = str(line.item_id)
                basket["items"][key] = basket["items"].get(key, 0) + line.quantity
            added.append(f"{line.quantity} x {line.name}")

        session["basket"] = basket
        session.modified = True

        if added:
            ok = True
            message = f"Added '{bundle.name}' to your request list ({', '.join(added)})."
        else:
            ok = False
            message = f"'{bundle.name}' has no available items to add right now."

        if is_ajax:
            return {"ok": ok, "message": message, "basket_count": basket_line_count(basket)}
        flash(message, "success" if ok else "error")
        return redirect(request.referrer or url_for("request_kit"))

    @app.route("/basket", methods=["GET", "POST"])
    def view_basket():
        basket = get_basket()
        line_items = basket_line_items(basket)

        if request.method == "POST":
            requester_name = request.form.get("requester_name", "").strip()
            requester_email = request.form.get("requester_email", "").strip()
            message = request.form.get("message", "").strip()
            needed_from = parse_date(request.form.get("needed_from"))
            needed_to = parse_date(request.form.get("needed_to"))
            session["requester"] = {"name": requester_name, "email": requester_email}

            if not requester_name:
                flash("Please enter your name.", "error")
            elif not requester_email:
                flash("Please enter your email so we can send you a receipt and return reminders.", "error")
            elif not EMAIL_RE.match(requester_email):
                flash("That email address doesn't look right — check it and try again.", "error")
            elif not line_items:
                flash("Your request list is empty.", "error")
            elif not needed_from or not needed_to:
                flash("Please choose when you need the kit and when you'll return it.", "error")
            elif needed_from < date.today():
                flash("The date you need the kit can't be in the past.", "error")
            elif needed_to < needed_from:
                flash("The return date must be on or after the date you need it.", "error")
            else:
                checkout_group_id = uuid.uuid4().hex
                token = secrets.token_urlsafe(24)
                new_lines = []
                for line in line_items:
                    new_line = ItemRequest(
                        requester_name=requester_name,
                        requester_email=requester_email,
                        item_name=line["name"],
                        quantity=line["quantity"],
                        message=message,
                        request_group=checkout_group_id,
                        group_id=line["group_id"],
                        needed_from=needed_from,
                        needed_to=needed_to,
                        status_token=token,
                    )
                    db.session.add(new_line)
                    new_lines.append(new_line)
                db.session.commit()
                session["basket"] = {"items": {}, "groups": {}, "other": []}
                session.modified = True
                send_request_email("received", new_lines)
                send_request_email("new", new_lines)
                return redirect(url_for("request_status", token=token, sent=1))

        return render_template("basket.html", line_items=line_items, requester=session.get("requester", {}))

    @app.route("/r/<token>")
    def request_status(token):
        lines = ItemRequest.query.filter_by(status_token=token).order_by(ItemRequest.id).all()
        if not lines:
            abort(404)
        email = (lines[0].requester_email or "").lower()
        loans = (
            SignOut.query.filter(func.lower(SignOut.borrower_email) == email, SignOut.signed_in_at.is_(None))
            .order_by(SignOut.due_date)
            .all()
            if email
            else []
        )
        return render_template(
            "request_status.html",
            lines=lines,
            first=lines[0],
            loans=loans,
            can_cancel=all(line.status == "pending" for line in lines),
            just_sent=bool(request.args.get("sent")),
        )

    @app.route("/r/<token>/cancel", methods=["POST"])
    def cancel_own_request(token):
        lines = ItemRequest.query.filter_by(status_token=token).all()
        if not lines:
            abort(404)
        if any(line.status != "pending" for line in lines):
            flash("This request has already been dealt with — reply to the email to change it.", "error")
            return redirect(url_for("request_status", token=token))
        db.session.execute(
            ItemRequest.__table__.update()
            .where(ItemRequest.status_token == token, ItemRequest.status == "pending")
            .values(status="cancelled", resolved_at=utcnow())
        )
        db.session.commit()
        send_request_email("new", lines, cancelled=True, subject=f"Kit request cancelled by {lines[0].requester_name}")
        flash("Your request has been cancelled.", "success")
        return redirect(url_for("request_status", token=token))

    @app.route("/basket/update/<key>", methods=["POST"])
    def update_basket_item(key):
        basket = get_basket()
        quantity = parse_quantity(request.form.get("quantity"))
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if quantity is None:
            ok, message = False, "Please enter a whole number for quantity."
        else:
            ok, message = True, "Quantity updated."
            if key.startswith("item:"):
                item_id = key.split(":", 1)[1]
                if item_id in basket["items"]:
                    basket["items"][item_id] = quantity
            elif key.startswith("group:"):
                group_id = key.split(":", 1)[1]
                if group_id in basket["groups"]:
                    basket["groups"][group_id] = quantity
            elif key.startswith("other:"):
                idx = _basket_other_index(key)
                if idx is not None and idx < len(basket["other"]):
                    basket["other"][idx]["quantity"] = quantity

            session["basket"] = basket
            session.modified = True

        if is_ajax:
            return {"ok": ok, "message": message, "basket_count": basket_line_count(basket)}
        if not ok:
            flash(message, "error")
        return redirect(url_for("view_basket"))

    @app.route("/basket/remove/<key>", methods=["POST"])
    def remove_from_basket(key):
        basket = get_basket()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if key.startswith("item:"):
            basket["items"].pop(key.split(":", 1)[1], None)
        elif key.startswith("group:"):
            basket["groups"].pop(key.split(":", 1)[1], None)
        elif key.startswith("other:"):
            idx = _basket_other_index(key)
            if idx is not None and idx < len(basket["other"]):
                basket["other"].pop(idx)

        session["basket"] = basket
        session.modified = True

        if is_ajax:
            return {"ok": True, "message": "Removed from your request list.", "basket_count": basket_line_count(basket)}
        flash("Removed from your request list.", "success")
        return redirect(url_for("view_basket"))

    # ------------------------------------------------------------------ auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            client_ip = request.remote_addr
            locked_until = _login_locked_until(client_ip)
            if locked_until:
                remaining = int(locked_until - time.time())
                flash(f"Too many incorrect attempts. Try again in {remaining} second(s).", "error")
                return render_template("login.html")

            password = request.form.get("password", "")
            committee_name = request.form.get("committee_name", "").strip()
            if not committee_name:
                flash("Enter your name — it's recorded against what you sign out and the requests you take on.", "error")
                return render_template("login.html")
            if secrets.compare_digest(password, app.config["COMMITTEE_PASSWORD"]):
                session["is_committee"] = True
                session["committee_name"] = committee_name
                _clear_login_attempts(client_ip)
                flash("Logged in.", "success")
                response = redirect(_safe_next_url(request.args.get("next")) or url_for("sign_in_out"))
                # Remembered on this device so the name is pre-filled next time.
                response.set_cookie(
                    "committee_name",
                    committee_name,
                    max_age=365 * 24 * 3600,
                    httponly=True,
                    samesite="Lax",
                    secure=app.config["SESSION_COOKIE_SECURE"],
                )
                return response

            _record_failed_login(client_ip)
            if _login_locked_until(client_ip):
                flash(f"Too many incorrect attempts. Try again in {LOGIN_LOCKOUT_SECONDS} seconds.", "error")
            else:
                flash("Incorrect password.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.pop("is_committee", None)
        session.pop("committee_name", None)
        flash("Logged out.", "success")
        return redirect(url_for("home"))

    # ------------------------------------------------------- committee: kit
    @app.route("/sign-in-out")
    @login_required
    def sign_in_out():
        # "Sign Out Manually" used to list every active KitItem/ItemGroup as
        # one flat 200+-row table on this page - deliberately not filtered to
        # member_visible categories or to ungrouped-only items like the
        # member-facing pages, since the committee needs to sign out anything
        # real (including "Cage" storage members never see), and needs the
        # option to hand over one exact KitItem directly even when it's also
        # part of a group. That's still true, but showing it all at once made
        # this page enormous - it's now a category-card grid instead (mirrors
        # admin_inventory()'s category_cards), and the actual per-item list
        # + sign-out forms live one click in, at sign_in_out_category().
        items = KitItem.query.filter_by(active=True, rack_id=None).all()
        groups = [group for group in ItemGroup.query.all() if group.active_items]
        entries_by_category = {}
        for entry in build_kit_entries(items, groups):
            entries_by_category.setdefault(entry["category"].id, []).append(entry)
        manual_category_cards = [
            {"category": category, "item_count": len(entries_by_category[category.id])}
            for category in Category.query.order_by(Category.name).all()
            if category.id in entries_by_category
        ]
        # Approved requests that haven't actually left the building yet - the
        # committee collects from here instead of re-typing an approved
        # request's borrower/item/quantity into the manual form below, and for
        # a group request (e.g. "Nuts") this is also where they pick which
        # specific size is actually being handed over.
        ready_for_collection = (
            ItemRequest.query.filter_by(status="approved", fulfilled_signout_id=None)
            .order_by(ItemRequest.resolved_at.asc())
            .all()
        )
        # Rack pieces are shown (and signed back in) as their whole rack in
        # the Racks card, not one row per piece here.
        active_sign_outs = (
            SignOut.query.filter_by(signed_in_at=None, rack_loan_id=None)
            .order_by(SignOut.signed_out_at.desc())
            .all()
        )
        # Recent sign-outs, both still-out and already-returned, so the
        # committee can track activity over time - separate from
        # active_sign_outs above, which only covers unreturned loans. Capped so
        # the page doesn't grow with every loan the club ever makes.
        sign_out_history = sorted(
            SignOut.query.filter_by(rack_loan_id=None)
            .order_by(SignOut.signed_out_at.desc())
            .limit(SIGN_OUT_HISTORY_LIMIT)
            .all()
            + RackLoan.query.order_by(RackLoan.signed_out_at.desc()).limit(SIGN_OUT_HISTORY_LIMIT).all(),
            key=lambda entry: entry.signed_out_at,
            reverse=True,
        )[:SIGN_OUT_HISTORY_LIMIT]
        racks = Rack.query.order_by(Rack.name).all()
        recently_returned = (
            SignOut.query.filter(
                SignOut.signed_in_at >= utcnow() - timedelta(minutes=15),
                SignOut.rack_loan_id.is_(None),
                SignOut.signed_in_at != SignOut.signed_out_at,
            )
            .order_by(SignOut.signed_in_at.desc())
            .all()
        )
        return render_template(
            "sign_in_out.html",
            manual_category_cards=manual_category_cards,
            ready_for_collection=ready_for_collection,
            loan_groups=_loans_by_borrower(active_sign_outs),
            recently_returned=recently_returned,
            unrecorded_out=_unrecorded_out(),
            sign_out_history=sign_out_history,
            racks=[(rack, _rack_blockers(rack)) for rack in racks],
            borrower_names=_borrower_names(),
            active_tab="sign_in_out",
        )

    @app.route("/sign-in-out/category/<int:category_id>")
    @login_required
    def sign_in_out_category(category_id):
        """The actual per-item sign-out list for one category, one click in
        from the category-card grid on sign_in_out() - see the comment
        there for why this isn't all shown on one page any more."""
        category = db.get_or_404(Category, category_id)
        # Variants are signed out from inside their group's row.
        items = (
            KitItem.query.filter_by(active=True, rack_id=None, group_id=None)
            .join(Subcategory)
            .filter(Subcategory.category_id == category.id)
            .order_by(KitItem.name)
            .all()
        )
        groups = [
            group
            for group in ItemGroup.query.join(Subcategory)
            .filter(Subcategory.category_id == category.id)
            .order_by(ItemGroup.name)
            .all()
            if group.active_items
        ]
        kit_entries = build_kit_entries(items, groups)
        subcategories = Subcategory.query.filter_by(category_id=category.id).order_by(Subcategory.name).all()
        borrower_names = _borrower_names()
        return render_template(
            "sign_in_out_category.html",
            category=category,
            kit_entries=kit_entries,
            subcategories=subcategories,
            borrower_names=borrower_names,
            active_tab="sign_in_out",
        )

    @app.route("/sign-out", methods=["POST"])
    @login_required
    def sign_out_item():
        item = form_id_or_404(KitItem, "item_id")
        quantity = parse_positive_int(request.form.get("quantity"))
        borrower_name = request.form.get("borrower_name", "").strip()

        due_date, due_error = _due_date_from_form()
        gear_unit, serial_error = _serial_from_form(item, request.form.get("serial"))

        if item.rack_id:
            flash(f"'{item.name}' is part of the {item.rack.name} rack — sign out the whole rack instead.", "error")
        elif due_error or serial_error:
            flash(due_error or serial_error, "error")
        elif not borrower_name:
            flash("Borrower name is required.", "error")
        elif quantity is None:
            # Distinct from the "not enough available" case below: this is a
            # bad/zero/negative number, not a stock problem, so it needs its
            # own message rather than reusing "Only N available" (which reads
            # as a non-sequitur when what you actually typed was 0 or "abc").
            flash("Enter a quantity of at least 1.", "error")
        elif quantity > item.quantity_available:
            flash(f"Only {item.quantity_available} of '{item.name}' available.", "error")
        else:
            try:
                _create_signout(
                    item,
                    quantity,
                    borrower_name,
                    request.form.get("notes", "").strip(),
                    session.get("committee_name", "Committee"),
                    due_date=due_date,
                    borrower_email=request.form.get("borrower_email", "").strip() or None,
                    gear_unit=gear_unit,
                )
                db.session.commit()
                flash(f"Signed out {quantity} x {item.name} to {borrower_name}, due back {due_date:%d %b}.", "success")
            except InsufficientStockError as e:
                db.session.rollback()
                flash(f"Only {e.item.quantity_available} of '{e.item.name}' available.", "error")

        # Back to whichever page the form was on - almost always a
        # sign_in_out_category() page now, so a committee member signing out
        # several things to the same borrower stays on that category instead
        # of being bounced to the top-level category grid after every item.
        return redirect(request.referrer or url_for("sign_in_out"))

    @app.route("/sign-out/group", methods=["POST"])
    @login_required
    def sign_out_group():
        """Sign out a group (e.g. 'Nuts') by splitting the amount needed
        across as many of its specific items as it takes - unlike
        sign_out_item, no single KitItem has to have enough stock on its own.
        Creates one SignOut row per item actually used, all sharing the same
        borrower/notes, so the sign-out history still shows exactly what
        physically left the store."""
        group = form_id_or_404(ItemGroup, "group_id")
        borrower_name = request.form.get("borrower_name", "").strip()
        notes = request.form.get("notes", "").strip()

        back_to = request.referrer or url_for("sign_in_out")

        if not borrower_name:
            flash("Borrower name is required.", "error")
            return redirect(back_to)
        due_date, due_error = _due_date_from_form()
        if due_error:
            flash(due_error, "error")
            return redirect(back_to)

        splits, error = _parse_group_split_quantities(group.active_items, request.form)
        if error:
            flash(error, "error")
            return redirect(back_to)
        if not splits:
            flash(f"Enter a quantity for at least one item in '{group.name}'.", "error")
            return redirect(back_to)

        checked_out_by = session.get("committee_name", "Committee")
        try:
            for item, quantity in splits:
                _create_signout(item, quantity, borrower_name, notes, checked_out_by, due_date=due_date)
            db.session.commit()
        except InsufficientStockError as e:
            # None of this split's SignOuts have committed yet, so rolling
            # back here discards all of them, not just the one that hit the
            # race - the committee member sees one clear error and re-enters
            # the whole split rather than ending up with half of it applied.
            db.session.rollback()
            flash(
                f"Only {e.item.quantity_available} of '{e.item.name}' available now — "
                "someone else may have just signed it out. Adjust and try again.",
                "error",
            )
            return redirect(back_to)

        total = sum(quantity for _, quantity in splits)
        summary = ", ".join(f"{quantity} x {item.name}" for item, quantity in splits)
        flash(f"Signed out {total} x {group.name} to {borrower_name} ({summary}).", "success")
        return redirect(back_to)

    @app.route("/sign-in/unrecorded/<int:item_id>", methods=["POST"])
    @login_required
    def sign_in_unrecorded(item_id):
        item = db.get_or_404(KitItem, item_id)
        quantity = parse_positive_int(request.form.get("quantity"))
        if quantity is None:
            flash("Enter a quantity of at least 1.", "error")
        elif _return_unrecorded(item, quantity, session.get("committee_name", "Committee")):
            db.session.commit()
            flash(f"Signed in {quantity} x {item.name} (no loan record).", "success")
        else:
            db.session.rollback()
            flash(
                f"Can't sign in {quantity} x {item.name} - that's more than is out without a loan record.",
                "error",
            )
        return redirect(url_for("sign_in_out", show="unrecorded") + "#unrecorded")

    @app.route("/sign-in/<int:signout_id>", methods=["POST"])
    @login_required
    def sign_in_item(signout_id):
        signout = db.get_or_404(SignOut, signout_id)
        quantity = parse_positive_int(request.form.get("quantity")) or signout.quantity
        if signout.rack_loan_id:
            flash(f"That's part of the {signout.rack_loan.rack.name} rack — sign the whole rack in.", "error")
        elif quantity > signout.quantity:
            flash(f"Only {signout.quantity} of that loan is out.", "error")
        elif quantity < signout.quantity:
            if _return_part(signout, quantity, session.get("committee_name", "Committee")):
                db.session.commit()
                flash(
                    f"Signed in {quantity} x {signout.item.name} from {signout.borrower_name} "
                    f"— {signout.quantity} still out.",
                    "success",
                )
        elif _return_signout(signout):
            db.session.commit()
            flash(f"Signed in {signout.quantity} x {signout.item.name} from {signout.borrower_name}.", "success")
        return redirect(url_for("sign_in_out") + "#loans")

    @app.route("/sign-in/many", methods=["POST"])
    @login_required
    def sign_in_many():
        ids = [i for i in (parse_positive_int(raw) for raw in request.form.getlist("signout_id")) if i]
        returned = 0
        for signout in SignOut.query.filter(SignOut.id.in_(ids), SignOut.rack_loan_id.is_(None)).all():
            returned += _return_signout(signout)
        db.session.commit()
        flash(f"Signed in {returned} loan{'s' if returned != 1 else ''}.", "success" if returned else "error")
        return redirect(url_for("sign_in_out") + "#loans")

    @app.route("/sign-in/<int:signout_id>/undo", methods=["POST"])
    @login_required
    def undo_sign_in(signout_id):
        signout = db.get_or_404(SignOut, signout_id)
        if _undo_return(signout):
            db.session.commit()
            flash(f"Undone — {signout.quantity} x {signout.item.name} is back out with {signout.borrower_name}.", "success")
        else:
            db.session.rollback()
            flash("Couldn't undo that — it's already out again or the stock has gone.", "error")
        return redirect(url_for("sign_in_out") + "#loans")

    @app.route("/sign-out/multi", methods=["GET", "POST"])
    @login_required
    def sign_out_multi():
        items = (
            KitItem.query.filter_by(active=True, rack_id=None)
            .join(Subcategory)
            .join(Category)
            .order_by(Category.name, KitItem.name)
            .all()
        )
        if request.method == "POST":
            borrower_name = request.form.get("borrower_name", "").strip()
            borrower_email = request.form.get("borrower_email", "").strip()
            notes = request.form.get("notes", "").strip()
            due_date, due_error = _due_date_from_form()
            wanted = {}
            for raw_id, raw_qty in zip(request.form.getlist("item_id"), request.form.getlist("quantity")):
                item_id = parse_positive_int(raw_id)
                if not item_id:
                    continue
                quantity = parse_positive_int(raw_qty)
                if quantity is None:
                    flash("Every chosen item needs a quantity of at least 1.", "error")
                    return render_template("sign_out_multi.html", items=items, borrower_names=_borrower_names(), form=request.form)
                wanted[item_id] = wanted.get(item_id, 0) + quantity
            error = None
            if not borrower_name:
                error = "Borrower name is required."
            elif borrower_email and not EMAIL_RE.match(borrower_email):
                error = "That email address doesn't look right."
            elif due_error:
                error = due_error
            elif not wanted:
                error = "Choose at least one item."
            if error:
                flash(error, "error")
                return render_template("sign_out_multi.html", items=items, borrower_names=_borrower_names(), form=request.form)
            checked_out_by = session.get("committee_name", "Committee")
            try:
                for item_id, quantity in wanted.items():
                    item = db.session.get(KitItem, item_id)
                    if not item or not item.active or item.rack_id:
                        abort(404)
                    _create_signout(
                        item, quantity, borrower_name, notes, checked_out_by,
                        due_date=due_date, borrower_email=borrower_email or None,
                    )
                db.session.commit()
            except InsufficientStockError as e:
                db.session.rollback()
                flash(f"Only {e.item.quantity_available} of '{e.item.name}' available — nothing was signed out.", "error")
                return render_template("sign_out_multi.html", items=items, borrower_names=_borrower_names(), form=request.form)
            total = sum(wanted.values())
            flash(f"Signed out {total} item{'s' if total != 1 else ''} to {borrower_name}, due back {due_date:%d %b}.", "success")
            return redirect(url_for("sign_in_out"))
        return render_template("sign_out_multi.html", items=items, borrower_names=_borrower_names(), form={})

    @app.route("/racks/<int:rack_id>/sign-out", methods=["POST"])
    @login_required
    def sign_out_rack(rack_id):
        rack = db.get_or_404(Rack, rack_id)
        borrower_name = request.form.get("borrower_name", "").strip()
        notes = request.form.get("notes", "").strip()
        due_date, due_error = _due_date_from_form()
        back_to = url_for("sign_in_out")

        if not borrower_name:
            flash("Borrower name is required.", "error")
            return redirect(back_to)
        if due_error:
            flash(due_error, "error")
            return redirect(back_to)
        blockers = _rack_blockers(rack)
        if blockers:
            flash(f"The {rack.name} rack can't go out: {'; '.join(blockers)}.", "error")
            return redirect(back_to)

        checked_out_by = session.get("committee_name", "Committee")
        loan = RackLoan(
            rack_id=rack.id, borrower_name=borrower_name, notes=notes, due_date=due_date, checked_out_by=checked_out_by
        )
        db.session.add(loan)
        db.session.flush()
        try:
            for item in rack.items:
                signout = _create_signout(
                    item, item.quantity_total, borrower_name, notes, checked_out_by, due_date=due_date
                )
                signout.rack_loan_id = loan.id
            db.session.commit()
        except InsufficientStockError as e:
            # All-or-nothing: none of the rack's pieces leave if any one can't.
            db.session.rollback()
            flash(f"The {rack.name} rack can't go out — '{e.item.name}' was just signed out elsewhere.", "error")
            return redirect(back_to)
        flash(f"Signed out the {rack.name} rack to {borrower_name}, due back {due_date:%d %b}.", "success")
        return redirect(back_to)

    @app.route("/racks/loans/<int:loan_id>/sign-in", methods=["POST"])
    @login_required
    def sign_in_rack(loan_id):
        loan = db.get_or_404(RackLoan, loan_id)
        result = db.session.execute(
            RackLoan.__table__.update()
            .where(RackLoan.id == loan.id, RackLoan.signed_in_at.is_(None))
            .values(signed_in_at=utcnow())
        )
        if result.rowcount == 1:
            for signout in loan.sign_outs:
                _return_signout(signout)
            db.session.commit()
            flash(f"Signed in the {loan.rack.name} rack from {loan.borrower_name}.", "success")
        else:
            flash(f"The {loan.rack.name} rack was already signed in.", "error")
        return redirect(url_for("sign_in_out"))

    # ----------------------------------------------------------- committee: admin
    # The admin area used to be one long page with every section (pending
    # requests, categories, items, groups, bundles, archive, resolved
    # history) stacked on top of each other. It's now a slim dashboard
    # (pending requests - the one thing that actually needs daily action)
    # plus a set of separate pages linked from admin_subnav.html, so no
    # single page tries to show everything at once.
    @app.route("/admin")
    @login_required
    def admin():
        pending_requests = (
            ItemRequest.query.filter_by(status="pending").order_by(ItemRequest.created_at.desc()).all()
        )
        # Note: deliberately not called "items" - dict has a built-in .items()
        # method, and Jinja's dot notation would silently resolve counts.items
        # to that method instead of the dict key, rendering a method repr
        # instead of the actual number.
        counts = {
            "kit_items": KitItem.query.filter_by(active=True).count(),
            "categories": Category.query.count(),
            "groups": ItemGroup.query.count(),
            "bundles": KitBundle.query.count(),
            "archived": KitItem.query.filter_by(active=False).count(),
            "resolved": ItemRequest.query.filter(ItemRequest.status != "pending").count(),
            "taken_out": SignOut.query.filter_by(signed_in_at=None).count(),
        }
        addable = build_kit_entries(
            KitItem.query.filter_by(active=True, group_id=None, rack_id=None).all(),
            [group for group in ItemGroup.query.all() if group.active_items],
        )
        return render_template(
            "admin.html",
            pending_groups=group_pending_requests(pending_requests),
            counts=counts,
            addable=addable,
            open_group=request.args.get("open"),
            active_tab="pending",
        )

    # -- categories / subcategories --
    @app.route("/admin/categories")
    @login_required
    def admin_categories():
        categories = Category.query.order_by(Category.name).all()
        subcategories = Subcategory.query.order_by(Subcategory.name).all()
        return render_template(
            "admin_categories.html", categories=categories, subcategories=subcategories, active_tab="categories"
        )

    @app.route("/admin/categories/add", methods=["POST"])
    @login_required
    def add_category():
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "error")
        elif _name_exists(Category, name):
            flash(f"Category '{name}' already exists.", "error")
        else:
            db.session.add(Category(name=name))
            db.session.commit()
            flash(f"Added category '{name}'.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/toggle-visibility", methods=["POST"])
    @login_required
    def toggle_category_visibility(category_id):
        category = db.get_or_404(Category, category_id)
        category.member_visible = not category.member_visible
        db.session.commit()
        state = "visible to members" if category.member_visible else "hidden from members"
        flash(f"'{category.name}' is now {state}.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
    @login_required
    def delete_category(category_id):
        category = db.get_or_404(Category, category_id)
        if category.subcategories:
            flash(f"Cannot delete '{category.name}' while it has subcategories.", "error")
        else:
            db.session.delete(category)
            db.session.commit()
            flash(f"Deleted category '{category.name}'.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/subcategories/add", methods=["POST"])
    @login_required
    def add_subcategory():
        name = request.form.get("name", "").strip()
        category_id = parse_positive_int(request.form.get("category_id"))
        category = db.session.get(Category, category_id) if category_id else None

        if not name or not category:
            flash("Subcategory name and category are required.", "error")
        elif _name_exists(Subcategory, name, category_id=category.id):
            flash(f"'{name}' already exists under '{category.name}'.", "error")
        else:
            db.session.add(Subcategory(name=name, category_id=category.id))
            db.session.commit()
            flash(f"Added subcategory '{name}' under '{category.name}'.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/subcategories/<int:subcategory_id>/delete", methods=["POST"])
    @login_required
    def delete_subcategory(subcategory_id):
        subcategory = db.get_or_404(Subcategory, subcategory_id)
        if subcategory.items:
            flash(f"Cannot delete '{subcategory.name}' while kit items use it.", "error")
        else:
            db.session.delete(subcategory)
            db.session.commit()
            flash(f"Deleted subcategory '{subcategory.name}'.", "success")
        return redirect(url_for("admin_categories"))

    # -- kit items --
    # Browsing the inventory is now a category picker (admin_inventory) that
    # drills into one short per-category table (admin_inventory_category)
    # instead of one giant table of every item in the store. Editing an item
    # is its own page too (a plain vertical form pre-filled with that item's
    # current values), rather than an inline row of form controls that
    # needed horizontal scrolling to reach.
    @app.route("/admin/inventory")
    @login_required
    def admin_inventory():
        category_cards = []
        for category in Category.query.order_by(Category.name).all():
            item_count = sum(1 for sub in category.subcategories for item in sub.items if item.active)
            category_cards.append({"category": category, "item_count": item_count})
        return render_template("admin_inventory.html", category_cards=category_cards, active_tab="inventory")

    @app.route("/admin/inventory/<int:category_id>")
    @login_required
    def admin_inventory_category(category_id):
        category = db.get_or_404(Category, category_id)
        items = (
            KitItem.query.filter_by(active=True)
            .join(Subcategory)
            .filter(Subcategory.category_id == category.id)
            .order_by(KitItem.name)
            .all()
        )
        subcategories = Subcategory.query.filter_by(category_id=category.id).order_by(Subcategory.name).all()
        return render_template(
            "admin_inventory_category.html",
            category=category,
            items=items,
            subcategories=subcategories,
            active_tab="inventory",
        )

    @app.route("/admin/items/add", methods=["GET", "POST"])
    @login_required
    def add_item():
        # Optional context for where "+ Add Kit Item" was clicked from, so the
        # back link and (on error) the redirect can return to the same
        # category page instead of dumping the committee member back at the
        # top-level inventory picker.
        preselect_category_id = request.args.get("category_id", type=int)

        if request.method == "POST":
            preselect_category_id = request.form.get("preselect_category_id", type=int)
            name = request.form.get("name", "").strip()
            subcategory_id = request.form.get("subcategory_id", type=int)
            quantity_total = parse_positive_int(request.form.get("quantity_total"))
            error_redirect = url_for("add_item", category_id=preselect_category_id) if preselect_category_id else url_for("add_item")

            if not name:
                flash("Item name is required.", "error")
                return redirect(error_redirect)
            if not subcategory_id:
                flash("Please choose a subcategory for the item.", "error")
                return redirect(error_redirect)
            if quantity_total is None:
                flash("Quantity must be a whole number of at least 1.", "error")
                return redirect(error_redirect)
            if _name_exists(KitItem, name):
                flash(
                    f"An item named '{name}' already exists — choose a different name, "
                    "or edit the existing one instead of adding a duplicate.",
                    "error",
                )
                return redirect(error_redirect)

            group_id = request.form.get("group_id", type=int)
            item = KitItem(
                name=name,
                subcategory_id=subcategory_id,
                group_id=group_id,
                rack_id=request.form.get("rack_id", type=int),
                description=request.form.get("description", "").strip(),
                location=request.form.get("location", "").strip(),
                quantity_total=quantity_total,
                quantity_available=quantity_total,
                purchase_date=parse_date(request.form.get("purchase_date")),
            )
            db.session.add(item)
            db.session.commit()
            flash(f"Added '{item.name}' to the kit store.", "success")
            return redirect(url_for("admin_inventory_category", category_id=item.subcategory.category_id))

        categories = Category.query.order_by(Category.name).all()
        item_groups = ItemGroup.query.order_by(ItemGroup.name).all()
        return render_template(
            "admin_item_form.html",
            racks=Rack.query.order_by(Rack.name).all(),
            mode="add",
            item=None,
            categories=categories,
            item_groups=item_groups,
            preselect_category_id=preselect_category_id,
            active_tab="inventory",
        )

    @app.route("/admin/items/<int:item_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_item(item_id):
        item = db.get_or_404(KitItem, item_id)

        if request.method == "POST":
            new_name = request.form.get("name", item.name).strip()
            new_total = parse_positive_int(request.form.get("quantity_total"))

            if new_total is None:
                flash("Quantity must be a whole number of at least 1.", "error")
                return redirect(url_for("edit_item", item_id=item.id))

            if new_name and new_name.lower() != item.name.lower():
                if _name_exists(KitItem, new_name, exclude_id=item.id):
                    flash(f"An item named '{new_name}' already exists.", "error")
                    return redirect(url_for("edit_item", item_id=item.id))

            new_rack_id = request.form.get("rack_id", type=int)
            if new_rack_id != item.rack_id and item.quantity_available < item.quantity_total:
                flash(f"'{item.name}' is signed out — change its rack once it's all back.", "error")
                return redirect(url_for("edit_item", item_id=item.id))
            item.rack_id = new_rack_id

            # A reduced total can never go below what's currently out on loan -
            # otherwise "available" would go negative-in-spirit now and could
            # exceed "total" later once loans are returned, corrupting the count.
            if not _set_item_total(item, new_total):
                db.session.rollback()
                on_loan = item.quantity_total - item.quantity_available
                flash(
                    f"Can't set '{item.name}''s total to {new_total} — {on_loan} unit(s) are currently "
                    "signed out. Wait for them to be returned, or set the total to at least that many.",
                    "error",
                )
                return redirect(url_for("edit_item", item_id=item.id))

            item.name = new_name or item.name
            subcategory_id = request.form.get("subcategory_id", type=int)
            if subcategory_id:
                item.subcategory_id = subcategory_id
            item.group_id = request.form.get("group_id", type=int)
            item.location = request.form.get("location", item.location).strip()
            item.description = request.form.get("description", item.description or "").strip()
            item.purchase_date = parse_date(request.form.get("purchase_date"))

            db.session.commit()
            flash(f"Updated '{item.name}'.", "success")
            return redirect(url_for("admin_inventory_category", category_id=item.subcategory.category_id))

        categories = Category.query.order_by(Category.name).all()
        item_groups = ItemGroup.query.order_by(ItemGroup.name).all()
        return render_template(
            "admin_item_form.html",
            racks=Rack.query.order_by(Rack.name).all(),
            mode="edit",
            item=item,
            categories=categories,
            item_groups=item_groups,
            preselect_category_id=None,
            active_tab="inventory",
        )

    @app.route("/admin/items/<int:item_id>/delete", methods=["POST"])
    @login_required
    def delete_item(item_id):
        item = db.get_or_404(KitItem, item_id)
        category_id = item.subcategory.category_id  # captured before any mutation below
        if any(so.is_out for so in item.sign_outs):
            flash(f"Cannot delete '{item.name}' while units are signed out.", "error")
        elif item.sign_outs or item.units:
            # It has loan history or registered serials (just none active right now) - the database
            # won't allow a hard delete here (old SignOut rows still point at
            # this item), so archive it instead: same effect for anyone
            # browsing the catalogue, but the history stays intact and it can
            # be brought back later if that was a mistake.
            item.active = False
            db.session.commit()
            flash(
                f"'{item.name}' has loan history or registered serials, so it's been archived rather than deleted "
                "— visit Archived Items to restore it.",
                "success",
            )
        elif KitBundleLine.query.filter_by(item_id=item.id).first():
            # Same idea as the loan-history case above: a hard delete would
            # leave a bundle line pointing at nothing, so it's blocked rather
            # than silently breaking a bundle the committee set up on purpose.
            flash(
                f"Cannot delete '{item.name}' while it's part of a kit bundle "
                "— remove it from the bundle first.",
                "error",
            )
        else:
            db.session.delete(item)
            db.session.commit()
            flash(f"Deleted '{item.name}'.", "success")
        return redirect(url_for("admin_inventory_category", category_id=category_id))

    @app.route("/admin/items/<int:item_id>/restore", methods=["POST"])
    @login_required
    def restore_item(item_id):
        item = db.get_or_404(KitItem, item_id)
        item.active = True
        db.session.commit()
        flash(f"Restored '{item.name}' — it's back in the catalogue.", "success")
        return redirect(url_for("admin_archived"))

    # -- item groups --
    @app.route("/admin/groups")
    @login_required
    def admin_groups():
        subcategories = Subcategory.query.order_by(Subcategory.name).all()
        item_groups = ItemGroup.query.order_by(ItemGroup.name).all()
        return render_template(
            "admin_groups.html", subcategories=subcategories, item_groups=item_groups, active_tab="groups"
        )

    @app.route("/admin/groups/add", methods=["POST"])
    @login_required
    def add_item_group():
        name = request.form.get("name", "").strip()
        subcategory_id = request.form.get("subcategory_id", type=int)
        description = request.form.get("description", "").strip()

        if not name:
            flash("Group name is required.", "error")
        elif not subcategory_id:
            flash("Please choose a subcategory for the group.", "error")
        elif _name_exists(ItemGroup, name):
            flash(f"A group named '{name}' already exists.", "error")
        else:
            db.session.add(ItemGroup(name=name, subcategory_id=subcategory_id, description=description))
            db.session.commit()
            flash(f"Created group '{name}' — assign kit items to it from Add/Edit Kit Item.", "success")
        return redirect(url_for("admin_groups"))

    @app.route("/admin/groups/<int:group_id>/delete", methods=["POST"])
    @login_required
    def delete_item_group(group_id):
        group = db.get_or_404(ItemGroup, group_id)
        if group.items:
            flash(
                f"Cannot delete '{group.name}' while kit items still belong to it "
                "— reassign or remove them first.",
                "error",
            )
        elif KitBundleLine.query.filter_by(group_id=group.id).first():
            # Same idea as the equivalent check on deleting a KitItem: a hard
            # delete here would leave a bundle line pointing at nothing.
            flash(
                f"Cannot delete '{group.name}' while it's part of a kit bundle "
                "— remove it from the bundle first.",
                "error",
            )
        else:
            db.session.delete(group)
            db.session.commit()
            flash(f"Deleted group '{group.name}'.", "success")
        return redirect(url_for("admin_groups"))

    # -- kit bundles --
    @app.route("/admin/bundles")
    @login_required
    def admin_bundles():
        categories = Category.query.order_by(Category.name).all()
        subcategories = Subcategory.query.order_by(Subcategory.name).all()
        items = KitItem.query.filter_by(active=True, rack_id=None).order_by(KitItem.name).all()
        groups = [group for group in ItemGroup.query.order_by(ItemGroup.name).all() if group.active_items]
        bundles = KitBundle.query.order_by(KitBundle.name).all()
        return render_template(
            "admin_bundles.html",
            categories=categories,
            subcategories=subcategories,
            items=items,
            groups=groups,
            bundles=bundles,
            active_tab="bundles",
        )

    @app.route("/admin/bundles/add", methods=["POST"])
    @login_required
    def add_bundle():
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not name:
            flash("Bundle name is required.", "error")
        elif _name_exists(KitBundle, name):
            flash(f"A bundle named '{name}' already exists.", "error")
        else:
            db.session.add(KitBundle(name=name, description=description))
            db.session.commit()
            flash(f"Created bundle '{name}' — add items to it below.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/edit", methods=["POST"])
    @login_required
    def edit_bundle(bundle_id):
        bundle = db.get_or_404(KitBundle, bundle_id)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not name:
            flash("Bundle name is required.", "error")
        elif _name_exists(KitBundle, name, exclude_id=bundle.id):
            flash(f"A bundle named '{name}' already exists.", "error")
        else:
            bundle.name = name
            bundle.description = description
            db.session.commit()
            flash(f"Updated bundle '{name}'.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/delete", methods=["POST"])
    @login_required
    def delete_bundle(bundle_id):
        bundle = db.get_or_404(KitBundle, bundle_id)
        name = bundle.name
        db.session.delete(bundle)  # cascade also removes its lines - the items themselves are untouched
        db.session.commit()
        flash(f"Deleted bundle '{name}'. The items themselves are still in the kit store.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/lines/add", methods=["POST"])
    @login_required
    def add_bundle_line(bundle_id):
        bundle = db.get_or_404(KitBundle, bundle_id)
        quantity = parse_positive_int(request.form.get("quantity"))
        # The picker is a single <select> covering both items and groups -
        # see admin_bundles.html - so its value is "item:<id>" or
        # "group:<id>" and we work out which table to look in from that
        # prefix, rather than needing two separate pickers.
        kind, _, raw_id = request.form.get("entry_id", "").partition(":")
        item = db.session.get(KitItem, int(raw_id)) if kind == "item" and raw_id.isdigit() else None
        if item and item.rack_id:
            item = None
        group = db.session.get(ItemGroup, int(raw_id)) if kind == "group" and raw_id.isdigit() else None

        if not item and not group:
            flash("Choose an item or group to add to the bundle.", "error")
        elif quantity is None:
            flash("Quantity must be a whole number of at least 1.", "error")
        elif item and any(line.item_id == item.id for line in bundle.lines):
            flash(
                f"'{item.name}' is already in '{bundle.name}' — remove it and re-add it "
                "to change the quantity.",
                "error",
            )
        elif group and any(line.group_id == group.id for line in bundle.lines):
            flash(
                f"'{group.name}' is already in '{bundle.name}' — remove it and re-add it "
                "to change the quantity.",
                "error",
            )
        else:
            name = item.name if item else group.name
            db.session.add(
                KitBundleLine(
                    bundle_id=bundle.id,
                    item_id=item.id if item else None,
                    group_id=group.id if group else None,
                    quantity=quantity,
                )
            )
            db.session.commit()
            flash(f"Added {quantity} x {name} to '{bundle.name}'.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/lines/<int:line_id>/delete", methods=["POST"])
    @login_required
    def delete_bundle_line(bundle_id, line_id):
        line = KitBundleLine.query.filter_by(id=line_id, bundle_id=bundle_id).first_or_404()
        bundle_name = line.bundle.name
        line_name = line.name
        db.session.delete(line)
        db.session.commit()
        flash(f"Removed '{line_name}' from '{bundle_name}'.", "success")
        return redirect(url_for("admin_bundles"))

    # -- racks --
    @app.route("/admin/racks")
    @login_required
    def admin_racks():
        racks = Rack.query.order_by(Rack.name).all()
        # Only items that are fully in the store and not already in a rack can
        # be added to one - grouped by category to keep the picker scannable.
        assignable = (
            KitItem.query.filter_by(active=True, rack_id=None)
            .filter(KitItem.quantity_available == KitItem.quantity_total)
            .join(Subcategory)
            .join(Category)
            .order_by(Category.name, KitItem.name)
            .all()
        )
        assignable_by_category = {}
        for item in assignable:
            assignable_by_category.setdefault(item.category.name, []).append(item)
        return render_template(
            "admin_racks.html",
            racks=[(rack, _rack_blockers(rack)) for rack in racks],
            assignable_by_category=assignable_by_category,
            active_tab="racks",
        )

    def _rack_form_values():
        name = request.form.get("name", "").strip()
        colour = request.form.get("colour", "").strip()
        colour_hex = request.form.get("colour_hex", "").strip()
        return name, colour, colour_hex if HEX_COLOUR_RE.match(colour_hex) else "#888888"

    @app.route("/admin/racks/add", methods=["POST"])
    @login_required
    def add_rack():
        name, colour, colour_hex = _rack_form_values()
        if not name or not colour:
            flash("Rack name and colour are required.", "error")
        elif _name_exists(Rack, name):
            flash(f"A rack named '{name}' already exists.", "error")
        else:
            db.session.add(
                Rack(name=name, colour=colour, colour_hex=colour_hex, notes=request.form.get("notes", "").strip())
            )
            db.session.commit()
            flash(f"Created the {name} rack — now add its kit below.", "success")
        return redirect(url_for("admin_racks"))

    @app.route("/admin/racks/<int:rack_id>/edit", methods=["POST"])
    @login_required
    def edit_rack(rack_id):
        rack = db.get_or_404(Rack, rack_id)
        name, colour, colour_hex = _rack_form_values()
        if not name or not colour:
            flash("Rack name and colour are required.", "error")
        elif _name_exists(Rack, name, exclude_id=rack.id):
            flash(f"A rack named '{name}' already exists.", "error")
        else:
            rack.name, rack.colour, rack.colour_hex = name, colour, colour_hex
            rack.notes = request.form.get("notes", "").strip()
            db.session.commit()
            flash(f"Updated the {name} rack.", "success")
        return redirect(url_for("admin_racks"))

    @app.route("/admin/racks/<int:rack_id>/delete", methods=["POST"])
    @login_required
    def delete_rack(rack_id):
        rack = db.get_or_404(Rack, rack_id)
        if rack.loans:
            flash(f"The {rack.name} rack has loan history, so it can't be deleted — rename it instead.", "error")
        else:
            name = rack.name
            for item in rack.items:
                item.rack_id = None
            db.session.delete(rack)
            db.session.commit()
            flash(f"Deleted the {name} rack. Its kit is back in the store as individual items.", "success")
        return redirect(url_for("admin_racks"))

    @app.route("/admin/racks/<int:rack_id>/items/add", methods=["POST"])
    @login_required
    def add_rack_item(rack_id):
        rack = db.get_or_404(Rack, rack_id)
        item = form_id_or_404(KitItem, "item_id")
        if item.rack_id:
            flash(f"'{item.name}' is already in the {item.rack.name} rack.", "error")
        elif not item.active:
            flash(f"'{item.name}' is archived.", "error")
        elif item.quantity_available < item.quantity_total:
            flash(f"'{item.name}' is partly signed out — add it once it's all back.", "error")
        else:
            item.rack_id = rack.id
            db.session.commit()
            flash(f"Added '{item.name}' to the {rack.name} rack.", "success")
        return redirect(url_for("admin_racks"))

    @app.route("/admin/racks/<int:rack_id>/items/<int:item_id>/remove", methods=["POST"])
    @login_required
    def remove_rack_item(rack_id, item_id):
        rack = db.get_or_404(Rack, rack_id)
        item = KitItem.query.filter_by(id=item_id, rack_id=rack.id).first_or_404()
        if rack.open_loan:
            flash(f"The {rack.name} rack is out — remove pieces once it's back.", "error")
        else:
            item.rack_id = None
            db.session.commit()
            flash(f"Removed '{item.name}' from the {rack.name} rack.", "success")
        return redirect(url_for("admin_racks"))

    # -- archived items --
    @app.route("/admin/archived")
    @login_required
    def admin_archived():
        archived_items = KitItem.query.filter_by(active=False).order_by(KitItem.name).all()
        return render_template("admin_archived.html", archived_items=archived_items, active_tab="archived")

    # -- resolved requests --
    @app.route("/admin/requests/resolved")
    @login_required
    def admin_resolved():
        resolved_requests = (
            ItemRequest.query.filter(ItemRequest.status != "pending")
            .order_by(ItemRequest.resolved_at.desc())
            .limit(20)
            .all()
        )
        return render_template("admin_resolved.html", resolved_requests=resolved_requests, active_tab="resolved")

    def _claim_lines(lines, from_status, values_for):
        """Move each line still in from_status to new values in one guarded
        UPDATE apiece, so a double-tap or two committee members acting at
        once can't both win. Returns False (caller rolls back) if any lost."""
        for line in lines:
            result = db.session.execute(
                ItemRequest.__table__.update()
                .where(ItemRequest.id == line.id, ItemRequest.status == from_status)
                .values(**values_for(line))
            )
            if result.rowcount != 1:
                return False
        return True

    @app.route("/admin/requests/<request_group>/accept", methods=["POST"])
    @login_required
    def accept_request(request_group):
        lines = request_lines(request_group)
        pending = [line for line in lines if line.status == "pending"]
        if not pending:
            flash(f"That request from {lines[0].requester_name} was already dealt with.", "error")
            return redirect(url_for("admin"))
        note = request.form.get("handover_note", "").strip()
        if not note:
            flash("Add a handover note so the member knows when and where to collect.", "error")
            return redirect(url_for("admin", open=request_group) + f"#req-{request_group}")
        approved = {}
        for line in pending:
            raw = (request.form.get(f"approved_{line.id}") or "").strip()
            quantity = 0 if raw == "0" else parse_positive_int(raw)
            if quantity is None or quantity > line.quantity:
                flash(f"Give between 0 and {line.quantity} for {line.item_name}.", "error")
                return redirect(url_for("admin", open=request_group) + f"#req-{request_group}")
            approved[line.id] = quantity
        if not any(approved.values()):
            flash("Nothing would be handed over — use Deny instead, with a reason.", "error")
            return redirect(url_for("admin", open=request_group) + f"#req-{request_group}")

        now = utcnow()
        name = session.get("committee_name", "Committee")

        def values_for(line):
            common = {"claimed_by": name, "handover_note": note, "resolved_at": now}
            if approved[line.id] == 0:
                return {**common, "status": "denied", "deny_reason": "None available"}
            return {**common, "status": "approved", "approved_quantity": approved[line.id]}

        if not _claim_lines(pending, "pending", values_for):
            db.session.rollback()
            flash("Someone else just dealt with that request — check it below.", "error")
            return redirect(url_for("admin"))
        db.session.commit()
        lines = request_lines(request_group)
        send_request_email("update", lines, subject="Your CUMC kit request has been approved")
        flash(f"Accepted {lines[0].requester_name}'s request — they've been emailed your note.", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/requests/<request_group>/deny", methods=["POST"])
    @login_required
    def deny_request(request_group):
        lines = request_lines(request_group)
        pending = [line for line in lines if line.status == "pending"]
        reason = request.form.get("deny_reason", "").strip()
        if not pending:
            flash(f"That request from {lines[0].requester_name} was already dealt with.", "error")
        elif not reason:
            flash("Give a short reason — it's emailed to the member.", "error")
        else:
            name = session.get("committee_name", "Committee")
            values = {"status": "denied", "deny_reason": reason, "claimed_by": name, "resolved_at": utcnow()}
            if _claim_lines(pending, "pending", lambda line: values):
                db.session.commit()
                send_request_email("update", request_lines(request_group), subject="Your CUMC kit request")
                flash(f"Denied {lines[0].requester_name}'s request and emailed them the reason.", "success")
            else:
                db.session.rollback()
                flash("Someone else just dealt with that request — check it below.", "error")
        return redirect(url_for("admin"))

    @app.route("/admin/requests/<request_group>/add-item", methods=["POST"])
    @login_required
    def add_request_item(request_group):
        lines = request_lines(request_group)
        first = lines[0]
        quantity = parse_positive_int(request.form.get("quantity"))
        kind, _, raw_id = (request.form.get("entry") or "").partition(":")
        entry_id = parse_positive_int(raw_id)
        target = None
        if kind == "item" and entry_id:
            target = db.session.get(KitItem, entry_id)
            target = target if target and target.active and target.rack_id is None else None
        elif kind == "group" and entry_id:
            target = db.session.get(ItemGroup, entry_id)
            target = target if target and target.active_items else None
        if any(line.status != "pending" for line in lines):
            flash("Items can only be added while the whole request is pending.", "error")
        elif target is None:
            flash("Choose an item to add.", "error")
        elif quantity is None:
            flash("Enter a quantity of at least 1.", "error")
        else:
            db.session.add(
                ItemRequest(
                    requester_name=first.requester_name,
                    requester_email=first.requester_email,
                    item_name=target.name,
                    quantity=quantity,
                    message=first.message,
                    request_group=first.request_group,
                    group_id=target.id if kind == "group" else None,
                    needed_from=first.needed_from,
                    needed_to=first.needed_to,
                    status_token=first.status_token,
                )
            )
            db.session.commit()
            flash(f"Added {quantity} x {target.name} to {first.requester_name}'s request.", "success")
        return redirect(url_for("admin", open=request_group) + f"#req-{request_group}")

    @app.route("/admin/requests/<request_group>/dates", methods=["POST"])
    @login_required
    def change_request_dates(request_group):
        lines = request_lines(request_group)
        needed_from = parse_date(request.form.get("needed_from"))
        needed_to = parse_date(request.form.get("needed_to"))
        open_lines = [
            line for line in lines
            if line.status == "pending" or (line.status == "approved" and not line.fulfilled_signout_id)
        ]
        if not needed_from or not needed_to or needed_to < needed_from:
            flash("Choose a need-by date and a return date on or after it.", "error")
        elif not open_lines:
            flash("Nothing left on that request to change.", "error")
        else:
            for line in open_lines:
                line.needed_from, line.needed_to = needed_from, needed_to
            db.session.commit()
            flash(f"Updated the dates on {lines[0].requester_name}'s request.", "success")
        return redirect(request.referrer or url_for("admin"))

    @app.route("/admin/requests/<request_group>/cancel", methods=["POST"])
    @login_required
    def cancel_request(request_group):
        lines = request_lines(request_group)
        uncollected = [line for line in lines if line.status == "approved" and not line.fulfilled_signout_id]
        if not uncollected:
            flash("Nothing on that request is waiting to be collected.", "error")
            return redirect(request.referrer or url_for("sign_in_out"))
        result = db.session.execute(
            ItemRequest.__table__.update()
            .where(
                ItemRequest.id.in_([line.id for line in uncollected]),
                ItemRequest.status == "approved",
                ItemRequest.fulfilled_signout_id.is_(None),
            )
            .values(status="cancelled", resolved_at=utcnow())
        )
        db.session.commit()
        if result.rowcount:
            send_request_email("update", request_lines(request_group), subject="Your CUMC kit request was cancelled")
            flash(f"Cancelled {lines[0].requester_name}'s request and emailed them.", "success")
        return redirect(request.referrer or url_for("sign_in_out"))

    @app.route("/admin/requests/<int:request_id>/collect", methods=["POST"])
    @login_required
    def collect_request(request_id):
        """Turn an approved request into a real sign-out once the member has
        actually picked the kit up, so the two systems stay in sync instead of
        relying on the committee to remember to do this manually."""
        item_request = db.get_or_404(ItemRequest, request_id)
        # Collect can be actioned from either Sign In/Out (the usual place) or
        # Resolved Requests, so send the committee back to whichever page they
        # actually clicked from instead of always landing on Resolved Requests.
        back_to = request.referrer or url_for("admin_resolved")
        # Errors land back on Sign In/Out with this request's panel still open.
        retry_to = url_for("sign_in_out", open=request_id) + f"#collect-{request_id}"

        if item_request.status != "approved":
            flash("Only approved requests can be marked as collected.", "error")
            return redirect(back_to)
        if item_request.fulfilled_signout_id:
            flash("This request has already been marked as collected.", "error")
            return redirect(back_to)
        due_date, due_error = _due_date_from_form()
        if due_error:
            flash(due_error, "error")
            return redirect(retry_to)

        if item_request.group_id:
            # A group request (e.g. "Nuts") doesn't name one specific KitItem,
            # so the committee says which size(s) are actually being handed
            # over - split across as many of the group's items as it takes to
            # reach the requested quantity (quantity_<item_id> fields, same
            # convention as sign_out_group), rather than requiring one single
            # item to have the whole amount in stock on its own.
            group = db.session.get(ItemGroup, item_request.group_id)
            splits, error = _parse_group_split_quantities(
                group.active_items if group else [], request.form
            )
            if error:
                flash(error, "error")
                return redirect(retry_to)

            total = sum(quantity for _, quantity in splits)
            if not splits:
                flash(
                    f"Choose which specific item(s) to hand over for '{item_request.item_name}'.",
                    "error",
                )
                return redirect(retry_to)
            if total != item_request.handover_quantity:
                flash(
                    f"Chosen quantities add up to {total}, but {item_request.requester_name} was approved for "
                    f"{item_request.handover_quantity} x {item_request.item_name} — adjust so they match exactly.",
                    "error",
                )
                return redirect(retry_to)

            checked_out_by = session.get("committee_name", "Committee")
            first_signout_id = None
            try:
                for candidate, quantity in splits:
                    signout = _create_signout(
                        candidate,
                        quantity,
                        item_request.requester_name,
                        item_request.message,
                        checked_out_by,
                        due_date=due_date,
                        borrower_email=item_request.requester_email,
                    )
                    db.session.flush()  # populate signout.id before we link/reference it
                    if first_signout_id is None:
                        first_signout_id = signout.id
                # fulfilled_signout_id only holds a single FK, so a split
                # collect links to the first SignOut row created - every row
                # it produced is still there in Sign-Out History, this field
                # is only used to tell "collected" apart from "not yet", not
                # as a complete record.
                if not _claim_request_collection(item_request, first_signout_id):
                    db.session.rollback()
                    flash("This request has already been marked as collected.", "error")
                    return redirect(back_to)
                db.session.commit()
            except InsufficientStockError as e:
                db.session.rollback()
                flash(
                    f"Only {e.item.quantity_available} of '{e.item.name}' available now — "
                    "someone else may have just signed it out. Adjust and try again.",
                    "error",
                )
                return redirect(back_to)
            summary = ", ".join(f"{quantity} x {candidate.name}" for candidate, quantity in splits)
            flash(
                f"Signed out {total} x {item_request.item_name} to {item_request.requester_name} "
                f"({summary}) and marked the request as collected.",
                "success",
            )
            return redirect(back_to)

        item = item_request.matching_item
        if not item:
            flash(
                f"No catalog item matches '{item_request.item_name}' — sign it out manually from Sign In/Out instead.",
                "error",
            )
            return redirect(back_to)

        gear_unit, serial_error = _serial_from_form(item, request.form.get("serial"))
        if serial_error:
            flash(serial_error, "error")
            return redirect(retry_to)
        if item_request.handover_quantity > item.quantity_available:
            flash(
                f"Only {item.quantity_available} of '{item.name}' available right now — "
                "adjust the loan or sign it out manually.",
                "error",
            )
            return redirect(back_to)

        try:
            signout = _create_signout(
                item,
                item_request.handover_quantity,
                item_request.requester_name,
                item_request.message,
                session.get("committee_name", "Committee"),
                due_date=due_date,
                borrower_email=item_request.requester_email,
                gear_unit=gear_unit,
            )
            db.session.flush()  # populate signout.id before we link it below
            if not _claim_request_collection(item_request, signout.id):
                db.session.rollback()
                flash("This request has already been marked as collected.", "error")
                return redirect(back_to)
            db.session.commit()
        except InsufficientStockError as e:
            db.session.rollback()
            flash(
                f"Only {e.item.quantity_available} of '{e.item.name}' available right now — "
                "adjust the loan or sign it out manually.",
                "error",
            )
            return redirect(back_to)
        flash(
            f"Signed out {item_request.handover_quantity} x {item.name} to {item_request.requester_name} "
            "and marked the request as collected.",
            "success",
        )
        return redirect(back_to)


    # --------------------------------------------------------- scheduled tasks
    @app.route("/tasks/send-reminders", methods=["POST"])
    @csrf.exempt
    def send_reminders_task():
        """Called hourly by an external cron with the X-Cron-Secret header."""
        secret = app.config.get("CRON_SECRET")  # already cleaned by config.py
        if not secret:
            abort(404)
        provided = request.headers.get("X-Cron-Secret")
        if provided is None:
            app.logger.warning("send-reminders refused: no X-Cron-Secret header sent")
            abort(403)
        # Same tidying as config.py, for a secret pasted into the cron service.
        if not secrets.compare_digest(clean_value(provided), secret):
            app.logger.warning("send-reminders refused: X-Cron-Secret doesn't match CRON_SECRET")
            abort(403)
        return run_reminders(_london_today())

    # -------------------------------------------------------- gear register
    @app.route("/gear/login", methods=["GET", "POST"])
    def gear_login():
        if request.method == "POST":
            lockout_key = f"gear:{request.remote_addr}"
            locked_until = _login_locked_until(lockout_key)
            if locked_until:
                flash(f"Too many incorrect attempts. Try again in {int(locked_until - time.time())} second(s).", "error")
                return render_template("gear_login.html")
            if secrets.compare_digest(request.form.get("password", ""), app.config["GEAR_PASSWORD"]):
                session["is_gear"] = True
                session["gear_name"] = request.form.get("gear_name", "").strip() or "Gear Secretary"
                _clear_login_attempts(lockout_key)
                flash("Logged in to the gear register.", "success")
                return redirect(_safe_next_url(request.args.get("next")) or url_for("gear_register"))
            _record_failed_login(lockout_key)
            flash("Incorrect gear password.", "error")
        return render_template("gear_login.html")

    @app.route("/gear/logout", methods=["POST"])
    def gear_logout():
        session.pop("is_gear", None)
        session.pop("gear_name", None)
        flash("Logged out of the gear register.", "success")
        return redirect(url_for("home"))

    @app.route("/gear")
    @gear_required
    def gear_register():
        show = request.args.get("show", "all")
        query = request.args.get("q", "").strip()
        if query:
            exact = GearUnit.query.filter(func.lower(GearUnit.serial) == query.lower()).first()
            if exact:
                return redirect(url_for("gear_unit", unit_id=exact.id))
        units = GearUnit.query.join(KitItem).order_by(KitItem.name, GearUnit.serial).all()
        in_service = [unit for unit in units if unit.in_service]
        needing_action = sorted((unit for unit in in_service if unit.flags()), key=lambda unit: not unit.past_lifespan)
        if query:
            show = "search"
            listed = [unit for unit in units if query.lower() in unit.serial.lower()]
        elif show == "retired":
            listed = [unit for unit in units if not unit.in_service]
        elif show == "action":
            listed = needing_action
        else:
            show, listed = "all", in_service
        by_subcategory = {}
        for unit in listed:
            by_subcategory.setdefault(unit.item.subcategory, []).append(unit)
        groups = sorted(by_subcategory.items(), key=lambda kv: (kv[0].category.name, kv[0].name))
        return render_template(
            "gear_register.html",
            groups=groups,
            show=show,
            query=query,
            to_inspect=len(_inspection_queue()[0]),
            counts={"all": len(in_service), "action": len(needing_action), "retired": len(units) - len(in_service)},
            gear_tab="register",
        )

    @app.route("/gear/decode")
    @gear_required
    def gear_decode():
        made, rule = serial_dates.decode(request.args.get("brand"), request.args.get("serial", ""))
        return {"date": made.isoformat() if made else None, "rule": rule}

    def _trackable_items():
        return (
            KitItem.query.filter_by(active=True)
            .join(Subcategory)
            .filter(Subcategory.track_serials.is_(True))
            .order_by(KitItem.name)
            .all()
        )

    @app.route("/gear/units/add", methods=["GET", "POST"])
    @gear_required
    def add_gear_unit():
        if request.method == "POST":
            item_id = parse_positive_int(request.form.get("item_id"))
            item = db.session.get(KitItem, item_id) if item_id else None
            serial = request.form.get("serial", "").strip()
            brand = serial_dates.normalise_brand(request.form.get("brand"))
            grade = _parse_grade(request.form.get("grade"))
            if not item:
                flash("Choose which kit item this piece belongs to.", "error")
            elif not serial:
                flash("Serial number is required.", "error")
            elif _serial_exists(serial):
                flash(f"Serial {serial} is already in the register.", "error")
            else:
                made, source = _manufacture_date_and_source(
                    brand, serial, parse_date(request.form.get("manufacture_date"))
                )
                unit = GearUnit(
                    item_id=item.id,
                    serial=serial,
                    brand=brand,
                    manufacture_date=made,
                    manufacture_date_source=source,
                    purchase_date=parse_date(request.form.get("purchase_date")),
                    notes=request.form.get("notes", "").strip() or None,
                )
                db.session.add(unit)
                db.session.flush()
                if grade:
                    db.session.add(
                        Inspection(unit_id=unit.id, grade=grade, notes="Condition when registered", inspected_by=session.get("gear_name"))
                    )
                db.session.commit()
                note = " Manufacture date decoded from the serial — check it against the label." if source == "decoded" else ""
                flash(f"Registered {serial} ({item.name}).{note}", "success")
                if request.form.get("another"):
                    return redirect(
                        url_for(
                            "add_gear_unit",
                            item_id=item.id,
                            brand=brand,
                            purchase_date=request.form.get("purchase_date") or None,
                        )
                    )
                return redirect(url_for("gear_unit", unit_id=unit.id))
        return render_template(
            "gear_unit_form.html",
            items=_trackable_items(),
            brands=serial_dates.BRANDS,
            grades={k: v for k, v in INSPECTION_GRADES.items() if k != "retire"},
            keep=request.form if request.method == "POST" else request.args,
            gear_tab="add",
        )

    @app.route("/gear/units/<int:unit_id>")
    @gear_required
    def gear_unit(unit_id):
        unit = db.get_or_404(GearUnit, unit_id)
        return render_template(
            "gear_unit.html",
            unit=unit,
            grades={k: v for k, v in INSPECTION_GRADES.items() if k != "retire"},
            brands=serial_dates.BRANDS,
            gear_tab="register",
        )

    @app.route("/gear/units/<int:unit_id>/edit", methods=["POST"])
    @gear_required
    def edit_gear_unit(unit_id):
        unit = db.get_or_404(GearUnit, unit_id)
        serial = request.form.get("serial", "").strip()
        if not serial:
            flash("Serial number is required.", "error")
        elif _serial_exists(serial, exclude_id=unit.id):
            flash(f"Serial {serial} is already in the register.", "error")
        else:
            unit.serial = serial
            unit.brand = serial_dates.normalise_brand(request.form.get("brand"))
            unit.manufacture_date, unit.manufacture_date_source = _manufacture_date_and_source(
                unit.brand, serial, parse_date(request.form.get("manufacture_date"))
            )
            unit.purchase_date = parse_date(request.form.get("purchase_date"))
            unit.notes = request.form.get("notes", "").strip() or None
            db.session.commit()
            flash(f"Updated {serial}.", "success")
        return redirect(url_for("gear_unit", unit_id=unit.id))

    @app.route("/gear/units/<int:unit_id>/inspect", methods=["POST"])
    @gear_required
    def inspect_gear_unit(unit_id):
        unit = db.get_or_404(GearUnit, unit_id)
        grade = _parse_grade(request.form.get("grade"))
        if not unit.in_service:
            flash(f"{unit.serial} is already retired.", "error")
        elif not grade:
            flash("Choose a condition grade.", "error")
        else:
            flash(_record_grade(unit, grade, request.form), "success")
            db.session.commit()
        return redirect(url_for("gear_unit", unit_id=unit.id))

    def _record_grade(unit, grade, form):
        """Log an inspection, or retire the piece (keeping its last real
        grade). Returns the message to flash; caller commits."""
        notes = form.get("notes", "").strip() or None
        if grade == "retire":
            stock = _retire_unit(unit, bool(form.get("reduce_stock")), reason=notes)
            return f"Retired {unit.serial}.{stock}"
        db.session.add(Inspection(unit_id=unit.id, grade=grade, notes=notes, inspected_by=session.get("gear_name")))
        return f"Recorded '{INSPECTION_GRADES[grade]}' for {unit.serial}."

    @app.route("/gear/units/<int:unit_id>/retire", methods=["POST"])
    @gear_required
    def retire_gear_unit(unit_id):
        unit = db.get_or_404(GearUnit, unit_id)
        if not unit.in_service:
            flash(f"{unit.serial} is already retired.", "error")
        elif not request.form.get("notes", "").strip():
            flash("Say why it's being retired.", "error")
        else:
            flash(_record_grade(unit, "retire", request.form), "success")
            db.session.commit()
        return redirect(url_for("gear_unit", unit_id=unit.id))

    @app.route("/gear/inspect")
    @gear_required
    def gear_inspect():
        queue, total = _inspection_queue()
        current = queue[0] if queue else None
        wanted = parse_positive_int(request.args.get("unit"))
        if wanted:
            current = next((unit for unit in queue if unit.id == wanted), current)
        by_item = {}
        for unit in queue:
            by_item.setdefault(unit.item, []).append(unit)
        return render_template(
            "gear_inspect.html",
            current=current,
            remaining=len(queue),
            total=total,
            by_item=sorted(by_item.items(), key=lambda kv: kv[0].name),
            gear_tab="inspect",
        )

    @app.route("/gear/inspect/<int:unit_id>", methods=["POST"])
    @gear_required
    def gear_inspect_unit(unit_id):
        unit = db.get_or_404(GearUnit, unit_id)
        grade = _parse_grade(request.form.get("grade"))
        if not unit.in_service:
            flash(f"{unit.serial} is already retired.", "error")
        elif not grade:
            flash("Choose a condition.", "error")
        elif grade == "retire" and not request.form.get("notes", "").strip():
            flash("Say why it's being retired.", "error")
            return redirect(url_for("gear_inspect", unit=unit.id))
        else:
            flash(_record_grade(unit, grade, request.form), "success")
            db.session.commit()
        return redirect(url_for("gear_inspect"))

    @app.route("/gear/inspect/item/<int:item_id>/all-good", methods=["POST"])
    @gear_required
    def gear_inspect_all_good(item_id):
        item = db.get_or_404(KitItem, item_id)
        if not request.form.get("confirm"):
            flash("Tick the box to confirm you've checked every piece.", "error")
            return redirect(url_for("gear_inspect"))
        units = [unit for unit in _inspection_queue()[0] if unit.item_id == item.id]
        for unit in units:
            db.session.add(
                Inspection(unit_id=unit.id, grade="good", notes="Checked in a batch", inspected_by=session.get("gear_name"))
            )
        db.session.commit()
        flash(f"Marked {len(units)} {item.name} as Good.", "success")
        return redirect(url_for("gear_inspect"))

    @app.route("/gear/settings", methods=["GET", "POST"])
    @gear_required
    def gear_settings():
        subcategories = Subcategory.query.join(Category).order_by(Category.name, Subcategory.name).all()
        if request.method == "POST":
            if request.form.get("ppe_defaults"):
                changed = apply_ppe_defaults()
                db.session.commit()
                flash(f"Turned on PPE tracking for {changed} subcategor{'y' if changed == 1 else 'ies'}.", "success")
                return redirect(url_for("gear_settings"))
            # Only rows shown on the page are posted (tracked ones, plus any added).
            for sub in subcategories:
                if f"max_age_{sub.id}" in request.form:
                    sub.track_serials = bool(request.form.get(f"track_{sub.id}"))
                    sub.max_age_years = parse_positive_int(request.form.get(f"max_age_{sub.id}"))
            added = db.session.get(Subcategory, parse_positive_int(request.form.get("add_subcategory")) or 0)
            if added:
                added.track_serials = True
            db.session.commit()
            flash("Saved register settings.", "success")
            return redirect(url_for("gear_settings"))
        return render_template(
            "gear_settings.html",
            tracked=[sub for sub in subcategories if sub.track_serials],
            untracked=[sub for sub in subcategories if not sub.track_serials],
            gear_tab="settings",
        )

    @app.route("/gear/import", methods=["GET", "POST"])
    @gear_required
    def gear_import():
        if request.method == "GET":
            return render_template("gear_import.html", columns=REGISTER_CSV_COLUMNS, gear_tab="import")
        if request.form.get("confirm"):
            csv_text = request.form.get("csv_text", "")
        else:
            upload = request.files.get("file")
            if not upload or not upload.filename:
                flash("Choose a CSV file to upload.", "error")
                return redirect(url_for("gear_import"))
            try:
                csv_text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                flash("That file isn't UTF-8 text - save it as CSV (UTF-8) and try again.", "error")
                return redirect(url_for("gear_import"))
        rows, errors = _validate_register_csv(csv_text)
        if request.form.get("confirm") and rows and not errors:
            for row in rows:
                grade = row.pop("grade")
                item = row.pop("item")
                unit = GearUnit(item_id=item.id, **row)
                db.session.add(unit)
                if grade:
                    db.session.flush()
                    db.session.add(
                        Inspection(unit_id=unit.id, grade=grade, notes="Imported from CSV", inspected_by=session.get("gear_name"))
                    )
            db.session.commit()
            flash(f"Imported {len(rows)} piece(s) into the register.", "success")
            return redirect(url_for("gear_register"))
        return render_template(
            "gear_import.html",
            columns=REGISTER_CSV_COLUMNS,
            rows=rows,
            errors=errors,
            csv_text=csv_text,
            grades=INSPECTION_GRADES,
            previewed=True,
            gear_tab="import",
        )

    def _csv_response(rows, filename):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(rows)
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.route("/gear/import/template.csv")
    @gear_required
    def gear_import_template():
        return _csv_response([REGISTER_CSV_COLUMNS], "gear-register-template.csv")

    @app.route("/gear/export.csv")
    @gear_required
    def gear_export():
        rows = [REGISTER_CSV_COLUMNS + ["status", "last_inspected", "retired_reason"]]
        for unit in GearUnit.query.join(KitItem).order_by(KitItem.name, GearUnit.serial):
            latest = unit.latest_grade_inspection
            rows.append(
                [
                    _csv_safe(value)
                    for value in (
                        unit.item.name,
                        unit.serial,
                        unit.brand,
                        unit.manufacture_date.isoformat() if unit.manufacture_date else "",
                        unit.purchase_date.isoformat() if unit.purchase_date else "",
                        latest.grade_label if latest else "",
                        unit.notes,
                        "retired" if not unit.in_service else "in service",
                        latest.inspected_at.date().isoformat() if latest else "",
                        unit.retired_reason,
                    )
                ]
            )
        return _csv_response(rows, f"gear-register-{date.today().isoformat()}.csv")


app = create_app()

if __name__ == "__main__":
    # debug follows Config.DEBUG (FLASK_DEBUG env var) rather than always
    # being True, so an accidental `python app.py` in production can't expose
    # the Werkzeug interactive debugger (arbitrary code execution on any
    # unhandled exception). The real deploy path is gunicorn (see Procfile),
    # which never hits this branch at all.
    app.run(debug=app.config["DEBUG"])
