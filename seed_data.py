"""Populate the database with CUMC's real kit inventory, taken from the
club's "Kit Store Tracker.xlsx" spreadsheet (6 sheets: Soft Items, Bouldering,
Hardwear, Personal Safety, Other, Cage).

The spreadsheet itself is messy in places - some quantities are written as
words ("Lots", "Loads", "two bags") instead of numbers, a few cells have
typos or placeholder borrower names ("?", "Unaccounted for"), and one cell is
a broken Excel formula ("#VALUE!"). Rather than silently guessing, anywhere a
quantity couldn't be read as a real number this script records a best-effort
default and leaves a note in the item's description explaining exactly what
the spreadsheet said, so a committee member can correct it from the admin
page once they know the real number.

Run with:  python seed_data.py
"""
from datetime import datetime

from app import create_app
from models import Category, KitItem, SignOut, Subcategory, db

# category name -> list of subcategory names, in the order the club's
# spreadsheet lists them.
CATALOG = {
    "Soft Items": [
        "Single rope",
        "Static rope",
        "Half",
        "Prussiks",
        "Daisy chain",
        "Slings",
    ],
    "Bouldering": [
        "Bouldering Pads",
    ],
    "Hardwear": [
        "Trad Quickdraws",
        "Alpine Quickdraws",
        "Sport Quickdraws",
        "Nuts",
        "Hexes",
        "Cams",
        "Mallions",
        "Nut Keys",
        "Ohm",
        "Assisted Breaking",
        "ATC",
        "Descenders",
        "Locking carabiners",
    ],
    "Personal Safety": [
        "Harness",
        "Helmets",
    ],
    "Other": [
        "Health and Safety",
        "Books",
        "Misc Climbing",
        "Misc",
    ],
    "Cage": [
        "Sleeping Bag",
        "Sleeping Mat",
        "Table",
        "Camping",
        "Tent",
        "Other",
    ],
}

