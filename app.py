import os
import secrets
import uuid
from datetime import date, datetime
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.schema import CreateColumn

from config import Config
from models import Category, ItemGroup, ItemRequest, KitBundle, KitBundleLine, KitItem, SignOut, Subcategory, db

# Basic brute-force throttle on the shared committee password: after this many
# wrong guesses in a row, further attempts are blocked for a short cooldown.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 30


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)
    db.init_app(app)

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


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_committee"):
            flash("Please log in as a committee member to view that page.", "error")
            return redirect(url_for("login", next=request.path))
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


def build_kit_entries(items, groups):
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
        }
        for item in items
    ]
    entries.sort(key=lambda entry: entry["name"].lower())
    return entries


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
        item = KitItem.query.get(int(item_id_str))
        if item and item.active:
            lines.append(
                {"key": f"item:{item_id_str}", "name": item.name, "quantity": quantity, "group_id": None}
            )
    for group_id_str, quantity in basket.get("groups", {}).items():
        group = ItemGroup.query.get(int(group_id_str))
        if group and group.active_items:
            lines.append(
                {"key": f"group:{group_id_str}", "name": group.name, "quantity": quantity, "group_id": group.id}
            )
    for idx, other in enumerate(basket.get("other", [])):
        lines.append(
            {"key": f"other:{idx}", "name": other["name"], "quantity": other["quantity"], "group_id": None}
        )
    return lines


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
                "request_group": r.request_group,
                "requester_name": r.requester_name,
                "requester_email": r.requester_email,
                "message": r.message,
                "created_at": r.created_at,
                "lines": [],  # named "lines" not "items" - dicts already have a built-in .items()
            }
            order.append(key)
        groups[key]["lines"].append(r)
    return [groups[key] for key in order]


