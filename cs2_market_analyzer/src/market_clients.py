from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple
import json
import requests

try:
    import brotli  # type: ignore
except ImportError:
    brotli = None  # type: ignore


STEAM_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"
SKINPORT_ITEMS_URL = "https://api.skinport.com/v1/items"

COMMON_HEADERS = {
    "Accept": "application/json; charset=utf-8",
    "Accept-Encoding": "br, gzip, deflate",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Referer": "https://skinport.com",
}


def _parse_price_text(txt: str) -> Optional[float]:
    s = (txt or "").strip().replace(",", ".")
    filtered = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    try:
        return float(filtered) if filtered else None
    except Exception:
        return None


def _read_raw_bytes_no_decode(r: requests.Response) -> bytes:
    """
    Read raw HTTP body bytes WITHOUT urllib3 auto-decoding content-encoding.
    Requires requests.get(..., stream=True).
    """
    r.raw.decode_content = False
    return r.raw.read()


def _decode_bytes_to_text(raw: bytes, content_encoding: str, fallback_encoding: str = "utf-8") -> str:
    enc = (content_encoding or "").lower().strip()

    if enc == "br":
        if brotli is None:
            raise RuntimeError(
                "Skinport returned Brotli (br) but 'brotli' is not installed. Run: pip install brotli"
            )
        raw = brotli.decompress(raw)

    return raw.decode(fallback_encoding, errors="replace")


def steam_get_price_usd(market_hash_name: str) -> Optional[float]:
    """
    Uses Steam Community Market priceoverview to get lowest or median USD price.
    currency=1 => USD.
    """
    try:
        params = {"appid": 730, "currency": 1, "market_hash_name": market_hash_name}
        r = requests.get(
            STEAM_PRICE_URL,
            params=params,
            timeout=15,
            headers=COMMON_HEADERS,
        )

        if r.status_code != 200:
            print("Steam HTTP status:", r.status_code)
            print("Steam response (first 200 chars):", r.text[:200])
            return None

        data = r.json()
        if not data.get("success"):
            print("Steam success=false JSON:", str(data)[:300])
            return None

        lp = data.get("lowest_price") or data.get("median_price")
        if not lp:
            return None

        return _parse_price_text(lp)

    except Exception as e:
        print("Steam exception:", repr(e))
        return None


def skinport_get_items_usd() -> List[Dict[str, Any]]:
    params = {"app_id": 730, "currency": "USD", "tradable": "true"}

    try:
        print("Requesting Skinport:", SKINPORT_ITEMS_URL, "params:", params)

        r = requests.get(
            SKINPORT_ITEMS_URL,
            params=params,
            timeout=25,
            headers=COMMON_HEADERS,
            stream=True,  # IMPORTANT: prevents eager decoding
        )

        print("Skinport HTTP status:", r.status_code)
        ce = r.headers.get("Content-Encoding")
        print("Skinport Content-Encoding:", ce)

        raw = _read_raw_bytes_no_decode(r)

        if r.status_code != 200:
            txt_err = _decode_bytes_to_text(raw, ce or "", r.encoding or "utf-8")
            print("Skinport response (first 300 chars):", txt_err[:300])
            return []

        txt = _decode_bytes_to_text(raw, ce or "", r.encoding or "utf-8")
        data = json.loads(txt)

        if not isinstance(data, list):
            print("Skinport JSON was not a list. Type:", type(data))
            print("Skinport JSON (first 300 chars):", str(data)[:300])
            return []

        return data

    except Exception as e:
        print("Skinport exception:", repr(e))
        return []


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _simplify(s: str) -> str:
    t = (s or "").replace("★", "").strip()
    if "(" in t and ")" in t:
        t = t[: t.rfind("(")].strip()
    return t


def _match_score(target: str, candidate: str) -> int:
    if candidate == target:
        return 100
    score = 0
    if candidate.startswith(target):
        score += 50
    if target in candidate:
        score += 30
    score -= abs(len(candidate) - len(target)) // 2
    return score


def skinport_find_min_price_usd(
    items: List[Dict[str, Any]],
    market_hash_name: str,
    debug: bool = False,
) -> Tuple[Optional[float], Optional[str]]:
    target = _norm(market_hash_name)
    target_simple = _norm(_simplify(market_hash_name))

    candidates: List[Tuple[float, str, int]] = []

    for it in items:
        mh = _norm(it.get("market_hash_name"))
        mp = it.get("min_price")

        try:
            val = float(mp) if mp is not None else None
        except Exception:
            val = None

        if val is None:
            continue

        if mh == target:
            candidates.append((val, f"exact match: {mh}", 100))
            continue

        if target_simple and target_simple in mh:
            score = _match_score(target_simple, mh)
            candidates.append((val, f"partial match: {mh}", score))

    if not candidates:
        return (None, "no candidates")

    candidates.sort(key=lambda x: (x[0], -x[2]))
    best_val, best_reason, _ = candidates[0]
    return (best_val, best_reason if debug else None)
