"""The hauler's web app: upload, review queue, exceptions dashboard, invoices, customer portal.

Run: uv run uvicorn app.main:app --port 8077   (from the demo folder; Postgres from TICKETS_DSN)
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wells import DATA

from . import db, seed, service

HERE = Path(__file__).parent
app = FastAPI(title="Field ticket desk")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
TICKETS = DATA / "tickets"
HAULER = json.loads((DATA / "world" / "hauler.json").read_text())


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    with db.connect() as c:
        ctx["queue_count"] = c.execute("select count(*) n from tickets where state='review'").fetchone()["n"]
    return tpl.TemplateResponse(request, name, {"hauler": HAULER, "labels": service.LABELS,
                                                  "order": service.FIELDS, **ctx})


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.connect() as c:
        counts = {r["state"]: r["n"] for r in c.execute("select state, count(*) n from tickets group by state")}
        fields = c.execute("select field_status from tickets where duplicate_of is null").fetchall()
        held = c.execute("select * from tickets where state='held' order by ticket_no").fetchall()
        lost = c.execute("select * from lost_loads order by date").fetchall()
        inv = c.execute("select customer, total from invoices order by customer").fetchall()
        cost = c.execute("select coalesce(sum(read_cost),0) s, count(*) n from tickets").fetchone()
    n_fields = sum(len(f["field_status"]) for f in fields)
    by_code = sum(1 for f in fields for s in f["field_status"].values() if s["state"] == "auto")
    return page(request, "dashboard.html", counts=counts, n_fields=n_fields, by_code=by_code, held=held, lost=lost,
                invoices=inv, cost=cost)


@app.get("/review", response_class=HTMLResponse)
def queue(request: Request):
    with db.connect() as c:
        rows = c.execute("select * from tickets where state='review' order by ticket_no").fetchall()
    return page(request, "queue.html", rows=rows)


@app.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request):
    with db.connect() as c:
        rows = c.execute("select * from tickets order by ticket_no, id").fetchall()
    return page(request, "tickets.html", rows=rows)


@app.get("/tickets/{tid}", response_class=HTMLResponse)
def ticket(request: Request, tid: int):
    with db.connect() as c:
        t = c.execute("select * from tickets where id=%s", (tid,)).fetchone()
        log = c.execute("select * from review_log where ticket_id=%s order by at", (tid,)).fetchall()
    hints = service.api_hints(t) if t["field_status"]["api"]["state"] == "review" else []
    return page(request, "ticket.html", t=t, log=log, hints=hints)


@app.post("/tickets/{tid}/resolve")
def resolve(tid: int, field: str = Form(...), value: str = Form(...)):
    service.resolve(tid, field, value, "Office (demo)")
    service.refresh_lost()
    service.build_invoices()
    return RedirectResponse(f"/tickets/{tid}", status_code=303)


@app.get("/invoices", response_class=HTMLResponse)
def invoices(request: Request):
    with db.connect() as c:
        rows = c.execute("select * from invoices order by customer").fetchall()
    return page(request, "invoices.html", rows=rows)


@app.get("/invoices/{doc}.json")
def invoice_json(doc: str):
    with db.connect() as c:
        r = c.execute("select qbo_payload from invoices where doc_number=%s", (doc,)).fetchone()
    return JSONResponse(r["qbo_payload"])


@app.get("/portal/{customer}", response_class=HTMLResponse)
def portal(request: Request, customer: str):
    with db.connect() as c:
        rows = c.execute("select * from tickets where customer=%s and duplicate_of is null order by ticket_no", (customer,)).fetchall()
        inv = c.execute("select * from invoices where customer=%s", (customer,)).fetchall()
    return page(request, "portal.html", customer=customer, rows=rows, invoices=inv)


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return page(request, "upload.html", samples=SAMPLES)


MAX_UPLOAD = 12 * 1024 * 1024
LIVE_CAP = int(os.environ.get("LIVE_READS_PER_DAY", "25"))  # new photos read by the AI readers per day (costs money)
SAMPLES = ["IMG_4310.jpg", "IMG_4355.jpg", "IMG_4313.jpg"]


def known_image(data: bytes) -> str | None:
    """A sample ticket uploaded back is matched by content, so its cached readings are reused."""
    h = hashlib.sha1(data).hexdigest()
    for p in TICKETS.glob("*.jpg"):
        if hashlib.sha1(p.read_bytes()).hexdigest() == h:
            return p.name
    return None


def take_live_read() -> bool:
    f = DATA / "live_reads.json"
    today = date.today().isoformat()
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        d = {}
    if d.get("date") != today:
        d = {"date": today, "n": 0}
    if d["n"] >= LIVE_CAP:
        return False
    d["n"] += 1
    f.write_text(json.dumps(d))
    return True


@app.post("/upload")
async def upload(request: Request, photo: UploadFile):
    data = await photo.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return page(request, "upload.html", error="That file is over 12 MB.", samples=SAMPLES)
    name = known_image(data)
    if name is None:
        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception:
            return page(request, "upload.html", error="That file is not an image.", samples=SAMPLES)
        if not take_live_read():
            return page(request, "upload.html", samples=SAMPLES,
                        error="Today's live readings for this public demo are used up. The sample tickets below still work.")
        img.thumbnail((2000, 2000))
        name = f"upload_{hashlib.sha1(data).hexdigest()[:12]}.jpg"
        img.save(TICKETS / name, "JPEG", quality=85)
    tid = service.ingest(name, "phone photo (upload)")
    service.refresh_lost()
    service.build_invoices()
    return RedirectResponse(f"/tickets/{tid}", status_code=303)


@app.get("/samples/{name}")
def sample(name: str):
    if name not in SAMPLES:
        return JSONResponse({"error": "not a sample"}, status_code=404)
    return FileResponse(TICKETS / name, filename=name)


def reset_demo() -> None:
    seed.main(["IMG_4310.jpg"])


@app.post("/reset")
def reset():
    reset_demo()
    return RedirectResponse("/", status_code=303)


@app.get("/healthz")
def healthz():
    with db.connect() as c:
        c.execute("select 1")
    return {"ok": True}


@app.middleware("http")
async def noindex(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.on_event("startup")
async def start_resets():
    """The public demo starts clean and goes back to its starting state every two hours."""
    reset_demo()

    async def loop():
        while True:
            await asyncio.sleep(2 * 3600)
            await asyncio.to_thread(reset_demo)
    asyncio.create_task(loop())


@app.get("/img/{name}")
def img(name: str):
    return FileResponse(TICKETS / Path(name).name)
