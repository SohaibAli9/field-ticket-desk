"""Checks on a ticket after it has been read. The model reads; these decide.

Each check is a plain function: ticket in, list of Issue out. They need the hauler's own records
(dispatch log, disposal-well receipts, rate sheet, fleet list) and the state's well registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from rapidfuzz import fuzz, process

import wells
from fields import code, hhmm, mdy, norm, num, words


@dataclass
class Issue:
    check: str
    field: str
    message: str
    suggestion: str = ""


@dataclass
class Books:
    """The hauler's records the checks compare a ticket against."""
    dispatch: list[dict]
    receipts: dict[str, dict]           # swd_ticket -> receipt row
    rates: dict[str, dict]              # customer -> {basis, rate, min_hours}
    operator_of_record: dict[str, str]  # customer short name -> OCD operator name
    trailers: dict[str, int]            # trailer -> capacity bbl
    trucks: dict[str, str]              # truck -> trailer it pulls
    drivers: list[str]
    swds: list[str]
    registry: dict[str, wells.Well] = field(default_factory=dict)
    # Registry names that belong to the same customer after mergers, e.g. ConocoPhillips wells still filed under
    # COG OPERATING LLC and MARATHON OIL PERMIAN LLC. customer -> extra registry operator names.
    operator_aliases: dict[str, list[str]] = field(default_factory=dict)

    def operators_for(self, customer: str) -> set[str]:
        main = self.operator_of_record.get(customer, "")
        return ({main} if main else set()) | set(self.operator_aliases.get(customer, []))

    def customer_for(self, operator: str) -> str:
        return next((c for c in self.operator_of_record if operator in self.operators_for(c)), operator)


REQUIRED = ["ticket_no", "date", "customer", "lease_well", "api", "truck", "driver", "time_in", "time_out", "swd",
            "swd_ticket", "bbls", "rate", "total"]
API_RE = re.compile(r"^30-(\d{3})-\d{5}$")
PREFIX = {"EDDY": "015", "LEA": "025"}


def lease_base(name: str) -> tuple[str, str]:
    """('BURCH KEELY UNIT', '61') from 'Burch Keely Unit #061'."""
    n = wells.norm_name(name)
    base, _, num_ = n.partition("#")
    return base.strip(), num_.strip()


def required(t: dict, b: Books) -> list[Issue]:
    out = [Issue("required", f, f"{f.replace('_', ' ')} is blank") for f in REQUIRED if str(t.get(f) or "").strip() == ""]
    if not t.get("customer_signed"):
        out.append(Issue("required", "customer_signed", "no customer signature; operators reject unsigned tickets"))
    return out


def api_number(t: dict, b: Books) -> list[Issue]:
    api = wells.norm_api(t.get("api", ""))
    m = API_RE.match(api)
    if not m:
        return [Issue("api", "api", f"API number '{t.get('api')}' is not in the 30-0XX-XXXXX form")]
    county = words(t.get("county", ""))
    if county in PREFIX and m.group(1) != PREFIX[county]:
        return [Issue("api", "api", f"API {api} is not a {county.title()} County number (those start 30-{PREFIX[county]})")]
    return []


def find_by_lease(lease: str, operator: str, reg: dict[str, wells.Well]) -> wells.Well | None:
    base, n = lease_base(lease)
    hits = [w for w in reg.values() if lease_base(w.name) == (base, n)]
    by_op = [w for w in hits if w.operator == operator]
    return (by_op or hits or [None])[0]


def registry(t: dict, b: Books) -> list[Issue]:
    """The API number must be a real well, and it must be the well named on the ticket, run by the customer billed."""
    api = wells.norm_api(t.get("api", ""))
    op = b.operator_of_record.get(t.get("customer", ""), "")
    expected = find_by_lease(t.get("lease_well", ""), op, b.registry)
    hint = f"the registry lists {expected.name.title()} as {expected.api}" if expected else ""
    w = b.registry.get(api)
    if w is None:
        return [Issue("registry", "api", f"API {api} is not in the NM OCD well registry", hint)]
    out = []
    if lease_base(w.name) != lease_base(t.get("lease_well", "")):
        out.append(Issue("registry", "api", f"API {api} is {w.name} ({w.operator}, {w.status}), not "
                         f"{t.get('lease_well')}", hint))
        return out
    if op and w.operator not in b.operators_for(t.get("customer", "")):
        out.append(Issue("registry", "customer", f"{w.name} is operated by {w.operator}; ticket bills "
                         f"{t.get('customer')} ({op})", f"bill {b.customer_for(w.operator)}"))
    if not w.status.startswith("Active"):
        out.append(Issue("registry", "api", f"{w.name} is '{w.status}' in the registry"))
    return out


def match_dispatch(t: dict, b: Books) -> dict | None:
    """The dispatched load this ticket belongs to: same day and same well, then driver or truck breaks ties."""
    day = mdy(t.get("date", ""))
    lease = lease_base(t.get("lease_well", ""))
    best, best_score = None, 0
    for d in b.dispatch:
        if d["date"] != day:
            continue
        s = 3 * (lease_base(d["lease_well"]) == lease) + 2 * (words(d["driver"]) == words(t.get("driver", ""))) \
            + (code(d["truck"]) == code(t.get("truck", ""))) + (code(d["customer"]) == code(t.get("customer", "")))
        if s > best_score:
            best, best_score = d, s
    return best if best_score >= 3 else None


def dispatch(t: dict, b: Books) -> list[Issue]:
    d = match_dispatch(t, b)
    if d is None:
        return [Issue("dispatch", "date", "no dispatched load matches this ticket's date, well and driver")]
    out = []
    for f in ("truck", "driver", "customer"):
        if norm(f, t.get(f)) != norm(f, d[f]):
            out.append(Issue("dispatch", f, f"ticket {f} {t.get(f)} but dispatch {d['load_id']} sent {d[f]}"))
    if lease_base(t.get("swd", "")) != lease_base(d["swd"]):
        out.append(Issue("dispatch", "swd", f"ticket SWD {t.get('swd')} but dispatch sent {d['swd']}"))
    return out


