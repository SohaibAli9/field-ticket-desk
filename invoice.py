"""Invoices from checked tickets: one per customer for the week.

Only tickets with no open issue are invoiced. Held tickets are listed with the reason and the fix
the checks suggest. Outputs:
  - qbo_invoices.json: request bodies for QuickBooks Online's Invoice endpoint (POST /v3/company/{realm}/invoice),
    built here and validated locally; not sent to a live company.
  - field_tickets_export.csv: one row per ticket for operators who take a CSV upload (OpenInvoice or AP).
  - invoice_<customer>.pdf: a printable invoice with each ticket photo attached as backup.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

import pymupdf as fitz
from PIL import Image
from pydantic import BaseModel, model_validator

from fields import mdy, num
from pipeline import books
from world import lease_label
from wells import DATA

RUN = DATA / "run"
WEEK_END = date(2026, 11, 6)
# QuickBooks ids come from the customer and item sync (milestone 3). Stand-ins for the demo:
QBO_CUSTOMER = {"EQV Operating": "58", "Spur Energy": "59", "Riley Permian": "60"}
QBO_ITEM = {"per_bbl": ("21", "Produced water haul (per bbl)"), "hourly": ("22", "Produced water haul (hourly)")}


class ItemDetail(BaseModel):
    ItemRef: dict
    Qty: float
    UnitPrice: float
    ServiceDate: str


class Line(BaseModel):
    DetailType: str
    Amount: float
    Description: str
    SalesItemLineDetail: ItemDetail

    @model_validator(mode="after")
    def amount_is_qty_times_price(self):
        # QuickBooks refuses a line whose Amount is not UnitPrice x Qty.
        d = self.SalesItemLineDetail
        assert abs(round(d.Qty * d.UnitPrice, 2) - self.Amount) < 0.005, (self.Description, d.Qty, d.UnitPrice, self.Amount)
        return self


class QboInvoice(BaseModel):
    CustomerRef: dict
    TxnDate: str
    DueDate: str
    DocNumber: str
    Line: list[Line]
    CustomerMemo: dict
    PrivateNote: str

    @model_validator(mode="after")
    def has_lines(self):
        assert self.Line and len(self.DocNumber) <= 21  # QuickBooks caps DocNumber at 21 characters
        return self


def build() -> dict:
    res = json.loads((RUN / "results.json").read_text())
    bk = books()
    ok, held = [], []
    for r in res["results"]:
        if r["duplicate_of"]:
            continue
        (held if r["issues"] else ok).append(r)
    invoices = {}
    for r in ok:
        v = r["resolved"]
        cust = v["customer"]
        sheet = bk.rates[cust]
        if sheet["basis"] == "per_bbl":
            qty = num(v["bbls"])
        else:
            qty = max(num(v["hours"]) or 0, sheet.get("min_hours", 0))
        api = v["api"]
        # The operator's AP matches on the well name, so print the registry's name, not the driver's spelling.
        reg = bk.registry.get(api)
        well = lease_label(reg) if reg else v["lease_well"]
        desc = (f"Ticket {v['ticket_no']}, {v['date']}: {well} (API {api}) to {v['swd']}, "
                f"SWD ticket {v['swd_ticket']}, {num(v['bbls']):g} bbl, truck {v['truck']}, {v['driver']}")
        if sheet["basis"] == "hourly":
            desc += f", {v['time_in']} to {v['time_out']}"
        item = QBO_ITEM[sheet["basis"]]
        line = Line(DetailType="SalesItemLineDetail", Amount=round(qty * sheet["rate"], 2), Description=desc,
                    SalesItemLineDetail=ItemDetail(ItemRef={"value": item[0], "name": item[1]}, Qty=qty,
                                                   UnitPrice=sheet["rate"], ServiceDate=mdy(v["date"])))
        invoices.setdefault(cust, []).append((line, r))
    out = {}
    for cust, lines in invoices.items():
        inv = QboInvoice(
            CustomerRef={"value": QBO_CUSTOMER[cust], "name": bk.operator_of_record[cust]},
            TxnDate=WEEK_END.isoformat(), DueDate=(WEEK_END + timedelta(days=30)).isoformat(),
            DocNumber=f"NDW-{WEEK_END:%m%d}-{QBO_CUSTOMER[cust]}",
            Line=[l for l, _ in lines],
            CustomerMemo={"value": "Produced water hauling, week of 2 Nov 2026. Signed field tickets attached."},
            PrivateNote="Built from checked field tickets: " + ", ".join(r["ticket_no"] for _, r in lines))
        out[cust] = {"invoice": inv.model_dump(), "tickets": [r["image"] for _, r in lines],
                     "total": round(sum(l.Amount for l, _ in lines), 2)}
    return {"invoices": out, "held": [{"ticket_no": r["ticket_no"], "image": r["image"],
                                       "customer": r["resolved"]["customer"], "issues": r["issues"]} for r in held]}


def export_csv(data: dict, path) -> None:
    bk = books()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["invoice_number", "operator", "ticket_number", "service_date", "lease_well", "api_number",
                    "disposal_facility", "swd_ticket", "truck", "driver", "bbls", "quantity", "unit", "unit_price",
                    "amount", "afe_or_po", "ticket_image"])
        res = {r["ticket_no"]: r for r in json.loads((RUN / "results.json").read_text())["results"] if not r["duplicate_of"]}
        for cust, inv in data["invoices"].items():
            for line, img in zip(inv["invoice"]["Line"], inv["tickets"]):
                tno = line["Description"].split(",")[0].split()[-1]
                v = res[tno]["resolved"]
                d = line["SalesItemLineDetail"]
                w.writerow([inv["invoice"]["DocNumber"], inv["invoice"]["CustomerRef"]["name"], tno, d["ServiceDate"],
                            lease_label(bk.registry[v["api"]]) if v["api"] in bk.registry else v["lease_well"], v["api"], v["swd"], v["swd_ticket"], v["truck"], v["driver"], v["bbls"],
                            d["Qty"], "BBL" if "bbl" in d["ItemRef"]["name"] else "HR", f"{d['UnitPrice']:.2f}",
                            f"{line['Amount']:.2f}", "", img])


def invoice_pdf(cust: str, inv: dict, hauler: dict, path) -> None:
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    q = inv["invoice"]
    rows = "".join(
        f"<tr><td>{l['SalesItemLineDetail']['ServiceDate']}</td><td>{l['Description']}</td>"
        f"<td style='text-align:right'>{l['SalesItemLineDetail']['Qty']:g}</td>"
        f"<td style='text-align:right'>{l['SalesItemLineDetail']['UnitPrice']:.2f}</td>"
        f"<td style='text-align:right'>{l['Amount']:,.2f}</td></tr>" for l in q["Line"])
    html = f"""<div style="font-family:sans-serif;font-size:9pt">
