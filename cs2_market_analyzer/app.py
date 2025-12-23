# app.py
import re
import time
import requests
import pandas as pd
import streamlit as st

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.market_clients import (
    skinport_get_items_usd,
    skinport_find_min_price_usd,
    steam_get_price_usd,
)

# ----------------------------
# Page (minimalistic)
# ----------------------------
st.set_page_config(page_title="CS2 Market Analyzer", layout="wide")
st.title("CS2 Market Analyzer")
st.caption(f"Local time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ----------------------------
# Helpers (Steam search + images)
# ----------------------------
STEAM_SEARCH_URL = "https://steamcommunity.com/market/search/render/"
STEAM_IMG_PREFIX = "https://community.cloudflare.steamstatic.com/economy/image/"

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

WEARS = [
    "Factory New",
    "Minimal Wear",
    "Field-Tested",
    "Well-Worn",
    "Battle-Scarred",
]


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s or "").strip()


def steam_icon_url_to_image_url(icon_url: Optional[str], size: int = 256) -> Optional[str]:
    if not icon_url:
        return None
    return f"{STEAM_IMG_PREFIX}{icon_url}/{size}fx{size}f"


def parse_market_hash_name(mh: str) -> Dict[str, Any]:
    """
    Parses Steam market_hash_name into:
      - is_stattrak (bool)
      - is_souvenir (bool)  [not used in UI yet, but we avoid breaking it]
      - base_name (str)     e.g., "AK-47 | Redline"
      - wear (str|None)     e.g., "Field-Tested"
    Works for common CS2 patterns.
    """
    mh = (mh or "").strip()

    is_st = mh.startswith("StatTrak™ ")
    is_souv = mh.startswith("Souvenir ")

    name = mh
    if is_st:
        name = name[len("StatTrak™ "):]
    if is_souv:
        name = name[len("Souvenir "):]

    wear = None
    m = re.search(r"\s\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$", name)
    if m:
        wear = m.group(1)
        base = name[: m.start()].strip()
    else:
        base = name.strip()

    return {
        "market_hash_name": mh,
        "is_stattrak": is_st,
        "is_souvenir": is_souv,
        "base_name": base,
        "wear": wear,
    }


def build_market_hash_name(base_name: str, wear: str, stattrak: bool = False) -> str:
    base_name = (base_name or "").strip()
    wear = (wear or "").strip()
    mh = f"{base_name} ({wear})"
    if stattrak:
        mh = f"StatTrak™ {mh}"
    return mh


@st.cache_data(ttl=600, show_spinner=False)
def steam_search_items(query: str, count: int = 60) -> List[Dict[str, Any]]:
    """
    Uses Steam Community Market search endpoint to get results + icon_url.
    No API key required.
    """
    q = (query or "").strip()
    if not q:
        return []

    params = {
        "query": q,
        "start": 0,
        "count": int(count),
        "appid": 730,
        "norender": 1,
    }

    r = requests.get(STEAM_SEARCH_URL, params=params, headers=COMMON_HEADERS, timeout=20)
    r.raise_for_status()
    payload = r.json()

    results = payload.get("results", []) or []
    out: List[Dict[str, Any]] = []

    for it in results:
        mh = it.get("hash_name") or it.get("market_hash_name") or it.get("name")
        if not mh:
            continue

        parsed = parse_market_hash_name(mh)

        out.append(
            {
                "market_hash_name": mh,
                "base_name": parsed["base_name"],
                "wear": parsed["wear"],
                "is_stattrak": parsed["is_stattrak"],
                "is_souvenir": parsed["is_souvenir"],
                "name": _strip_html(it.get("name", "")) or mh,
                "sell_price_text": _strip_html(it.get("sell_price_text", "")),
                "sell_listings": it.get("sell_listings"),
                "icon_url": it.get("asset_description", {}).get("icon_url"),
            }
        )

    return out


