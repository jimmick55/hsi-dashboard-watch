#!/usr/bin/env python3
"""Watch the HSI (PURR) dashboard and send a Telegram alert when its weekly data changes.

Only the weekly inputs are compared. Rows that move with the live HYPE/PURR price are
ignored, so normal price ticks never trigger an alert.

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional STATE_FILE (default state.json)
Usage:    python monitor.py          # check once
          python monitor.py --test   # just send a test message
          python monitor.py --debug  # print what was parsed, send nothing
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

REQUIRED = ["HYPE Tokens Held (M)", "Date of last update"]

# Page counts as loaded once "HYPE Tokens Held (M)" is followed by a number in the
# rendered text (works whether the page uses <table>, CSS grid, or <div>s)
READY_JS = r"""() => /HYPE Tokens Held \(M\)[a-z\d]*[\s\S]*?\d/.test(document.body.innerText)"""
VALUE_RE = r"(\(?-?\$?\d[\d,]*(?:\.\d+)?\)?[MBK%]?)"


def extract(text: str, label: str):
    """Value that follows `label` (optional footnote digit) in rendered text.

    The dashboard renders label and value on the same line separated by
    whitespace, e.g. ``Net Asset Value $1,872.9`` or
    ``Less: Cash From Operations (6.8)``.  The label may carry a trailing
    footnote digit (``Common Shares1``) that must be stripped before matching.
    """
    # Strip optional trailing footnote digits OR tooltip markers (e.g. 'i')
    # from the label, then allow any whitespace between label and value.
    base = re.escape(label)
    pattern = r"(?<!Adjusted )" + base + r"[a-z\d]*[ \t]*\s+" + VALUE_RE
    m = re.search(pattern, text)
    return m.group(1) if m else None


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


def snapshot(debug: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 2400})
        page.goto(URL, wait_until="load", timeout=90_000)
        try:  # live price feeds may keep the network busy forever; don't depend on idle
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        try:
            page.wait_for_function(READY_JS, timeout=60_000)
        except Exception:
            pass  # validated below
        page.wait_for_timeout(3_000)
        text = page.evaluate("document.body.innerText")  # visible, rendered text only
        browser.close()

    data = {}
    for label in TRACKED:
        value = extract(text, label)
        if value:
            data[label] = value
    for key in ("Date of last update", "Date of next update"):
        m = re.search(key + r":?\s*(\d{1,2}/\d{1,2}/\d{4})", text)
        if m:
            data[key] = m.group(1)

    if debug or any(k not in data for k in REQUIRED):
        print("---- rendered text near tracked labels ----")
        for line_no, line in enumerate(text.splitlines()):
            if any(lbl.split(" (")[0].split(": ")[-1] in line for lbl in TRACKED + REQUIRED):
                print(f"{line_no:5d}: {line!r}")
        print("---- parsed ----")
        print(json.dumps(data, indent=2))

    missing = [k for k in REQUIRED if k not in data]
    if missing:
        sys.exit(f"Dashboard didn't load properly (missing {missing}); no alert sent.")
    return data


def main() -> None:
    if "--test" in sys.argv:
        send_telegram("✅ Test message from your HSI dashboard watcher.")
        print("Test message sent.")
        return

    if "--debug" in sys.argv:
        snapshot(debug=True)
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
            '<a href="' + URL + '">Open dashboard</a>')
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
    lines += ["", '<a href="' + URL + '">Open dashboard</a>']
    send_telegram("\n".join(lines))

    merged = {**old, **{k: v for k, v in new.items() if v not in PLACEHOLDERS}}
    STATE_FILE.write_text(json.dumps(merged, indent=2))
    print(f"Alert sent for {len(changes)} change(s).")


if __name__ == "__main__":
    main()
