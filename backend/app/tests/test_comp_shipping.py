"""How each sold comp shipped, from SoldComps' fields. Pure."""

import pytest

from app.services import pricing


@pytest.mark.parametrize("item,ship", [
    ({"shippingType": "free", "shippingPrice": None}, {"ship": "free", "ship_cost": None}),
    ({"shippingType": "paid", "shippingPrice": "8.50"}, {"ship": "paid", "ship_cost": 8.5}),
    ({"shippingType": "paid", "shippingPrice": "$1,005.00"}, {"ship": "paid", "ship_cost": 1005.0}),
    ({"shippingType": "pickup"}, {"ship": "pickup", "ship_cost": None}),
    ({"shippingType": "unknown"}, {"ship": None, "ship_cost": None}),
    ({}, {"ship": None, "ship_cost": None}),
])
def test_shipping_of(item, ship):
    assert pricing.shipping_of(item) == ship
