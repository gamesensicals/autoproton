"""Choose a Proton version and launch options from a game's ProtonDB reports.

Everything here is a pure function of its inputs (reports, hardware,
installed tools, current settings, the time), so it is easy to test.

How it decides:

1. **Which reports count.** Reports from the same kind of device (Steam Deck
   or PC) are used when there are enough; otherwise all reports are used, with
   the other kind weighted down. Newer reports count more (half-life about
   9 months), reports from the same GPU vendor count a little more, and
   reports listing problems count less.
2. **Proton version.** Reports are grouped into native / official Proton /
   Proton Experimental / GE-Proton. Each group gets the lower bound of a
   confidence interval on its weighted success rate (Wilson score), so a
   handful of glowing reports can't beat hundreds of good ones. Near-ties go
   to the least disruptive choice: what you already use, then native, then
   official Proton, then Experimental, then GE-Proton.
3. **Launch options.** Every piece of every working report's launch options is
   counted. Fixes and game options used by enough reporters are ticked by
   default; personal extras (overlays, HDR, resolution) are offered unticked;
   anything unsafe or for another GPU vendor is left out.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from ..models import CompatSetting, CompatTool, GameSettings, HardwareProfile
from ..protondb.parse import Report
from ..steam import compat
from . import knowledge
from .launchopts import Component, assemble, merge, parse

DAY = 86400.0
FAMILY_ORDER = ("native", "official", "experimental", "ge")
FAMILY_LABELS = {
    "native": "Native Linux version (no Proton)",
    "official": "Proton",
    "experimental": "Proton Experimental",
    "ge": "GE-Proton",
}


@dataclass(frozen=True)
class Params:
    half_life_days: float = 270.0
    max_age_days: float = 1460.0
    min_same_hw_reports: int = 8
    min_same_hw_weight: float = 2.0
    deck_user_pc_factor: float = 0.35
    pc_user_deck_factor: float = 0.5
    vendor_match: float = 1.25
    vendor_mismatch: float = 0.8
    fault_penalty: float = 0.1
    min_quality: float = 0.4
    significant_bug_factor: float = 0.7
    wilson_z: float = 1.28
    family_min_reports: int = 2
    family_min_weight: float = 0.3
    tie_margin: float = 0.03
    keep_rate_margin: float = 0.05
    keep_min_reports: int = 5
    version_min_reports: int = 3
    option_default_share: float = 0.15
    option_default_count: int = 3
    unknown_env_share: float = 0.30
    unknown_env_count: int = 5
    option_suggest_share: float = 0.05
    option_suggest_count: int = 2
    oob_failed_boost: float = 1.5


@dataclass
class FamilyScore:
    family: str
    score: float
    rate: float  # weighted success rate, before the confidence adjustment
    reports: int
    working: int
    weight: float
    newest: int
    versions: dict = field(default_factory=dict)  # version -> (score, reports)

    @property
    def success_rate(self) -> float:
        return self.working / self.reports if self.reports else 0.0


@dataclass(frozen=True)
class ToolChoice:
    action: str  # "set" | "clear" | "keep"
    family: str | None
    setting: CompatSetting | None
    label: str
    detail: str = ""
    needs_download: bool = False  # GE-Proton isn't installed yet
    ge_major: int | None = None

    def resulting(self, current: CompatSetting | None) -> CompatSetting | None:
        if self.action == "set":
            # a GE-Proton that still has to be downloaded can't be set yet
            return self.setting if self.setting is not None else current
        if self.action == "clear":
            return None
        return current


@dataclass
class OptionSuggestion:
    component: Component
    category: str
    share: float
    count: int
    failing: int
    default_on: bool
    available: bool
    hidden: bool
    reason: str
    alternatives: list[tuple[str, int]] = field(default_factory=list)  # other values seen for this variable
    vendor: str | None = None

    @property
    def description(self) -> str:
        return knowledge.describe(self.component.key)

    @property
    def percent(self) -> str:
        """The share as a whole percentage, rounded down so it never looks over the tick threshold."""
        return f"{math.floor(self.share * 100 + 1e-9)}%"


@dataclass
class Recommendation:
    appid: int
    hw_class: str
    pool_description: str
    total_reports: int
    used_reports: int
    working_reports: int
    newest: int
    confidence: str  # "high" | "medium" | "low" | "none"
    families: list[FamilyScore]
    tool: ToolChoice
    alternatives: list[ToolChoice]
    options: list[OptionSuggestion]
    notes: list[tuple[int, str, str]]  # (timestamp, tool label, note)
    warnings: list[str]

    def default_components(self) -> list[Component]:
        return [o.component for o in self.options if o.default_on]

    def launch_options(self, current: str | None, components: list[Component] | None = None,
                       replace: bool = False) -> str | None:
        """The launch options to save. None means "leave the key out"."""
        chosen = self.default_components() if components is None else components
        text = assemble(chosen) if replace else merge(current, chosen)
        if not text:
            return None if replace or current is None else current
        return text

    def settings(self, current: GameSettings, tool: ToolChoice | None = None, components: list[Component] | None = None,
                 replace: bool = False) -> GameSettings:
        tool = tool or self.tool
        return GameSettings(self.launch_options(current.launch_options, components, replace),
                            tool.resulting(current.compat_tool))


def wilson_lower_bound(p: float, n: float, z: float) -> float:
    if n <= 0:
        return 0.0
    p = min(max(p, 0.0), 1.0)
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def _weights(reports: list[Report], hw: HardwareProfile, now: float, params: Params) -> tuple[list[tuple[Report, float]], str]:
    fresh = [r for r in reports if r.family != "unknown" and (now - r.timestamp) <= params.max_age_days * DAY]

    def decay(r: Report) -> float:
        age = max(0.0, now - r.timestamp) / DAY
        return 0.5 ** (age / params.half_life_days)

    device = "Steam Deck" if hw.is_deck else "PC"
    same = [r for r in fresh if r.hw == hw.hw_class]
    if len(same) >= params.min_same_hw_reports and sum(decay(r) for r in same) >= params.min_same_hw_weight:
        pool = [(r, decay(r)) for r in same]
        desc = f"Based on {len(same)} {device} reports"
    else:
        other_factor = params.deck_user_pc_factor if hw.is_deck else params.pc_user_deck_factor
        pool = [(r, decay(r) * (1.0 if r.hw == hw.hw_class else other_factor)) for r in fresh]
        desc = (f"Based on {len(fresh)} reports from all devices "
                f"(only {len(same)} from a {device}, so the rest count for less)")
    if not hw.is_deck and hw.primary_vendor != "unknown":
        adjusted = []
        for r, w in pool:
            if r.hw == "pc" and r.gpu_vendor != "unknown":
                w *= params.vendor_match if r.gpu_vendor == hw.primary_vendor else params.vendor_mismatch
            adjusted.append((r, w))
        pool = adjusted
    return pool, desc


def _quality(r: Report, params: Params) -> float:
    q = max(params.min_quality, 1 - params.fault_penalty * r.faults)
    return q * (params.significant_bug_factor if r.significant_bugs else 1.0)


def _score(items: list[tuple[Report, float]], params: Params) -> tuple[float, float, float]:
    n = sum(w for _, w in items)
    good = sum(w * _quality(r, params) for r, w in items if r.works)
    rate = good / n if n else 0.0
    return wilson_lower_bound(rate, n, params.wilson_z), n, rate


def score_families(pool: list[tuple[Report, float]], params: Params) -> list[FamilyScore]:
    groups: dict[str, list[tuple[Report, float]]] = defaultdict(list)
    for r, w in pool:
        if r.family in FAMILY_ORDER:
            groups[r.family].append((r, w))
    out = []
    for fam, items in groups.items():
        score, n, rate = _score(items, params)
        versions: dict = {}
        by_version: dict = defaultdict(list)
        for r, w in items:
            if r.version is not None:
                key = r.version if fam == "official" else (r.version[0],)
                by_version[key].append((r, w))
        for v, vitems in by_version.items():
            versions[v] = (_score(vitems, params)[0], len(vitems))
        out.append(FamilyScore(
            fam, score, rate, len(items), sum(1 for r, _ in items if r.works), n,
            max((r.timestamp for r, _ in items), default=0), versions,
        ))
    return sorted(out, key=lambda f: f.score, reverse=True)


def _eligible(f: FamilyScore, params: Params) -> bool:
    return f.reports >= params.family_min_reports and f.weight >= params.family_min_weight


def current_family(current: CompatSetting | None, tools: list[CompatTool]) -> str | None:
    if current is None:
        return None
    if current.name == compat.EXPERIMENTAL:
        return "experimental"
    if compat.ge_version(current.name):
        return "ge"
    if current.name.startswith("proton_"):
        return "official"
    return "other"


def pick_family(families: list[FamilyScore], current: str | None, params: Params) -> FamilyScore | None:
    eligible = [f for f in families if _eligible(f, params)]
    if not eligible:
        return None
    top = eligible[0]
    order = ([current] if current else []) + list(FAMILY_ORDER)

    def close_enough(f: FamilyScore) -> bool:
        if f.score >= top.score - params.tie_margin:
            return True
        # don't move someone off what they use, or off the native version, when it works about as well
        return (f.family in (current, "native") and f.reports >= params.keep_min_reports
                and f.rate >= top.rate - params.keep_rate_margin)

    near = [f for f in eligible if close_enough(f)]

    def rank(f: FamilyScore) -> int:
        return order.index(f.family) if f.family in order else len(order)

    return min(near, key=rank)


def _pick_version(f: FamilyScore, params: Params, newer_wins_ties: bool = True):
    if not f.versions:
        return None
    best = max(s for s, _ in f.versions.values())
    good = [v for v, (s, n) in f.versions.items() if n >= params.version_min_reports and s >= best - params.tie_margin]
    if good:
        return max(good)
    return max(f.versions, key=lambda v: (f.versions[v][0], v))


def resolve_tool(f: FamilyScore, current: CompatSetting | None, tools: list[CompatTool], params: Params) -> ToolChoice:
    installed = {t.internal_name: t for t in tools}
    stats = f"{f.working} of {f.reports} reports work"
    if f.family == "native":
        if current is None:
            return ToolChoice("keep", "native", None, FAMILY_LABELS["native"], f"Runs natively; {stats}.")
        return ToolChoice("clear", "native", None, FAMILY_LABELS["native"],
                          f"Stop forcing {current.name} so the native Linux version runs; {stats}.")
    if f.family == "experimental":
        setting = CompatSetting(compat.EXPERIMENTAL)
        label = "Proton Experimental"
    elif f.family == "official":
        v = _pick_version(f, params)
        if v is None:
            return ToolChoice("keep", "official", None, "Proton", stats)
        name = compat.official_name(v[0], v[1])
        setting, label = CompatSetting(name), compat.display_name(name)
        if name not in installed:
            stats += f"; Steam downloads {label} the first time the game starts"
    else:  # ge
        v = _pick_version(f, params)
        major = v[0] if v else 0
        tool = compat.newest_ge(tools, major)
        if tool is None:
            return ToolChoice(
                "set", "ge", None, f"GE-Proton (latest{', ' + str(major) + '.x or newer' if major else ''})",
                f"{stats}. Not installed yet: autoproton can download it from GitHub.",
                needs_download=True, ge_major=major or None,
            )
        setting, label = CompatSetting(tool.internal_name), tool.display_name
    if current is not None and current.name == setting.name:
        return ToolChoice("keep", f.family, current, label, f"Already set; {stats}.")
    if current is not None and f.family == "ge":
        cur_v = compat.ge_version(current.name)
        if cur_v and setting.name in installed and cur_v >= (installed[setting.name].ge_version or (0,)):
            return ToolChoice("keep", "ge", current, current.name, f"Already on a recent GE-Proton; {stats}.")
    return ToolChoice("set", f.family, setting, label, stats + ".")


def _option_pool(pool: list[tuple[Report, float]], family: str | None) -> list[tuple[Report, float]]:
    if family == "native":
        return [(r, w) for r, w in pool if r.family == "native"]
    return [(r, w) for r, w in pool if r.family != "native"]


def suggest_options(pool: list[tuple[Report, float]], family: str | None, hw: HardwareProfile,
                    which: Callable[[str], str | None], params: Params) -> list[OptionSuggestion]:
    reports = _option_pool(pool, family)
    working = [(r, w * (params.oob_failed_boost if r.oob_failed else 1.0)) for r, w in reports if r.works]
    total = sum(w for _, w in working)
    if total <= 0:
        return []
    votes: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    values: dict = defaultdict(lambda: defaultdict(float))
    value_counts: dict = defaultdict(lambda: defaultdict(int))
    examples: dict = {}
    for r, w in working:
        seen = set()
        for c in parse(r.launch_options, strict=True).components:
            k = c.vote_key
            values[k][c.value or ""] += w
            value_counts[k][c.value or ""] += 1
            examples.setdefault((k, c.value or ""), c)
            if k in seen:
                continue
            seen.add(k)
            votes[k] += w
            counts[k] += 1
    failing: dict = defaultdict(int)
    for r, _ in reports:
        if not r.works:
            for k in {c.vote_key for c in parse(r.launch_options, strict=True).components}:
                failing[k] += 1

    vendors = set(hw.gpu_vendors) | ({hw.primary_vendor} if hw.primary_vendor != "unknown" else set())
    out = []
    for k, weight in votes.items():
        best_value = max(values[k], key=lambda v: values[k][v])
        comp = examples[(k, best_value)]
        share, count = weight / total, counts[k]
        vendor = knowledge.env_vendor(comp.key) if comp.kind == "env" else None
        if comp.kind == "env":
            category = knowledge.env_category(comp.key, comp.value)
        elif comp.kind == "wrapper":
            category = knowledge.WRAPPERS.get(comp.key, "preference")
        else:
            category = knowledge.arg_category(comp.key, comp.value)

        available, hidden, reason = True, False, ""
        if comp.kind == "wrapper" and not which(comp.key):
            available, reason = False, f"{comp.key} isn't installed on this computer."
        if vendor and vendors and vendor not in vendors:
            hidden, reason = True, f"Only for {vendor.upper() if vendor == 'amd' else vendor.capitalize()} GPUs."
        if category in ("obsolete", "debug"):
            hidden = True
            reason = reason or ("No longer needed on current Proton." if category == "obsolete" else "Only for debugging.")
        if share < params.option_suggest_share or count < params.option_suggest_count:
            hidden = True
            reason = reason or "Used by too few reports."

        default_on = False
        if available and not hidden and failing[k] < count:
            if category in ("fix", "arg"):
                default_on = share >= params.option_default_share and count >= params.option_default_count
            elif category == "unknown":
                default_on = share >= params.unknown_env_share and count >= params.unknown_env_count
        if not default_on and not reason:
            if category in ("preference", "situational"):
                reason = "Personal preference; tick it if you want it." if category == "preference" else \
                    "Depends on your screen or setup."
            else:
                reason = "Some reports use it; not enough to tick it for you."
        alts = sorted(((v, value_counts[k][v]) for v in values[k] if v != best_value), key=lambda x: -x[1])
        out.append(OptionSuggestion(comp, category, share, count, failing[k], default_on, available, hidden,
                                    reason, alts[:5], vendor))
    out.sort(key=lambda o: (not o.default_on, o.hidden, -o.share))
    return out


def recommend(
    appid: int,
    reports: list[Report],
    hw: HardwareProfile,
    tools: list[CompatTool],
    current: GameSettings,
    now: float,
    which: Callable[[str], str | None],
    params: Params = Params(),
) -> Recommendation:
    pool, desc = _weights(reports, hw, now, params)
    families = score_families(pool, params)
    cur_family = current_family(current.compat_tool, tools)
    winner = pick_family(families, cur_family, params)
    warnings: list[str] = []
    working = [r for r, _ in pool if r.works]

    if winner is None:
        tool = ToolChoice("keep", None, current.compat_tool, "No change",
                          "Not enough recent ProtonDB reports to recommend a Proton version.")
        alternatives: list[ToolChoice] = []
        confidence = "none"
        warnings.append("No ProtonDB reports for this game yet." if not reports else
                        "Not enough recent reports to make a confident recommendation.")
    else:
        tool = resolve_tool(winner, current.compat_tool, tools, params)
        alternatives = []
        for f in families:
            if _eligible(f, params):
                alt = resolve_tool(f, current.compat_tool, tools, params)
                if alt.action != "keep" or f.family == winner.family:
                    alternatives.append(alt)
        if winner.weight >= 10 and winner.reports >= 15 and winner.score >= 0.75:
            confidence = "high"
        elif winner.reports >= 5 and winner.weight >= 2:
            confidence = "medium"
        else:
            confidence = "low"
            warnings.append("Only a few recent reports; treat this as a best guess.")
        if winner.score < 0.4:
            warnings.append("Most recent reports say this game doesn't run well right now.")

    options = suggest_options(pool, winner.family if winner else None, hw, which, params) if pool else []
    notes = sorted(((r.timestamp, r.tool_label, r.note) for r, _ in pool if r.works and r.note), reverse=True)[:3]
    return Recommendation(
        appid=appid, hw_class=hw.hw_class, pool_description=desc, total_reports=len(reports),
        used_reports=len(pool), working_reports=len(working), newest=max((r.timestamp for r, _ in pool), default=0),
        confidence=confidence, families=families, tool=tool, alternatives=alternatives, options=options,
        notes=notes, warnings=warnings,
    )
