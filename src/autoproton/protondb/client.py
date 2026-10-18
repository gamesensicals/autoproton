"""Fetch a game's ProtonDB summary and every report for it, with caching."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..cache import Cache
from ..net import HttpError, Net, NetError
from .keys import report_key
from .parse import trim

BASE = "https://www.protondb.com"
COUNTS_TTL = 3600
SUMMARY_TTL = 24 * 3600
REPORTS_TTL = 24 * 3600


class ProtonDBError(RuntimeError):
    """Message is meant for the user."""


class ProtonDBFormatError(ProtonDBError):
    def __init__(self):
        super().__init__(
            "ProtonDB changed how it serves reports, so autoproton can't read them right now. "
            "The rating still shows; check for an autoproton update."
        )


@dataclass(frozen=True)
class Summary:
    tier: str
    score: float
    confidence: str
    total: int
    trending_tier: str
    best_tier: str

    @classmethod
    def from_json(cls, d: dict) -> Summary:
        return cls(
            str(d.get("tier") or "pending"), float(d.get("score") or 0), str(d.get("confidence") or ""),
            int(d.get("total") or 0), str(d.get("trendingTier") or ""), str(d.get("bestReportedTier") or ""),
        )


@dataclass(frozen=True)
class ReportSet:
    reports: list[dict]
    fetched_at: float
    stale: bool = False  # True when ProtonDB failed and these came from an older cache


def game_url(appid: int) -> str:
    return f"{BASE}/app/{appid}"


class ProtonDB:
    def __init__(self, net: Net, cache: Cache, clock: Callable[[], float] = time.time):
        self.net = net
        self.cache = cache
        self.clock = clock

    def counts(self, refresh: bool = False) -> dict:
        if not refresh:
            hit = self.cache.get("counts", COUNTS_TTL)
            if hit:
                return hit
        try:
            data = self.net.get_json(f"{BASE}/data/counts.json")
        except NetError as e:
            raise ProtonDBError(f"Couldn't reach ProtonDB: {e}") from e
        if not isinstance(data, dict) or "reports" not in data or "timestamp" not in data:
            raise ProtonDBFormatError()
        self.cache.put("counts", data)
        return data

    def summary(self, appid: int, refresh: bool = False) -> Summary | None:
        """The game's rating, or None if ProtonDB has no reports for it."""
        key = f"summary-{appid}"
        if not refresh:
            hit = self.cache.get(key, SUMMARY_TTL)
            if hit is not None:
                return Summary.from_json(hit) if hit else None
        try:
            data = self.net.get_json(f"{BASE}/api/v1/reports/summaries/{appid}.json")
        except HttpError as e:
            if e.status == 404:
                self.cache.put(key, {})
                return None
            raise ProtonDBError(f"ProtonDB answered with an error ({e.status}).") from e
        except NetError as e:
            stale = self.cache.get_stale(key)
            if stale is not None:
                return Summary.from_json(stale[0]) if stale[0] else None
            raise ProtonDBError(f"Couldn't reach ProtonDB: {e}") from e
        if not isinstance(data, dict):
            raise ProtonDBFormatError()
        self.cache.put(key, data)
        return Summary.from_json(data)

    def _fetch_reports(self, appid: int, counts: dict) -> list[dict] | None:
        key = report_key(appid, counts["reports"], counts["timestamp"])
        try:
            data = self.net.get_json(f"{BASE}/data/reports/all-devices/app/{key}.json")
        except HttpError as e:
            if e.status == 404:
                return None
            raise
        if not isinstance(data, dict) or not isinstance(data.get("reports"), list):
            raise ProtonDBFormatError()
        return [trim(r) for r in data["reports"] if isinstance(r, dict)]

    def reports(self, appid: int, refresh: bool = False) -> ReportSet:
        key = f"reports-{appid}"
        if not refresh:
            hit = self.cache.get_stale(key)
            if hit is not None and hit[1] <= REPORTS_TTL:
                return ReportSet(hit[0], self.clock() - hit[1])
        try:
            counts = self.counts()
            found = self._fetch_reports(appid, counts)
            if found is None:
                # the counts may have moved on since we cached them; try once more with fresh ones
                fresh = self.counts(refresh=True)
                if (fresh["reports"], fresh["timestamp"]) != (counts["reports"], counts["timestamp"]):
                    found = self._fetch_reports(appid, fresh)
            if found is None:
                summary = self.summary(appid)
                if summary is None or summary.total == 0:
                    found = []
                else:
                    raise ProtonDBFormatError()
        except (ProtonDBError, NetError) as e:
            stale = self.cache.get_stale(key)
            if stale is not None:
                return ReportSet(stale[0], self.clock() - stale[1], stale=True)
            if isinstance(e, ProtonDBError):
                raise
            raise ProtonDBError(f"Couldn't reach ProtonDB: {e}") from e
        self.cache.put(key, found)
        return ReportSet(found, self.clock())
