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

# --- GovDeals (Liquidity Services maestro API) ---
# These two keys are NOT secrets: they're the anonymous app keys served to
# every visitor inside govdeals.com's public JS bundle (x-user-id -1, no
# account). Env overrides exist so a site release that rotates them is a
# config change, not a deploy.
GOVDEALS_API_KEY = os.environ.get(
    "GOVDEALS_API_KEY", "af93060f-337e-428c-87b8-c74b5837d6cd")
GOVDEALS_SUB_KEY = os.environ.get(
    "GOVDEALS_SUB_KEY", "cf620d1d8f904b5797507dc5fd1fdb80")
# Buyer's premium varies by seller (12.5%-18%, shown per asset page, absent
# from the search API). 15% is the conservative middle: overestimating cost
# a little makes max bids safer, never riskier.
GOVDEALS_PREMIUM_MULT = float(os.environ.get("GOVDEALS_PREMIUM_MULT", "1.15"))

# --- PublicSurplus ---
# Server-rendered site, no keys needed. Region is the state slug in its
# URLs; the buyer's premium is ~10-12% depending on payment method, so 12%
# errs on the safe side of every max bid.
PUBLICSURPLUS_REGION = os.environ.get("PUBLICSURPLUS_REGION", "tx")
PUBLICSURPLUS_PREMIUM_MULT = float(
    os.environ.get("PUBLICSURPLUS_PREMIUM_MULT", "1.12"))

# --- Vinted (fixed-price sourcing watches) ---
# Buyer protection is ~5% + $0.70 plus sales tax; 1.08 swallows all three
# on typical item prices, erring toward overestimating cost. Shipping is
# paid by the buyer at checkout ($4-8 for most parcels).
VINTED_PREMIUM_MULT = float(os.environ.get("VINTED_PREMIUM_MULT", "1.08"))
VINTED_SHIP_ESTIMATE = float(os.environ.get("VINTED_SHIP_ESTIMATE", "6.0"))

# --- eBay API (comps + image search); empty string disables those features ---
EBAY_APP_ID = os.environ.get("EBAY_APP_ID", "")
EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID", "")

# --- vision provider (image understanding for enrichment + inspection) ---
# Gemini identified the user's jewelry lots better than Claude in practice,
# so image analysis is switchable. Setting GEMINI_API_KEY is the switch:
# vision calls route to Gemini automatically, VISION_PROVIDER overrides
# explicitly ("claude" | "gemini"). Text enrichment and the gold-check
# audit stay on Claude regardless — the audit especially, because a value
# priced by one model and audited by a different one is a genuinely
# independent second opinion.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# 3.6-flash: the stable everyday multimodal tier — extraction at volume.
# Set GEMINI_MODEL=gemini-3.8-flash for the smarter, pricier flash.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "")


def vision_provider() -> str:
    if VISION_PROVIDER:
        return VISION_PROVIDER.strip().lower()
    return "gemini" if GEMINI_API_KEY else "claude"


# --- default commercial assumptions when an auction doesn't specify ---
DEFAULT_BUYER_PREMIUM_PCT = float(os.environ.get("DEFAULT_BUYER_PREMIUM_PCT", "15.0"))

# --- watched-lot closing alerts (phone push via ntfy) ---
# Setup: install the ntfy app (ntfy.sh, free, no account), subscribe to a
# topic name nobody would guess (it's effectively a password), and set that
# name here. Empty disables the notifier entirely.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")
# Alert when a watched lot's auction closes within this many hours.
WATCH_ALERT_HOURS = float(os.environ.get("WATCH_ALERT_HOURS", "2"))

# Auto-delete lots from closed auctions this often (hours). 0 disables and
# leaves cleanup to the manual "Flush closed items" button.
FLUSH_CLOSED_HOURS = float(os.environ.get("FLUSH_CLOSED_HOURS", "12"))

