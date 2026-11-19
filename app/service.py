"""What happens to a ticket: read twice, merge, check, store; a person settles queued fields; invoices
are built from tickets that are clean."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checks  # noqa: E402
import wells  # noqa: E402
from fields import code, mdy, num  # noqa: E402
from invoice import QBO_CUSTOMER, QBO_ITEM, ItemDetail, Line, QboInvoice  # noqa: E402
from pipeline import books, merge  # noqa: E402
from read import FIELDS, read_one  # noqa: E402

from . import db  # noqa: E402

BOOKS = books()
LABELS = {"ticket_no": "Ticket no.", "date": "Date", "customer": "Customer", "lease_well": "Lease / well",
          "api": "API no.", "county": "County", "truck": "Truck", "trailer": "Trailer", "driver": "Driver",
          "load_type": "Load type", "time_in": "Time in", "time_out": "Time out", "hours": "Hours", "swd": "Disposal (SWD)",
          "swd_ticket": "SWD ticket", "bbls": "Barrels", "rate": "Rate", "total": "Total $",
          "driver_signed": "Driver signed", "customer_signed": "Customer signed"}


def state_of(status: dict, issues: list, duplicate_of) -> str:
    if duplicate_of:
        return "duplicate"
    if any(s["state"] == "review" for s in status.values()):
        return "review"
    return "held" if issues else "ready"


def ingest(image: str, source: str) -> int:
    a, b = read_one("a", image), read_one("b", image)
    values, status = merge(a, b, BOOKS)
    with db.connect() as c:
        dup = c.execute("select image from tickets where ticket_no=%s and duplicate_of is null and image<>%s",
                        (values["ticket_no"], image)).fetchone()
        issues = [] if dup else [i.__dict__ for i in checks.run_all(values, BOOKS)]
        load = (checks.match_dispatch(values, BOOKS) or {}).get("load_id")
        row = c.execute(
            """insert into tickets (image, source, ticket_no, customer, load_id, values, field_status, issues, duplicate_of,
               state, read_cost) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (image) do update set values=excluded.values, field_status=excluded.field_status,
               issues=excluded.issues, state=excluded.state returning id""",
            (image, source, values["ticket_no"], values["customer"], load, db.j(values), db.j(status), db.j(issues),
             dup["image"] if dup else None, state_of(status, issues, dup), a["_meta"]["cost"] + b["_meta"]["cost"])).fetchone()
    return row["id"]


def resolve(ticket_id: int, field: str, value: str, reviewer: str) -> None:
    with db.connect() as c:
        t = c.execute("select * from tickets where id=%s", (ticket_id,)).fetchone()
        values, status = t["values"], t["field_status"]
        st = status[field]
        values[field] = value
        st.update(state="person", how=f"set by {reviewer}")
        issues = [i.__dict__ for i in checks.run_all(values, BOOKS)]
        load = (checks.match_dispatch(values, BOOKS) or {}).get("load_id")
        c.execute("insert into review_log (ticket_id, field, chosen, reading_a, reading_b, reviewer) values (%s,%s,%s,%s,%s,%s)",
                  (ticket_id, field, value, str(st.get("a")), str(st.get("b")), reviewer))
        c.execute("update tickets set values=%s, field_status=%s, issues=%s, state=%s, load_id=%s, customer=%s where id=%s",
                  (db.j(values), db.j(status), db.j(issues), state_of(status, issues, t["duplicate_of"]), load,
                   values["customer"], ticket_id))


def api_hints(t: dict) -> list[dict]:
    """For a queued API number: what each reading is in the state registry, and what the lease name points to."""
    reg = BOOKS.registry
    out = []
    st = t["field_status"]["api"]
    for who, v in (("Reader A", st.get("a")), ("Reader B", st.get("b"))):
        w = reg.get(wells.norm_api(v or ""))
        out.append({"who": who, "value": v, "well": w})
    op = BOOKS.operator_of_record.get(t["values"].get("customer", ""), "")
    exp = checks.find_by_lease(t["values"].get("lease_well", ""), op, reg)
    if exp:
        out.append({"who": "Registry, by lease name", "value": exp.api, "well": exp})
    return out


def refresh_lost() -> None:
    with db.connect() as c:
        rows = c.execute("select values from tickets where duplicate_of is null").fetchall()
        tickets = [r["values"] for r in rows]
        claimed = {code(t.get("swd_ticket", "")) for t in tickets}
        c.execute("delete from lost_loads")
        for d in checks.find_lost(tickets, BOOKS):
            rc = next((x for x in BOOKS.receipts.values() if x["date"] == d["date"] and code(x["truck"]) == code(d["truck"])
                       and code(x["swd_ticket"]) not in claimed), None)
            sheet = BOOKS.rates[d["customer"]]
            value = sheet["rate"] * float(rc["bbls_received"]) if rc and sheet["basis"] == "per_bbl" else sheet["rate"] * sheet.get("min_hours", 0)
            c.execute("insert into lost_loads values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (d["load_id"], d["date"], d["customer"], d["lease_well"], d["truck"], d["driver"],
                       rc["swd_ticket"] if rc else None, rc["bbls_received"] if rc else None, round(value, 2)))


def build_invoices(week_end: date = date(2026, 11, 6)) -> None:
    with db.connect() as c:
        ready = c.execute("select * from tickets where state in ('ready','invoiced') order by ticket_no").fetchall()
        c.execute("delete from invoices")
        by = {}
        for t in ready:
            by.setdefault(t["values"]["customer"], []).append(t)
        for cust, ts in by.items():
            sheet = BOOKS.rates[cust]
            lines = []
            for t in ts:
                v = t["values"]
                qty = num(v["bbls"]) if sheet["basis"] == "per_bbl" else max(num(v["hours"]) or 0, sheet.get("min_hours", 0))
                item = QBO_ITEM[sheet["basis"]]
                lines.append(Line(DetailType="SalesItemLineDetail", Amount=round(qty * sheet["rate"], 2),
                                  Description=f"Ticket {v['ticket_no']}, {v['date']}: {v['lease_well']} (API {v['api']}) to "
                                              f"{v['swd']}, SWD ticket {v['swd_ticket']}, truck {v['truck']}",
                                  SalesItemLineDetail=ItemDetail(ItemRef={"value": item[0], "name": item[1]}, Qty=qty,
                                                                 UnitPrice=sheet["rate"], ServiceDate=mdy(v["date"]))))
            inv = QboInvoice(CustomerRef={"value": QBO_CUSTOMER[cust], "name": BOOKS.operator_of_record[cust]},
                             TxnDate=week_end.isoformat(), DueDate=(week_end + timedelta(days=30)).isoformat(),
                             DocNumber=f"NDW-{week_end:%m%d}-{QBO_CUSTOMER[cust]}", Line=lines,
                             CustomerMemo={"value": "Produced water hauling, week of 2 Nov 2026. Signed field tickets attached."},
                             PrivateNote="Tickets " + ", ".join(t["ticket_no"] for t in ts))
            c.execute("insert into invoices values (%s,%s,%s,%s,%s,%s)",
                      (inv.DocNumber, cust, round(sum(l.Amount for l in lines), 2), db.j(inv.model_dump()), "draft",
                       [t["id"] for t in ts]))
