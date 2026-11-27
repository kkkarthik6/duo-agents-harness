"""Approximate dollar cost of one agent call. A cost the CLI reports wins; otherwise tokens are priced from
rubrics/model-prices.json; an unknown model has no price rather than a guess."""
import json

from . import config

RATES_FILE = "rubrics/model-prices.json"
TOKENS_PER_UNIT = 1_000_000


def rates():
    try:
        table = json.loads((config.APP_DIR / RATES_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return {model: rate for model, rate in table.items() if isinstance(rate, dict)}


def usage_cost(model, usage, table=None):
    """Return {"usd", "source"} for one call, or None when the call cannot be priced."""
    usage = usage or {}
    reported = usage.get("total_cost_usd")
    if isinstance(reported, (int, float)):
        return {"usd": float(reported), "source": "cli"}
    rate = (rates() if table is None else table).get(model or "")
    if not rate:
        return None
    total_in = usage.get("input_tokens") or 0
    cached = min(usage.get("cached_input_tokens") or 0, total_in)
    usd = ((total_in - cached) * rate["input"] + cached * rate.get("cached_input", rate["input"]) +
           (usage.get("output_tokens") or 0) * rate["output"]) / TOKENS_PER_UNIT
    return {"usd": usd, "source": "rates"}
