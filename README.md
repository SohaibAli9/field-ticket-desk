# Field Ticket Desk

Field Ticket Desk turns handwritten oilfield water hauling field tickets (phone photos and scans) into QuickBooks Online invoice requests and operator CSV exports. Two AI readers from different providers (Gemini 3.8 Flash and Claude Sonnet 5.5) read each ticket. Code settles verified fields, queues only unsettled fields for a person, and holds problem tickets before invoicing. A FastAPI and PostgreSQL web app provides upload, the review queue, an exceptions dashboard, invoices, and a customer portal. Live demo: https://fieldtickets.olympuslabs.ink (resets every two hours; upload a file from `samples/` or a photo of your own ticket; live readings are capped per day).

## Ticket 10412: one lease name, three real wells

For Burch Keely Unit #61, the right API number is `30-015-05911`. The driver wrote `30-015-05411` (a plugged SandRidge well), and one reader returned `30-015-05471` (an active LH Operating injection well). All three are real New Mexico wells, so a format check passes any of them. Only checking the API number against the lease name and operator catches it and holds the ticket before invoicing.

## Demo week results

Across 20 handwritten tickets and 21 photos (5 faint carbon copies, one ticket also as an office scan):

- First read across 420 fields: 414 and 415 right.
- After checks: 5 fields went to a person, 0 wrong values accepted without a person.
- Validation: all 9 planted problems held before invoicing with a suggested fix, 0 flags on the 11 clean tickets.
- Reconciliation: 2 dispatched loads with a disposal receipt and no ticket found ($257.25), duplicate ticket billed once.
- Invoices and cost: 11 clean tickets on 3 invoices ($1,893.00), $0.24 for all 42 readings, 17 unit tests with no model calls.

## How it works

1. **Intake**: Upload scans or phone photos, deduplicated by ticket number.
2. **Two readings**: Both AI readers copy written fields verbatim without auto-correcting.
3. **Code settles**: Matching readings clear most fields; list fields (trucks, trailers, drivers, customers, disposal wells) must also appear on the list, else they go to the review queue.
4. **Checks**: Rules check tickets against dispatch logs, disposal meters, trailer limits, rate sheets, signatures, timestamps, and operator aliases.
5. **Invoice**: Clean tickets build invoice PDFs with ticket backup, QuickBooks Online JSON requests, and an operator CSV export.
6. **Portal**: Haulers work exceptions on the dashboard, while operators view their tickets and invoices.

## Files

| Path | Purpose |
| --- | --- |
| `wells.py` | NM OCD registry loader |
| `world.py` | Invented week and answer key |
| `render.py` | Draws tickets, carbon copies, and phone photos |
| `read.py` | Two readings, cached by image hash |
| `fields.py` | Normalising written values |
| `checks.py` | Every check as plain functions |
| `pipeline.py` | Merge, review queue, and scoring |
| `score.py` | Reader accuracy scoring |
| `invoice.py` | QuickBooks Online request bodies, CSV export, invoice PDF with ticket backup |
| `app/` | FastAPI web app |
| `shots.py` | Drives the app with Playwright for screenshots and video |
| `tests/` | Unit tests |
| `Dockerfile`, `deploy/docker-compose.yml` | App plus Postgres |
| `samples/` | Three ticket photos |

## Run it

```bash
uv sync
uv run pytest -q tests            # 17 tests, no API calls
mkdir -p data/ocd data/fonts
curl -L -o data/ocd/wells.csv 'https://ocd-hub-nm-emnrd.hub.arcgis.com/api/download/v1/items/387f397ebf164c7aa6a752aac7d22b17/csv?layers=0'
# fonts: Caveat, Homemade Apple, Reenie Beanie, Nanum Pen Script (Google Fonts, OFL) as .ttf in data/fonts/
uv run python world.py && uv run python render.py
echo 'GEMINI_API_KEY=...' > .env; echo 'ANTHROPIC_API_KEY=...' >> .env
uv run python read.py && uv run python pipeline.py && uv run python invoice.py
```

Web app: set `TICKETS_DSN=postgresql://postgres:demo@db:5432/tickets` in `.env` (the compose file mounts `../data` and reads `../.env`), run `docker build -t field-ticket-desk:local . && cd deploy && docker compose up -d`, then open http://127.0.0.1:8220.

## What is invented and what is real

I invented the hauler, drivers, trucks, rates, dispatch log, and disposal receipts. All wells, API numbers, operators, and disposal wells come from the real New Mexico OCD well registry. I planted the 9 problems in an answer key written before any model ran, and wrote the checks after seeing these 21 photos. QuickBooks Online requests build and validate locally, not sent to a live company.

## License

All rights reserved; ask before reuse.
