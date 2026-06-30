"""Optional: populate the database with a few sample kit items for testing.

Run with:  python seed_data.py
"""
from app import create_app
from models import KitItem, db

SAMPLE_ITEMS = [
    {"name": "50m Dynamic Rope", "category": "Climbing", "quantity_total": 4, "location": "Shelf A"},
    {"name": "Climbing Helmet", "category": "Climbing", "quantity_total": 10, "location": "Bin 2"},
    {"name": "4-Person Tent", "category": "Camping", "quantity_total": 3, "location": "Storage Room"},
    {"name": "Trangia Stove", "category": "Camping", "quantity_total": 6, "location": "Storage Room"},
    {"name": "Walking Poles (Pair)", "category": "Hiking", "quantity_total": 8, "location": "Shelf B"},
]

app = create_app()

with app.app_context():
    for data in SAMPLE_ITEMS:
        if not KitItem.query.filter_by(name=data["name"]).first():
            item = KitItem(quantity_available=data["quantity_total"], **data)
            db.session.add(item)
    db.session.commit()
    print(f"Seeded {len(SAMPLE_ITEMS)} sample items (skipping any that already exist).")
