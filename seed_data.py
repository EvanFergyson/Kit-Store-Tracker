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
from models import Category, ItemGroup, KitBundle, KitBundleLine, KitItem, SignOut, Subcategory, db, utcnow

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
        "Maillons",
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
    # Its own top-level category (not just an "Other" subcategory) so it gets
    # its own card on Kit Inventory and Request Kit, per club committee
    # request - each region below is a subcategory, each guidebook title its
    # own KitItem (never grouped: unlike e.g. Sport Quickdraws, one guidebook
    # isn't an interchangeable substitute for another). "Books" under "Other"
    # still exists separately and still holds OS maps - deliberately not
    # reused here to avoid two same-named things confusing the admin UI.
    "Guidebooks": [
        "Wales",
        "Peaks and Lakes",
        "South and West",
        "Wye Valley",
        "Yorkshire",
        "Scotland",
        "Spain",
        "France",
    ],
}

# Category names in here start out hidden from the member-facing Request Kit
# pages (Category.member_visible = False) - same setting as the "Hide from
# members" toggle on the admin Categories page, just set up front instead of
# clicked after the fact. This only applies the first time a category is
# created: re-running the script never touches member_visible on a category
# that already exists, so a committee member's later toggle (either
# direction) always wins over whatever's listed here.
HIDDEN_CATEGORIES = {"Cage"}

