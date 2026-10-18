"""Turn raw ProtonDB report JSON into small, typed Report objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

FAULT_KEYS = (
    "audioFaults", "graphicalFaults", "inputFaults", "performanceFaults",
    "saveGameFaults", "stabilityFaults", "windowingFaults",
)
_GE = re.compile(r"GE-?Proton[-_ ]?(\d+)[-.](\d+)", re.I)
_LEGACY_GE = re.compile(r"Proton-(\d+)\.(\d+)-GE-(\d+)", re.I)
_VERSION = re.compile(r"^\s*(?:Proton\s*)?(\d+)\.(\d+)")
_DECK_GPU = re.compile(r"Custom GPU 0405|Custom GPU 0932|vangogh|sephiroth", re.I)


@dataclass(frozen=True)
class Report:
    id: str
    timestamp: int
    hw: str  # "deck" | "pc" | "other"
    gpu_vendor: str  # "nvidia" | "amd" | "intel" | "unknown"
    works: bool
    tried_oob: bool
    oob_works: bool | None
    family: str  # "official" | "experimental" | "ge" | "native" | "other" | "unknown"
    version: tuple[int, int] | None  # official: (major, minor); GE: (major, build)
    tool_label: str
    launch_options: str
    faults: int
    significant_bugs: bool
    note: str

    @property
    def oob_failed(self) -> bool:
        """The reporter tried it without changes first, and that didn't work."""
        return self.tried_oob and self.oob_works is False


def _gpu_vendor(gpu: str, hw: str) -> str:
    g = gpu.lower()
    if hw == "deck":
        return "amd"
    if any(k in g for k in ("nvidia", "geforce", "rtx", "gtx", "quadro")):
        return "nvidia"
    if any(k in g for k in ("amd", "radeon", "radv", "ati ")):
        return "amd"
    if "intel" in g:
        return "intel"
    return "unknown"


def _ge(text: str) -> tuple[int, int] | None:
    m = _GE.search(text or "")
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _LEGACY_GE.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _official(text: str) -> tuple[int, int] | None:
    m = _VERSION.match(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def classify_tool(resp: dict) -> tuple[str, tuple[int, int] | None, str]:
    variant = resp.get("variant")
    proton = str(resp.get("protonVersion") or "")
    custom = str(resp.get("customProtonVersion") or "")
    if variant == "native":
        return "native", None, "Native Linux version"
    if variant == "experimental":
        return "experimental", None, "Proton Experimental"
    if variant == "ge":
        v = _ge(custom)
        return "ge", v, custom or "GE-Proton"
    if variant == "notListed":
        v = _ge(custom)
        if v:
            return "ge", v, custom
        return "other", None, custom or "custom Proton build"
    if variant in ("official", "older"):
        v = _official(proton)
        return ("official", v, f"Proton {proton}") if v else ("unknown", None, proton or "?")
    # reports from before ProtonDB's 2022 questionnaire have no variant
    if proton:
        v = _ge(proton)
        if v:
            return "ge", v, proton
        if "experimental" in proton.lower():
            return "experimental", None, "Proton Experimental"
        v = _official(proton)
        if v:
            return "official", v, f"Proton {proton}"
    return "unknown", None, proton or "?"


def _note(resp: dict) -> str:
    notes = resp.get("notes")
    parts = []
    if isinstance(notes, dict):
        for key in ("verdict", "extra", "concludingNotes"):
            v = notes.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
                break
    elif isinstance(notes, str) and notes.strip():
        parts.append(notes.strip())
    if not parts and isinstance(resp.get("concludingNotes"), str):
        parts.append(resp["concludingNotes"].strip())
    text = " ".join(parts)
    return text[:400]


def parse_report(raw: dict) -> Report | None:
    try:
        resp = raw.get("responses") or {}
        verdict = resp.get("verdict")
        if verdict not in ("yes", "no"):
            return None
        device = raw.get("device") or {}
        steam = ((device.get("inferred") or {}).get("steam")) or {}
        gpu = str(steam.get("gpu") or "")
        hw_type = device.get("hardwareType")
        if hw_type == "steamDeck":
            hw = "deck"
        elif hw_type in ("pc", None, ""):
            hw = "deck" if (_DECK_GPU.search(gpu) or "steamos holo" in str(steam.get("os", "")).lower()) else "pc"
        else:
            hw = "other"
        family, version, label = classify_tool(resp)
        lo = resp.get("launchOptions")
        return Report(
            id=str(raw.get("id") or ""),
            timestamp=int(raw.get("timestamp") or 0),
            hw=hw,
            gpu_vendor=_gpu_vendor(gpu, hw),
            works=verdict == "yes",
            tried_oob=resp.get("triedOob") == "yes",
            oob_works={"yes": True, "no": False}.get(resp.get("verdictOob")),
            family=family,
            version=version,
            tool_label=label,
            launch_options=lo.strip() if isinstance(lo, str) else "",
            faults=sum(1 for k in FAULT_KEYS if resp.get(k) == "yes"),
            significant_bugs=resp.get("significantBugs") == "yes",
            note=_note(resp),
        )
    except (TypeError, ValueError, AttributeError):
        return None


def parse_reports(raws) -> list[Report]:
    out = []
    for raw in raws or []:
        if isinstance(raw, dict):
            r = parse_report(raw)
            if r is not None:
                out.append(r)
    return out


_KEEP_RESPONSES = (
    "verdict", "verdictOob", "triedOob", "variant", "protonVersion", "customProtonVersion", "launchOptions",
    "significantBugs", "notes", "concludingNotes", *FAULT_KEYS,
)


def trim(raw: dict) -> dict:
    """Only the fields autoproton uses, so the cache stays small. No names or avatars."""
    resp = raw.get("responses") or {}
    device = raw.get("device") or {}
    steam = ((device.get("inferred") or {}).get("steam")) or {}
    return {
        "id": raw.get("id"),
        "timestamp": raw.get("timestamp"),
        "device": {
            "hardwareType": device.get("hardwareType"),
            "inferred": {"steam": {k: steam.get(k) for k in ("gpu", "os") if steam.get(k)}},
        },
        "responses": {k: resp[k] for k in _KEEP_RESPONSES if k in resp},
    }