<p style="font-size:15pt;font-weight:bold;margin:0">{hauler['name']}</p>
<p style="margin:0">{hauler['address']} &nbsp; {hauler['phone']}</p>
<p style="font-size:12pt;font-weight:bold;margin-top:14px">INVOICE {q['DocNumber']}</p>
<p>Bill to: <b>{q['CustomerRef']['name']}</b><br>Invoice date: {q['TxnDate']} &nbsp; Due: {q['DueDate']} (Net 30)</p>
<table style="width:100%;border-collapse:collapse" border="1" cellpadding="3">
<tr style="background:#eee"><th width="58">Date</th><th>Ticket and load</th><th width="34">Qty</th><th width="40">Rate</th><th width="54">Amount</th></tr>
{rows}
<tr><td colspan="4" style="text-align:right"><b>Total</b></td><td style="text-align:right"><b>{inv['total']:,.2f}</b></td></tr></table>
<p>{q['CustomerMemo']['value']}</p>
<p style="color:#777">Invented hauler and amounts, for a demo. Wells and API numbers are real, from the NM OCD registry.</p></div>"""
    pg.insert_htmlbox(fitz.Rect(40, 40, 572, 760), html, css="td,th{border:1px solid #bbb;padding:3px}table{border-collapse:collapse}")
    for img in inv["tickets"]:
        p = doc.new_page(width=612, height=792)
        p.insert_text((40, 40), f"Backup: field ticket photo {img}", fontsize=10)
        im = Image.open(DATA / "tickets" / img).convert("RGB")
        im.thumbnail((1400, 1400))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        p.insert_image(fitz.Rect(40, 60, 572, 460), stream=buf.getvalue())
    doc.save(path, garbage=4, deflate=True)


def main() -> None:
    data = build()
    (RUN / "qbo_invoices.json").write_text(json.dumps({c: v["invoice"] for c, v in data["invoices"].items()}, indent=1))
    (RUN / "invoices.json").write_text(json.dumps(data, indent=1))
    export_csv(data, RUN / "field_tickets_export.csv")
    hauler = json.loads((DATA / "world" / "hauler.json").read_text())
    for cust, inv in data["invoices"].items():
        invoice_pdf(cust, inv, hauler, RUN / f"invoice_{cust.split()[0].lower()}.pdf")
    for c, v in data["invoices"].items():
        print(c, len(v["tickets"]), "tickets", v["total"])
    print("held:", [(h["ticket_no"], h["issues"][0]["message"][:60]) for h in data["held"]])


if __name__ == "__main__":
    main()
