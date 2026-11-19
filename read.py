"""Two independent readings of every ticket photo, from two different providers.

One vision call per image, a strict schema, and the instruction to copy what is written and never
correct it: a reader that fixes a wrong API number hides it from the checks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic
from google import genai
from google.genai import types as gtypes
from pydantic import BaseModel

from wells import DATA

READERS = {"a": "gemini-3.8-flash", "b": "claude-sonnet-5-5"}
# $ per 1M input, output tokens: the list prices used in the 19 Nov doc-ai-eval demo (providers.py).
PRICES = {"gemini-3.8-flash": (0.825, 4.125), "claude-sonnet-5-5": (2.00, 10.00)}
FIELDS = ["ticket_no", "date", "customer", "lease_well", "api", "county", "truck", "trailer", "driver", "load_type",
          "time_in", "time_out", "hours", "swd", "swd_ticket", "bbls", "rate", "total", "driver_signed",
          "customer_signed"]


class Ticket(BaseModel):
    ticket_no: str
    date: str
    customer: str
    lease_well: str
    api: str
    county: str
    truck: str
    trailer: str
    driver: str
    load_type: str
    time_in: str
    time_out: str
    hours: str
    swd: str
    swd_ticket: str
    bbls: str
    rate: str
    total: str
    driver_signed: bool
    customer_signed: bool
    hard_to_read: list[str]


PROMPT = """This is a photo of one paper water hauling field ticket from an oilfield trucking company, filled in by
hand by the driver. Fill every field from what is written on it. Rules:
- Copy each value exactly as written, character for character, even if it looks wrong. Never correct, complete or
  normalise a value. If an API number or a total looks wrong, still copy what is written.
- ticket_no is the printed red number at the top right.
- date as written (MM/DD/YY). Times as written (HH:MM).
- api: the API number as written, with its dashes.
- load_type: the label of the ticked box (Produced Water, Flowback, Fresh Water or Brine).
- Numbers (hours, bbls, rate, total) as written, without a dollar sign.
- driver_signed and customer_signed: true only if there is a handwritten signature in that box.
- If a character is genuinely hard to read, give your best reading and name the field in hard_to_read.
- Use an empty string when a box is blank."""


def _gemini(img: bytes) -> tuple[dict, int, int]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = gtypes.GenerateContentConfig(response_mime_type="application/json", response_schema=Ticket,
                                       thinking_config=gtypes.ThinkingConfig(thinking_level="low"), max_output_tokens=4000)
    r = client.models.generate_content(model=READERS["a"], config=cfg,
                                       contents=[gtypes.Part.from_bytes(data=img, mime_type="image/jpeg"), PROMPT])
    u = r.usage_metadata
    return json.loads(r.text), u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)


def _claude(img: bytes) -> tuple[dict, int, int]:
    client = anthropic.Anthropic()
    r = client.messages.parse(
        model=READERS["b"], max_tokens=4000, thinking={"type": "between_tools"}, output_format=Ticket,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(img).decode()}},
            {"type": "text", "text": PROMPT}]}])
    return r.parsed_output.model_dump(), r.usage.input_tokens, r.usage.output_tokens


CALL = {"a": _gemini, "b": _claude}


def load_keys(names=("GEMINI_API_KEY", "ANTHROPIC_API_KEY"), path=".env") -> None:
    """Read just the keys this demo needs from a local .env file, without printing them."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip().removeprefix("export ")
        k, _, v = line.partition("=")
        if k in names and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def cache_path(reader: str, image: str, img: bytes | None = None):
    """One reading per image content: a re-rendered image is read again, an unchanged one is not paid for twice."""
    img = img if img is not None else (DATA / "tickets" / image).read_bytes()
    return DATA / "reads" / reader / f"{image}.{hashlib.sha1(img).hexdigest()[:10]}.json"


def read_one(reader: str, image: str) -> dict:
    img = (DATA / "tickets" / image).read_bytes()
    out = cache_path(reader, image, img)
    if out.exists():
        return json.loads(out.read_text())
    t0 = time.time()
    for attempt in range(5):
        try:
            data, i, o = CALL[reader](img)
            break
        except Exception as e:  # rate limits and 5xx: back off and try again
            if attempt == 4:
                raise
            print("retry", reader, image, str(e)[:120], file=sys.stderr)
            time.sleep(5 * 2 ** attempt)
    pin, pout = PRICES[READERS[reader]]
    data["_meta"] = {"model": READERS[reader], "seconds": round(time.time() - t0, 1), "input_tokens": i,
                     "output_tokens": o, "cost": (i * pin + o * pout) / 1e6}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    return data


def main(readers=("a", "b")) -> None:
    load_keys()
    manifest = json.loads((DATA / "tickets" / "manifest.json").read_text())
    jobs = [(r, m["image"]) for r in readers for m in manifest]
    with ThreadPoolExecutor(8) as ex:
        for (r, im), d in zip(jobs, ex.map(lambda j: read_one(*j), jobs)):
            print(r, im, d.get("ticket_no"), d.get("api"), d["_meta"]["seconds"], "s")


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or ("a", "b"))
