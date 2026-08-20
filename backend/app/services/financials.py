"""ROI / bid-ceiling math, ported from the Streamlit prototype's utils/financials.py.

Pure functions, no I/O — everything here is deterministic arithmetic on a lot's
resale estimate and current bid. The knobs (premium, tax, fees) mirror the
prototype's defaults; override per-call if an auction house differs.
"""

from dataclasses import dataclass

TARGET_ROI = 5.0          # 500% — resale must clear 5x all-in cost to be a "buy"
BUYERS_PREMIUM = 0.15     # auction house premium on the hammer price
SALES_TAX = 0.0825        # TX sales tax, applied on hammer + premium
PLATFORM_FEE = 0.15       # eBay final-value fee on the resale side
BUFFER = 15.0             # flat $ for packing/misc per lot
MAX_DTS = 90.0            # days-to-sell ceiling for viability


def acquisition_multiplier(buyers_premium: float = BUYERS_PREMIUM,
                           sales_tax: float = SALES_TAX) -> float:
    return 1 + buyers_premium + sales_tax


def days_to_sell(sold_count: int, active_count: int) -> float:
    """Estimated days to sell: how long the active-listing queue takes to clear
    at the observed 90-day sales velocity, with you joining the back of it.
    999 = illiquid (nothing sold in 90 days)."""
    if sold_count <= 0:
        return 999.0
    daily_velocity = sold_count / 90
    return (active_count + 1) / daily_velocity


def max_bid(resale_value: float,
            logistics_penalty: float = 0.0,
            target_roi: float = TARGET_ROI,
            buyers_premium: float = BUYERS_PREMIUM,
            sales_tax: float = SALES_TAX,
            platform_fee: float = PLATFORM_FEE,
            buffer: float = BUFFER) -> float:
    """Highest hammer price that still hits the target ROI after premium, tax,
    platform fees, shipping penalty, and buffer."""
    net_proceeds = resale_value * (1 - platform_fee)
    allowable = net_proceeds - logistics_penalty - buffer
    ceiling = allowable / ((1 + target_roi) * acquisition_multiplier(buyers_premium, sales_tax))
    return max(0.0, round(ceiling, 2))


@dataclass
class LeadEvaluation:
    max_bid: float
    total_cost: float
    profit: float
    roi: float
    is_viable: bool
    status: str


def evaluate_lead(resale_value: float,
                  current_bid: float,
                  logistics_penalty: float = 0.0,
                  dts: float = 999.0,
                  max_dts: float = MAX_DTS,
                  target_roi: float = TARGET_ROI) -> LeadEvaluation:
    """Grade a lot at its current bid. Viable = bid under the ROI ceiling AND
    the item actually moves on eBay (dts within bounds)."""
    ceiling = max_bid(resale_value, logistics_penalty, target_roi)
    total_cost = current_bid * acquisition_multiplier() + logistics_penalty + BUFFER
    profit = resale_value * (1 - PLATFORM_FEE) - total_cost
    roi = profit / total_cost if total_cost > 0 else 0.0
    is_viable = current_bid <= ceiling and dts <= max_dts and ceiling > 0
    return LeadEvaluation(
        max_bid=ceiling,
        total_cost=round(total_cost, 2),
        profit=round(profit, 2),
        roi=round(roi, 4),
        is_viable=is_viable,
        status="GOLD MINE" if is_viable else "PASS",
    )
