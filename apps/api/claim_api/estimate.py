"""Money invariants and the explicitly synthetic G1 cost hypothesis."""

from __future__ import annotations

from claim_api.models import EstimateItem, EstimateView, QuoteView


G1_DEMO_ITEMS = [
    EstimateItem(id="bodywork", label="Carrosserie", amount_minor=48_000),
    EstimateItem(id="paint", label="Peinture", amount_minor=18_000),
    EstimateItem(id="rear_panel", label="Panneau arrière", amount_minor=26_000),
    EstimateItem(id="labour", label="Main-d'œuvre", amount_minor=24_000),
    EstimateItem(id="supplies", label="Fournitures", amount_minor=8_000),
]


def quote_status(estimate: EstimateView | None, quote: QuoteView | None) -> str:
    if estimate is None:
        return "no_estimate"
    if quote is None:
        return "no_quote"
    if quote.attached_estimate_version != estimate.version:
        return "outdated"
    return "matched" if quote.total_ttc_minor == estimate.total_minor else "mismatch"
