import secrets
import uuid
from datetime import date, datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

db = SQLAlchemy()


def utcnow():
    """Current UTC time as a naive datetime, matching how timestamps are
    stored (replacement for the deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Category(db.Model):
    """A top-level grouping of kit, e.g. 'Hardware' or 'Soft Goods'."""

    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    # Lets the committee hide an entire category from the member-facing
    # request pages (e.g. "Cage" storage the club doesn't want members
    # browsing) while keeping it fully visible and manageable on the admin
    # side. server_default is set (not just default=) so that adding this
    # column to an already-populated production table backfills existing
    # rows as visible rather than leaving them NULL - see _ensure_schema in
    # app.py, which adds this column to databases created before it existed.
    member_visible = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text("true"))

    subcategories = db.relationship(
        "Subcategory", backref="category", lazy=True, order_by="Subcategory.name"
    )


class Subcategory(db.Model):
    """A specific type of kit within a category, e.g. 'Single Rope' or 'Static Rope'."""

    __tablename__ = "subcategories"
    __table_args__ = (db.UniqueConstraint("name", "category_id", name="uq_subcategory_name_per_category"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    # Gear register settings, set by the Gear Secretary: whether kit in this
    # subcategory gets per-piece serial tracking, and the manufacturer's
    # maximum lifespan from manufacture (e.g. 10 years for textiles).
    track_serials = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("false"))
    max_age_years = db.Column(db.Integer, nullable=True)

    items = db.relationship("KitItem", backref="subcategory", lazy=True, order_by="KitItem.name")


class ItemGroup(db.Model):
    """A member-facing 'product' that stands in for several individually
    tracked KitItem variants a member shouldn't need to tell apart - e.g. a
    'Nuts' group covering Nut Size 1 through 11, each still its own KitItem
    row with its own real stock count. Members browse and request the group
    as one thing ('Nuts'); the committee still hands over one specific size
    underneath it when they fulfil the request, so the actual stock list
    stays accurate per-size."""

    __tablename__ = "item_groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    subcategory_id = db.Column(db.Integer, db.ForeignKey("subcategories.id"), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    subcategory = db.relationship("Subcategory")
    # backref="group" gives every KitItem in this group a `.group` attribute
    # pointing back here, mirroring how Subcategory.items/KitItem.subcategory work.
    items = db.relationship("KitItem", backref="group", lazy=True, order_by="KitItem.name")

    @property
    def category(self):
        return self.subcategory.category if self.subcategory else None

    @property
    def active_items(self):
        # Rack pieces only ever leave as part of their rack, so they never
        # count towards a group's lendable stock.
        return [item for item in self.items if item.active and item.rack_id is None]

    @property
    def quantity_available(self):
        """Aggregate stock across every active variant in the group - what a
        member sees as 'how many are available', without caring which
        specific size(s) that total is made up of."""
        return sum(item.quantity_available for item in self.active_items)

    @property
    def quantity_total(self):
        return sum(item.quantity_total for item in self.active_items)


class KitItem(db.Model):
    """A piece of kit owned by the club (e.g. a tent, a rope, a helmet)."""

    __tablename__ = "kit_items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subcategory_id = db.Column(db.Integer, db.ForeignKey("subcategories.id"), nullable=False)
    # Optional: if set, members see this item folded into its group's single
    # aggregated listing (e.g. 'Nuts') instead of as its own row - see
    # ItemGroup above. Leave unset for kit that's requested individually as normal.
    group_id = db.Column(db.Integer, db.ForeignKey("item_groups.id"), nullable=True)
    # Set if this item is part of a colour-coded rack (e.g. the Red trad
    # rack). Rack pieces are rack-only: they're signed out and back in with
    # the whole rack, never on their own.
    rack_id = db.Column(db.Integer, db.ForeignKey("racks.id"), nullable=True)
    # For a rack line: the shared item its units were taken from (e.g. the
    # Red rack's ".4 Silver" came out of the store's ".4 Silver" cams), so
    # removing it from the rack puts them back.
    rack_source_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=True)
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(120), nullable=True)
    quantity_total = db.Column(db.Integer, nullable=False, default=1)
    quantity_available = db.Column(db.Integer, nullable=False, default=1)
    # Items with loan history can't be hard-deleted (the database would refuse
    # it - old SignOut rows still point at them), so "deleting" an item that
    # has ever been signed out archives it instead: active=False hides it from
    # the catalogue and sign-out pages, but keeps it (and its history) in the
    # database. Committee can restore it from the admin page.
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Admin-only info: never rendered on the public request or committee sign-in/out pages.
    purchase_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    sign_outs = db.relationship("SignOut", backref="item", lazy=True)
    rack_source = db.relationship("KitItem", remote_side=[id])

    @property
    def short_name(self):
        """Name without the rack suffix, for listing inside its rack."""
        return self.rack_source.name if self.rack_source else self.name

    @property
    def quantity_out(self):
        return self.quantity_total - self.quantity_available

    @property
    def category(self):
        return self.subcategory.category if self.subcategory else None


class Rack(db.Model):
    """A colour-coded set of kit (e.g. the Red trad rack) that is signed out
    and returned as one unit."""

    __tablename__ = "racks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    colour = db.Column(db.String(40), nullable=False)
    colour_hex = db.Column(db.String(7), nullable=False, default="#888888")
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    items = db.relationship("KitItem", backref="rack", lazy=True, order_by="KitItem.name")
    loans = db.relationship("RackLoan", backref="rack", lazy=True, order_by="RackLoan.signed_out_at.desc()")

    @property
    def open_loan(self):
        return next((loan for loan in self.loans if loan.signed_in_at is None), None)


class RackLoan(db.Model):
    """One whole-rack sign-out. Each piece also gets its own SignOut row
    (linked via SignOut.rack_loan_id) so stock moves through the usual
    atomic _create_signout path."""

    __tablename__ = "rack_loans"

    id = db.Column(db.Integer, primary_key=True)
    rack_id = db.Column(db.Integer, db.ForeignKey("racks.id"), nullable=False)
    borrower_name = db.Column(db.String(120), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    checked_out_by = db.Column(db.String(120), nullable=True)
    signed_out_at = db.Column(db.DateTime, default=utcnow)
    signed_in_at = db.Column(db.DateTime, nullable=True)

    sign_outs = db.relationship("SignOut", backref="rack_loan", lazy=True)


class KitBundle(db.Model):
    """A named collection of distinct kit items grouped together for
    browsing/requesting as one unit - e.g. a 'Trad Rack' bundle of nuts, cams
    and hexes, or a one-off kit list saved for a specific club trip. This is
    different from KitItem.quantity_total: a bundle doesn't hold its own
    stock, it's just a saved shortcut for adding several existing items to a
    basket (or, on the admin side, tracking what a request should contain) in
    one go."""

    __tablename__ = "kit_bundles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    lines = db.relationship(
        "KitBundleLine",
        backref="bundle",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="KitBundleLine.id",
    )


class KitBundleLine(db.Model):
    """One line inside a KitBundle: how many of *either* a specific KitItem
    or an ItemGroup the bundle includes (never both) - e.g. the 'Trad Rack'
    bundle might have a line for the specific 'Set of Nuts' item, and also a
    line for the 'Helmets' group, letting the committee pick which helmet to
    actually hand over exactly like a member requesting Helmets on its own."""

    __tablename__ = "kit_bundle_lines"

    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey("kit_bundles.id"), nullable=False)
    # Exactly one of item_id/group_id is set per line - enforced in the
    # add_bundle_line route, not at the database level, matching how the
    # rest of the app (e.g. add_to_basket) already handles "one of several
    # optional fields" without a DB constraint.
    item_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("item_groups.id"), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    item = db.relationship("KitItem")
    group = db.relationship("ItemGroup")

    @property
    def name(self):
        return self.group.name if self.group_id else self.item.name

    @property
    def active(self):
        """Whether this line still points at something a member could
        actually be given: an active KitItem, or a group that still has at
        least one active item left in it."""
        if self.group_id:
            return bool(self.group.active_items)
        return self.item.active and self.item.rack_id is None


INSPECTION_GRADES = {
    "good": "Good",
    "minor_wear": "Minor wear",
    "needs_attention": "Needs attention",
    "retire": "Retire",
}
# Grades that mean a piece shouldn't be lent until someone deals with it.
ACTION_GRADES = ("needs_attention", "retire")


class GearUnit(db.Model):
    """One physical, serial-numbered piece of a KitItem (e.g. one specific
    harness out of the 'Harness - Medium' stock), kept in the gear register
    by the Gear Secretary. Sign-outs stay quantity-based; this is the record
    of each piece's age and condition."""

    __tablename__ = "gear_units"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=False)
    serial = db.Column(db.String(80), nullable=False)
    brand = db.Column(db.String(40), nullable=True)
    manufacture_date = db.Column(db.Date, nullable=True)
    # "decoded" if the date came from the serial number, "manual" if typed in.
    manufacture_date_source = db.Column(db.String(10), nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(12), nullable=False, default="in_service")  # in_service / retired
    retired_at = db.Column(db.DateTime, nullable=True)
    retired_reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    item = db.relationship("KitItem", backref=db.backref("units", lazy=True, order_by="GearUnit.serial"))
    inspections = db.relationship(
        "Inspection", backref="unit", lazy=True, order_by="Inspection.inspected_at.desc()", cascade="all, delete-orphan"
    )

    @property
    def latest_inspection(self):
        return self.inspections[0] if self.inspections else None

    @property
    def latest_grade_inspection(self):
        """Newest inspection that recorded a real condition (older records
        used a "retire" grade before retiring became its own action)."""
        return next((i for i in self.inspections if i.grade != "retire"), None)

    @property
    def open_loan(self):
        return SignOut.query.filter_by(gear_unit_id=self.id, signed_in_at=None).first()

    @property
    def past_lifespan(self):
        lifespan = self.item.subcategory.max_age_years if self.item.subcategory else None
        age = self.age_years
        return bool(lifespan and age is not None and age >= lifespan)

    @property
    def in_service(self):
        return self.status == "in_service"

    @property
    def age_years(self):
        if not self.manufacture_date:
            return None
        return (date.today() - self.manufacture_date).days / 365.25

    def flags(self):
        """Reasons this in-service piece needs the Gear Secretary's attention."""
        if not self.in_service:
            return []
        flags = []
        latest = self.latest_inspection
        if latest is None:
            flags.append("Never inspected")
        elif latest.grade in ACTION_GRADES:
            flags.append(INSPECTION_GRADES[latest.grade])
        lifespan = self.item.subcategory.max_age_years if self.item.subcategory else None
        age = self.age_years
        if lifespan and age is not None:
            if age >= lifespan:
                flags.append("Past lifespan")
            elif age >= lifespan - 1:
                flags.append("Within a year of lifespan")
        return flags


