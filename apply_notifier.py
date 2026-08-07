#!/usr/bin/env python3
"""
Google Careers Apply-Button Notifier
=====================================

Polls one or more Google Careers job posting pages and fires a notification
the moment a job's "Apply" action becomes available.

Google Careers job cards render an <a> element like:

    <a class="WpHeLc VfPpkd-mRLv6 VfPpkd-RLmnJb"
       href="./apply?jobId=...&loc=US&title=..."
       aria-label="Apply"
       id="apply-action-button"
       data-navigation="server"></a>

Before a role opens for applications, this element is either absent,
disabled, or replaced with different markup (e.g. no href, or a
"Coming soon" / "Notify me" state). This script treats the presence of a
live, hrefed <a aria-label="Apply"> as the signal that applications are open.

Usage
-----
    python apply_notifier.py --config config.yaml

    # or pass URLs directly
    python apply_notifier.py \
        --url "https://www.google.com/about/careers/applications/jobs/results/..." \
        --interval 300

Notification channels (enable via env vars, all optional):
    - Console (always on)
    - Slack:   SLACK_WEBHOOK_URL
    - Email:   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL_TO
    - Generic webhook (Discord, ntfy, custom): WEBHOOK_URL

Gaps addressed beyond the original two rules (poll + detect "Apply"):
    - Handles missing/renamed CSS classes by matching on aria-label + href
      pattern instead of brittle hashed class names (Google rotates these).
    - Distinguishes a *disabled* apply link (present in DOM but not
      clickable/no href) from a live one.
    - De-duplicates alerts: only notifies once per job per state change,
      persisted to a local JSON state file so restarts don't re-spam.
    - Retries transient network failures with exponential backoff.
    - Randomized jitter on the poll interval to avoid being fingerprinted
      as a bot hitting the endpoint on a perfectly fixed cadence.
    - Configurable polite User-Agent + request timeout.
    - Structured logging to stdout and a rotating log file.
    - Graceful shutdown on SIGINT/SIGTERM.
    - Supports multiple job URLs in one run.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import random
import signal
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 4
DEFAULT_STATE_FILE = "state.json"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; ApplyButtonWatcher/1.0; "
    "+https://github.com/bartut22/google-apply-notifier)"
)

logger = logging.getLogger("apply_notifier")


@dataclass
class JobTarget:
    url: str
    label: Optional[str] = None

    def display_name(self) -> str:
        return self.label or self.url


@dataclass
class Config:
    targets: list[JobTarget] = field(default_factory=list)
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    jitter_seconds: int = 20
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    state_file: str = DEFAULT_STATE_FILE
    user_agent: str = DEFAULT_USER_AGENT
    log_file: Optional[str] = "apply_notifier.log"


def load_config(path: Optional[str], cli_urls: list[str], cli_interval: Optional[int]) -> Config:
    cfg = Config()

    if path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        cfg.interval_seconds = raw.get("interval_seconds", cfg.interval_seconds)
        cfg.jitter_seconds = raw.get("jitter_seconds", cfg.jitter_seconds)
        cfg.timeout_seconds = raw.get("timeout_seconds", cfg.timeout_seconds)
        cfg.max_retries = raw.get("max_retries", cfg.max_retries)
        cfg.state_file = raw.get("state_file", cfg.state_file)
        cfg.user_agent = raw.get("user_agent", cfg.user_agent)
        cfg.log_file = raw.get("log_file", cfg.log_file)
        for job in raw.get("jobs", []):
            cfg.targets.append(JobTarget(url=job["url"], label=job.get("label")))

    for url in cli_urls:
        cfg.targets.append(JobTarget(url=url))

    if cli_interval:
        cfg.interval_seconds = cli_interval

    if not cfg.targets:
        raise SystemExit("No job URLs provided. Use --url or a --config file with a 'jobs' list.")

    return cfg


def setup_logging(log_file: Optional[str]) -> None:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)


def load_state(state_file: str) -> dict:
    p = Path(state_file)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("State file %s is corrupt; starting fresh.", state_file)
    return {}


def save_state(state_file: str, state: dict) -> None:
    Path(state_file).write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_page(url: str, user_agent: str, timeout: int, max_retries: int) -> Optional[str]:
    headers = {"User-Agent": user_agent}
    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning(
                "Fetch failed (attempt %d/%d) for %s: %s", attempt, max_retries, url, exc
            )
            if attempt == max_retries:
                return None
            time.sleep(backoff)
            backoff *= 2
    return None


def apply_button_status(html: str) -> str:
    """
    Returns one of: 'open', 'disabled', 'missing'.

    'open'     - a live <a aria-label="Apply"> with an href is present.
    'disabled' - an apply element exists (id/aria-label match) but has no
                 usable href, or is otherwise not a real link.
    'missing'  - no apply-related element found at all (page structure
                 changed, job closed/removed, or blocked/CAPTCHA page).
    """
    soup = BeautifulSoup(html, "html.parser")

    candidates = soup.select(
        'a#apply-action-button[aria-label="Apply"], a[aria-label="Apply"]'
    )

    if not candidates:
        candidates = [
            el for el in soup.find_all("a")
            if el.get("aria-label", "").strip().lower() == "apply"
        ]

    if not candidates:
        return "missing"

    for el in candidates:
        href = el.get("href", "")
        if href and "/apply" in href:
            return "open"

    return "disabled"


def notify(message: str) -> None:
    logger.info("NOTIFY: %s", message)

    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        try:
            requests.post(slack_url, json={"text": message}, timeout=10)
        except requests.RequestException as exc:
            logger.error("Slack notification failed: %s", exc)

    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        try:
            requests.post(webhook_url, json={"content": message}, timeout=10)
        except requests.RequestException as exc:
            logger.error("Webhook notification failed: %s", exc)

    to_addr = os.environ.get("NOTIFY_EMAIL_TO")
    smtp_host = os.environ.get("SMTP_HOST")
    if to_addr and smtp_host:
        try:
            msg = MIMEText(message)
            msg["Subject"] = "Google Apply Button Alert"
            msg["From"] = os.environ.get("SMTP_USER", "apply-notifier@localhost")
            msg["To"] = to_addr
            with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT", "587"))) as server:
                server.starttls()
                user = os.environ.get("SMTP_USER")
                pw = os.environ.get("SMTP_PASS")
                if user and pw:
                    server.login(user, pw)
                server.send_message(msg)
        except Exception as exc:
            logger.error("Email notification failed: %s", exc)


def check_target(target: JobTarget, cfg: Config, state: dict) -> None:
    html = fetch_page(target.url, cfg.user_agent, cfg.timeout_seconds, cfg.max_retries)
    if html is None:
        logger.error("Could not fetch %s after retries.", target.display_name())
        return

    status = apply_button_status(html)
    prev_status = state.get(target.url, {}).get("status")

    if status != prev_status:
        logger.info(
            "Status change for %s: %s -> %s", target.display_name(), prev_status, status
        )
        if status == "open":
            notify(f'Apply button is now LIVE for "{target.display_name()}": {target.url}')
        state[target.url] = {"status": status, "last_checked": time.time()}
    else:
        state.setdefault(target.url, {})["last_checked"] = time.time()
        logger.debug("No change for %s (status=%s)", target.display_name(), status)


_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down after current cycle...", signum)
    _shutdown = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Google Careers job pages for open Apply buttons.")
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--url", action="append", default=[], help="Job URL to watch (repeatable).")
    parser.add_argument("--interval", type=int, help="Poll interval in seconds (overrides config).")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit (no loop).")
    args = parser.parse_args()

    cfg = load_config(args.config, args.url, args.interval)
    setup_logging(cfg.log_file)
    state = load_state(cfg.state_file)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Starting watch on %d target(s), interval=%ds", len(cfg.targets), cfg.interval_seconds
    )

    while not _shutdown:
        for target in cfg.targets:
            check_target(target, cfg, state)
            save_state(cfg.state_file, state)

        if args.once:
            break

        sleep_for = cfg.interval_seconds + random.randint(0, cfg.jitter_seconds)
        logger.debug("Sleeping %ds before next cycle.", sleep_for)
        for _ in range(sleep_for):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Stopped.")


if __name__ == "__main__":
    main()
