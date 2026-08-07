"""Shared helpers: apply-button detection, SES email, Twilio SMS."""

import base64
import html as html_lib
import os
import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
APPLY_HREF_RE = re.compile(r'href="(\./apply\?jobId=[^"]+)"')
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def job_id_from_url(url: str) -> str | None:
    m = re.search(r"/jobs/results/([^/?#]+)", url)
    return m.group(1) if m else None


def job_name(url: str) -> str:
    m = re.search(r"/jobs/results/\d+-([a-z0-9-]+)", url)
    return m.group(1).replace("-", " ") if m else "your watched Google job"


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


def send_sms(to: str, body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    data = urllib.parse.urlencode({
        "From": os.environ["TWILIO_FROM"],
        "To": to,
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
    with urllib.request.urlopen(req, timeout=15):
        pass


def send_email(ses_client, to: str, subject: str, body: str) -> None:
    ses_client.send_email(
        FromEmailAddress=os.environ["SES_SENDER"],
        Destination={"ToAddresses": [to]},
        Content={"Simple": {
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        }},
    )