# Every item has a short, stable `key` (first element of each tuple) used to
# reference it from ITEM_GROUPS/BUNDLES below, instead of repeating its full
# name string. Names/descriptions can be freely edited without breaking any
# group or bundle, since they're wired up by key, not by matching text -
# rename an item and every group/bundle it belongs to still finds it.
# (key, name, category name, subcategory name, quantity_total, quantity_available, description)
KIT_ITEMS = [
    ("rope_single_beal_47m", "Beal Antidote 10.2, 47m, Blue", "Soft Items", "Single rope", 1, 1, None),
    ("rope_single_70m_brown_a", "70m, Brown/murky", "Soft Items", "Single rope", 1, 1, None),
    ("rope_single_70m_brown_b", "Another Brown 70m", "Soft Items", "Single rope", 1, 1, None),
    ("rope_single_mammut_50m_chopped", "Mammut, 50m, Green Chopped", "Soft Items", "Single rope", 1, 1, None),
    ("rope_single_beal10_mixed_yellow", "Beal 10, one of them is 42m, Yellow", "Soft Items", "Single rope", 2, 2, None),
    ("rope_single_mammut_60m_orange", "Mammut 60m, Orange", "Soft Items", "Single rope", 1, 1, None),
    ("rope_single_edelweiss_80m", "Edelweiss, 80m, Blue (very soft)", "Soft Items", "Single rope", 1, 1, None),
    ("rope_static_60m", "60m, white and black", "Soft Items", "Static rope", 1, 1, None),
    ("rope_static_30m_a", "30m, white and black", "Soft Items", "Static rope", 1, 0, None),
    ("rope_static_30m_b", "30m, white and black (Static rope)", "Soft Items", "Static rope", 1, 0, None),
    ("rope_static_15m", "15m, white and black", "Soft Items", "Static rope", 1, 0, None),
    ("rope_half_beal_pair_50m", "Pair of Beal Cobra II Unicore 8.6, 50m, Red+Green", "Soft Items", "Half", 5, 3, None),
    ("rope_half_beal_single_50m", "Beal Cobra II Unicore 8.6, 50m, Green", "Soft Items", "Half", 3, 3, None),
    ("prussiks_blue", "Blue Prussiks, all on screwgates", "Soft Items", "Prussiks", 4, 2, None),
    ("daisy_chain_white_blue", "White and Blue, all on screwgates", "Soft Items", "Daisy chain", 3, 2, None),
    ("sling_60cm", "60cm", "Soft Items", "Slings", 3, 2, "2 grey, 1 orange, all on screwgates"),
    ("sling_120cm", "120cm", "Soft Items", "Slings", 7, 6, "grey, red, purple, all on screwgates"),
    ("sling_240cm", "240cm", "Soft Items", "Slings", 5, 4, "green, blue, purple, all on screwgates"),
    ("sling_400cm", "400cm", "Soft Items", "Slings", 2, 0, "blue, all on screwgates"),
    ("pad_petzl", "Petzl", "Bouldering", "Bouldering Pads", 1, 1, "orange and black"),
    ("pad_alpkit", "Alpkit", "Bouldering", "Bouldering Pads", 2, 2, "red and blue"),
    ("pad_ocun", "Ocun", "Bouldering", "Bouldering Pads", 1, 1, "orange and black"),
    ("pad_ocun_dominator", "Ocun Dominator", "Bouldering", "Bouldering Pads", 1, 1, None),
    ("pad_ocun_incubator", "Ocun Incubator", "Bouldering", "Bouldering Pads", 1, 0, None),
    ("pad_black_diamond", "Black Diamond", "Bouldering", "Bouldering Pads", 4, 2, "Orange Blue"),
    ("pad_psychi", "Psychi", "Bouldering", "Bouldering Pads", 1, 1, "Pink"),
    ("qd_trad_18cm_silver", "18cm, silver slings", "Hardwear", "Trad Quickdraws", 20, 18, None),
    ("qd_trad_22cm_red", "22cm, red slings", "Hardwear", "Trad Quickdraws", 1, 1, None),
    ("qd_trad_18cm_green", "18 cm", "Hardwear", "Trad Quickdraws", 12, 6, "green slings"),
    ("qd_trad_18cm_blue", "18 cm", "Hardwear", "Trad Quickdraws", 12, 0, "blue slings"),
    ("qd_alpine_18cm", "18cm", "Hardwear", "Alpine Quickdraws", 10, 7, "Orange/yellow sling"),
    ("qd_sport_dmm_alpha", "DMM Alphas", "Hardwear", "Sport Quickdraws", 6, 5, "yellow"),
    ("qd_sport_petzl_djinn", "Petzl Djinn", "Hardwear", "Sport Quickdraws", 11, 0, "two lengths, orange"),
    ("qd_sport_clog", "Clog", "Hardwear", "Sport Quickdraws", 1, 0, "blue"),
    ("qd_sport_dmm_shadow", "DMM Shadow", "Hardwear", "Sport Quickdraws", 9, 4, "purple"),
    ("nuts_complete_set", "Wall Nuts 1-11 ", "Hardwear", "Nuts", 10, 3, "Complete 1-11 set of nuts on 2 snapgates"),
    ("nuts_offset_set", "Offset Nuts 7-11", "Hardwear", "Nuts", 4, 2, "Complete set of offsets, 7-11, on 1 bent snapgate"),
    # ("nuts_microwire", "Microwire", "Hardwear", "Nuts", 0, 0, None),
    ("hexes_dmm_set4", "Torque Nuts 1-4", "Hardwear", "Hexes", 7, 3, "DMM set of 4, yellow, red, blue, green"),
    ("cam_5", "5 - Big purp", "Hardwear", "Cams", 1, 0, "Black Diamond: 1"),
    ("cam_4", "4 - Silver", "Hardwear", "Cams", 1, 0, "Black Diamond: 1"),
    ("cam_3", "3 - Blue", "Hardwear", "Cams", 5, 3, "Black Diamond: 3, DMM: 2"),
    ("cam_2", "2 - Gold", "Hardwear", "Cams", 5, 3, "Black Diamond: 3, DMM: 2"),
    ("cam_1", "1 - Red", "Hardwear", "Cams", 4, 2, "Black Diamond: 4"),
    ("cam_075", ".75 - Green", "Hardwear", "Cams", 5, 2, "Black Diamond: 4, DMM: 1"),
    ("cam_05", ".5 - Purple", "Hardwear", "Cams", 4, 2, "Black Diamond: 2, Wild Country: 2"),
    ("cam_04", ".4 - Silver", "Hardwear", "Cams", 5, 3, "Black Diamond: 2, DMM: 3"),
    ("cam_03", ".3 - Blue", "Hardwear", "Cams", 2, 1, "Black Diamond: 1"),
    ("cam_01", ".1 - Red", "Hardwear", "Cams", 1, 0, "Black Diamond: 1"),
    ("maillons", "Maillons", "Hardwear", "Maillons", 4, 1, "All attatched to a screwgate"),
    ("nut_keys", "Nut Keys", "Hardwear", "Nut Keys", 3, 2, "Various types of nut key, on snapgate"),
    ("ohm_elderid", "Elderid Ohm", "Hardwear", "Ohm", 1, 1, "1st edition"),
    ("abd_petzl_grigri", "Petzl Gri GrI 2", "Hardwear", "Assisted Breaking", 1, 1, "On screwgate"),
    ("abd_mammut_smart", "Mammut smart", "Hardwear", "Assisted Breaking", 1, 0, "On screwgate"),
    ("atc", "ATC", "Hardwear", "ATC", 10, 5, "Various models, all on screwgates"),
    ("descender_figure8", "Figure 8 descender", "Hardwear", "Descenders", 2, 0, None),
    ("locking_carabiners", "Lockers", "Hardwear", "Locking carabiners", 9, 1, "Loose carabiners"),
    ("harness_dmm", "DMM Brennin and DMM Centre", "Personal Safety", "Harness", 20, 2, None),
    ("helmet_blue", "Blue helmets", "Personal Safety", "Helmets", 8, 2, None),
    ("helmet_light_blue", "Light Blue Helmets", "Personal Safety", "Helmets", 4, 2, None),
    ("helmet_red", "Red Helmets", "Personal Safety", "Helmets", 10, 8, None),
    ("helmet_green", "Green Helmets", "Personal Safety", "Helmets", 5, 2, None),
    ("first_aid_green", "Green first aid  kits", "Other", "Health and Safety", 5, 5, "Check if in date"),
    ("first_aid_red", "Red first aid kits", "Other", "Health and Safety", 3, 3, None),
    ("first_aid_brown_bag", "Brown bag of first aid gear", "Other", "Health and Safety", 1, 1, None),
    ("storm_shelters", "Storm shelters, all fit minimum 4 people", "Other", "Health and Safety", 5, 5, None),
    # Replaced by the real itemised list under "Guidebooks" below - this was
    # a placeholder count from before anyone had actually gone through the
    # shelf and listed real titles.
    ("os_maps", "OS maps", "Other", "Books", 27, 27, None),
    ("climbing_shoes", "Climbing shoes", "Other", "Misc Climbing", 4, 0, "spreadsheet quantity was 'Lots'"),
    ("clip_sticks", "Clip Sticks", "Other", "Misc Climbing", 2, 2, None),
    ("rope_tarps_bags", "Rope tarps and bags", "Other", "Misc Climbing", 5, 5, None),
    ("haul_bags", "Red and yellow haul bags", "Other", "Misc Climbing", 2, 2, None),
    ("rope_protector", "Green rope protector", "Other", "Misc Climbing", 1, 1, None),
    ("carpet", "Carpet", "Other", "Misc Climbing", 1, 1, None),
    ("walkie_talkies", "Walkie Talkies", "Other", "Misc Climbing", 11, 11, None),
    ("ice_axes", "Ice Axes", "Other", "Misc Climbing", 1, 1, "spreadsheet quantity was 'Lots'"),
    ("crampons", "Yellow crampons in brown bag", "Other", "Misc Climbing", 1, 1, "spreadsheet quantity was 'Lots'"),
    ("misc_table", "Table", "Other", "Misc", 1, 1, None),
    ("crutches", "pair of crutches", "Other", "Misc", 2, 2, None),
    ("litter_pickers", "litter pickers", "Other", "Misc", 6, 6, None),
    ("compasses", "Compasses", "Other", "Misc", 1, 1, "quantity wasn't recorded in the spreadsheet"),
    ("rope_marker", "Rope marker", "Other", "Misc", 1, 1, None),
    ("electrical_tape", "Electrical Tape", "Other", "Misc", 1, 1, "quantity wasn't recorded in the spreadsheet"),
    ("sleeping_bag_vango", "Vango Nitestar 250", "Cage", "Sleeping Bag", 1, 1, None),
    ("sleeping_bag_forclaz", "Forclaz MT 500", "Cage", "Sleeping Bag", 1, 1, None),
    ("sleeping_bag_blue", "Blue sleeping bag in blue bag", "Cage", "Sleeping Bag", 1, 1, None),
    ("sleeping_bag_green", "Green sleeping bag in blue dry bag", "Cage", "Sleeping Bag", 1, 1, None),
    ("sleeping_mat_self_inflating", "Self Inflating mat blue", "Cage", "Sleeping Mat", 1, 1, None),
    ("sleeping_mat_peak_oyl", "Peak Oyl Aero MTN: Long", "Cage", "Sleeping Mat", 1, 1, None),
    ("sleeping_mat_foam", "Blue/Green Foam Mats", "Cage", "Sleeping Mat", 3, 3, None),
    ("cage_table", "White Folding Table", "Cage", "Table", 3, 3, None),
    ("camp_food_container", "Metal Food Contatiner/Bowl", "Cage", "Camping", 1, 1, None),
    ("camp_stove", "Camping Stove, blue", "Cage", "Camping", 6, 6, None),
    ("camp_kettle", "White Kettle", "Cage", "Camping", 1, 1, None),
    ("camp_washing_bowls", "Washing up bowls", "Cage", "Camping", 7, 7, None),
    ("camp_tongs", "Metal Tongs", "Cage", "Camping", 1, 1, None),
    ("camp_spatula", "Plastic Spatula", "Cage", "Camping", 1, 1, None),
    ("camp_saucepan_small", "Little Saucepan", "Cage", "Camping", 3, 3, None),
    ("camp_saucepan_big", "Big Saucepan", "Cage", "Camping", 5, 5, None),
    ("camp_mains_kit", "Mobile Mains Kit", "Cage", "Camping", 1, 1, None),
    ("camp_bowl", "Bowl", "Cage", "Camping", 1, 1, None),
    ("camp_pan_big", "Big Pan", "Cage", "Camping", 3, 3, None),
    ("camp_fire_starters", "Fire Starter Cubes Pack", "Cage", "Camping", 2, 2, None),
    ("camp_chairs", "Camping Chairs", "Cage", "Camping", 4, 4, None),
    ("camp_tarp", "Grey Tarp in Green Bag", "Cage", "Camping", 1, 1, None),
    ("camp_fuel_cans", "Fuel cans", "Cage", "Camping", 8, 8, None),
    ("tent_gelert_quickpitch", "Gelert Quickpitch camo pop up", "Cage", "Tent", 1, 1, None),
    ("tent_horizon8_inner", "Horizon 8 Inner Tent", "Cage", "Tent", 1, 1, None),
    ("tent_arpenaz2", "Arpenaz 2+ tent", "Cage", "Tent", 1, 1, None),
    ("tent_horizon8_outer", "Horizon 8 Outer Tent", "Cage", "Tent", 1, 1, None),
    ("tent_vango_beta250", "Vango Beta 250", "Cage", "Tent", 2, 2, None),
    ("tent_gelert_porch", "Gelert Horizion Porch, Blue", "Cage", "Tent", 1, 1, None),
    ("other_cricket_balls", "Black Bag of Cricket Balls", "Cage", "Other", 1, 1, None),
    ("other_su_banner", "Old CUMC SU Banner", "Cage", "Other", 1, 1, None),
    ("other_frisbee", "Orange Frisbee", "Cage", "Other", 1, 1, None),
    ("other_hi_vis", "Hi-Vis", "Cage", "Other", 10, 10, None),
    ("other_warning_triangle", "AA Emergancy Warning Triangle", "Cage", "Other", 5, 5, None),
    ("other_car_sticker", "UK Car Sticker", "Cage", "Other", 7, 7, None),
    ("slackline_ultrasport", "Ultrasport Slack Line, red, black bag", "Cage", "Other", 1, 1, None),
    ("slackline_barefoot", "Barefoot Slackline, yellow", "Cage", "Other", 1, 1, None),
    ("other_breathalyzers", "Single Use Breathalyzers", "Cage", "Other", 4, 4, None),
    ("other_flag", "Old CUMC Flag", "Cage", "Other", 1, 1, None),
    ("other_tent_pegs", "Random tent pegs", "Cage", "Other", 1, 1, "spreadsheet quantity was 'Loads'"),
    ("other_tent_poles", "Random tent poles", "Cage", "Other", 1, 1, "spreadsheet quantity was 'two bags'"),
    ("other_zip_ties", "Zip ties", "Cage", "Other", 1, 1, "spreadsheet quantity was 'loads'"),
    ("other_washing_tub", "Red washing up tub", "Cage", "Other", 2, 2, None),
    ("other_bags", "Bags", "Cage", "Other", 4, 4, None),
    ("other_guy_lines", "Spare guy lines", "Cage", "Other", 1, 1, None),
    # Guidebooks, itemised from a physical count of the kit store shelf -
    # quantity_total/available both set to the count found (no evidence any
    # are currently signed out), description holds the publisher/author.
    ("book_south_wales_sport_2016", "South Wales Sport 2016", "Guidebooks", "Wales", 1, 1, "RockFax"),
    ("book_snowdonia_walks_scrambles_2020", "Snowdonia Mountain Walks and Scrambles 2020", "Guidebooks", "Wales", 1, 1, "RockFax"),
    ("book_gower_rock_2020", "Gower Rock 2nd edition 2020", "Guidebooks", "Wales", 1, 1, "Stuart Llewellyn & Matt Woodfield"),
    ("book_tremadog_2000", "Tremadog 2000", "Guidebooks", "Wales", 4, 4, "Climbers Club"),
    ("book_gower_se_wales_2003", "Gower & South East Wales 2003", "Guidebooks", "Wales", 1, 1, "South Wales Mountaineering Club"),
    ("book_snowdonia_rock_2005", "Rock Climbing In Snowdonia 2005", "Guidebooks", "Wales", 1, 1, "Francis Lincoln"),
    ("book_nwales_classics_2010", "North Wales Classics 2010", "Guidebooks", "Wales", 2, 2, "RockFax"),
    ("book_nwales_climbs_2013", "North Wales Climbs 2013", "Guidebooks", "Wales", 1, 1, "Ground Up"),
    ("book_nwales_climbs_2023", "North Wales Climbs 2023", "Guidebooks", "Wales", 1, 1, "RockFax"),
    ("book_nwales_rock_2009", "North Wales Rock 2009", "Guidebooks", "Wales", 1, 1, "RockFax"),
    ("book_nwales_bouldering_vol1_2021", "North Wales Bouldering Volume 1 2021", "Guidebooks", "Wales", 1, 1, "Ground Up"),

    ("book_peak_ne_2007", "Peak NE 2007", "Guidebooks", "Peaks and Lakes", 3, 3, "RockFax"),
    ("book_peak_rock_climbing_1996", "Rock Climbing in the Peak District 1996", "Guidebooks", "Peaks and Lakes", 1, 1, "Constable"),
    ("book_peak_district_climbing_2008", "Peak District Climbing 2008", "Guidebooks", "Peaks and Lakes", 1, 1, "Vertebrate"),
    ("book_roaches_2009", "The Roaches 2009", "Guidebooks", "Peaks and Lakes", 1, 1, "BMC"),
    ("book_peak_bouldering_2023", "Peak Bouldering 2023", "Guidebooks", "Peaks and Lakes", 1, 1, "RockFax"),
    ("book_roaches_2004", "The Roaches 2004", "Guidebooks", "Peaks and Lakes", 2, 2, "BMC"),
    ("book_stanage_2007", "Stanage 2007", "Guidebooks", "Peaks and Lakes", 2, 2, "BMC"),
    ("book_chatsworth_1996", "Chatsworth 1996", "Guidebooks", "Peaks and Lakes", 4, 4, "BMC"),
    ("book_lake_district_rock_2015", "Lake District Rock 2015", "Guidebooks", "Peaks and Lakes", 2, 2, "Wired Guidebooks"),

    ("book_west_country_climbs_2010", "West Country Climbs 2010", "Guidebooks", "South and West", 1, 1, "RockFax"),
    ("book_south_devon_dartmoor_1995", "South Devon and Dartmoor 1995", "Guidebooks", "South and West", 1, 1, "Cordee"),
    ("book_north_devon_cornwall_1988", "North Devon and Cornwall 1988", "Guidebooks", "South and West", 1, 1, "Climbers Club"),
    ("book_se_england_sandstone_2010", "Sandstone Climbing in South East England 2010", "Guidebooks", "South and West", 1, 1, "Jingo Wobbly"),
    ("book_southern_sandstone_bouldering_2010", "Southern Sandstone Bouldering 2010", "Guidebooks", "South and West", 1, 1, "SSB Guides"),
    ("book_south_devon_dartmoor_supplement_2007", "South Devon and Dartmoor Supplement 2007", "Guidebooks", "South and West", 1, 1, "Cordee"),

    ("book_lower_wye_valley_2007", "Lower Wye Valley 2007", "Guidebooks", "Wye Valley", 1, 1, "Climbers Club"),
    ("book_simmonds_yat_2010", "Simmonds Yat 2010", "Guidebooks", "Wye Valley", 1, 1, "Climbers Club"),
    ("book_simonside_hills_2008", "Simonside Hills 2008", "Guidebooks", "Wye Valley", 1, 1, "Northumbria Mountaineering Club & CLIMBER"),
    ("book_west_midlands_rock_1995", "West Midlands Rock 1995", "Guidebooks", "Wye Valley", 1, 1, "Cicerone"),
    ("book_forest_of_dean_sandstone_vol3_2006", "The Sandstone Outcrops of the Forest of Dean Volume 3 2006", "Guidebooks", "Wye Valley", 1, 1, "Climbers Club"),

    ("book_yorkshire_limestone_2005", "Yorkshire Limestone 2005", "Guidebooks", "Yorkshire", 1, 1, "Yorkshire Mountaineering Club"),
    ("book_yorkshire_peak_limestone_1991", "Yorkshire and the Peak District Limestone 1991", "Guidebooks", "Yorkshire", 1, 1, "Crowood"),

    ("book_bernina_bregaglia_1995", "Bernina and Bregaglia 1995", "Guidebooks", "Scotland", 2, 2, "Alpine Club Guide Books"),
    ("book_scottish_winter_climbs_1996", "Scottish Winter Climbs 1996", "Guidebooks", "Scotland", 1, 1, "The Scottish Mountaineering Club"),
    ("book_cairngorms_1995", "The Cairngorms 1995", "Guidebooks", "Scotland", 1, 1, "The Scottish Mountaineering Club"),
    ("book_highland_outcrops_1998", "Highland Outcrops 1998", "Guidebooks", "Scotland", 1, 1, "The Scottish Mountaineering Club"),
    ("book_ben_nevis_2002", "Ben Nevis 2002", "Guidebooks", "Scotland", 1, 1, "The Scottish Mountaineering Club"),
    ("book_winter_climbs_cairngorms_2011", "Winter Climbs In the Cairngorms 2011", "Guidebooks", "Scotland", 1, 1, "Cicerone"),

    ("book_pyrenees_rock_climbs_1990", "Rock Climbs in the Pyrenees 1990", "Guidebooks", "Spain", 1, 1, "Cicerone"),
    ("book_deportiva_valencia_2005", "Deportiva próxima a Valencia 2005", "Guidebooks", "Spain", 1, 1, "Escalada"),
    ("book_el_chorro_2018_rockfax", "Spain: El Chorro 2018", "Guidebooks", "Spain", 2, 2, "RockFax"),
    ("book_el_chorro_2013", "El Chorro 2013", "Guidebooks", "Spain", 1, 1, "Escalada en Malaga"),
    ("book_el_chorro_topo_2018", "El Chorro Topo 2018", "Guidebooks", "Spain", 1, 1, "Climbing Lodge"),

    ("book_font_fun_block_2012", "Fontainebleau Fun Block 2012", "Guidebooks", "France", 2, 2, "Jingo Wobbly"),
    ("book_font_magique_2005", "Fontainebleau Magique 2005", "Guidebooks", "France", 1, 1, "Jingo Wobbly"),
]

