import gzip
import json

import pytest
from conftest import FIXTURES
from fakenet import FakeNet

from autoproton.cache import Cache
from autoproton.net import NetError
from autoproton.protondb import client as pdb
from autoproton.protondb.keys import report_key
from autoproton.protondb.parse import classify_tool, parse_report, parse_reports, trim

COUNTS = json.loads((FIXTURES / "protondb/counts.json").read_text())


def load_game(appid):
    return json.loads(gzip.decompress((FIXTURES / f"protondb/game-{appid}.json.gz").read_bytes()))


def test_report_key_matches_protondb_site():
    # values checked against protondb.com during development
    assert report_key(1091500, 456539, 1792167747) == 1211714814
    assert report_key(1091500, "456539", "1792167747", page=1) == 1021335941


def reports_url(appid, counts=COUNTS):
    return f"{pdb.BASE}/data/reports/all-devices/app/{report_key(appid, counts['reports'], counts['timestamp'])}.json"


def make_client(tmp_path, routes, t=None):
    clock = (lambda: t[0]) if t else (lambda: 1_800_000_000.0)
    return pdb.ProtonDB(FakeNet(routes), Cache(tmp_path / "cache", clock=clock), clock=clock)


def test_fetches_reports_with_key_and_caches(tmp_path):
    game = load_game(570)
    db = make_client(tmp_path, {
        f"{pdb.BASE}/data/counts.json": COUNTS,
        reports_url(570): {"reports": game["reports"], "total": len(game["reports"])},
    })
    got = db.reports(570)
    assert len(got.reports) == len(game["reports"]) and not got.stale
    assert db.reports(570).reports == got.reports
    assert db.net.calls.count(reports_url(570)) == 1


def test_404_refreshes_counts_and_retries(tmp_path):
    newer = {**COUNTS, "reports": COUNTS["reports"] + 5, "timestamp": COUNTS["timestamp"] + 60}
    answers = iter([COUNTS, newer])
    db = make_client(tmp_path, {
        f"{pdb.BASE}/data/counts.json": lambda: next(answers),
        reports_url(570, newer): {"reports": [], "total": 0},
    })
    assert db.reports(570).reports == []
    assert db.net.calls[-1] == reports_url(570, newer)


def test_game_without_reports_is_empty_not_an_error(tmp_path):
    db = make_client(tmp_path, {f"{pdb.BASE}/data/counts.json": COUNTS})
    assert db.reports(12345).reports == []
    assert db.summary(12345) is None


def test_changed_format_is_reported_clearly(tmp_path):
    db = make_client(tmp_path, {
        f"{pdb.BASE}/data/counts.json": COUNTS,
        f"{pdb.BASE}/api/v1/reports/summaries/570.json": {"tier": "gold", "total": 300},
    })
    with pytest.raises(pdb.ProtonDBFormatError):
        db.reports(570)
    db2 = make_client(tmp_path / "b", {
        f"{pdb.BASE}/data/counts.json": COUNTS,
        reports_url(570): "<html>cloudflare</html>",
    })
    with pytest.raises(pdb.ProtonDBFormatError):
        db2.reports(570)


def test_stale_cache_used_when_offline(tmp_path):
    t = [1_800_000_000.0]
    routes = {f"{pdb.BASE}/data/counts.json": COUNTS, reports_url(570): {"reports": [{"id": "a"}], "total": 1}}
    db = make_client(tmp_path, routes, t)
    db.reports(570)
    t[0] += 3 * 86400
    db.net.routes = {f"{pdb.BASE}/data/counts.json": NetError("offline")}
    got = db.reports(570)
    assert got.stale and got.reports[0]["id"] == "a"


def test_offline_without_cache_raises_friendly_error(tmp_path):
    db = make_client(tmp_path, {f"{pdb.BASE}/data/counts.json": NetError("offline")})
    with pytest.raises(pdb.ProtonDBError, match="Couldn't reach ProtonDB"):
        db.reports(570)


def test_summary(tmp_path):
    db = make_client(tmp_path, {f"{pdb.BASE}/api/v1/reports/summaries/570.json": load_game(570)["summary"]})
    s = db.summary(570)
    assert s.tier in ("platinum", "gold", "silver", "bronze", "borked") and s.total > 0


@pytest.mark.parametrize("resp,expected", [
    ({"variant": "official", "protonVersion": "10.0-3"}, ("official", (10, 0))),
    ({"variant": "older", "protonVersion": "6.3-8"}, ("official", (6, 3))),
    ({"variant": "ge", "customProtonVersion": "GE-Proton10-34"}, ("ge", (10, 34))),
    ({"variant": "notListed", "customProtonVersion": "GE-Proton9-20"}, ("ge", (9, 20))),
    ({"variant": "notListed", "customProtonVersion": "proton-cachyos-10.0"}, ("other", None)),
    ({"variant": "experimental"}, ("experimental", None)),
    ({"variant": "native"}, ("native", None)),
    ({"protonVersion": "Proton-6.21-GE-2"}, ("ge", (6, 21))),
    ({"protonVersion": "5.13-6"}, ("official", (5, 13))),
    ({}, ("unknown", None)),
])
def test_tool_classification(resp, expected):
    fam, ver, _label = classify_tool(resp)
    assert (fam, ver) == expected


def test_parse_report_fields():
    raw = {"id": "x", "timestamp": 100, "device": {"hardwareType": "pc", "inferred": {"steam": {"gpu": "NVIDIA GeForce RTX 4090"}}},
           "responses": {"verdict": "yes", "triedOob": "yes", "verdictOob": "no", "variant": "official",
                         "protonVersion": "10.0-3", "launchOptions": " gamemoderun %command% ", "audioFaults": "yes",
                         "notes": {"verdict": "Works after tweaks"}}}
    r = parse_report(raw)
    assert r.works and r.oob_failed and r.gpu_vendor == "nvidia" and r.hw == "pc"
    assert r.launch_options == "gamemoderun %command%" and r.faults == 1 and r.note == "Works after tweaks"
    assert parse_report({"responses": {"type": "tinker"}}) is None


def test_deck_inferred_from_gpu():
    r = parse_report({"timestamp": 1, "device": {"inferred": {"steam": {"gpu": "AMD Custom GPU 0405 (vangogh)"}}},
                      "responses": {"verdict": "yes", "variant": "native"}})
    assert r.hw == "deck"


def test_trim_drops_personal_data():
    raw = {"id": "a", "timestamp": 1, "contributor": {"steam": {"nickname": "someone"}},
           "device": {"hardwareType": "pc", "inferred": {"steam": {"gpu": "x", "cpu": "y", "kernel": "z"}}},
           "responses": {"verdict": "yes", "answerToWhatGame": "1"}}
    t = trim(raw)
    assert "contributor" not in t and t["device"]["inferred"]["steam"] == {"gpu": "x"}
    assert t["responses"] == {"verdict": "yes"}


def test_fixture_reports_parse():
    for appid in (1091500, 570):
        reps = parse_reports(load_game(appid)["reports"])
        assert len(reps) > 100