class Inspection(db.Model):
    """One damage/condition check of a GearUnit. The newest one is the
    piece's current condition; the rest are its history."""

    __tablename__ = "inspections"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("gear_units.id"), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    inspected_by = db.Column(db.String(120), nullable=True)
    inspected_at = db.Column(db.DateTime, default=utcnow)

    @property
    def grade_label(self):
        return INSPECTION_GRADES.get(self.grade, self.grade)


class SignOut(db.Model):
    """A record of kit being signed out and (eventually) signed back in."""

    __tablename__ = "sign_outs"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=False)
    borrower_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text, nullable=True)
    checked_out_by = db.Column(db.String(120), nullable=True)  # committee member who processed it
    rack_loan_id = db.Column(db.Integer, db.ForeignKey("rack_loans.id"), nullable=True)
    # When it's due back, and (for loans collected from an online request)
    # who to send receipts and reminders to.
    due_date = db.Column(db.Date, nullable=True)
    borrower_email = db.Column(db.String(200), nullable=True)
    # The exact serial-numbered piece handed over, when one was recorded.
    gear_unit_id = db.Column(db.Integer, db.ForeignKey("gear_units.id"), nullable=True)
    gear_unit = db.relationship("GearUnit")
    signed_out_at = db.Column(db.DateTime, default=utcnow)
    signed_in_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_out(self):
        return self.signed_in_at is None

    @property
    def is_overdue(self):
        return self.is_out and self.due_date is not None and self.due_date < date.today()