# Groups fold several specific variants of the same real item into one
# member-facing "product" (see ItemGroup in models.py) - a member requests
# "8 Sport Quickdraws", not "6 DMM Alphas and 2 Petzl Djinns", and the
# committee picks the exact ones to hand over when they approve it. Only
# applied where the variants are genuinely interchangeable for a member's
# purposes (brand/colour differences); deliberately NOT applied to Cams or
# Nuts, where the exact size actually matters for what the member can safely
# use it for. Trad Quickdraws *is* grouped (unlike the other two) since the
# length difference there is a rack-organisation preference rather than a
# safety-relevant choice - worth reconsidering if that's not the case.
# (group name, category name, subcategory name, [KitItem keys to fold in])
ITEM_GROUPS = [
    (
        "Sport Quickdraws",
        "Hardwear",
        "Sport Quickdraws",
        ["qd_sport_dmm_alpha", "qd_sport_petzl_djinn", "qd_sport_clog", "qd_sport_dmm_shadow"],
    ),
    (
        "Bouldering Pads",
        "Bouldering",
        "Bouldering Pads",
        [
            "pad_petzl",
            "pad_alpkit",
            "pad_ocun",
            "pad_ocun_dominator",
            "pad_black_diamond",
            "pad_ocun_incubator",
            "pad_psychi",
        ],
    ),
    (
        "Helmets",
        "Personal Safety",
        "Helmets",
        ["helmet_blue", "helmet_light_blue", "helmet_red", "helmet_green"],
    ),
    (
        "Assisted Braking Devices",
        "Hardwear",
        "Assisted Breaking",
        ["abd_petzl_grigri", "abd_mammut_smart"],
    ),
    (
        "First Aid Kits",
        "Other",
        "Health and Safety",
        ["first_aid_green", "first_aid_red", "first_aid_brown_bag"],
    ),
    (
        "Trad Quickdraws",
        "Hardwear",
        "Trad Quickdraws",
        ["qd_trad_18cm_silver", "qd_trad_22cm_red", "qd_trad_18cm_green", "qd_trad_18cm_blue"],
    ),
    # Sleeping bag/mat warmth and insulation type can matter for winter trips,
    # similar to why Cams/Nuts aren't grouped - flagged here so the committee
    # can split these back out via the admin page if that distinction turns
    # out to matter more than it saves in simplicity.
    (
        "Sleeping Bags",
        "Cage",
        "Sleeping Bag",
        ["sleeping_bag_vango", "sleeping_bag_forclaz", "sleeping_bag_blue", "sleeping_bag_green"],
    ),
    (
        "Sleeping Mats",
        "Cage",
        "Sleeping Mat",
        ["sleeping_mat_self_inflating", "sleeping_mat_peak_oyl", "sleeping_mat_foam"],
    ),
    (
        "Slacklines",
        "Cage",
        "Other",
        ["slackline_ultrasport", "slackline_barefoot"],
    ),
    # Every subcategory below has exactly one KitItem row in it - wrapping
    # each in a (single-item) group just gives it the same "product card"
    # presentation as everything else on the request page, rather than one
    # odd bare row sitting among a grid of group/item cards.
    ("Prussiks", "Soft Items", "Prussiks", ["prussiks_blue"]),
    ("Daisy Chain", "Soft Items", "Daisy chain", ["daisy_chain_white_blue"]),
    ("Alpine Quickdraws", "Hardwear", "Alpine Quickdraws", ["qd_alpine_18cm"]),
    ("Hexes", "Hardwear", "Hexes", ["hexes_dmm_set4"]),
    ("Maillons", "Hardwear", "Maillons", ["maillons"]),
    ("Nut Keys", "Hardwear", "Nut Keys", ["nut_keys"]),
    ("Ohm", "Hardwear", "Ohm", ["ohm_elderid"]),
    ("ATC", "Hardwear", "ATC", ["atc"]),
    ("Descenders", "Hardwear", "Descenders", ["descender_figure8"]),
    ("Locking Carabiners", "Hardwear", "Locking carabiners", ["locking_carabiners"]),
    ("Harness", "Personal Safety", "Harness", ["harness_dmm"]),
    ("Table", "Cage", "Table", ["cage_table"]),
    # Ropes: same principle as Cams - length is the safety-relevant spec a
    # member actually needs to choose correctly, brand/colour at the same
    # length isn't. Grouped by length within each rope type instead, so
    # "Single Rope - 70m" still means a member gets a 70m rope, just not
    # told/asked which specific one.
    ("Single Rope - 42m", "Soft Items", "Single rope", ["rope_single_beal10_mixed_yellow"]),
    ("Single Rope - 47m", "Soft Items", "Single rope", ["rope_single_beal_47m"]),
    ("Single Rope - 50m", "Soft Items", "Single rope", ["rope_single_mammut_50m_chopped"]),
    ("Single Rope - 60m", "Soft Items", "Single rope", ["rope_single_mammut_60m_orange"]),
    ("Single Rope - 70m", "Soft Items", "Single rope", ["rope_single_70m_brown_a", "rope_single_70m_brown_b"]),
    ("Single Rope - 80m", "Soft Items", "Single rope", ["rope_single_edelweiss_80m"]),
    ("Static Rope - 15m", "Soft Items", "Static rope", ["rope_static_15m"]),
    ("Static Rope - 30m", "Soft Items", "Static rope", ["rope_static_30m_a", "rope_static_30m_b"]),
    ("Static Rope - 60m", "Soft Items", "Static rope", ["rope_static_60m"]),
    ("Half Rope - 50m", "Soft Items", "Half", ["rope_half_beal_pair_50m", "rope_half_beal_single_50m"]),
]

