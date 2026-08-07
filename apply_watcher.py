#!/usr/bin/env python3
"""
Google Careers apply-button watcher.

Polls a Google Careers job posting until its Apply button appears, then
fires a macOS notification, plays a sound, and opens the page in your
browser so you can apply immediately.

Detection: live postings server-render an anchor like
    <a class="..." href="./apply?jobId=...">  (next to a span "Apply")
so a plain HTTP fetch is enough -- no headless browser needed.

Usage:
    python3 apply_watcher.py <job_url> [--interval 180] [--once]

Example:
    python3 apply_watcher.py \
        "https://www.google.com/about/careers/applications/jobs/results/123456-software-engineer-new-grad" \
        --interval 120

Requires: Python 3 (stdlib only). macOS for notifications/sound.
"""

import argparse
import html as html_lib
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Live postings render the apply link into the page HTML.
APPLY_HREF_RE = re.compile(r'href="(\./apply\?jobId=[^"]+)"')


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def find_apply_url(job_url: str, html: str) -> str | None:
    m = APPLY_HREF_RE.search(html)
    if not m:
        return None
    # Resolve "./apply?jobId=..." relative to .../jobs/results/<job>
    base = job_url.split("?")[0].rstrip("/")
    base = base.rsplit("/", 1)[0]  # drop the job slug -> .../jobs/results
    return f"{base}/{html_lib.unescape(m.group(1)[2:])}"


def notify(title: str, message: str, open_url: str | None) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" sound name "Glass"'],
            check=False,
        )
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
        if open_url:
            subprocess.run(["open", open_url], check=False)
    except FileNotFoundError:
        pass  # non-macOS: console output above is still there


def check_once(job_url: str) -> tuple[str, str | None]:
    """Returns (status, apply_url). status: 'live', 'waiting', 'gone', 'error'."""
    try:
        code, html = fetch(job_url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "gone", None
        return "error", None
    except (urllib.error.URLError, TimeoutError, OSError):
        return "error", None

    apply_url = find_apply_url(job_url, html)
    if apply_url:
        return "live", apply_url
    # Dead or mistyped job URLs get the generic search page shell instead
    # of a job detail page — its <title> is "Jobs search — Google Careers".
    title = re.search(r"<title>([^<]*)</title>", html)
    if not title or title.group(1).strip().lower().startswith("jobs search"):
        return "gone", None
    return "waiting", None


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch a Google Careers posting for its Apply button.")
    parser.add_argument("job_url", help="Full URL of the job posting (…/jobs/results/<id>-<slug>)")
    parser.add_argument("--interval", type=int, default=180,
                        help="Seconds between checks (default 180; a little jitter is added)")
    parser.add_argument("--once", action="store_true", help="Check one time and exit")
    args = parser.parse_args()

    log(f"Watching: {args.job_url}")
    consecutive_errors = 0

    while True:
        status, apply_url = check_once(args.job_url)

        if status == "live":
            log("APPLY BUTTON IS LIVE!")
            log(f"Apply link: {apply_url}")
            notify("Google Apply button is LIVE",
                   "Go apply right now — new grad roles fill fast!",
                   args.job_url)
            return 0

        if status == "gone":
            log("This URL doesn't resolve to a job posting — it may have been "
                "taken down, or the URL is mistyped.")
            notify("Google posting not found",
                   "The job URL no longer shows a posting.", args.job_url)
            return 1

        if status == "error":
            consecutive_errors += 1
            log(f"Fetch problem (attempt {consecutive_errors}) — will retry.")
            if consecutive_errors >= 10:
                log("10 consecutive failures; check your connection or the URL.")
                notify("Apply watcher stuck",
                       "10 consecutive fetch failures — check the terminal.", None)
                consecutive_errors = 0
        else:
            consecutive_errors = 0
            log("No apply button yet.")

        if args.once:
            return 0 if status == "waiting" else 1

        time.sleep(args.interval + random.uniform(0, args.interval * 0.2))


if __name__ == "__main__":
    sys.exit(main())