def group_by_base(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Groups Steam search results by base_name (weapon | skin).
    Keeps one representative image + remembers which wears were seen.
    """
    groups: Dict[str, Dict[str, Any]] = {}

    for r in results:
        base = r.get("base_name") or r.get("market_hash_name")
        if not base:
            continue

        g = groups.get(base)
        if not g:
            groups[base] = {
                "base_name": base,
                "icon_url": r.get("icon_url"),
                "seen_wears": set(),
                "seen_stattrak": False,
                "seen_nonstattrak": False,
                "samples": [],
            }
            g = groups[base]

        w = r.get("wear")
        if w:
            g["seen_wears"].add(w)
        if r.get("is_stattrak"):
            g["seen_stattrak"] = True
        else:
            g["seen_nonstattrak"] = True

        # keep a few samples for optional meta/debug if you want later
        if len(g["samples"]) < 3:
            g["samples"].append(r)

        # prefer having an image
        if not g.get("icon_url") and r.get("icon_url"):
            g["icon_url"] = r.get("icon_url")

    # convert sets to sorted lists
    out = []
    for base, g in groups.items():
        g["seen_wears"] = sorted(list(g["seen_wears"]), key=lambda x: WEARS.index(x) if x in WEARS else 999)
        out.append(g)

    # sort alphabetically (stable)
    out.sort(key=lambda x: x["base_name"].lower())
    return out


# ----------------------------
# Skinport cache (big list)
# ----------------------------
@st.cache_data(ttl=3600, show_spinner=True)
def load_skinport_items() -> List[Dict[str, Any]]:
    return skinport_get_items_usd()


# ----------------------------
# Watchlist helpers
# ----------------------------
def ensure_watchlist():
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = []  # list[str]


def add_to_watchlist(mh: str):
    ensure_watchlist()
    if mh and mh not in st.session_state.watchlist:
        st.session_state.watchlist.append(mh)


def remove_from_watchlist(mh: str):
    ensure_watchlist()
    st.session_state.watchlist = [x for x in st.session_state.watchlist if x != mh]


ensure_watchlist()

# ----------------------------
# Sidebar (minimal)
# ----------------------------
with st.sidebar:
    st.header("Controls")

    st.markdown("**Skinport**")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Load / Refresh", use_container_width=True):
            load_skinport_items.clear()
            load_skinport_items()
            st.success("Skinport cache refreshed.")
    with col_b:
        sp_items_now = load_skinport_items()
        st.metric("Skinport items", f"{len(sp_items_now):,}")

    st.divider()

    st.markdown("**Watchlist**")
    if st.session_state.watchlist:
        for mh in st.session_state.watchlist[:50]:
            row = st.columns([1, 0.25])
            row[0].write(mh)
            if row[1].button("✕", key=f"rm_{mh}", use_container_width=True):
                remove_from_watchlist(mh)
                st.rerun()
        if len(st.session_state.watchlist) > 50:
            st.caption(f"+ {len(st.session_state.watchlist) - 50} more…")
    else:
        st.info("Empty. Add items in the gallery.")

# ----------------------------
# Main layout
# ----------------------------
tab_add, tab_check = st.tabs(["Add items", "Run check"])

with tab_add:
    st.subheader("Pick a skin → toggle wear + StatTrak")
    st.caption("Search for a skin, then choose wear + StatTrak on the same card and add the generated market_hash_name.")

    top = st.columns([2, 1, 1, 1])
    query = top[0].text_input("Search", value="AK-47 | Redline")
    results_count = top[1].selectbox("Results", [24, 48, 72], index=1)
    img_size = top[2].selectbox("Image size", [128, 256, 360], index=1)
    grid_cols = top[3].selectbox("Grid", [3, 4, 5], index=1)

    if query.strip():
        with st.spinner("Searching Steam Market…"):
            results = steam_search_items(query, count=int(results_count))
            groups = group_by_base(results)

        if not groups:
            st.warning("No results found.")
        else:
            # Gallery
            rows = (len(groups) + int(grid_cols) - 1) // int(grid_cols)
            idx = 0

            for _ in range(rows):
                row_cols = st.columns(int(grid_cols))
                for c in row_cols:
                    if idx >= len(groups):
                        break
                    g = groups[idx]
                    idx += 1

                    base = g["base_name"]
                    img = steam_icon_url_to_image_url(g.get("icon_url"), size=int(img_size))

                    # per-card state keys
                    key_prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", base)[:60]
                    wear_key = f"wear_{key_prefix}"
                    st_key = f"st_{key_prefix}"

                    with c:
                        if img:
                            st.image(img, use_container_width=True)

                        st.markdown(f"**{base}**")

                        # Default wear: if Steam search already saw a wear, pick that; else Field-Tested
                        default_wear = "Field-Tested"
                        if g["seen_wears"]:
                            default_wear = g["seen_wears"][0]  # most "common" by our sort order

                        wear = st.selectbox(
                            "Wear",
                            WEARS,
                            index=WEARS.index(default_wear) if default_wear in WEARS else 2,
                            key=wear_key,
                        )

                        stattrak = st.toggle("StatTrak", value=False, key=st_key)

                        mh = build_market_hash_name(base, wear, stattrak=stattrak)
                        st.caption(mh)

                        if mh in st.session_state.watchlist:
                            st.button("Added", disabled=True, use_container_width=True, key=f"added_{key_prefix}")
                        else:
                            if st.button("Add", use_container_width=True, key=f"add_{key_prefix}"):
                                add_to_watchlist(mh)
                                st.rerun()

            st.divider()
            st.caption(
                "Note: This assumes the item exists for the selected wear/StatTrak. "
                "If it doesn’t exist, Steam/Skinport price may return None."
            )

    st.divider()
    with st.expander("Advanced: paste exact market_hash_names (optional)"):
        text = st.text_area(
            "One per line",
            height=120,
            placeholder="AK-47 | Redline (Field-Tested)\nStatTrak™ AK-47 | Redline (Minimal Wear)\n...",
        )
        if st.button("Add pasted names", use_container_width=True):
            lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
            added = 0
            for mh in lines:
                if mh not in st.session_state.watchlist:
                    add_to_watchlist(mh)
                    added += 1
            st.success(f"Added {added} items.")

with tab_check:
    st.subheader("Price check")
    st.caption("Compares Steam USD vs Skinport min_price USD for your watchlist.")

    if not st.session_state.watchlist:
        st.info("Your watchlist is empty. Add items first.")
    else:
        colx, coly, colz = st.columns([1, 1, 1])
        with colx:
            max_items = st.number_input(
                "Max items to check",
                min_value=1,
                max_value=500,
                value=min(50, len(st.session_state.watchlist)),
            )
        with coly:
            delay = st.number_input(
                "Delay between Steam calls (sec)",
                min_value=0.0,
                max_value=3.0,
                value=0.2,
                step=0.1,
            )
        with colz:
            show_debug = st.toggle("Show matching debug", value=False)

        if st.button("Run check", type="primary"):
            sp_items = load_skinport_items()
            watch = st.session_state.watchlist[: int(max_items)]

            rows_out: List[Dict[str, Any]] = []
            prog = st.progress(0, text="Checking…")

            for i, mh in enumerate(watch, start=1):
                sp_price, reason = skinport_find_min_price_usd(sp_items, mh, debug=True)
                steam_price = steam_get_price_usd(mh)

                spread = None
                if isinstance(steam_price, (int, float)) and isinstance(sp_price, (int, float)):
                    spread = steam_price - sp_price

                parsed = parse_market_hash_name(mh)

                row = {
                    "base_name": parsed["base_name"],
                    "wear": parsed["wear"],
                    "stattrak": bool(parsed["is_stattrak"]),
                    "market_hash_name": mh,
                    "steam_usd": steam_price,
                    "skinport_min_usd": sp_price,
                    "spread_usd": spread,
                }
                if show_debug:
                    row["match_debug"] = reason

                rows_out.append(row)

                prog.progress(i / len(watch), text=f"{i}/{len(watch)} checked")
                if delay:
                    time.sleep(float(delay))

            prog.empty()

            df = pd.DataFrame(rows_out)

            # Sort by spread desc (None last)
            if "spread_usd" in df.columns:
                df["spread_sort"] = df["spread_usd"].apply(lambda x: x if isinstance(x, (int, float)) else -10**9)
                df = df.sort_values("spread_sort", ascending=False).drop(columns=["spread_sort"])

            # Summary
            c1, c2, c3 = st.columns(3)
            c1.metric("Checked", len(df))
            c2.metric("Steam OK", int(df["steam_usd"].apply(lambda x: isinstance(x, (int, float))).sum()))
            c3.metric("Skinport OK", int(df["skinport_min_usd"].apply(lambda x: isinstance(x, (int, float))).sum()))

            st.dataframe(df, use_container_width=True, hide_index=True)

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download results (CSV)",
                data=csv,
                file_name="cs2_price_check.csv",
                mime="text/csv",
            )

# Footer
st.divider()
st.caption("Minimal UI • Cached Skinport (1h) • Cached Steam search (10m)")
