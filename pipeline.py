"""Merge two readings per ticket, settle what code can settle, queue the rest for a person, then check.

A field is accepted without a person only when
  - both readings agree and the value is on its list (fleet, drivers, customers, disposal wells), or
  - both readings point to the same single list entry (truck 'J-14' and 'T-14' -> T-14), or
  - the readings disagree and exactly one of them satisfies the arithmetic or the well registry.
Everything else goes to the review queue with both readings shown.
"""
from __future__ import annotations

import csv
import json
from collections import Counter

import yaml

import checks
import wells
from fields import code, same
from read import FIELDS, READERS, cache_path
from wells import DATA

W = DATA / "world"
OUT = DATA / "run"
PLANT_CHECK = {"volume": ("disposal", "bbls"), "truck": ("dispatch", "truck"), "api": ("registry", "api"),
               "bill-to": ("registry", "customer"), "rate": ("billing", "rate"), "math": ("billing", "total"),
               "capacity": ("capacity", "bbls"), "signature": ("required", "customer_signed"),
               "time": ("times", "time_out")}


def books() -> checks.Books:
    sheet = yaml.safe_load((W / "rate_sheet.yaml").read_text())
    dispatch = list(csv.DictReader(open(W / "dispatch.csv")))
    receipts = {code(r["swd_ticket"]): r for r in csv.DictReader(open(W / "swd_receipts.csv"))}
    return checks.Books(
        dispatch=dispatch, receipts=receipts, rates=sheet["customers"], operator_of_record=sheet["operator_of_record"],
        trailers=sheet["trailers"], trucks=sheet["trucks"], drivers=sorted({d["driver"] for d in dispatch}),
        swds=sorted({d["swd"] for d in dispatch}), registry=wells.registry())


def merge(a: dict, b: dict, bk: checks.Books) -> tuple[dict, dict]:
    """Returns (values, status) where status[f] = {'state': auto|review, 'how': ..., 'a':..., 'b':...}."""
    closed = checks.lists(bk)
    vals, status = {}, {}
    hard = set(a.get("hard_to_read", [])) & set(b.get("hard_to_read", []))
    for f in FIELDS:
        va, vb = a.get(f), b.get(f)
        st = {"a": va, "b": vb}
        if f in closed:
            on_list = [o for o in closed[f] if same(f, o, va)]
            sa, _ = checks.snap(str(va or ""), closed[f], f)
            sb, _ = checks.snap(str(vb or ""), closed[f], f)
            if same(f, va, vb) and on_list:
                vals[f], st["state"], st["how"] = on_list[0], "auto", "both readings agree, on the list"
            elif sa and sa == sb:
                vals[f], st["state"] = sa, "auto"
                st["how"] = f"both readings match {sa} on the {f} list" + ("" if same(f, va, vb) else " (readings differed)")
            else:
                vals[f], st["state"], st["how"] = sa or sb or va, "review", "not settled by the list"
        elif same(f, va, vb) and f not in hard:
            vals[f], st["state"], st["how"] = va, "auto", "both readings agree"
        else:
            vals[f], st["state"], st["how"] = va, "review", "readings differ" if not same(f, va, vb) else "both marked it hard to read"
        status[f] = st
    # Second pass: a disagreement that only one reading can satisfy is settled by code, with the reason kept.
    for f in ("total", "rate", "bbls", "hours", "api"):
        st = status[f]
        if st["state"] != "review" or same(f, st["a"], st["b"]):
            continue
        fits = []
        for v in (st["a"], st["b"]):
            t = dict(vals, **{f: v})
            bad = [i for i in (checks.registry(t, bk) if f == "api" else checks.billing(t, bk)) if i.field == f]
            if not bad:
                fits.append(v)
        if len(fits) == 1:
            vals[f], st["state"] = fits[0], "auto"
            st["how"] = "readings differ; only one fits the well registry" if f == "api" else \
                "readings differ; only one fits quantity x rate"
    return vals, status


