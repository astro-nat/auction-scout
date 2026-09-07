"""Ship-tier classification — every case here was a real production bug."""

from app.services.hibid import classify_logistics


def test_clothing_always_easy():
    assert classify_logistics("Leather Trench Coat Size L", "Clothing", "") == "EASY"
    assert classify_logistics("Lot of Mens Jeans", "", "") == "EASY"
    assert classify_logistics("Nike Pullover Hoodie", "", "") == "EASY"


def test_real_furniture_and_appliances_stay_hard():
    assert classify_logistics("Oak Dining Table and Chairs", "", "") == "HARD"
    assert classify_logistics("GE Refrigerator Stainless", "", "") == "HARD"
    assert classify_logistics("Round Wooden Pedestal Accent Table", "", "") == "HARD"
    assert classify_logistics("Sentry Safe Fireproof Box", "", "") == "HARD"


def test_accessory_phrases_are_not_furniture():
    # each of these false-positived in production before the lookarounds
    assert classify_logistics("$8 1Pc Car Photo Holder, Acrylic Magnetic", "", "") != "HARD"
    assert classify_logistics("Pull Out Trash Can Under Cabinet", "", "") != "HARD"
    assert classify_logistics("Dishwasher Safe Cutting Board Set", "", "") != "HARD"
    assert classify_logistics("Mount-It! Adjustable Ergonomic Desk Armrest Mount", "", "") != "HARD"
    assert classify_logistics("Coleman Cool Mesh Quad Folding Camping Chair", "", "") != "HARD"
    assert classify_logistics("$250 25 FT 10 Gauge Heavy Duty Flag Pole", "", "") != "HARD"


def test_description_boilerplate_cannot_poison_classification():
    # auctioneer footers mention furniture/vehicles on EVERY lot — the
    # killer scan must ignore descriptions entirely
    boilerplate = ("We sell everything - small collectibles, coins, furniture, "
                   "vehicles and more. Our load-up crew and moving truck can "
                   "come directly to you!")
    assert classify_logistics("Royal Winton Lidded Dish", "", boilerplate) != "HARD"
    assert classify_logistics("Stanley Quencher 30oz Tumbler", "", boilerplate) != "HARD"


def test_pickup_only_description_is_still_trusted():
    assert classify_logistics("Nice Small Item", "", "LOCAL PICKUP ONLY, no shipping") == "HARD"


def test_mailbox_winners_easy():
    assert classify_logistics("14k Gold Ring with Diamonds", "Jewelry", "") == "EASY"
    assert classify_logistics("Pokemon Cards Binder", "", "") == "EASY"
