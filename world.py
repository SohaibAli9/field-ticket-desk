"""The test week: one invented hauler, its dispatch log, the disposal wells' receipt logs, and the
tickets its drivers wrote by hand.

Real and public: the wells, their API numbers, operators and the disposal wells (NM OCD registry).
Invented: the hauler, drivers, trucks, rates, volumes, dispatch log and every ticket.

`truth.json` is the answer key: exactly what is written on each ticket, written here before any
reader runs. `planted` names the problem planted on each ticket, so the scorecard can show
which check caught it. Clean tickets have no entry.
"""
from __future__ import annotations

import csv
import json
import math
import random
from datetime import date, datetime, timedelta

import yaml

import wells
from wells import DATA

OUT = DATA / "world"
SEED = 1119

HAULER = {
    "name": "Ninemile Draw Water Hauling, LLC",
    "address": "PO Box 2210, Carlsbad, NM 88221",
    "phone": "(575) 555-0148",
}
# driver, handwriting font, truck, trailer, trailer capacity in barrels
CREW = [
    ("Ray Baca", "Caveat.ttf", "T-12", "TR-112", 130),
    ("Jesse Torres", "HomemadeApple-Regular.ttf", "T-14", "TR-114", 130),
    ("Mike Whitfield", "ReenieBeanie.ttf", "T-21", "TR-121", 130),
    ("Dale Nez", "NanumPenScript-Regular.ttf", "T-33", "TR-133", 130),
    ("Cody Ortega", "Caveat.ttf", "T-41", "TR-141", 120),
    ("Tim Hollis", "ReenieBeanie.ttf", "T-07", "VAC-07", 80),
]
CUSTOMERS = {
    "EQV Operating NM LLC": {"short": "EQV Operating", "lease": "BURCH KEELY UNIT"},
    "Spur Energy Partners LLC": {"short": "Spur Energy", "lease": "KOOL AID STATE"},
    "RILEY PERMIAN OPERATING COMPANY, LLC": {"short": "Riley Permian", "lease": "STALEY STATE"},
}
# Invented rate sheet: two customers pay by the barrel, one by the hour with a minimum.
RATES = {
    "EQV Operating": {"basis": "per_bbl", "rate": 1.15},
    "Spur Energy": {"basis": "per_bbl", "rate": 1.05},
    "Riley Permian": {"basis": "hourly", "rate": 98.00, "min_hours": 2.0},
}
WEEK = [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]
CONFUSABLE = {"1": "7", "7": "1", "3": "8", "8": "3", "5": "6", "6": "5", "4": "9", "9": "4", "0": "6"}


def dist(a: wells.Well, b: wells.Well) -> float:
    """Miles between two wells (flat-earth is fine at county scale)."""
    dy = (a.lat - b.lat) * 69.0
    dx = (a.lon - b.lon) * 69.0 * math.cos(math.radians(a.lat))
    return math.hypot(dx, dy)


def pick_wells(rng: random.Random) -> dict[str, list[wells.Well]]:
    reg = wells.registry()
    out = {}
    for op, c in CUSTOMERS.items():
        ws = sorted((w for w in reg.values() if w.operator == op and w.status == "Active" and w.type == "Oil"
                     and w.county == "Eddy" and w.name.startswith(c["lease"])), key=lambda w: w.api)
        out[c["short"]] = rng.sample(ws, 4)
    return out


def pick_swds(all_wells: list[wells.Well]) -> list[wells.Well]:
    reg = wells.registry()
    lat = sum(w.lat for w in all_wells) / len(all_wells)
    lon = sum(w.lon for w in all_wells) / len(all_wells)
    centre = wells.Well("", "", "", "", "", "", "", lat, lon)
    swds = [w for w in reg.values() if w.type == "Salt Water Disposal" and w.status == "Active"
            and w.county == "Eddy" and "SWD" in w.name]
    return sorted(swds, key=lambda w: dist(centre, w))[:3]


def misread_twin(api: str) -> tuple[str, wells.Well] | None:
    """A real Eddy well whose API differs from `api` by one easily confused handwritten digit."""
    reg = wells.registry()
    for i in range(7, len(api)):
        ch = api[i]
        if ch in CONFUSABLE:
            alt = api[:i] + CONFUSABLE[ch] + api[i + 1:]
            if alt in reg and wells.norm_name(reg[alt].name).split("#")[0] != wells.norm_name(reg[api].name).split("#")[0]:
                return alt, reg[alt]
    return None


