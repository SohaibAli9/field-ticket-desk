"""Turn a written value into a comparable one, field by field. Shared by the checks and the scorer,
so a value the scorer calls right is the same value the checks see."""
from __future__ import annotations

import re
from datetime import datetime

from wells import norm_api, norm_name

NUMERIC = {"bbls", "rate", "total", "hours"}
BOOL = {"driver_signed", "customer_signed"}


def num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def hhmm(v: str) -> str:
    m = re.match(r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})", v or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else (v or "").strip()


def mdy(v: str) -> str:
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%m-%d-%y", "%m-%d-%Y"):
        try:
            return datetime.strptime((v or "").strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return (v or "").strip()


def code(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def words(v: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", str(v or "").upper()).split())


def norm(field: str, v):
    if field in NUMERIC:
        return num(v)
    if field in BOOL:
        return bool(v)
    if field in ("time_in", "time_out"):
        return hhmm(v)
    if field == "date":
        return mdy(v)
    if field == "api":
        return norm_api(v)
    if field in ("lease_well", "swd"):
        return norm_name(v or "")
    if field in ("truck", "trailer", "ticket_no", "swd_ticket"):
        return code(v)
    return words(v)


def same(field: str, a, b) -> bool:
    x, y = norm(field, a), norm(field, b)
    if field in NUMERIC:
        return (x is None and y is None) or (x is not None and y is not None and abs(x - y) < 0.005)
    return x == y