# Trad rack "parts": unlike ITEM_GROUPS above (interchangeable variants of the
# *same* product, where any one satisfies the request), a real trad rack is
# built from several *different* items requested together - e.g. "a set of
# cams 0.4-3" means six distinct sizes handed over as one bundle, not any one
# of them. That's exactly what KitBundle already models (see admin_bundles /
# add_bundle_to_basket), so these are seeded as bundles, not groups. Cams
# themselves are still never folded into an ItemGroup - each size is still
# its own KitItem row with its own real stock count, since a 0.4 and a 3
# aren't interchangeable; a bundle just adds several specific sizes to the
# request list in one click.
# A line can reference a KitItem key as normal, or an ItemGroup by name using
# "group:<Group Name>" - e.g. "group:Trad Quickdraws" below adds a line for
# the whole Trad Quickdraws group rather than one specific length, matching
# how bundle lines can point at either an item or a group in the admin UI.
# (bundle name, description, [(KitItem key OR "group:<Group Name>", quantity), ...])
BUNDLES = [
    (
        "Trad Rack",
        "The core trad rack range most routes need: Cams, Nuts, Hexes, Quickdraws, Nut Keys",
        [
            ("cam_04", 1),
            ("cam_05", 1),
            ("cam_075", 1),
            ("cam_1", 1),
            ("cam_2", 1),
            ("cam_3", 1),
            ("nuts_complete_set", 1),
            ("hexes_dmm_set4", 1),
            ("group:Trad Quickdraws", 8),
            ("nut_keys", 2),
        ],
    ),
]

