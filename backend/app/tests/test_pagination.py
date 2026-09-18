"""Paged lot listing has to tile: no gaps, no repeats.

The UI chains pages of 2000 to assemble a large auction. LIMIT/OFFSET with
no ORDER BY gives Postgres licence to reshuffle rows between those calls,
and it does so under exactly the workload this app runs constantly — a bid
refresh or enrichment pass writing while the list loads. A 2,169-lot auction
paged during updates came back with 20 lots duplicated and 20 missing.
"""

import re
from pathlib import Path


def test_paged_lot_query_is_ordered():
    src = Path(__file__).resolve().parents[1] / "routers" / "lots.py"
    text = src.read_text(encoding="utf-8")
    paged = [ln for ln in text.splitlines() if ".offset(" in ln and ".limit(" in ln]
    assert paged, "expected a paged query in lots.py"
    for line in paged:
        assert "order_by" in line, f"paged query without ORDER BY: {line.strip()}"


def test_no_other_router_pages_without_ordering():
    """A new paginated endpoint must not reintroduce this."""
    routers = (Path(__file__).resolve().parents[1] / "routers").glob("*.py")
    for path in routers:
        for line in path.read_text(encoding="utf-8").splitlines():
            if ".offset(" in line and ".limit(" in line:
                assert "order_by" in line, f"{path.name}: {line.strip()}"