# (item name, category name, subcategory name, quantity_total, quantity_available, description)
KIT_ITEMS = [
    ("Beal Antidote 10.2, 47m, Blue", "Soft Items", "Single rope", 1, 1, None),
    ("70m, Brown/murky", "Soft Items", "Single rope", 1, 1, None),
    ("Another Brown 70m", "Soft Items", "Single rope", 1, 1, None),
    ("Mammut, 50m, Green        Chopped", "Soft Items", "Single rope", 1, 1, None),
    ("Beal 10, one of them is 42m, Yellow", "Soft Items", "Single rope", 2, 2, None),
    ("Mammut 60m, Orange", "Soft Items", "Single rope", 1, 1, None),
    ("Edelweiss, 80m, Blue (very soft)", "Soft Items", "Single rope", 1, 1, None),
    ("60m, white and black", "Soft Items", "Static rope", 1, 1, None),
    ("30m, white and black", "Soft Items", "Static rope", 1, 0, None),
    ("30m, white and black (Static rope)", "Soft Items", "Static rope", 1, 0, None),
    ("15m, white and black", "Soft Items", "Static rope", 1, 0, None),
    ("Pair of Beal Cobra II Unicore 8.6, 50m, Red+Green", "Soft Items", "Half", 5, 3, None),
    ("Beal Cobra II Unicore 8.6, 50m, Green", "Soft Items", "Half", 3, 3, None),
    ("Blue Prussiks, all on screwgates", "Soft Items", "Prussiks", 4, 2, None),
    ("White and Blue, all on screwgates", "Soft Items", "Daisy chain", 3, 2, None),
    ("60cm, 2 grey, 1 orange, all on screwgates", "Soft Items", "Slings", 3, 2, None),
    ("120cm, grey, red, purple, all on screwgates", "Soft Items", "Slings", 7, 6, None),
    ("240cm, green, blue, purple, all on screwgates", "Soft Items", "Slings", 5, 4, None),
    ("400cm, blue, all on screwgates", "Soft Items", "Slings", 2, 0, None),
    ("Petzl, orange and black", "Bouldering", "Bouldering Pads", 1, 1, None),
    ("Alpkit, red and blue", "Bouldering", "Bouldering Pads", 2, 2, None),
    ("Ocun, orange and black", "Bouldering", "Bouldering Pads", 1, 1, None),
    ("Dominator", "Bouldering", "Bouldering Pads", 1, 1, None),
    ("Black Diamond, Orange Blue", "Bouldering", "Bouldering Pads", 4, 2, None),
    ("Incubator", "Bouldering", "Bouldering Pads", 1, 0, None),
    ("Pink Psychi", "Bouldering", "Bouldering Pads", 1, 1, None),
    ("18cm, silver slings", "Hardwear", "Trad Quickdraws", 20, 18, None),
    ("22cm, red slings", "Hardwear", "Trad Quickdraws", 1, 1, None),
    ("cm, green slings", "Hardwear", "Trad Quickdraws", 12, 6, None),
    ("cm, blue slings", "Hardwear", "Trad Quickdraws", 12, 0, None),
    ("18cm, Orange/yellow sling", "Hardwear", "Alpine Quickdraws", 10, 7, None),
    ("DMM Alphas, yellow", "Hardwear", "Sport Quickdraws", 6, 5, None),
    ("Petzl Djinn, two lengths, orange", "Hardwear", "Sport Quickdraws", 11, 0, None),
    ("Clog, blue", "Hardwear", "Sport Quickdraws", 1, 0, None),
    ("DMM Shadow, purple", "Hardwear", "Sport Quickdraws", 9, 4, None),
    ("Complete 1-11 set of nuts on 2 snapgates", "Hardwear", "Nuts", 10, 3, None),
    ("Set of offsets, 7-11", "Hardwear", "Nuts", 4, 2, None),
    ("Microwire", "Hardwear", "Nuts", 0, 0, None),
    ("DMM set of 4, yellow, red, blue, green", "Hardwear", "Hexes", 7, 3, None),
    ("5,  Big purp, (1 Black Diamond)", "Hardwear", "Cams", 1, 0, None),
    ("4, Silver, (1 Black Diamond)", "Hardwear", "Cams", 1, 0, None),
    ("3, Blue, (3 Black Diamond, 2 DMM)", "Hardwear", "Cams", 5, 3, None),
    ("2, Gold, (3 Black Diamond, 2 DMM)", "Hardwear", "Cams", 5, 3, None),
    ("1, Red, (4 Black Diamond)", "Hardwear", "Cams", 4, 2, None),
    (".75, Green, (4 Black Diamond, 1 DMM)", "Hardwear", "Cams", 5, 2, None),
    (".5, Small purp, (2 Black Diamond, 2 Wild Country)", "Hardwear", "Cams", 4, 2, None),
    (".4,  Silver, (2 Black Diamond, 3 DMM)", "Hardwear", "Cams", 5, 3, None),
    (".3, Blue, (Black Diamond)", "Hardwear", "Cams", 2, 1, None),
    (".1, Red, (Black Diamond)", "Hardwear", "Cams", 1, 0, None),
    ("All attatched to a screwgate", "Hardwear", "Mallions", 4, 1, None),
    ("Various types of nut key, on snapgate", "Hardwear", "Nut Keys", 3, 2, None),
    ("Elderid, 1st edition", "Hardwear", "Ohm", 1, 1, None),
    ("Petzl Gri GrI 2, on screwgate", "Hardwear", "Assisted Breaking", 1, 1, None),
    ("Mammut smart, on screwgate", "Hardwear", "Assisted Breaking", 1, 0, None),
    ("Various models, all on screwgates", "Hardwear", "ATC", 10, 5, None),
    ("Figure 8 descender", "Hardwear", "Descenders", 2, 0, None),
    ("Loose carabiners", "Hardwear", "Locking carabiners", 9, 1, None),
    ("DMM Brennin and DMM Centre", "Personal Safety", "Harness", 20, 2, None),
    ("Blue helmets", "Personal Safety", "Helmets", 8, 2, None),
    ("Light Blue Helmerts", "Personal Safety", "Helmets", 4, 2, None),
    ("Red helmets", "Personal Safety", "Helmets", 10, 8, None),
    ("Green Helmets", "Personal Safety", "Helmets", 5, 2, None),
    ("Green first aid  kits", "Other", "Health and Safety", 5, 5, "Check if in date"),
    ("Red first aid kits", "Other", "Health and Safety", 3, 3, None),
    ("Brown bag of first aid gear", "Other", "Health and Safety", 1, 1, None),
    ("Storm shelters, all fit minimum 4 people", "Other", "Health and Safety", 5, 5, None),
    ("Guidebooks for Uk, france, spain, etc", "Other", "Books", 62, 57, None),
    ("OS maps", "Other", "Books", 27, 27, None),
    ("Climbing shoes", "Other", "Misc Climbing", 4, 0, "spreadsheet quantity was 'Lots'"),
    ("Clip Sticks", "Other", "Misc Climbing", 2, 2, None),
    ("Rope tarps and bags", "Other", "Misc Climbing", 5, 5, None),
    ("Red and yellow haul bags", "Other", "Misc Climbing", 2, 2, None),
    ("Green rope protector", "Other", "Misc Climbing", 1, 1, None),
    ("Carpet", "Other", "Misc Climbing", 1, 1, None),
    ("Walkie Talkies", "Other", "Misc Climbing", 11, 11, None),
    ("Ice Axes", "Other", "Misc Climbing", 1, 1, "spreadsheet quantity was 'Lots'"),
    ("Yellow crampons in brown bag", "Other", "Misc Climbing", 1, 1, "spreadsheet quantity was 'Lots'"),
    ("Table", "Other", "Misc", 1, 1, None),
    ("pair of crutches", "Other", "Misc", 2, 2, None),
    ("litter pickers", "Other", "Misc", 6, 6, None),
    ("Compasses", "Other", "Misc", 1, 1, "quantity wasn't recorded in the spreadsheet"),
    ("Rope marker", "Other", "Misc", 1, 1, None),
    ("Electrical Tape", "Other", "Misc", 1, 1, "quantity wasn't recorded in the spreadsheet"),
    ("Vango Nitestar 250", "Cage", "Sleeping Bag", 1, 1, None),
    ("Forclaz MT 500", "Cage", "Sleeping Bag", 1, 1, None),
    ("Blue sleeping bag in blue bag", "Cage", "Sleeping Bag", 1, 1, None),
    ("Green sleeping bag in blue dry bag", "Cage", "Sleeping Bag", 1, 1, None),
    ("Self Inflating mat blue", "Cage", "Sleeping Mat", 1, 1, None),
    ("Peak Oyl Aero MTN: Long", "Cage", "Sleeping Mat", 1, 1, None),
    ("Blue/Green Foam Mats", "Cage", "Sleeping Mat", 3, 3, None),
    ("White Folding Table", "Cage", "Table", 3, 3, None),
    ("Metal Food Contatiner/Bowl", "Cage", "Camping", 1, 1, None),
    ("Camping Stove, blue", "Cage", "Camping", 6, 6, None),
    ("White Kettle", "Cage", "Camping", 1, 1, None),
    ("Washing up bowls", "Cage", "Camping", 7, 7, None),
    ("Metal Tongs", "Cage", "Camping", 1, 1, None),
    ("Plastic Spatula", "Cage", "Camping", 1, 1, None),
    ("Little Saucepan", "Cage", "Camping", 3, 3, None),
    ("Big Saucepan", "Cage", "Camping", 5, 5, None),
    ("Mobile Mains Kit", "Cage", "Camping", 1, 1, None),
    ("Bowl", "Cage", "Camping", 1, 1, None),
    ("Big Pan", "Cage", "Camping", 3, 3, None),
    ("Fire Starter Cubes Pack", "Cage", "Camping", 2, 2, None),
    ("Camping Chairs", "Cage", "Camping", 4, 4, None),
    ("Grey Tarp in Green Bag", "Cage", "Camping", 1, 1, None),
    ("Fuel cans", "Cage", "Camping", 8, 8, None),
    ("Gelert Quickpitch camo pop up", "Cage", "Tent", 1, 1, None),
    ("Horizon 8 Inner Tent", "Cage", "Tent", 1, 1, None),
    ("Arpenaz 2+ tent", "Cage", "Tent", 1, 1, None),
    ("Horizon 8 Outer Tent", "Cage", "Tent", 1, 1, None),
    ("Vango Beta 250", "Cage", "Tent", 2, 2, None),
    ("Gelert Horizion Porch, Blue", "Cage", "Tent", 1, 1, None),
    ("Black Bag of Cricket Balls", "Cage", "Other", 1, 1, None),
    ("Old CUMC SU Banner", "Cage", "Other", 1, 1, None),
    ("Orange Frisbee", "Cage", "Other", 1, 1, None),
    ("Hi-Vis", "Cage", "Other", 10, 10, None),
    ("AA Emergancy Warning Triangle", "Cage", "Other", 5, 5, None),
    ("UK Car Sticker", "Cage", "Other", 7, 7, None),
    ("Ultrasport Slack Line, red, black bag", "Cage", "Other", 1, 1, None),
    ("Barefoot Slackline, yellow", "Cage", "Other", 1, 1, None),
    ("Single Use Breathalyzers", "Cage", "Other", 4, 4, None),
    ("Old CUMC Flag", "Cage", "Other", 1, 1, None),
    ("Random tent pegs", "Cage", "Other", 1, 1, "spreadsheet quantity was 'Loads'"),
    ("Random tent poles", "Cage", "Other", 1, 1, "spreadsheet quantity was 'two bags'"),
    ("Zip ties", "Cage", "Other", 1, 1, "spreadsheet quantity was 'loads'"),
    ("Red washing up tub", "Cage", "Other", 2, 2, None),
    ("Bags", "Cage", "Other", 4, 4, None),
    ("Spare guy lines", "Cage", "Other", 1, 1, None),
]

