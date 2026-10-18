"""The file name ProtonDB uses for a game's full list of reports.

ProtonDB's website loads every report for a game from
``/data/reports/<device>/app/<key>.json``. The key is a hash of the app id,
the page and the totals in ``/data/counts.json``. This mirrors the site's own
JavaScript; when the site changes it, this is the one place to update.
"""

from __future__ import annotations

import math


def _js_number(x: float) -> str:
    if math.isnan(x):
        return "NaN"
    if float(x).is_integer() and abs(x) < 1e21:
        return str(int(x))
    return repr(x)


def _part(e, t, n) -> str:
    """JavaScript: `${t}p${e * (t % n)}`, with JS number semantics."""
    try:
        value = float(e) * math.fmod(float(t), float(n))
    except ValueError:
        value = float("nan")
    return f"{t}p{_js_number(value)}"


def _string_hash(s: str) -> int:
    """abs() of Java's 32-bit String.hashCode."""
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h)


def report_key(appid: int, reports: int | str, timestamp: int | str, page: str | int = "all") -> int:
    reports, timestamp = str(reports), str(timestamp)
    return _string_hash(
        "p" + _part(appid, reports, timestamp) + "*vRT" + _part(page, str(appid), timestamp) + "undefined" + "m"
    )
