from typing import Dict, Any
import yaml

def load_fees(path: str = "config/fees.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def apply_buyer_total_usd(market: str, listing_price_usd: float, fees_cfg: Dict[str, Any]) -> float:
    mf = fees_cfg["market_fees"].get(market, {})
    if market == "steam":
        return listing_price_usd
    if market in ("skinport", "skinbaron"):
        extra = mf.get("buyer_extra_rate", 0.0)
        return listing_price_usd * (1.0 + extra)
    return listing_price_usd

def apply_seller_net_usd(market: str, listing_price_usd: float, fees_cfg: Dict[str, Any]) -> float:
    mf = fees_cfg["market_fees"].get(market, {})
    if market == "steam":
        rate = mf.get("seller_fee_rate", 0.15)
        return listing_price_usd * (1.0 - rate)
    if market == "skinport":
        rate = mf.get("seller_fee_rate", 0.12)
        return listing_price_usd * (1.0 - rate)
    if market == "skinbaron":
        rate = mf.get("seller_fee_rate", 0.10)
        return listing_price_usd * (1.0 - rate)
    return listing_price_usd