def run() -> dict:
    bk = books()
    truth = json.loads((W / "truth.json").read_text())
    key = {t["ticket_no"]: t for t in truth["tickets"]}
    manifest = json.loads((DATA / "tickets" / "manifest.json").read_text())
    results = []
    for m in manifest:
        ra = json.loads(cache_path("a", m["image"]).read_text())
        rb = json.loads(cache_path("b", m["image"]).read_text())
        vals, status = merge(ra, rb, bk)
        tk = key[m["ticket_no"]]
        # The reviewer: in this demo the answer key stands in for the person who looks at queued fields.
        resolved = {f: (tk.get(f) if status[f]["state"] == "review" else vals[f]) for f in FIELDS}
        for f in FIELDS:
            status[f]["right"] = same(f, tk.get(f), vals[f])
        results.append({"image": m["image"], "source": m["source"], "ticket_no": tk["ticket_no"], "values": vals,
                        "resolved": resolved, "status": status, "cost": ra["_meta"]["cost"] + rb["_meta"]["cost"],
                        "seconds": max(ra["_meta"]["seconds"], rb["_meta"]["seconds"])})

    dups = checks.find_duplicates([r["resolved"] for r in results])
    for i, r in enumerate(results):
        r["duplicate_of"] = results[dups[i]]["image"] if i in dups else None
        r["issues"] = [] if r["duplicate_of"] else [i_.__dict__ for i_ in checks.run_all(r["resolved"], bk)]
        r["load"] = (checks.match_dispatch(r["resolved"], bk) or {}).get("load_id")
    unique = [r["resolved"] for r in results if not r["duplicate_of"]]
    lost = checks.find_lost(unique, bk)
    claimed = {code(r["resolved"]["swd_ticket"]) for r in results}
    for d in lost:
        rc = [x for x in bk.receipts.values() if x["date"] == d["date"] and code(x["truck"]) == code(d["truck"])
              and code(x["swd_ticket"]) not in claimed]
        d["receipt"] = rc[0] if rc else None
        sheet = bk.rates[d["customer"]]
        d["value_at_risk"] = round(sheet["rate"] * float(rc[0]["bbls_received"]), 2) if rc and sheet["basis"] == "per_bbl" else \
            round(sheet["rate"] * sheet.get("min_hours", 0), 2)

    summary = score(results, truth, lost)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"results": results, "lost": lost, "summary": summary}, indent=1, default=str))
    return summary


def score(results: list[dict], truth: dict, lost: list[dict]) -> dict:
    n = len(results) * len(FIELDS)
    auto = [(r["image"], f, s) for r in results for f, s in r["status"].items() if s["state"] == "auto"]
    review = [(r["image"], f, s) for r in results for f, s in r["status"].items() if s["state"] == "review"]
    auto_wrong = [(im, f, s["a"], s["b"]) for im, f, s in auto if not s["right"]]
    first = {}
    key = {t["ticket_no"]: t for t in truth["tickets"]}
    for rd in READERS:
        right = sum(same(f, key[r["ticket_no"]].get(f), r["status"][f][rd]) for r in results for f in FIELDS)
        first[READERS[rd]] = right
    caught, missed = {}, {}
    for tno, desc in truth["planted"].items():
        kind = desc.split(":")[0]
        want = PLANT_CHECK[kind]
        r = next(r for r in results if r["ticket_no"] == tno and not r["duplicate_of"])
        hit = [i for i in r["issues"] if (i["check"], i["field"]) == want]
        (caught if hit else missed)[tno] = {"planted": desc, "flag": hit[0]["message"] if hit else None}
    clean_flags = [(r["ticket_no"], i["message"]) for r in results if r["ticket_no"] not in truth["planted"]
                   and not r["duplicate_of"] for i in r["issues"]]
    dup_caught = any(r["duplicate_of"] and r["ticket_no"] == truth["dup_of"] for r in results)
    lost_caught = sorted(d["load_id"] for d in lost) == sorted(truth["lost"])
    return {
        "images": len(results), "tickets": len({r["ticket_no"] for r in results}), "fields": n,
        "first_read_right": first, "auto": len(auto), "review": len(review), "auto_wrong": auto_wrong,
        "review_by_field": Counter(f for _, f, _ in review),
        "review_items": [(im, f, s["how"], s["a"], s["b"]) for im, f, s in review],
        "settled_by_code": [(im, f, s["how"], s["a"], s["b"]) for im, f, s in auto
                            if "differ" in s["how"] or "match" in s["how"]],
        "planted_caught": caught, "planted_missed": missed, "clean_ticket_flags": clean_flags,
        "duplicate_caught": dup_caught, "lost_caught": lost_caught,
        "lost": [(d["load_id"], d["date"], d["customer"], d["lease_well"], d["truck"], d["value_at_risk"]) for d in lost],
        "cost": round(sum(r["cost"] for r in results), 4), "max_seconds": max(r["seconds"] for r in results),
    }


if __name__ == "__main__":
    s = run()
    print(json.dumps(s, indent=1, default=str))
