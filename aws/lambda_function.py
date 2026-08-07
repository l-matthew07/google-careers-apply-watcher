"""
AWS Lambda: Google Careers apply-button watcher with Twilio SMS.

Runs on an EventBridge schedule. Checks every URL in JOB_URLS; when a
posting's Apply button goes live (or a posting disappears), sends an SMS
via Twilio. Already-notified URLs are remembered in an SSM parameter so
you get exactly one text per event.

Environment variables:
    JOB_URLS            comma- or newline-separated job posting URLs
    TWILIO_ACCOUNT_SID  from twilio.com/console
    TWILIO_AUTH_TOKEN   from twilio.com/console
    TWILIO_FROM         your Twilio phone number, e.g. +15551234567
    TWILIO_TO           your phone number to text, e.g. +15557654321
    STATE_PARAM         SSM parameter name for state (default /apply-watcher/state)

No third-party packages — Twilio is called through its plain REST API.
"""

import base64
import html as html_lib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import boto3

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
APPLY_HREF_RE = re.compile(r'href="(\./apply\?jobId=[^"]+)"')

ssm = boto3.client("ssm")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def job_name(url: str) -> str:
    m = re.search(r"/jobs/results/\d+-([a-z0-9-]+)", url)
    return m.group(1).replace("-", " ") if m else url[:60]


def find_apply_url(job_url: str, page: str) -> str | None:
    m = APPLY_HREF_RE.search(page)
    if not m:
        return None
    base = job_url.split("?")[0].rstrip("/").rsplit("/", 1)[0]
    return f"{base}/{html_lib.unescape(m.group(1)[2:])}"


def check(job_url: str) -> tuple[str, str | None]:
    """Returns (status, apply_url). status: live | waiting | gone | error."""
    try:
        page = fetch(job_url)
    except urllib.error.HTTPError as e:
        return ("gone", None) if e.code == 404 else ("error", None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return "error", None

    apply_url = find_apply_url(job_url, page)
    if apply_url:
        return "live", apply_url
    title = re.search(r"<title>([^<]*)</title>", page)
    if not title or title.group(1).strip().lower().startswith("jobs search"):
        return "gone", None
    return "waiting", None


def send_sms(body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    data = urllib.parse.urlencode({
        "From": os.environ["TWILIO_FROM"],
        "To": os.environ["TWILIO_TO"],
        "Body": body,
    }).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=data,
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Twilio: {resp.status}")


def load_state(param: str) -> dict:
    try:
        raw = ssm.get_parameter(Name=param)["Parameter"]["Value"]
        return json.loads(raw)
    except (ssm.exceptions.ParameterNotFound, json.JSONDecodeError):
        return {"notified": []}


def save_state(param: str, state: dict) -> None:
    ssm.put_parameter(Name=param, Value=json.dumps(state),
                      Type="String", Overwrite=True)


def lambda_handler(event, context):
    param = os.environ.get("STATE_PARAM", "/apply-watcher/state")
    urls = [u.strip() for u in re.split(r"[,\n]", os.environ["JOB_URLS"]) if u.strip()]
    state = load_state(param)
    results = {}

    for url in urls:
        key_live, key_gone = f"live:{url}", f"gone:{url}"
        status, apply_url = check(url)
        results[job_name(url)] = status

        if status == "live" and key_live not in state["notified"]:
            send_sms(f"🚨 Google Apply button is LIVE: {job_name(url)}\n"
                     f"Apply now: {apply_url or url}")
            state["notified"].append(key_live)
        elif status == "gone" and key_gone not in state["notified"]:
            send_sms(f"Google posting no longer resolves (taken down?): "
                     f"{job_name(url)}\n{url}")
            state["notified"].append(key_gone)

    save_state(param, state)
    print(json.dumps(results))
    return results