# Real current loans from the spreadsheet's "Who has it" / "Amount taken out"
# columns, seeded as SignOut rows so the sign-out history/currently-out lists
# start out reflecting where the club's kit actually is. Placeholder borrower
# names from the spreadsheet ("?", "??", "Unkown") have been normalised to
# "Unknown"; genuinely unclear group loans ("Unaccounted for") are kept as-is
# since that's real information about the club's kit tracking gaps.
# (KitItem key, borrower name, quantity, signed_out_at date "YYYY-MM-DD" or None for "now")
SIGN_OUTS = []


def run():
    app = create_app()
    with app.app_context():
        categories_added = 0
        subcategories_added = 0
        for category_name, subcategory_names in CATALOG.items():
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(
                    name=category_name, member_visible=category_name not in HIDDEN_CATEGORIES
                )
                db.session.add(category)
                db.session.flush()  # get category.id before creating subcategories
                categories_added += 1

            for subcategory_name in subcategory_names:
                exists = Subcategory.query.filter_by(name=subcategory_name, category_id=category.id).first()
                if not exists:
                    db.session.add(Subcategory(name=subcategory_name, category_id=category.id))
                    subcategories_added += 1
        db.session.commit()

        # Maps each KIT_ITEMS key to the real KitItem row it belongs to -
        # whether that row was just created below or already existed from a
        # previous run - so ITEM_GROUPS/BUNDLES can look items up by key
        # instead of by matching name text.
        items_by_key = {}
        items_added = 0
        for key, name, category_name, subcategory_name, quantity_total, quantity_available, description in KIT_ITEMS:
            item = KitItem.query.filter_by(name=name).first()
            if not item:
                subcategory = (
                    Subcategory.query.join(Category).filter(Subcategory.name == subcategory_name, Category.name == category_name).first()
                )
                if not subcategory:
                    print(f"  skipping '{name}' ({key}) - no subcategory '{subcategory_name}' under '{category_name}'")
                    continue
                item = KitItem(
                    name=name,
                    subcategory_id=subcategory.id,
                    quantity_total=quantity_total,
                    quantity_available=quantity_available,
                    description=description,
                )
                db.session.add(item)
                db.session.flush()  # get item.id before it's referenced by key below
                items_added += 1
            items_by_key[key] = item
        db.session.commit()

        sign_outs_added = 0
        for key, borrower_name, quantity, date_str in SIGN_OUTS:
            item = items_by_key.get(key)
            if not item:
                print(f"  skipping sign-out for '{key}' - no such kit item")
                continue
            # Skip if this exact loan already looks seeded (keeps the script
            # safe to re-run without piling up duplicate sign-outs).
            already = SignOut.query.filter_by(item_id=item.id, borrower_name=borrower_name, quantity=quantity).first()
            if already:
                continue
            signed_out_at = datetime.strptime(date_str, "%Y-%m-%d") if date_str else utcnow()
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

        groups_added = 0
        items_grouped = 0
        for group_name, category_name, subcategory_name, item_keys in ITEM_GROUPS:
            subcategory = (
                Subcategory.query.join(Category).filter(Subcategory.name == subcategory_name, Category.name == category_name).first()
            )
            if not subcategory:
                print(f"  skipping group '{group_name}' - no subcategory '{subcategory_name}' under '{category_name}'")
                continue

            group = ItemGroup.query.filter_by(name=group_name).first()
            if not group:
                group = ItemGroup(name=group_name, subcategory_id=subcategory.id)
                db.session.add(group)
                db.session.flush()  # get group.id before assigning items to it
                groups_added += 1

            for key in item_keys:
                item = items_by_key.get(key)
                if not item:
                    print(f"  skipping '{key}' in group '{group_name}' - no such kit item")
                    continue
                if item.group_id is None:
                    item.group_id = group.id
                    items_grouped += 1
        db.session.commit()

        bundles_added = 0
        bundle_lines_added = 0
        for bundle_name, description, lines in BUNDLES:
            bundle = KitBundle.query.filter_by(name=bundle_name).first()
            if not bundle:
                bundle = KitBundle(name=bundle_name, description=description)
                db.session.add(bundle)
                db.session.flush()  # get bundle.id before adding lines to it
                bundles_added += 1

            for key, quantity in lines:
                if key.startswith("group:"):
                    group_name = key[len("group:") :]
                    group = ItemGroup.query.filter_by(name=group_name).first()
                    if not group:
                        print(f"  skipping group '{group_name}' in bundle '{bundle_name}' - no such item group")
                        continue
                    already = KitBundleLine.query.filter_by(bundle_id=bundle.id, group_id=group.id).first()
                    if already:
                        continue
                    db.session.add(KitBundleLine(bundle_id=bundle.id, group_id=group.id, quantity=quantity))
                    bundle_lines_added += 1
                    continue

                item = items_by_key.get(key)
                if not item:
                    print(f"  skipping '{key}' in bundle '{bundle_name}' - no such kit item")
                    continue
                already = KitBundleLine.query.filter_by(bundle_id=bundle.id, item_id=item.id).first()
                if already:
                    continue
                db.session.add(KitBundleLine(bundle_id=bundle.id, item_id=item.id, quantity=quantity))
                bundle_lines_added += 1
        db.session.commit()

        print(
            f"Seeded {categories_added} categories, {subcategories_added} subcategories, "
            f"{items_added} kit items, {sign_outs_added} sign-out records, "
            f"{groups_added} item groups ({items_grouped} items folded into a group) and "
            f"{bundles_added} kit bundles ({bundle_lines_added} lines added) "
            f"(skipping anything that already exists)."
        )


if __name__ == "__main__":
    run()
