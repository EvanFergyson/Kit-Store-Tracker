from datetime import datetime, timezone

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
        return [item for item in self.items if item.active]

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

    @property
    def quantity_out(self):
        return self.quantity_total - self.quantity_available

    @property
    def category(self):
        return self.subcategory.category if self.subcategory else None


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
        return bool(self.group.active_items) if self.group_id else self.item.active


class SignOut(db.Model):
    """A record of kit being signed out and (eventually) signed back in."""

    __tablename__ = "sign_outs"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=False)
    borrower_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text, nullable=True)
    checked_out_by = db.Column(db.String(120), nullable=True)  # committee member who processed it
    signed_out_at = db.Column(db.DateTime, default=utcnow)
    signed_in_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_out(self):
        return self.signed_in_at is None


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
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending / approved / denied
    # Set when this line came from the basket as a group request (e.g. member
    # asked for "Nuts" rather than a specific size). item_name is still the
    # group's display name either way, so existing name-based display code
    # doesn't need to change - this just tells the fulfilment step it needs to
    # ask the committee which specific KitItem to actually hand over.
    group_id = db.Column(db.Integer, db.ForeignKey("item_groups.id"), nullable=True)
    # Shared by every line item submitted together from one basket checkout, so
    # the admin page can display and resolve them as a single request.
    request_group = db.Column(db.String(32), nullable=True)
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
    def matching_item(self):
        """The catalog KitItem this request refers to, if any (custom/'other'
        requests typed in free text on the basket page won't match one).
        Matches case-insensitively (so 'climbing helmet' still finds 'Climbing
        Helmet') and only against active items, so an archived item can never
        be silently signed out via this shortcut."""
        name = (self.item_name or "").strip().lower()
        return KitItem.query.filter(
            func.lower(KitItem.name) == name, KitItem.active.is_(True)
        ).first()

    @property
    def matching_group(self):
        """The ItemGroup this request refers to, if it was made against a
        group rather than a specific item (see group_id above)."""
        return db.session.get(ItemGroup, self.group_id) if self.group_id else None
