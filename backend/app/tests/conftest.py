import os

# Tests never fetch live gold and silver prices (services/metal.spot); the
# ones that need a price pass it in.
os.environ.setdefault("SPOT_OFFLINE", "1")