# Only refresh bids for auctions closing inside this window. Bids barely
# move while a sale is days out, and every refresh is a full HiBid re-fetch
# of every lot — so pulling a 500-lot auction hourly for a week costs a lot
# to learn nothing. 0 disables the window and refreshes everything open.
#
# The trade is real: outside the window the Bid column holds its last known
# value, so ROI on a lot closing in three days is computed against a bid
# that may be hours old.
BID_REFRESH_WINDOW_HOURS = float(os.environ.get("BID_REFRESH_WINDOW_HOURS", "1"))

# How many lots enrich/inspect in parallel. The work is HTTP-bound (Claude,
# eBay, image downloads), so a few threads give a ~Nx queue speedup; keep
# modest to respect API rate limits and container memory.
ENRICH_CONCURRENCY = int(os.environ.get("ENRICH_CONCURRENCY", "3"))

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
    r"|(?<!usb )(?<!can )(?<!data )\bbus(es)?\b(?!\s*(?:powered|interface|bars?|strips?))"
    r"|\bvan\b(?!\s+gogh)"
    r"|\bhouse\b|real estate|\bproperty\b|\bland\b|\bacreage\b|\bcondo\b|\bshed\b|\bbarn\b"
    r"|\bfence\b|\bpallet\b|bulk lot|pool table|hot tub|aquarium|fish tank"
    r"|oversized"
)

# --- titled vehicles (cars, buses, boats — DMV paperwork, not parcels) ---
# The government platforms are full of fleet vehicles whose resale math is
# genuinely right ($5,679 max bid on a $12,500 school bus) and whose
# logistics are genuinely not this business. Matching is category-first
# (the platforms label vehicles cleanly), then title evidence. Parts,
# accessories and toys are NOT vehicles — a truck bed liner, a diecast
# Mustang and a "Motor Pool Parts" pallet all stay flippable.
VEHICLE_CATEGORY = (
    r"\b(automobiles?|cars?|vehicles?|suvs?|trucks?|vans?|bus(es)?"
    r"|motorcycles?|motor ?homes?|rvs?|boats?|motor pool|aviation)\b"
)
VEHICLE_CATEGORY_GUARD = r"parts|supplies|accessor"
# Unambiguous vehicle nouns in a title. Bare "scooter"/"moped" stay out —
# a kids kick scooter is a $30 flip, and real ones carry year/make anyway.
VEHICLE_NOUNS = (
    r"\b(sedan|coupe|hatchback|minivan|cargo van|passenger van|box truck"
    r"|tow truck|dump truck|school bus|transit bus|shuttle bus|motorhome"
    r"|travel trailer|5th wheel|fifth wheel|jet ski|waverunner)\b"
)
# Title evidence: paperwork words, odometer readings, or year + make.
VEHICLE_MARKERS = (
    r"\b(vin\b|odometer|(salvage|clean|rebuilt) title|title in hand"
    r"|\d{1,3},\d{3} miles|\d+k miles"
    r"|(19|20)\d{2}\s+(ford|chevrolet|chevy|gmc|dodge|ram|toyota|honda"
    r"|nissan|jeep|chrysler|buick|cadillac|lincoln|kia|hyundai|subaru"
    r"|mazda|volkswagen|vw|bmw|mercedes|audi|lexus|acura|infiniti|volvo"
    r"|tesla|aston martin|freightliner|international|kenworth|peterbilt"
    r"|mack|isuzu|hino|navistar|thomas|saf-?t-?liner|blue ?bird"
    r"|harley|yamaha|kawasaki|suzuki|polaris|kubota)\b)"
)
# Things that only look like vehicles: toys, models, parts callouts.
VEHICLE_TOY_GUARD = (
    r"\b(diecast|die-cast|hot ?wheels|matchbox|\brc\b|remote control"
    r"|toy|model kit|scale model|1[:/]\d{2}\b|lego|tonka|pedal car"
    r"|parts? only|for parts)\b"
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
