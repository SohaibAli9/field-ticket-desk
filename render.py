"""Draw each ticket as a carbon-pad field ticket filled in by hand, then 'photograph' it.

Each driver keeps one handwriting for the whole week. Photos get what a phone in a truck cab
adds: tilt, perspective, uneven light, a shadow, blur and JPEG compression. The duplicate ticket
also arrives as a flat office scan.
"""
from __future__ import annotations

import json
import math
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from wells import DATA

W, H = 1700, 1100
FONTS = DATA / "fonts"
PRINT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PRINT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUT = DATA / "tickets"
INKS = [(22, 38, 110), (25, 25, 35), (30, 50, 140)]

# label, key, x, y, width (row height is fixed)
BOXES = [
    ("DATE", "date", 40, 200, 300), ("CUSTOMER / OPERATOR", "customer", 340, 200, 820), ("COUNTY", "county", 1160, 200, 500),
    ("LEASE / WELL NAME", "lease_well", 40, 300, 820), ("API NO.", "api", 860, 300, 480), ("STATE", None, 1340, 300, 320),
    ("TRUCK NO.", "truck", 40, 400, 360), ("TRAILER NO.", "trailer", 400, 400, 360), ("DRIVER", "driver", 760, 400, 900),
    ("TIME IN (PICKUP)", "time_in", 40, 560, 420), ("TIME OUT (DELIVERED)", "time_out", 460, 560, 420), ("HOURS", "hours", 880, 560, 300),
    ("SWD TICKET NO.", "swd_ticket", 1180, 560, 480),
    ("DISPOSAL FACILITY (SWD)", "swd", 40, 660, 1140), ("BBLS", "bbls", 1180, 660, 480),
    ("RATE  ($/BBL or $/HR)", "rate", 40, 760, 560), ("TOTAL  $", "total", 600, 760, 580), ("PO / AFE NO.", None, 1180, 760, 480),
    ("DRIVER SIGNATURE", "driver_signed", 40, 880, 800), ("CUSTOMER REP SIGNATURE", "customer_signed", 840, 880, 820),
]
ROW_H = 100
LOAD_TYPES = ["Produced Water", "Flowback", "Fresh Water", "Brine"]


def fmt(key: str, v) -> str:
    if v is None:
        return ""
    if key == "rate":
        return f"{v:.2f}"
    if key == "total":
        return f"{v:,.2f}"
    if key == "hours":
        return f"{v:g}"
    return str(v)


def hand(draw: ImageDraw.ImageDraw, img: Image.Image, xy, text: str, font_file: str, size: int,
         ink, rng: random.Random, slant: float) -> None:
    """Write text one glyph at a time with a wandering baseline, size drift and per-glyph tilt."""
    x, y = xy
    font = ImageFont.truetype(str(FONTS / font_file), size)
    for ch in text:
        if ch == " ":
            x += size * 0.32
            continue
        f = ImageFont.truetype(str(FONTS / font_file), max(10, int(size * rng.uniform(0.92, 1.08))))
        box = f.getbbox(ch)
        gw, gh = box[2] - box[0] + 8, box[3] + 12
        g = Image.new("L", (gw + 10, gh + 10), 0)
        ImageDraw.Draw(g).text((5 - box[0], 5), ch, font=f, fill=255)
        g = g.rotate(rng.uniform(-6, 6) + slant, resample=Image.BICUBIC, expand=True)
        col = Image.new("RGB", g.size, ink)
        img.paste(col, (int(x), int(y + rng.uniform(-3, 3))), g.point(lambda p: int(p * rng.uniform(0.82, 0.97))))
        x += font.getlength(ch) * rng.uniform(0.95, 1.08)


def signature(img: Image.Image, x: int, y: int, ink, rng: random.Random) -> None:
    d = ImageDraw.Draw(img)
    pts = []
    cx, cy = x, y
    for i in range(46):
        cx += rng.uniform(4, 13)
        cy = y + 18 * math.sin(i * rng.uniform(0.5, 1.2)) + rng.uniform(-8, 8)
        pts.append((cx, cy))
    d.line(pts, fill=ink, width=3, joint="curve")
    d.line([(x + 10, y + 26), (cx - 20, y + 20)], fill=ink, width=2)


