#!/usr/bin/env python3
"""
Google Careers apply-button watcher.

Polls one or more Google Careers job postings until their Apply button
appears, then fires a macOS notification, plays a sound, and opens the
page in your browser so you can apply immediately.

Detection: live postings server-render an anchor like
    <a class="..." href="./apply?jobId=...">  (next to a span "Apply")
so a plain HTTP fetch is enough -- no headless browser needed.

Usage:
    python3 apply_watcher.py <job_url> [<job_url> ...] [--interval 180] [--once]
    python3 apply_watcher.py --urls-file urls.txt

Example:
    python3 apply_watcher.py \
        "https://www.google.com/about/careers/applications/jobs/results/123-swe-new-grad" \
        "https://www.google.com/about/careers/applications/jobs/results/456-swe-early-career-campus" \
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
from pathlib import Path

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


def job_name(url: str) -> str:
    """Short human-readable name from the URL slug."""
    m = re.search(r"/jobs/results/\d+-([a-z0-9-]+)", url)
    return m.group(1).replace("-", " ") if m else url[:60]


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
    parser = argparse.ArgumentParser(description="Watch Google Careers postings for their Apply button.")
    parser.add_argument("job_urls", nargs="*", help="Job posting URLs (…/jobs/results/<id>-<slug>)")
    parser.add_argument("--urls-file", help="File with one job URL per line (# comments allowed)")
    parser.add_argument("--interval", type=int, default=180,
                        help="Seconds between rounds (default 180; a little jitter is added)")
    parser.add_argument("--once", action="store_true", help="Check one time and exit")
    args = parser.parse_args()

    urls = list(args.job_urls)
    if args.urls_file:
        for line in Path(args.urls_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if not urls:
        parser.error("Provide at least one job URL (or --urls-file).")

    watching = {url: 0 for url in urls}  # url -> consecutive error count
    for url in watching:
        log(f"Watching: {job_name(url)}")

    while watching:
        for url in list(watching):
            status, apply_url = check_once(url)
            name = job_name(url)

            if status == "live":
                log(f"APPLY BUTTON IS LIVE: {name}")
                log(f"Apply link: {apply_url}")
                notify("Google Apply button is LIVE",
                       f"{name} — go apply right now!", url)
                del watching[url]
            elif status == "gone":
                log(f"{name}: URL doesn't resolve to a job posting — taken "
                    "down or mistyped. Dropping it.")
                notify("Google posting not found", name, url)
                del watching[url]
            elif status == "error":
                watching[url] += 1
                log(f"{name}: fetch problem (attempt {watching[url]}) — will retry.")
                if watching[url] >= 10:
                    notify("Apply watcher stuck",
                           f"10 consecutive fetch failures on {name}.", None)
                    watching[url] = 0
            else:
                watching[url] = 0
                log(f"{name}: no apply button yet.")

        if args.once:
            return 0
        if watching:
            time.sleep(args.interval + random.uniform(0, args.interval * 0.2))

    log("All postings resolved — nothing left to watch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