def register_routes(app):
    @app.context_processor
    def inject_basket_count():
        return {"basket_count": basket_line_count(get_basket())}

    # ---------------------------------------------------------------- public
    @app.route("/")
    def home():
        return render_template("home.html")

    # Some browsers/crawlers request /favicon.ico at the site root regardless
    # of the <link rel="icon"> tag in base.html - without this route that
    # request 404s even though the real icon file exists under /static/img.
    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            os.path.join(app.static_folder, "img"), "favicon.ico", mimetype="image/vnd.microsoft.icon"
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
                if item.active and item.group_id is None
            )
            group_count = sum(
                1 for sub in category.subcategories
                for group in ItemGroup.query.filter_by(subcategory_id=sub.id)
                if group.active_items
            )
            category_cards.append({"category": category, "item_count": item_count, "group_count": group_count})
        return render_template("request.html", category_cards=category_cards, bundles=bundles)

    @app.route("/request/category/<int:category_id>")
    def request_category(category_id):
        category = Category.query.get_or_404(category_id)
        # Also block direct URL access to a hidden category, not just its
        # card on the browse page - otherwise "hidden" would only mean
        # "not linked to", which isn't what the committee is asking for.
        if not category.member_visible:
            abort(404)
        # Items that belong to a group (e.g. individual nut sizes) are folded
        # into that group's single row instead of listed on their own here -
        # a member requests "Nuts", not a specific size.
        items = (
            KitItem.query.filter_by(active=True, group_id=None)
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
                KitItem.query.filter(KitItem.active.is_(True), KitItem.group_id.is_(None))
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
        kit_entries = build_kit_entries(items, groups)
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
            ok = True
            item = KitItem.query.get_or_404(item_id)
            key = str(item.id)
            basket["items"][key] = basket["items"].get(key, 0) + quantity
            message = f"Added {quantity} x {item.name} to your request list."
        elif group_id:
            ok = True
            group = ItemGroup.query.get_or_404(group_id)
            key = str(group.id)
            basket["groups"][key] = basket["groups"].get(key, 0) + quantity
            message = f"Added {quantity} x {group.name} to your request list."
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
        bundle = KitBundle.query.get_or_404(bundle_id)
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

            if not requester_name:
                flash("Please enter your name.", "error")
            elif not line_items:
                flash("Your request list is empty.", "error")
            else:
                checkout_group_id = uuid.uuid4().hex
                for line in line_items:
                    db.session.add(
                        ItemRequest(
                            requester_name=requester_name,
                            requester_email=requester_email,
                            item_name=line["name"],
                            quantity=line["quantity"],
                            message=message,
                            request_group=checkout_group_id,
                            group_id=line["group_id"],
                        )
                    )
                db.session.commit()
                session["basket"] = {"items": {}, "groups": {}, "other": []}
                session.modified = True
                flash("Your request has been sent to the committee. We'll be in touch!", "success")
                return redirect(url_for("request_kit"))

        return render_template("basket.html", line_items=line_items)

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
                idx = int(key.split(":", 1)[1])
                if 0 <= idx < len(basket["other"]):
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
            idx = int(key.split(":", 1)[1])
            if 0 <= idx < len(basket["other"]):
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
            locked_until = session.get("login_locked_until")
            now = datetime.utcnow().timestamp()
            if locked_until and now < locked_until:
                flash(
                    f"Too many incorrect attempts. Try again in {int(locked_until - now)} second(s).",
                    "error",
                )
                return render_template("login.html")

            password = request.form.get("password", "")
            if secrets.compare_digest(password, app.config["COMMITTEE_PASSWORD"]):
                session["is_committee"] = True
                session.pop("login_attempts", None)
                session.pop("login_locked_until", None)
                flash("Logged in.", "success")
                next_url = request.args.get("next") or url_for("sign_in_out")
                return redirect(next_url)

            attempts = session.get("login_attempts", 0) + 1
            if attempts >= LOGIN_MAX_ATTEMPTS:
                session["login_attempts"] = 0
                session["login_locked_until"] = now + LOGIN_LOCKOUT_SECONDS
                flash(f"Too many incorrect attempts. Try again in {LOGIN_LOCKOUT_SECONDS} seconds.", "error")
            else:
                session["login_attempts"] = attempts
                flash("Incorrect password.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.pop("is_committee", None)
        flash("Logged out.", "success")
        return redirect(url_for("home"))

    # ------------------------------------------------------- committee: kit
    @app.route("/sign-in-out")
    @login_required
    def sign_in_out():
        categories = Category.query.order_by(Category.name).all()
        subcategories = Subcategory.query.order_by(Subcategory.name).all()
        items = KitItem.query.filter_by(active=True).order_by(KitItem.name).all()
        active_sign_outs = (
            SignOut.query.filter_by(signed_in_at=None).order_by(SignOut.signed_out_at.desc()).all()
        )
        # Every sign-out ever recorded, both still-out and already-returned, so
        # the committee has a full audit trail to track activity over time -
        # separate from active_sign_outs above, which only covers unreturned loans.
        sign_out_history = SignOut.query.order_by(SignOut.signed_out_at.desc()).all()
        # Previously-used borrower names, so committee can autocomplete instead
        # of retyping (and to cut down on near-duplicate spellings of the same person).
        borrower_names = [
            row[0]
            for row in db.session.query(SignOut.borrower_name).distinct().order_by(SignOut.borrower_name).all()
        ]
        return render_template(
            "sign_in_out.html",
            categories=categories,
            subcategories=subcategories,
            items=items,
            active_sign_outs=active_sign_outs,
            sign_out_history=sign_out_history,
            borrower_names=borrower_names,
        )

    @app.route("/sign-out", methods=["POST"])
    @login_required
    def sign_out_item():
        item = KitItem.query.get_or_404(request.form.get("item_id"))
        quantity = parse_positive_int(request.form.get("quantity"))
        borrower_name = request.form.get("borrower_name", "").strip()

        if not borrower_name:
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
            item.quantity_available -= quantity
            signout = SignOut(
                item_id=item.id,
                borrower_name=borrower_name,
                quantity=quantity,
                notes=request.form.get("notes", "").strip(),
                checked_out_by=session.get("committee_name", "Committee"),
            )
            db.session.add(signout)
            db.session.commit()
            flash(f"Signed out {quantity} x {item.name} to {borrower_name}.", "success")

        return redirect(url_for("sign_in_out"))

    @app.route("/sign-in/<int:signout_id>", methods=["POST"])
    @login_required
    def sign_in_item(signout_id):
        signout = SignOut.query.get_or_404(signout_id)
        if signout.signed_in_at is None:
            signout.signed_in_at = datetime.utcnow()
            signout.item.quantity_available += signout.quantity
            db.session.commit()
            flash(f"Signed in {signout.quantity} x {signout.item.name} from {signout.borrower_name}.", "success")
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
        }
        return render_template(
            "admin.html",
            pending_groups=group_pending_requests(pending_requests),
            counts=counts,
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
        elif Category.query.filter(func.lower(Category.name) == name.lower()).first():
            flash(f"Category '{name}' already exists.", "error")
        else:
            db.session.add(Category(name=name))
            db.session.commit()
            flash(f"Added category '{name}'.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/toggle-visibility", methods=["POST"])
    @login_required
    def toggle_category_visibility(category_id):
        category = Category.query.get_or_404(category_id)
        category.member_visible = not category.member_visible
        db.session.commit()
        state = "visible to members" if category.member_visible else "hidden from members"
        flash(f"'{category.name}' is now {state}.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
    @login_required
    def delete_category(category_id):
        category = Category.query.get_or_404(category_id)
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
        category_id = request.form.get("category_id")
        category = Category.query.get(category_id) if category_id else None

        if not name or not category:
            flash("Subcategory name and category are required.", "error")
        elif Subcategory.query.filter(
            func.lower(Subcategory.name) == name.lower(), Subcategory.category_id == category.id
        ).first():
            flash(f"'{name}' already exists under '{category.name}'.", "error")
        else:
            db.session.add(Subcategory(name=name, category_id=category.id))
            db.session.commit()
            flash(f"Added subcategory '{name}' under '{category.name}'.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/subcategories/<int:subcategory_id>/delete", methods=["POST"])
    @login_required
    def delete_subcategory(subcategory_id):
        subcategory = Subcategory.query.get_or_404(subcategory_id)
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
        category = Category.query.get_or_404(category_id)
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
            subcategory_id = request.form.get("subcategory_id")
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
            if KitItem.query.filter(func.lower(KitItem.name) == name.lower()).first():
                flash(
                    f"An item named '{name}' already exists — choose a different name, "
                    "or edit the existing one instead of adding a duplicate.",
                    "error",
                )
                return redirect(error_redirect)

            group_id = request.form.get("group_id")
            item = KitItem(
                name=name,
                subcategory_id=int(subcategory_id),
                group_id=int(group_id) if group_id else None,
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
        item = KitItem.query.get_or_404(item_id)

        if request.method == "POST":
            new_name = request.form.get("name", item.name).strip()
            new_total = parse_positive_int(request.form.get("quantity_total"))

            if new_total is None:
                flash("Quantity must be a whole number of at least 1.", "error")
                return redirect(url_for("edit_item", item_id=item.id))

            # A reduced total can never go below what's currently out on loan -
            # otherwise "available" would go negative-in-spirit now and could
            # exceed "total" later once loans are returned, corrupting the count.
            on_loan = item.quantity_total - item.quantity_available
            if new_total < on_loan:
                flash(
                    f"Can't set '{item.name}''s total to {new_total} — {on_loan} unit(s) are currently "
                    "signed out. Wait for them to be returned, or set the total to at least that many.",
                    "error",
                )
                return redirect(url_for("edit_item", item_id=item.id))

            if new_name and new_name.lower() != item.name.lower():
                clash = KitItem.query.filter(
                    func.lower(KitItem.name) == new_name.lower(), KitItem.id != item.id
                ).first()
                if clash:
                    flash(f"An item named '{new_name}' already exists.", "error")
                    return redirect(url_for("edit_item", item_id=item.id))

            item.name = new_name or item.name
            subcategory_id = request.form.get("subcategory_id")
            if subcategory_id:
                item.subcategory_id = int(subcategory_id)
            group_id = request.form.get("group_id")
            item.group_id = int(group_id) if group_id else None
            item.location = request.form.get("location", item.location).strip()
            item.description = request.form.get("description", item.description or "").strip()
            item.quantity_total = new_total
            item.quantity_available = new_total - on_loan
            item.purchase_date = parse_date(request.form.get("purchase_date"))

            db.session.commit()
            flash(f"Updated '{item.name}'.", "success")
            return redirect(url_for("admin_inventory_category", category_id=item.subcategory.category_id))

        categories = Category.query.order_by(Category.name).all()
        item_groups = ItemGroup.query.order_by(ItemGroup.name).all()
        return render_template(
            "admin_item_form.html",
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
        item = KitItem.query.get_or_404(item_id)
        category_id = item.subcategory.category_id  # captured before any mutation below
        if any(so.is_out for so in item.sign_outs):
            flash(f"Cannot delete '{item.name}' while units are signed out.", "error")
        elif item.sign_outs:
            # It has loan history (just none active right now) - the database
            # won't allow a hard delete here (old SignOut rows still point at
            # this item), so archive it instead: same effect for anyone
            # browsing the catalogue, but the history stays intact and it can
            # be brought back later if that was a mistake.
            item.active = False
            db.session.commit()
            flash(
                f"'{item.name}' has loan history, so it's been archived rather than deleted "
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
        item = KitItem.query.get_or_404(item_id)
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
        subcategory_id = request.form.get("subcategory_id")
        description = request.form.get("description", "").strip()

        if not name:
            flash("Group name is required.", "error")
        elif not subcategory_id:
            flash("Please choose a subcategory for the group.", "error")
        elif ItemGroup.query.filter(func.lower(ItemGroup.name) == name.lower()).first():
            flash(f"A group named '{name}' already exists.", "error")
        else:
            db.session.add(ItemGroup(name=name, subcategory_id=int(subcategory_id), description=description))
            db.session.commit()
            flash(f"Created group '{name}' — assign kit items to it from Add/Edit Kit Item.", "success")
        return redirect(url_for("admin_groups"))

    @app.route("/admin/groups/<int:group_id>/delete", methods=["POST"])
    @login_required
    def delete_item_group(group_id):
        group = ItemGroup.query.get_or_404(group_id)
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
        items = KitItem.query.filter_by(active=True).order_by(KitItem.name).all()
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
        elif KitBundle.query.filter(func.lower(KitBundle.name) == name.lower()).first():
            flash(f"A bundle named '{name}' already exists.", "error")
        else:
            db.session.add(KitBundle(name=name, description=description))
            db.session.commit()
            flash(f"Created bundle '{name}' — add items to it below.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/edit", methods=["POST"])
    @login_required
    def edit_bundle(bundle_id):
        bundle = KitBundle.query.get_or_404(bundle_id)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not name:
            flash("Bundle name is required.", "error")
        elif KitBundle.query.filter(
            func.lower(KitBundle.name) == name.lower(), KitBundle.id != bundle.id
        ).first():
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
        bundle = KitBundle.query.get_or_404(bundle_id)
        name = bundle.name
        db.session.delete(bundle)  # cascade also removes its lines - the items themselves are untouched
        db.session.commit()
        flash(f"Deleted bundle '{name}'. The items themselves are still in the kit store.", "success")
        return redirect(url_for("admin_bundles"))

    @app.route("/admin/bundles/<int:bundle_id>/lines/add", methods=["POST"])
    @login_required
    def add_bundle_line(bundle_id):
        bundle = KitBundle.query.get_or_404(bundle_id)
        quantity = parse_positive_int(request.form.get("quantity"))
        # The picker is a single <select> covering both items and groups -
        # see admin_bundles.html - so its value is "item:<id>" or
        # "group:<id>" and we work out which table to look in from that
        # prefix, rather than needing two separate pickers.
        kind, _, raw_id = request.form.get("entry_id", "").partition(":")
        item = KitItem.query.get(int(raw_id)) if kind == "item" and raw_id.isdigit() else None
        group = ItemGroup.query.get(int(raw_id)) if kind == "group" and raw_id.isdigit() else None

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

    @app.route("/admin/requests/<int:request_id>/<action>", methods=["POST"])
    @login_required
    def resolve_request(request_id, action):
        if action not in ("approve", "deny"):
            flash("Unknown action.", "error")
            return redirect(url_for("admin"))

        item_request = ItemRequest.query.get_or_404(request_id)
        item_request.status = "approved" if action == "approve" else "denied"
        item_request.resolved_at = datetime.utcnow()
        db.session.commit()
        flash(f"Request from {item_request.requester_name} {item_request.status}.", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/requests/group/<request_group>/<action>", methods=["POST"])
    @login_required
    def resolve_request_group(request_group, action):
        if action not in ("approve", "deny"):
            flash("Unknown action.", "error")
            return redirect(url_for("admin"))

        items = ItemRequest.query.filter_by(request_group=request_group, status="pending").all()
        status = "approved" if action == "approve" else "denied"
        for item_request in items:
            item_request.status = status
            item_request.resolved_at = datetime.utcnow()
        db.session.commit()
        if items:
            flash(f"{len(items)} item(s) from {items[0].requester_name}'s request {status}.", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/requests/<int:request_id>/collect", methods=["POST"])
    @login_required
    def collect_request(request_id):
        """Turn an approved request into a real sign-out once the member has
        actually picked the kit up, so the two systems stay in sync instead of
        relying on the committee to remember to do this manually."""
        item_request = ItemRequest.query.get_or_404(request_id)

        if item_request.status != "approved":
            flash("Only approved requests can be marked as collected.", "error")
            return redirect(url_for("admin_resolved"))
        if item_request.fulfilled_signout_id:
            flash("This request has already been marked as collected.", "error")
            return redirect(url_for("admin_resolved"))

        if item_request.group_id:
            # A group request (e.g. "Nuts") doesn't name one specific KitItem,
            # so the committee has to say which size/variant is actually
            # being handed over - the form on the admin page supplies that as
            # item_id, chosen from the group's own members only (never
            # trust a stray item_id from outside the group).
            item_id = request.form.get("item_id")
            item = KitItem.query.get(item_id) if item_id else None
            if not item or item.group_id != item_request.group_id:
                flash(
                    f"Choose which specific item to hand over for '{item_request.item_name}'.",
                    "error",
                )
                return redirect(url_for("admin_resolved"))
        else:
            item = item_request.matching_item
            if not item:
                flash(
                    f"No catalog item matches '{item_request.item_name}' — sign it out manually from Sign In/Out instead.",
                    "error",
                )
                return redirect(url_for("admin_resolved"))

        if item_request.quantity > item.quantity_available:
            flash(
                f"Only {item.quantity_available} of '{item.name}' available right now — "
                "adjust the loan or sign it out manually.",
                "error",
            )
            return redirect(url_for("admin_resolved"))

        item.quantity_available -= item_request.quantity
        signout = SignOut(
            item_id=item.id,
            borrower_name=item_request.requester_name,
            quantity=item_request.quantity,
            notes=item_request.message,
            checked_out_by=session.get("committee_name", "Committee"),
        )
        db.session.add(signout)
        db.session.flush()  # populate signout.id before we link it below
        item_request.fulfilled_signout_id = signout.id
        db.session.commit()
        flash(
            f"Signed out {item_request.quantity} x {item.name} to {item_request.requester_name} "
            "and marked the request as collected.",
            "success",
        )
        return redirect(url_for("admin_resolved"))


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