def carbon(t: dict, hauler: dict, font_file: str, seed: str) -> Image.Image:
    """The customer's carbon copy: coloured paper, handwriting transferred faint, grainy and a little offset."""
    rng = random.Random(seed + "c")
    full = form(t, hauler, font_file, random.Random(seed), (255, 255, 255)).convert("L")
    blank = form(t, hauler, font_file, random.Random(seed), (255, 255, 255), write=False).convert("L")
    hand_mask = np.asarray(ImageChops.difference(full, blank), dtype=np.float32)
    grain = np.random.default_rng(rng.randint(0, 10**6)).uniform(0.35, 1.0, hand_mask.shape)
    hand_mask = Image.fromarray(np.clip(hand_mask * grain * 0.8, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.3))
    hand_mask = ImageChops.offset(hand_mask, rng.randint(1, 4), rng.randint(1, 3))
    paper = rng.choice([(247, 212, 220), (250, 238, 168)])
    out = ImageChops.multiply(Image.new("RGB", (W, H), paper), blank.convert("RGB"))
    out.paste(Image.new("RGB", (W, H), (70, 60, 125)), (0, 0), hand_mask)
    return out


def form(t: dict, hauler: dict, font_file: str, rng: random.Random, paper, write: bool = True) -> Image.Image:
    img = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(img)
    fb = ImageFont.truetype(PRINT_B, 40)
    f = ImageFont.truetype(PRINT, 22)
    fl = ImageFont.truetype(PRINT, 17)
    red = (190, 30, 40)
    d.text((40, 30), hauler["name"].upper(), font=fb, fill=(20, 20, 20))
    d.text((40, 82), f"{hauler['address']}   {hauler['phone']}", font=f, fill=(40, 40, 40))
    d.text((40, 125), "WATER HAULING FIELD TICKET", font=ImageFont.truetype(PRINT_B, 30), fill=(20, 20, 20))
    d.text((1300, 40), "No.", font=ImageFont.truetype(PRINT_B, 34), fill=red)
    d.text((1370, 34), t["ticket_no"], font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 46), fill=red)
    for label, key, x, y, w in BOXES:
        d.rectangle([x, y, x + w, y + ROW_H], outline=(60, 60, 60), width=2)
        d.text((x + 8, y + 5), label, font=fl, fill=(70, 70, 70))
    # load type checkboxes
    d.rectangle([40, 500, 1660, 560], outline=(60, 60, 60), width=2)
    d.text((48, 505), "LOAD TYPE", font=fl, fill=(70, 70, 70))
    for i, lt in enumerate(LOAD_TYPES):
        bx = 250 + i * 340
        d.rectangle([bx, 515, bx + 28, 543], outline=(40, 40, 40), width=2)
        d.text((bx + 40, 515), lt.upper(), font=f, fill=(40, 40, 40))
    d.text((40, 1060), "WHITE: OFFICE    YELLOW: CUSTOMER    PINK: DRIVER", font=fl, fill=(90, 90, 90))
    d.text((1180, 1060), "Rates per signed rate sheet. Net 30.", font=fl, fill=(90, 90, 90))

    if not write:
        return img
    ink = rng.choice(INKS)
    slant = rng.uniform(-4, 4)
    size = rng.randint(50, 60)
    for label, key, x, y, w in BOXES:
        if key is None:
            continue
        if key in ("driver_signed", "customer_signed"):
            if t[key]:
                signature(img, x + 60 + rng.randint(0, 120), y + 50, ink, rng)
            continue
        text = fmt(key, t.get(key))
        if key == "county":
            text = "Eddy"
        if text:
            hand(d, img, (x + 14 + rng.randint(0, 30), y + 26 + rng.randint(-4, 6)), text, font_file, size, ink, rng, slant)
    hand(d, img, (1360, 326), "NM", font_file, size, ink, rng, slant)
    # tick the load type
    i = LOAD_TYPES.index(t["load_type"]) if t["load_type"] in LOAD_TYPES else 0
    bx = 250 + i * 340
    d.line([(bx + 2, 530), (bx + 12, 545), (bx + 36, 505)], fill=ink, width=5)
    return img


