"""Load the week's ticket photos into Postgres. `--hold IMG_x.jpg` leaves one out to upload live."""
from __future__ import annotations

import json
import sys

from wells import DATA

from . import db, service


def main(hold: list[str]) -> None:
    """Wipe and reload; tickets not in the sample set (public uploads) are dropped."""
    db.init(reset=True)
    manifest = json.loads((DATA / "tickets" / "manifest.json").read_text())
    for m in manifest:
        if m["image"] not in hold:
            service.ingest(m["image"], m["source"])
    service.refresh_lost()
    service.build_invoices()
    with db.connect() as c:
        print({r["state"]: r["n"] for r in c.execute("select state, count(*) n from tickets group by state")})


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[args.index("--hold") + 1:] if "--hold" in args else [])
