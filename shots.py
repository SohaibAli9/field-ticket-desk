"""Drive the running app the way the office would, recording video and taking screenshots.

Needs the app on :8077, seeded with `python -m app.seed --hold IMG_4310.jpg`.
Each queued field is settled with what the ticket actually says (the answer key stands in for the person).
"""
from __future__ import annotations

import json
import subprocess

from playwright.sync_api import sync_playwright

from fields import same
from wells import DATA

BASE = "http://127.0.0.1:8077"
OUT = DATA / "shots"
TRUTH = {t["ticket_no"]: t for t in json.loads((DATA / "world" / "truth.json").read_text())["tickets"]}


def settle_all(page) -> None:
    while True:
        page.goto(f"{BASE}/review")
        link = page.locator("table a").first
        if link.count() == 0:
            return
        link.click()
        page.wait_for_load_state()
        tno = page.locator("h1").inner_text().split()[1]
        form = page.locator("form.choices").first
        field = form.locator("input[name=field]").get_attribute("value")
        for b in form.locator("button").all():
            if same(field, TRUTH[tno][field], b.get_attribute("value")):
                page.wait_for_timeout(1190)
                b.click()
                page.wait_for_load_state()
                page.wait_for_timeout(1530)
                break
        else:
            raise SystemExit(f"no button matches the ticket for {tno} {field}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path="/usr/bin/chromium")
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT / "video"),
                             record_video_size={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_timeout(3060)
        page.screenshot(path=OUT / "1_dashboard_before.png")
        page.goto(f"{BASE}/upload")
        page.wait_for_timeout(1360)
        page.set_input_files("input[type=file]", str(DATA / "tickets" / "IMG_4310.jpg"))
        page.wait_for_timeout(1360)
        page.click("text=Upload ticket")
        page.wait_for_load_state()
        page.wait_for_timeout(4250)
        page.screenshot(path=OUT / "2_review_api.png", full_page=True)
        page.locator(".f.review").first.screenshot(path=OUT / "2b_api_field.png")
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(2550)
        page.mouse.wheel(0, -400)
        # the person reads the ticket: the driver wrote 30-015-05411
        page.locator("form.choices button[value='30-015-05411']").click()
        page.wait_for_load_state()
        page.wait_for_timeout(4250)
        page.screenshot(path=OUT / "3_held_api.png", full_page=True)
        page.goto(f"{BASE}/review")
        page.wait_for_timeout(3060)
        page.screenshot(path=OUT / "4_queue.png")
        settle_all(page)
        page.goto(BASE)
        page.wait_for_timeout(4250)
        page.screenshot(path=OUT / "5_dashboard_after.png", full_page=True)
        page.goto(f"{BASE}/tickets")
        page.wait_for_timeout(2550)
        page.goto(f"{BASE}/invoices")
        page.wait_for_timeout(4250)
        page.screenshot(path=OUT / "6_invoices.png")
        page.goto(f"{BASE}/portal/EQV Operating")
        page.wait_for_timeout(4250)
        page.screenshot(path=OUT / "7_portal.png")
        video = page.video.path()
        ctx.close()
        m = br.new_context(viewport={"width": 390, "height": 760}, device_scale_factor=2, is_mobile=True)
        mp = m.new_page()
        mp.goto(f"{BASE}/upload")
        mp.screenshot(path=OUT / "8_phone_upload.png")
        mp.goto(f"{BASE}/tickets/1")
        mp.screenshot(path=OUT / "9_phone_ticket.png")
        m.close()
        br.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-c:v", "libx264", "-crf", "26", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(OUT / "Field-Ticket-Desk-walkthrough.mp4")], check=True)
    print("done", video)


if __name__ == "__main__":
    main()