# Real current loans from the spreadsheet's "Who has it" / "Amount taken out"
# columns, seeded as SignOut rows so the sign-out history/currently-out lists
# start out reflecting where the club's kit actually is. Placeholder borrower
# names from the spreadsheet ("?", "??", "Unkown") have been normalised to
# "Unknown"; genuinely unclear group loans ("Unaccounted for") are kept as-is
# since that's real information about the club's kit tracking gaps.
# (item name, borrower name, quantity, signed_out_at date "YYYY-MM-DD" or None for "now")
SIGN_OUTS = [
    # ("30m, white and black", "Unknown", 1, None),
    # ("30m, white and black (Static rope)", "Unknown", 1, None),
    # ("15m, white and black", "Unknown", 1, None),
    # ("Pair of Beal Cobra II Unicore 8.6, 50m, Red+Green", "Unknown", 2, None),
    # ("Blue Prussiks, all on screwgates", "Unknown", 2, None),
    # ("White and Blue, all on screwgates", "Unknown", 1, None),
    # ("60cm, 2 grey, 1 orange, all on screwgates", "Will G", 1, None),
    # ("120cm, grey, red, purple, all on screwgates", "Unknown", 1, None),
    # ("240cm, green, blue, purple, all on screwgates", "Unknown", 1, None),
    # ("400cm, blue, all on screwgates", "Unknown", 2, None),
    # ("Black Diamond, Orange Blue", "Lucas", 1, None),
    # ("Black Diamond, Orange Blue", "Issac", 1, None),
    # ("Incubator", "Unknown", 1, None),
    # ("18cm, silver slings", "Unknown", 2, None),
    # ("cm, green slings", "Unknown", 6, None),
    # ("18cm, Orange/yellow sling", "Unknown, Rob(2 draws)", 3, None),
    # ("DMM Alphas, yellow", "Unknown", 1, None),
    # ("Petzl Djinn, two lengths, orange", "Chris/jos", 7, None),
    # ("Petzl Djinn, two lengths, orange", "Unknown", 4, None),
    # ("Clog, blue", "Unknown", 1, None),
    # ("DMM Shadow, purple", "Unknown", 5, None),
    # ("Complete 1-11 set of nuts on 2 snapgates", "Unaccounted for", 7, None),
    # ("Set of offsets, 7-11", "Unknown", 2, None),
    # ("DMM set of 4, yellow, red, blue, green", "Unaccounted for", 4, None),
    # ("5,  Big purp, (1 Black Diamond)", "James??", 1, None),
    # ("4, Silver, (1 Black Diamond)", "Unknown", 1, None),
    # ("3, Blue, (3 Black Diamond, 2 DMM)", "Rob", 1, None),
    # ("3, Blue, (3 Black Diamond, 2 DMM)", "Unknown", 1, None),
    # ("2, Gold, (3 Black Diamond, 2 DMM)", "Rob", 1, None),
    # ("2, Gold, (3 Black Diamond, 2 DMM)", "Unknown", 1, None),
    # ("1, Red, (4 Black Diamond)", "Rob", 1, None),
    # ("1, Red, (4 Black Diamond)", "Unknown", 1, None),
    # (".75, Green, (4 Black Diamond, 1 DMM)", "Unknown", 2, None),
    # (".75, Green, (4 Black Diamond, 1 DMM)", "Rob", 1, None),
    # (".75, Green, (4 Black Diamond, 1 DMM)", "Unknown", 1, None),
    # (".5, Small purp, (2 Black Diamond, 2 Wild Country)", "Rob", 1, None),
    # (".4,  Silver, (2 Black Diamond, 3 DMM)", "Rob", 1, None),
    # (".4,  Silver, (2 Black Diamond, 3 DMM)", "Unknown", 1, None),
    # (".3, Blue, (Black Diamond)", "Rob", 1, None),
    # (".1, Red, (Black Diamond)", "Unknown", 1, None),
    # ("All attatched to a screwgate", "Unknown", 3, None),
    # ("Various types of nut key, on snapgate", "Unknown", 1, None),
    # ("Various models, all on screwgates", "Unaccounted for", 5, None),
    # ("Figure 8 descender", "Unknown", 2, None),
    # ("Loose carabiners", "Will G", 1, None),
    # ("Loose carabiners", "Antoni", 6, None),
    # ("Loose carabiners", "Ben", 1, None),
    # ("DMM Brennin and DMM Centre", "Will G", 1, None),
    # ("DMM Brennin and DMM Centre", "Molly", 1, None),
    # ("DMM Brennin and DMM Centre", "Caitlin", 1, None),
    # ("DMM Brennin and DMM Centre", "Unaccounted for", 15, None),
    # ("Blue helmets", "Caitlin", 1, None),
    # ("Blue helmets", "Will G", 1, None),
    # ("Blue helmets", "Arran", 1, "2026-05-26"),
    # ("Blue helmets", "Unaccounted for", 3, None),
    # ("Light Blue Helmerts", "Unaccounted for", 2, None),
    # ("Red helmets", "Unaccounted for", 2, None),
    # ("Green Helmets", "Unaccounted for", 3, None),
    # ("Guidebooks for Uk, france, spain, etc", "Juno: Wye Valley? Rose: north wales bouldering Freya: Gower Rock & Pembrookshire", 5, None),
    # ("Climbing shoes", "Caitlin", 4, None),
]