def photo(img: Image.Image, rng: random.Random) -> Image.Image:
    """A phone photo of the ticket on a clipboard in a truck cab."""
    bw, bh = 2000, 1500
    base = rng.choice([(78, 70, 62), (60, 62, 66), (120, 92, 60)])
    bg = Image.new("RGB", (bw, bh), base)
    noise = (np.random.default_rng(rng.randint(0, 10**6)).normal(0, 9, (bh, bw, 1))).astype(np.int16)
    bg = Image.fromarray(np.clip(np.asarray(bg, dtype=np.int16) + noise, 0, 255).astype(np.uint8))
    scale = rng.uniform(0.98, 1.05)
    t = img.resize((int(W * scale), int(H * scale)))
    t = t.rotate(rng.uniform(-4.5, 4.5), expand=True, fillcolor=(0, 0, 0), resample=Image.BICUBIC)
    ox, oy = (bw - t.width) // 2 + rng.randint(-60, 60), (bh - t.height) // 2 + rng.randint(-50, 50)
    m = Image.new("L", t.size, 0)
    m.paste(255, (0, 0), t.convert("L").point(lambda p: 255 if p > 8 else 0))
    bg.paste(t, (ox, oy), m)
    # perspective: move each corner a little
    j = lambda: rng.uniform(-0.035, 0.035)
    src = [(0, 0), (bw, 0), (bw, bh), (0, bh)]
    dst = [(x + j() * bw, y + j() * bh) for x, y in src]
    coeffs = _persp(dst, src)
    bg = bg.transform((bw, bh), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=base)
    # light falls off from one side, plus a soft shadow band (a hand or the mirror)
    a = np.asarray(bg).astype(np.float32)
    yy, xx = np.mgrid[0:bh, 0:bw]
    ang = rng.uniform(0, 2 * math.pi)
    grad = 1.0 - 0.28 * ((xx * math.cos(ang) + yy * math.sin(ang)) / (bw + bh) + 0.5)
    sx = rng.randint(200, 1700)
    shadow = 1.0 - 0.22 * np.exp(-((xx + 0.4 * yy - sx) ** 2) / (2 * 160.0 ** 2))
    a *= (grad * shadow)[..., None]
    a += np.random.default_rng(rng.randint(0, 10**6)).normal(0, 4, a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.7, 1.4)))
    return out.resize((1600, 1200))


def scan(img: Image.Image, rng: random.Random) -> Image.Image:
    g = img.convert("L").rotate(rng.uniform(-1.2, 1.2), expand=True, fillcolor=255, resample=Image.BICUBIC)
    return g.point(lambda p: 255 if p > 200 else int(p * 0.9)).filter(ImageFilter.GaussianBlur(0.5)).convert("RGB")


def _persp(pa, pb):
    m = []
    for (x, y), (u, v) in zip(pa, pb):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(m, dtype=float)
    B = np.array(pb, dtype=float).reshape(8)
    return np.linalg.solve(A, B).tolist()


def main() -> None:
    truth = json.loads((DATA / "world" / "truth.json").read_text())
    hauler = json.loads((DATA / "world" / "hauler.json").read_text())
    fonts = {r["ticket_no"]: r["font"] for r in truth["render"]}
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, t in enumerate(truth["tickets"]):
        rng = random.Random(f"tk{t['ticket_no']}")
        paper = rng.choice([(250, 249, 244), (252, 250, 236), (248, 246, 240)])
        f = form(t, hauler, fonts[t["ticket_no"]], rng, paper)
        f.save(OUT / f"form_{t['ticket_no']}.png")
        name = f"IMG_{4310 + i * 3}.jpg"
        # Every fourth ticket reaches the office only as the customer's carbon copy.
        is_carbon = i % 4 == 3
        shot = carbon(t, hauler, fonts[t["ticket_no"]], f"tk{t['ticket_no']}") if is_carbon else f
        photo(shot, rng).save(OUT / name, quality=rng.randint(62, 78))
        manifest.append({"image": name, "ticket_no": t["ticket_no"],
                         "source": "phone photo, carbon copy" if is_carbon else "phone photo"})
        if t["ticket_no"] == truth["dup_of"]:
            scan(f, rng).save(OUT / f"office_scan_{t['ticket_no']}.jpg", quality=80)
            manifest.append({"image": f"office_scan_{t['ticket_no']}.jpg", "ticket_no": t["ticket_no"], "source": "office scan"})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(len(manifest), "images")


if __name__ == "__main__":
    main()
