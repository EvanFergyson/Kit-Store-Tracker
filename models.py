from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class KitItem(db.Model):
    """A piece of kit owned by the club (e.g. a tent, a rope, a helmet)."""

    __tablename__ = "kit_items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False, default="General")
    description = db.Column(db.Text, nullable=True)
    condition = db.Column(db.String(40), nullable=False, default="Good")
    location = db.Column(db.String(120), nullable=True)
    quantity_total = db.Column(db.Integer, nullable=False, default=1)
    quantity_available = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sign_outs = db.relationship("SignOut", backref="item", lazy=True)

    @property
    def quantity_out(self):
        return self.quantity_total - self.quantity_available


class SignOut(db.Model):
    """A record of kit being signed out and (eventually) signed back in."""

    __tablename__ = "sign_outs"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("kit_items.id"), nullable=False)
    borrower_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text, nullable=True)
    checked_out_by = db.Column(db.String(120), nullable=True)  # committee member who processed it
    signed_out_at = db.Column(db.DateTime, default=datetime.utcnow)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
