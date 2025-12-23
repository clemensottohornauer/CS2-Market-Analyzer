from typing import Optional, Union

def enforce_scm_cap_display(price_usd: Optional[float], usd_cap: float) -> Union[str, float, None]:
    """
    If Steam price exceeds the display cap (e.g., 2000), return "exceeds limit".
    Otherwise return the numeric price unchanged.
    Accepts None and returns None for missing prices.
    """
    if price_usd is None:
        return None
    try:
        return "exceeds limit" if float(price_usd) > float(usd_cap) else float(price_usd)
    except Exception:
        return None
