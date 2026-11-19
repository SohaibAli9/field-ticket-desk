"""New Mexico OCD well registry: the state's public list of every permitted well.

Source: NM EMNRD Oil Conservation Division, "New Mexico OCD Oil and Gas Wells"
(ocd-hub-nm-emnrd.hub.arcgis.com, item 387f397ebf164c7aa6a752aac7d22b17), CSV download.
The statewide file is about 80 MB; we keep Eddy and Lea counties, where Carlsbad haulers work.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(os.environ.get("TICKETS_DATA", Path(__file__).resolve().parent / "data"))
STATEWIDE = DATA / "ocd" / "wells.csv"
EXTRACT = DATA / "ocd" / "wells_eddy_lea.csv"
COUNTY_PREFIX = {"Eddy": "30-015", "Lea": "30-025"}
API_RE = re.compile(r"^30-0(15|25)-\d{5}$")


@dataclass(frozen=True)
class Well:
    api: str
    name: str
    type: str
    status: str
    operator: str
    county: str
    ulstr: str
    lat: float
    lon: float


def build_extract() -> None:
    keep = ["id", "name", "type", "status", "ogrid_name", "county", "ulstr", "latitude", "longitude"]
    with open(STATEWIDE, encoding="utf-8-sig") as f, open(EXTRACT, "w", newline="") as g:
        w = csv.writer(g)
        w.writerow(keep)
        for row in csv.DictReader(f):
            if row["county"] in COUNTY_PREFIX:
                w.writerow([row[k].strip() for k in keep])


@lru_cache(maxsize=1)
def registry() -> dict[str, Well]:
    if not EXTRACT.exists():
        build_extract()
    out = {}
    with open(EXTRACT) as f:
        for r in csv.DictReader(f):
            out[r["id"]] = Well(r["id"], r["name"], r["type"], r["status"], r["ogrid_name"], r["county"],
                                r["ulstr"], float(r["latitude"] or 0), float(r["longitude"] or 0))
    return out


def norm_name(s: str) -> str:
    """'Burch Keely Unit # 533' and 'BURCH KEELY UNIT #533' compare equal; well numbers lose leading zeros."""
    s = s.upper().replace("#", " # ")
    s = re.sub(r"[^A-Z0-9# ]", " ", s)
    s = re.sub(r"\b0+(\d)", r"\1", s)
    return " ".join(s.split()).replace("# ", "#")


def norm_api(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if len(digits) >= 10:
        digits = digits[:10]
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}"
    return (s or "").strip()
