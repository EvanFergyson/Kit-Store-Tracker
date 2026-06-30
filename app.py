import os
import secrets
from datetime import datetime
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for

from config import Config
from models import ItemRequest, KitItem, SignOut, db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)
    db.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    return app


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_committee"):
            flash("Please log in as a committee member to view that page.", "error")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def register_routes(app):
    # ---------------------------------------------------------------- public
    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/request", methods=["GET", "POST"])
    def request_kit():
        items = KitItem.query.order_by(KitItem.name).all()
        if request.method == "POST":
            req = ItemRequest(
                requester_name=request.form.get("requester_name", "").strip(),
                requester_email=request.form.get("requester_email", "").strip(),
                item_name=request.form.get("item_name", "").strip(),
                quantity=int(request.form.get("quantity") or 1),
                message=request.form.get("message", "").strip(),
            )
            if not req.requester_name or not req.item_name:
                flash("Please fill in your name and the item you need.", "error")
                return render_template("request.html", items=items)

            db.session.add(req)
            db.session.commit()
            flash("Your request has been sent to the committee. We'll be in touch!", "success")
            return redirect(url_for("request_kit"))

        return render_template("request.html", items=items)

    # ------------------------------------------------------------------ auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if secrets.compare_digest(password, app.config["COMMITTEE_PASSWORD"]):
                session["is_committee"] = True
                flash("Logged in.", "success")
                next_url = request.args.get("next") or url_for("sign_in_out")
                return redirect(next_url)
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
        items = KitItem.query.order_by(KitItem.name).all()
        active_sign_outs = (
            SignOut.query.filter_by(signed_in_at=None).order_by(SignOut.signed_out_at.desc()).all()
        )
        return render_template("sign_in_out.html", items=items, active_sign_outs=active_sign_outs)

    @app.route("/sign-out", methods=["POST"])
    @login_required
    def sign_out_item():
        item = KitItem.query.get_or_404(request.form.get("item_id"))
        quantity = int(request.form.get("quantity") or 1)
        borrower_name = request.form.get("borrower_name", "").strip()

        if not borrower_name:
            flash("Borrower name is required.", "error")
        elif quantity < 1 or quantity > item.quantity_available:
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
    @app.route("/admin")
    @login_required
    def admin():
        items = KitItem.query.order_by(KitItem.name).all()
        pending_requests = (
            ItemRequest.query.filter_by(status="pending").order_by(ItemRequest.created_at.desc()).all()
        )
        resolved_requests = (
            ItemRequest.query.filter(ItemRequest.status != "pending")
            .order_by(ItemRequest.resolved_at.desc())
            .limit(20)
            .all()
        )
        return render_template(
            "admin.html", items=items, pending_requests=pending_requests, resolved_requests=resolved_requests
        )

    @app.route("/admin/items/add", methods=["POST"])
    @login_required
    def add_item():
        quantity_total = int(request.form.get("quantity_total") or 1)
        item = KitItem(
            name=request.form.get("name", "").strip(),
            category=request.form.get("category", "General").strip() or "General",
            description=request.form.get("description", "").strip(),
            condition=request.form.get("condition", "Good").strip() or "Good",
            location=request.form.get("location", "").strip(),
            quantity_total=quantity_total,
            quantity_available=quantity_total,
        )
        if not item.name:
            flash("Item name is required.", "error")
        else:
            db.session.add(item)
            db.session.commit()
            flash(f"Added '{item.name}' to the kit store.", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/items/<int:item_id>/edit", methods=["POST"])
    @login_required
    def edit_item(item_id):
        item = KitItem.query.get_or_404(item_id)
        new_total = int(request.form.get("quantity_total") or item.quantity_total)
        # Keep quantity_available in sync when the total changes
        diff = new_total - item.quantity_total
        item.name = request.form.get("name", item.name).strip()
        item.category = request.form.get("category", item.category).strip()
        item.description = request.form.get("description", item.description)
        item.condition = request.form.get("condition", item.condition).strip()
        item.location = request.form.get("location", item.location).strip()
        item.quantity_total = new_total
        item.quantity_available = max(0, item.quantity_available + diff)
        db.session.commit()
        flash(f"Updated '{item.name}'.", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/items/<int:item_id>/delete", methods=["POST"])
    @login_required
    def delete_item(item_id):
        item = KitItem.query.get_or_404(item_id)
        if any(so.is_out for so in item.sign_outs):
            flash(f"Cannot delete '{item.name}' while units are signed out.", "error")
        else:
            db.session.delete(item)
            db.session.commit()
            flash(f"Deleted '{item.name}'.", "success")
        return redirect(url_for("admin"))

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


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