def run():
    app = create_app()
    with app.app_context():
        categories_added = 0
        subcategories_added = 0
        for category_name, subcategory_names in CATALOG.items():
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(name=category_name)
                db.session.add(category)
                db.session.flush()  # get category.id before creating subcategories
                categories_added += 1

            for subcategory_name in subcategory_names:
                exists = Subcategory.query.filter_by(name=subcategory_name, category_id=category.id).first()
                if not exists:
                    db.session.add(Subcategory(name=subcategory_name, category_id=category.id))
                    subcategories_added += 1
        db.session.commit()

        items_added = 0
        for name, category_name, subcategory_name, quantity_total, quantity_available, description in KIT_ITEMS:
            if KitItem.query.filter_by(name=name).first():
                continue
            subcategory = Subcategory.query.join(Category).filter(
                Subcategory.name == subcategory_name, Category.name == category_name
            ).first()
            if not subcategory:
                print(f"  skipping '{name}' - no subcategory '{subcategory_name}' under '{category_name}'")
                continue
            db.session.add(
                KitItem(
                    name=name,
                    subcategory_id=subcategory.id,
                    quantity_total=quantity_total,
                    quantity_available=quantity_available,
                    description=description,
                )
            )
            items_added += 1
        db.session.commit()

        sign_outs_added = 0
        for item_name, borrower_name, quantity, date_str in SIGN_OUTS:
            item = KitItem.query.filter_by(name=item_name).first()
            if not item:
                continue
            # Skip if this exact loan already looks seeded (keeps the script
            # safe to re-run without piling up duplicate sign-outs).
            already = SignOut.query.filter_by(
                item_id=item.id, borrower_name=borrower_name, quantity=quantity
            ).first()
            if already:
                continue
            signed_out_at = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
            db.session.add(
                SignOut(
                    item_id=item.id,
                    borrower_name=borrower_name,
                    quantity=quantity,
                    checked_out_by="Imported from spreadsheet",
                    signed_out_at=signed_out_at,
                )
            )
            sign_outs_added += 1
        db.session.commit()

        print(
            f"Seeded {categories_added} categories, {subcategories_added} subcategories, "
            f"{items_added} kit items and {sign_outs_added} sign-out records "
            f"(skipping anything that already exists)."
        )


if __name__ == "__main__":
    run()