def disposal(t: dict, b: Books, tolerance_bbl: float = 2.0) -> list[Issue]:
    r = b.receipts.get(code(t.get("swd_ticket", "")))
    if r is None:
        return [Issue("disposal", "swd_ticket", f"no receipt {t.get('swd_ticket')} in the disposal wells' logs")]
    out = []
    bbls, got = num(t.get("bbls")), num(r["bbls_received"])
    if bbls is not None and got is not None and abs(bbls - got) > tolerance_bbl:
        out.append(Issue("disposal", "bbls", f"ticket says {bbls:g} bbl; {r['swd']} metered {got:g} bbl on "
                         f"receipt {r['swd_ticket']}", f"bill {got:g} bbl"))
    if code(r["truck"]) != code(t.get("truck", "")):
        out.append(Issue("disposal", "truck", f"receipt {r['swd_ticket']} was for truck {r['truck']}, ticket says {t.get('truck')}"))
    if r["date"] != mdy(t.get("date", "")):
        out.append(Issue("disposal", "date", f"receipt {r['swd_ticket']} is dated {r['date']}"))
    return out


def capacity(t: dict, b: Books) -> list[Issue]:
    cap = b.trailers.get(next((k for k in b.trailers if code(k) == code(t.get("trailer", ""))), ""), None)
    bbls = num(t.get("bbls"))
    if cap and bbls and bbls > cap:
        return [Issue("capacity", "bbls", f"{bbls:g} bbl on {t.get('trailer')}, which holds {cap} bbl")]
    return []


def billing(t: dict, b: Books) -> list[Issue]:
    sheet = b.rates.get(t.get("customer", ""))
    if not sheet:
        return [Issue("billing", "customer", f"no rate sheet for {t.get('customer')}")]
    out = []
    rate = num(t.get("rate"))
    if rate is not None and abs(rate - sheet["rate"]) > 0.005:
        unit = "bbl" if sheet["basis"] == "per_bbl" else "hr"
        out.append(Issue("billing", "rate", f"ticket rate {rate:.2f}; {t.get('customer')}'s sheet says "
                         f"{sheet['rate']:.2f}/{unit}", f"bill at {sheet['rate']:.2f}"))
    if sheet["basis"] == "per_bbl":
        qty = num(t.get("bbls")) or 0
    else:
        qty = max(num(t.get("hours")) or worked_hours(t) or 0, sheet.get("min_hours", 0))
    expect = round((rate or 0) * qty, 2)
    total = num(t.get("total"))
    if total is not None and abs(total - expect) > 0.01:
        out.append(Issue("billing", "total", f"total {total:,.2f} but {qty:g} x {rate:.2f} = {expect:,.2f}"))
    return out


def worked_hours(t: dict) -> float | None:
    try:
        a = datetime.strptime(hhmm(t.get("time_in", "")), "%H:%M")
        z = datetime.strptime(hhmm(t.get("time_out", "")), "%H:%M")
    except ValueError:
        return None
    return (z - a).total_seconds() / 3600


def times(t: dict, b: Books) -> list[Issue]:
    h = worked_hours(t)
    if h is None:
        return []
    if h <= 0:
        return [Issue("times", "time_out", f"delivered {t.get('time_out')} is before pickup {t.get('time_in')}")]
    written = num(t.get("hours"))
    if written is not None and abs(written - round(h * 2) / 2) > 0.26:
        return [Issue("times", "hours", f"{written:g} hours written; {t.get('time_in')} to {t.get('time_out')} is {h:.2f}")]
    return []


CHECKS = [required, api_number, registry, dispatch, disposal, capacity, billing, times]


def run_all(t: dict, b: Books) -> list[Issue]:
    return [i for c in CHECKS for i in c(t, b)]


# ---- closed lists: fleet, drivers, customers, disposal wells ------------------------------------------------------

def snap(value: str, options: list[str], field_: str, cutoff: float = 75) -> tuple[str | None, float]:
    """The single list entry this reading is closest to, or None when two entries are close or none is."""
    if not value:
        return None, 0
    key = (lambda s: code(s)) if field_ in ("truck", "trailer") else (lambda s: words(s))
    choices = {o: key(o) for o in options}
    ranked = process.extract(key(value), choices, scorer=fuzz.ratio, limit=2)
    if not ranked or ranked[0][1] < cutoff:
        return None, ranked[0][1] if ranked else 0
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 10:
        return None, ranked[0][1]
    return ranked[0][2], ranked[0][1]


def lists(b: Books) -> dict[str, list[str]]:
    return {"truck": list(b.trucks), "trailer": list(b.trailers), "driver": b.drivers, "customer": list(b.rates),
            "swd": b.swds, "load_type": ["Produced Water", "Flowback", "Fresh Water", "Brine"], "county": ["Eddy", "Lea"]}


def find_duplicates(tickets: list[dict]) -> dict[int, int]:
    """Index of a later copy -> index of the first copy with the same ticket number."""
    seen, out = {}, {}
    for i, t in enumerate(tickets):
        k = code(t.get("ticket_no", ""))
        if k in seen:
            out[i] = seen[k]
        else:
            seen[k] = i
    return out


def find_lost(tickets: list[dict], b: Books) -> list[dict]:
    """Dispatched loads that reached a disposal well but never came back as a ticket."""
    matched = {d["load_id"] for t in tickets if (d := match_dispatch(t, b))}
    return [d for d in b.dispatch if d["load_id"] not in matched]
