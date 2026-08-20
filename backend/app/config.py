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
SHIP_KILLERS = (
    r"\btable\b|desk|sofa|couch|loveseat|recliner|\bchair\b|\bchairs\b|\bbed\b|\bbeds\b"
    r"|bedframe|bed frame|mattress|box spring|headboard|dresser|armoire|wardrobe|\bhutch\b"
    r"|bookcase|bookshelf|\bcabinet\b|credenza|buffet|sideboard|china cabinet|nightstand"
    r"|end table|coffee table|dining set|office chair|mower|lawn mower|tractor|snow blower"
    r"|generator|mirror|large|heavy|oversized|pickup only|furniture|crate|appliance"
    r"|refrigerator|\bfridge\b|freezer|\bwasher\b|\bdryer\b|dishwasher|\bstove\b|\brange\b"
    r"|\boven\b|microwave|ac unit|air conditioner|water heater|\bgrill\b|\bbbq\b|\bpiano\b"
    r"|treadmill|elliptical|exercise bike|\bgym\b|\bsafe\b|\bvault\b|gun safe|toolbox"
    r"|tool chest|workbench|\bladder\b|\bcar\b|\bcars\b|vehicle|\btruck\b|\bsuv\b|\bsedan\b"
    r"|motorcycle|\batv\b|\butv\b|\bboat\b|jet ski|\btrailer\b|\brv\b|\bcamper\b|motorhome"
    r"|\bhouse\b|real estate|\bproperty\b|\bland\b|\bacreage\b|\bcondo\b|\bshed\b|\bbarn\b"
    r"|\bfence\b|\bpallet\b|bulk lot|pool table|hot tub|aquarium|fish tank"
)

# Small, dense, valuable — fits in a mailbox, ships cheap: EASY logistics.
MAILBOX_WINNERS = (
    r"jewelry|watch|camera|card|game|gold|silver|nintendo|apple|ink|pen|coin|currency"
    r"|stamp|numismatic"
)
