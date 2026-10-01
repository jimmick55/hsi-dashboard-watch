#!/usr/bin/env python3
"""Watch the HSI (PURR) dashboard and send a Telegram alert when its weekly data changes.

Only the weekly inputs are compared. Rows that move with the live HYPE/PURR price are
ignored, so normal price ticks never trigger an alert.

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional STATE_FILE (default state.json)
Usage:    python monitor.py          # check once
          python monitor.py --test   # just send a test message
"""
import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

URL = "https://hypestrat.xyz/dashboard"
STATE_FILE = pathlib.Path(os.environ.get("STATE_FILE", "state.json"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Weekly inputs only (price-independent rows)
TRACKED = [
    "HYPE Tokens Held (M)",
    "Net Asset Value",
    "Less: Cash From Operations",
    "Plus: Cash From Financing",
    "Less: Treasury Deployment",
    "Less: Reported Value of Digital Assets",
    "Cash Holdings",
    "Common Shares",
    "Preferred Shares",
]
PLACEHOLDERS = {"", "—", "--", "-", "N/A"}

# Page counts as loaded once the HYPE Tokens Held row has a number in it
READY_JS = """() => [...document.querySelectorAll('tr')].some(r =>
  r.cells.length > 1 &&
  r.cells[0].textContent.includes('HYPE Tokens Held') &&
  /\\d/.test(r.cells[1].textContent))"""


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        if r.status != 200:
            sys.exit(f"Telegram error: {r.read()!r}")


def snapshot() -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_function(READY_JS, timeout=45_000)
        except Exception:
            pass  # validated below
        page.wait_for_timeout(2_000)
        rows = page.eval_on_selector_all(
            "tr", "rs => rs.map(r => [...r.cells].map(c => c.textContent))")
        body = page.evaluate("document.body.textContent")
        browser.close()

    data = {}
    for cells in rows:
        if len(cells) < 2:
            continue
        raw = cells[0].split(" Definition:")[0]  # strip tooltip definition text
        label = re.sub(r"\d+$", "", " ".join(raw.split()))  # drop footnote digits
        if label in TRACKED and label not in data:
            data[label] = " ".join(cells[1].split())

    m = re.search(r"Date of last update:\s*([0-9/.\-]+)", body)
    if m:
        data["Date of last update"] = m.group(1)
    m = re.search(r"Date of next update:\s*([0-9/.\-]+)", body)
    if m:
        data["Date of next update"] = m.group(1)

    if not any(re.search(r"\d", data.get(k, "")) for k in TRACKED):
        sys.exit(f"Dashboard didn't load properly, skipping. Got: {data}")
    return data


def main() -> None:
    if "--test" in sys.argv:
        send_telegram("✅ Test message from your HSI dashboard watcher.")
        print("Test message sent.")
        return

    new = snapshot()
    print("Current:", json.dumps(new, indent=2))
    old = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None

    if old is None:
        STATE_FILE.write_text(json.dumps(new, indent=2))
        send_telegram(
            "👀 Now watching the HSI dashboard.\n"
            f"Last update: <b>{html.escape(new.get('Date of last update', '?'))}</b>\n"
            f"HYPE held: <b>{html.escape(new.get('HYPE Tokens Held (M)', '?'))}M</b>\n"
            f'<a href="{URL}">Open dashboard</a>')
        return

    changes = [
        (k, old.get(k, "—"), v)
        for k, v in new.items()
        if v not in PLACEHOLDERS and old.get(k) != v
    ]
    if not changes:
        print("No change.")
        return

    lines = ["📊 <b>HSI dashboard data updated</b>", ""]
    for k, before, after in changes:
        lines.append(f"• {html.escape(k)}: {html.escape(before)} → <b>{html.escape(after)}</b>")
    lines += ["", f'<a href="{URL}">Open dashboard</a>']
    send_telegram("\n".join(lines))

    merged = {**old, **{k: v for k, v in new.items() if v not in PLACEHOLDERS}}
    STATE_FILE.write_text(json.dumps(merged, indent=2))
    print(f"Alert sent for {len(changes)} change(s).")


if __name__ == "__main__":
    main()