def lease_label(w: wells.Well) -> str:
    """How a driver writes the well: 'Burch Keely Unit #533' from 'BURCH KEELY UNIT #533'."""
    base, _, num = w.name.partition("#")
    words = " ".join(x if any(ch.isdigit() for ch in x) or x in ("SWD", "NW", "SE", "NE", "SW") or len(x) <= 2
                     else x.capitalize() for x in base.split())
    return f"{words} #{num.lstrip('0') or '0'}"


def hours_between(t_in: str, t_out: str) -> float:
    a = datetime.strptime(t_in, "%H:%M")
    b = datetime.strptime(t_out, "%H:%M")
    return round(((b - a).seconds / 3600) * 2) / 2


def build() -> dict:
    rng = random.Random(SEED)
    by_cust = pick_wells(rng)
    swds = pick_swds([w for ws in by_cust.values() for w in ws])
    custs = list(by_cust)

    # 22 dispatched loads over the week; tickets come back for 20 of them.
    loads = []
    swd_seq = {s.api: 20400 + 300 * i for i, s in enumerate(swds)}
    for n in range(22):
        day = WEEK[n % 5]
        cust = custs[n % 3]
        well = by_cust[cust][(n // 3) % 4]
        driver, font, truck, trailer, cap = CREW[n % len(CREW)]
        swd = min(swds, key=lambda s: dist(s, well))
        start = 6 * 60 + rng.randrange(0, 9 * 60, 15)
        t_in = f"{start // 60:02d}:{start % 60:02d}"
        dur = rng.choice([90, 105, 120, 150, 165, 180, 210])
        end = start + dur
        t_out = f"{end // 60:02d}:{end % 60:02d}"
        bbls = min(cap, rng.choice([cap, cap, cap - 5, cap - 10, cap - 20]))
        swd_seq[swd.api] += rng.randrange(3, 40)
        loads.append({
            "load_id": f"D-{1101 + n}", "date": day.isoformat(), "customer": cust, "well": well, "api": well.api,
            "driver": driver, "font": font, "truck": truck, "trailer": trailer, "capacity": cap, "swd": swd,
            "swd_ticket": str(swd_seq[swd.api]), "t_in": t_in, "t_out": t_out, "bbls": bbls,
        })

    tickets = []
    planted: dict[str, str] = {}
    lost = {"D-1108", "D-1117"}  # dispatched, hauled, received at the SWD; no ticket ever reached the office
    ticket_no = 10412
    for ld in loads:
        if ld["load_id"] in lost:
            continue
        cust = ld["customer"]
        rate = RATES[cust]
        hours = hours_between(ld["t_in"], ld["t_out"])
        t = {
            "ticket_no": str(ticket_no), "date": datetime.fromisoformat(ld["date"]).strftime("%m/%d/%y"),
            "customer": cust, "lease_well": lease_label(ld["well"]), "api": ld["api"], "county": "Eddy",
            "truck": ld["truck"], "trailer": ld["trailer"], "driver": ld["driver"], "load_type": "Produced Water",
            "time_in": ld["t_in"], "time_out": ld["t_out"], "swd": lease_label(ld["swd"]),
            "swd_ticket": ld["swd_ticket"], "bbls": ld["bbls"],
            "rate": rate["rate"], "hours": hours if rate["basis"] == "hourly" else None,
            "driver_signed": True, "customer_signed": True,
        }
        t["total"] = round(t["rate"] * (max(hours, rate["min_hours"]) if rate["basis"] == "hourly" else t["bbls"]), 2)
        t["_load"] = ld["load_id"]
        t["_font"] = ld["font"]
        tickets.append(t)
        ticket_no += 1

    def tk(load_id):
        return next(t for t in tickets if t["_load"] == load_id)

    # Planted problems, one per ticket. What the driver wrote is the truth; the records disagree.
    t = tk("D-1102"); t["bbls"] = 130; t["total"] = round(130 * t["rate"], 2) if t["hours"] is None else t["total"]
    planted[t["ticket_no"]] = "volume: ticket says 130 bbl, disposal well metered fewer"
    [l for l in loads if l["load_id"] == "D-1102"][0]["swd_bbls"] = 112

    t = tk("D-1104"); t["truck"] = "T-41" if t["truck"] != "T-41" else "T-14"
    planted[t["ticket_no"]] = "truck: ticket truck differs from dispatch"

    t = tk("D-1106")
    twin = None
    for ld in loads:  # find a load whose API has a real look-alike well one digit away
        twin = misread_twin(ld["api"])
        if twin and ld["load_id"] not in lost and tk(ld["load_id"])["ticket_no"] not in planted:
            t = tk(ld["load_id"])
            break
    t["api"] = twin[0]
    planted[t["ticket_no"]] = f"api: one digit off; {twin[0]} is a real well ({twin[1].name}, {twin[1].operator}, {twin[1].status})"

    t = tk("D-1110")
    other = [c for c in custs if c != t["customer"]][0]
    real_op = t["customer"]
    t["customer"] = other  # well belongs to another operator; billed to the wrong company
    planted[t["ticket_no"]] = f"bill-to: well's operator of record is {real_op}, ticket bills {other}"

    t = tk("D-1112")
    if RATES[t["customer"]]["basis"] == "per_bbl":
        t["rate"] = round(t["rate"] + 0.15, 2); t["total"] = round(t["rate"] * t["bbls"], 2)
    else:
        t["rate"] = 110.0; t["total"] = round(t["rate"] * max(t["hours"], 2.0), 2)
    planted[t["ticket_no"]] = "rate: ticket rate is not the customer's rate sheet"

    t = tk("D-1113"); t["total"] = round(t["total"] + 10, 2)
    planted[t["ticket_no"]] = "math: total does not equal quantity times rate"

    t = tk("D-1115"); t["bbls"] = 145
    if t["hours"] is None:
        t["total"] = round(145 * t["rate"], 2)
    planted[t["ticket_no"]] = f"capacity: 145 bbl on a {[l for l in loads if l['load_id']=='D-1115'][0]['capacity']} bbl trailer"
    [l for l in loads if l["load_id"] == "D-1115"][0]["swd_bbls"] = 145

    t = tk("D-1119"); t["customer_signed"] = False
    planted[t["ticket_no"]] = "signature: no customer signature"

    t = tk("D-1120"); t["time_in"], t["time_out"] = t["time_out"], t["time_in"]
    planted[t["ticket_no"]] = "time: delivered before pickup (times swapped)"

    # One ticket reaches the office twice: the driver's phone photo and the office scan.
    dup_of = tk("D-1116")["ticket_no"]

    return {"loads": loads, "tickets": tickets, "planted": planted, "lost": sorted(lost), "dup_of": dup_of,
            "swds": swds, "wells": by_cust}


def write(world: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    loads = world["loads"]
    with open(OUT / "dispatch.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["load_id", "date", "customer", "lease_well", "api", "truck", "trailer", "driver", "swd", "dispatched_at"])
        for ld in loads:
            w.writerow([ld["load_id"], ld["date"], ld["customer"], lease_label(ld["well"]), ld["api"], ld["truck"],
                        ld["trailer"], ld["driver"], lease_label(ld["swd"]), ld["t_in"]])
    with open(OUT / "swd_receipts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["swd", "swd_api", "swd_ticket", "date", "truck", "bbls_received", "time_in"])
        for ld in loads:
            arrive = (datetime.strptime(ld["t_in"], "%H:%M") + timedelta(minutes=55)).strftime("%H:%M")
            w.writerow([lease_label(ld["swd"]), ld["swd"].api, ld["swd_ticket"], ld["date"], ld["truck"],
                        ld.get("swd_bbls", ld["bbls"]), arrive])
    rates = {c: {k: v for k, v in r.items()} for c, r in RATES.items()}
    (OUT / "rate_sheet.yaml").write_text(yaml.safe_dump(
        {"customers": rates, "operator_of_record": {v["short"]: k for k, v in CUSTOMERS.items()},
         "trailers": {c[3]: c[4] for c in CREW}, "trucks": {c[2]: c[3] for c in CREW}}, sort_keys=False))
    clean = [{k: v for k, v in t.items() if not k.startswith("_")} for t in world["tickets"]]
    meta = [{"ticket_no": t["ticket_no"], "font": t["_font"], "load": t["_load"]} for t in world["tickets"]]
    (OUT / "truth.json").write_text(json.dumps({"tickets": clean, "planted": world["planted"], "lost": world["lost"],
                                                "dup_of": world["dup_of"], "render": meta}, indent=1))
    (OUT / "hauler.json").write_text(json.dumps(HAULER, indent=1))


if __name__ == "__main__":
    wd = build()
    write(wd)
    print(len(wd["tickets"]), "tickets;", len(wd["loads"]), "loads; lost", wd["lost"], "; dup", wd["dup_of"])
    for k, v in wd["planted"].items():
        print(k, v)
    print("SWDs:", [(s.name, s.operator) for s in wd["swds"]])
