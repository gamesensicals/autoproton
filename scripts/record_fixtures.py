"""Save trimmed real ProtonDB data for the tests (developer tool; needs internet).

    python scripts/record_fixtures.py 1091500 570 ...

The files are an extract of ProtonDB data, which is available under the
Open Database License (ODbL) — see tests/fixtures/protondb/README.md.
"""

import gzip
import json
import sys
import time
from pathlib import Path

from autoproton.cache import Cache
from autoproton.net import Net
from autoproton.protondb.client import ProtonDB

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "protondb"
MAX_AGE = 3 * 365 * 86400


def main(appids):
    db = ProtonDB(Net(), Cache(Path("/nonexistent-cache")))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "counts.json").write_text(json.dumps(db.counts(), indent=1))
    now = time.time()
    for appid in appids:
        reports = db.reports(int(appid), refresh=True).reports
        recent = [r for r in reports if now - (r.get("timestamp") or 0) < MAX_AGE]
        summary = db.net.get_json(f"https://www.protondb.com/api/v1/reports/summaries/{appid}.json")
        data = {"recorded": int(now), "summary": summary, "reports": recent}
        (OUT / f"game-{appid}.json.gz").write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
        print(appid, len(reports), "->", len(recent))


if __name__ == "__main__":
    main(sys.argv[1:])
