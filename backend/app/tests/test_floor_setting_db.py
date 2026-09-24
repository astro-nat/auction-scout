"""The per-auction floor setting (`pytest -m db`)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)


def test_floor_setting_roundtrips_without_regrade():
    before = client.get("/settings").json()
    try:
        r = client.patch("/settings", json={"auction_floor_usd": 150}).json()
        assert r["regrading"] == 0        # display-only: no regrade
        got = client.get("/settings").json()
        assert got["auction_floor_usd"] == 150
        assert got["target_roi_pct"] == before["target_roi_pct"]
    finally:
        client.patch("/settings", json={"auction_floor_usd": before["auction_floor_usd"]})


def test_settings_validation_rejects_garbage_without_side_effects():
    before = client.get("/settings").json()
    assert client.patch("/settings", json={}).status_code == 422
    assert client.patch("/settings", json={"auction_floor_usd": -5}).status_code == 422
    assert client.patch("/settings", json={"auction_floor_usd": "lots"}).status_code == 422
    assert client.patch("/settings", json={"target_roi_pct": 0}).status_code == 422
    assert client.get("/settings").json() == before   # nothing half-saved
