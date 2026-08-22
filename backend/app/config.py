"""App settings, read from environment with sane defaults.

Secrets (API keys) come only from env / .env — never hardcode them here.
The logistics regexes are operational tuning, not secrets, so they live in code.
"""

import os

# --- sourcing ---
SOURCING_ZIP = os.environ.get("SOURCING_ZIP", "77058")
SOURCING_RADIUS_MILES = int(os.environ.get("SOURCING_RADIUS_MILES", "20"))
CLOSING_WITHIN_DAYS = int(os.environ.get("CLOSING_WITHIN_DAYS", "7"))

# --- HiBid API ---
HIBID_USER_AGENT = os.environ.get(
    "HIBID_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
)
HIBID_TIMEOUT_SECONDS = float(os.environ.get("HIBID_TIMEOUT_SECONDS", "15.0"))

# --- eBay API (comps + image search); empty string disables those features ---
EBAY_APP_ID = os.environ.get("EBAY_APP_ID", "")
EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID", "")

# --- default commercial assumptions when an auction doesn't specify ---
DEFAULT_BUYER_PREMIUM_PCT = float(os.environ.get("DEFAULT_BUYER_PREMIUM_PCT", "15.0"))

# Items that are miserable/impossible to ship — HARD logistics.
# Matched against TITLE + CATEGORY only (never descriptions — auctioneer
# boilerplate like "we sell furniture, vehicles... our moving truck..."
# flagged entire auctions HARD). Lookarounds carve accessory phrases out of
# the furniture/vehicle words: "table lamp", "under cabinet", "car charger",
# "dishwasher safe" are small items, not the furniture the bare word implies.
# Bare "large"/"heavy" removed — they're product adjectives ("heavy duty",
# "large print") far more often than freight warnings.
SHIP_KILLERS = (
    r"\btables?\b(?!\s+(?:lamps?|runners?|cloths?|linens?|top|tennis|book|clocks?|fans?|saw blade))"
    r"|\bdesks?\b(?!\s+(?:clock|lamp|fan|organizers?|pads?|mats?|armrest|accessor))"
    r"|sofa|couch|loveseat|recliner"
    r"|(?<!camp )(?<!camping )\bchairs?\b(?!\s+(?:covers?|pads?|cushions?|legs?|mats?))"
    r"|(?<!dog )(?<!pet )(?<!cat )\bbeds?\b(?!\s+(?:sheets?|pillows?|skirts?|rails?|liners?))"
    r"|bedframe|bed frame|mattress|box spring|headboard|dresser|armoire|wardrobe|\bhutch\b"
    r"|bookcase|bookshelf|(?<!under )\bcabinets?\b(?!\s+(?:knobs?|pulls?|hardware|hinges?))"
    r"|credenza|buffet|sideboard|china cabinet|nightstand"
    r"|end table|coffee table|dining set|office chair|mower|lawn mower|tractor|snow blower"
    r"|generator|(?<!compact )(?<!hand )(?<!makeup )(?<!side )\bmirrors?\b(?!\s*(?:finish|polish|image))"
    r"|pickup only|(?<!doll )(?<!dollhouse )furniture|\bcrates?\b(?!\s*(?:&|and)\s*barrel)"
    r"|(?<!small )appliances?\b"
    r"|refrigerator|\bfridge\b|freezer|\bwasher\b|\bdryer\b|dishwasher(?![- ]safe)|\bstove\b"
    r"|(?<!free )(?<!driving )\brange\b(?!\s*finder)"
    r"|\boven\b(?!\s*mitt)|microwave|ac unit|air conditioner|water heater"
    r"|\bgrills?\b(?!\s+(?:pans?|brush(?:es)?|covers?|mats?|scrapers?|tools?|gloves?|thermometers?))"
    r"|\bbbq\b(?!\s+(?:tools?|brush|sauce|rub|gloves?))|\bpiano\b"
    r"|treadmill|elliptical|exercise bike|\bgym\b(?!\s+(?:bags?|shorts|towels?))"
    r"|(?<!dishwasher )(?<!microwave )(?<!oven )(?<!kid )(?<!child )(?<!food )(?<!skin )\bsafe\b"
    r"|\bvault\b|gun safe|toolbox"
    r"|tool chest|workbench|\bladder\b"
    r"|(?<!rc )(?<!toy )(?<!slot )\bcars?\b(?!\s+(?:photo|chargers?|mounts?|keys?|covers?|mats?|seats?|wash|care|audio|stereo|holders?|organizers?|vacuums?|adapters?|fresheners?))"
    r"|vehicle|(?<!rc )(?<!toy )\btrucks?\b(?!\s+bed liner)|\bsuv\b|\bsedan\b"
    r"|motorcycle|\batv\b|\butv\b|\bboat\b|jet ski|\btrailer\b|\brv\b|\bcamper\b|motorhome"
    r"|\bhouse\b|real estate|\bproperty\b|\bland\b|\bacreage\b|\bcondo\b|\bshed\b|\bbarn\b"
    r"|\bfence\b|\bpallet\b|bulk lot|pool table|hot tub|aquarium|fish tank"
    r"|oversized"
)

# Small, dense, valuable — fits in a mailbox, ships cheap: EASY logistics.
# Word-boundaried: bare substrings false-positive constantly ("gold" inside
# "QuartzGold", "pen" inside "expensive", "ink" inside "drink").
MAILBOX_WINNERS = (
    r"\b(jewelry|watch(es)?|camera|cards?|games?|gold|silver|nintendo|apple|ink"
    r"|pens?|coins?|currency|stamps?|numismatic)\b"
)

# Clothing/apparel: soft, foldable, light — always cheap to ship regardless
# of the item itself, so it overrides even the HARD-ship keyword list (a
# "leather trench coat" shouldn't flag HARD just because "coat" isn't on any
# killer list — the point is apparel as a category is never the problem).
CLOTHING = (
    r"\b(clothing|apparel|garments?|outfits?|wardrobe|wearables?"
    r"|shirts?|blouses?|dress(es)?|jackets?|coats?|jeans|pants|trousers"
    r"|skirts?|sweaters?|hoodies?|suits?|vests?|denim|rompers?|jumpsuits?"
    r"|overalls|cardigans?|blazers?|tunics?|sportswear|activewear"
    r"|underwear|lingerie|pajamas|bathrobes?|shorts|t-?shirts?|polo shirts?"
    r"|tank tops?|socks?|gloves?|scarf|scarves|beanies?|neckties?"
    r"|gowns?|kimonos?|ponchos?|parkas?|windbreakers?|raincoats?"
    r"|swimsuits?|bikinis?|leggings|joggers|sweatpants|uniforms?)\b"
)