class EmailLog(db.Model):
    """One reminder-type email sent about one loan (or a digest, with no
    loan). Lets the hourly reminder run send each email exactly once."""

    __tablename__ = "email_log"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)  # receipt / day_before / overdue / digest / unclaimed
    signout_id = db.Column(db.Integer, db.ForeignKey("sign_outs.id"), nullable=True)
    request_group = db.Column(db.String(32), nullable=True)
    recipient = db.Column(db.String(200), nullable=False)
    # "sent", or "rejected" when Resend refused the recipient address - logged
    # either way so a bad address isn't retried on every hourly run.
    status = db.Column(db.String(10), nullable=True, default="sent")
    sent_on = db.Column(db.Date, nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow)


class ItemRequest(db.Model):
    """A request from a regular (non-committee) member to take kit out."""

    __tablename__ = "item_requests"

    id = db.Column(db.Integer, primary_key=True)
    requester_name = db.Column(db.String(120), nullable=False)
    requester_email = db.Column(db.String(200), nullable=True)
    item_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    needed_from = db.Column(db.Date, nullable=True)
    needed_to = db.Column(db.Date, nullable=True)
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending / approved / denied / cancelled
    # Set when committee accept: how many they'll actually hand over (may be
    # fewer than asked for), who took it on and where/when to collect.
    approved_quantity = db.Column(db.Integer, nullable=True)
    claimed_by = db.Column(db.String(120), nullable=True)
    handover_note = db.Column(db.Text, nullable=True)
    deny_reason = db.Column(db.Text, nullable=True)
    # Unguessable key for the member's status page, shared by every line in
    # one request_group.
    status_token = db.Column(db.String(64), nullable=True, index=True, default=lambda: secrets.token_urlsafe(24))
    # Set when this line came from the basket as a group request (e.g. member
    # asked for "Nuts" rather than a specific size). item_name is still the
    # group's display name either way, so existing name-based display code
    # doesn't need to change - this just tells the fulfilment step it needs to
    # ask the committee which specific KitItem to actually hand over.
    group_id = db.Column(db.Integer, db.ForeignKey("item_groups.id"), nullable=True)
    # Shared by every line item submitted together from one basket checkout, so
    # the admin page can display and resolve them as a single request.
    request_group = db.Column(db.String(32), nullable=True, default=lambda: uuid.uuid4().hex)
    # Which club session the member will collect/return the kit at (e.g. "Taf
    # Monday"), or "Other" - set once per checkout and copied onto every line
    # in that request_group, same as message/requester_name. The *_other
    # columns hold the free-text description when the member picked "Other"
    # instead of one of the fixed sessions.
    pickup_slot = db.Column(db.String(50), nullable=True)
    pickup_other = db.Column(db.String(200), nullable=True)
    return_slot = db.Column(db.String(50), nullable=True)
    return_other = db.Column(db.String(200), nullable=True)
    # Set once an approved request has actually been handed over and turned into
    # a real SignOut record, so the admin page can tell "approved" apart from
    # "approved and collected" instead of the two systems drifting out of sync.
    fulfilled_signout_id = db.Column(db.Integer, db.ForeignKey("sign_outs.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    fulfilled_signout = db.relationship("SignOut")

    @property
    def pickup_display(self):
        return self.pickup_other if self.pickup_slot == "Other" else self.pickup_slot

    @property
    def return_display(self):
        return self.return_other if self.return_slot == "Other" else self.return_slot

    @property
    def handover_quantity(self):
        return self.approved_quantity if self.approved_quantity is not None else self.quantity

    @property
    def matching_item(self):
        """The catalog KitItem this request refers to, if any (custom/'other'
        requests typed in free text on the basket page won't match one).
        Matches case-insensitively (so 'climbing helmet' still finds 'Climbing
        Helmet') and only against active items, so an archived item can never
        be silently signed out via this shortcut."""
        name = (self.item_name or "").strip().lower()
        return KitItem.query.filter(
            func.lower(KitItem.name) == name, KitItem.active.is_(True), KitItem.rack_id.is_(None)
        ).first()

    @property
    def matching_group(self):
        """The ItemGroup this request refers to, if it was made against a
        group rather than a specific item (see group_id above)."""
        return db.session.get(ItemGroup, self.group_id) if self.group_id else None
