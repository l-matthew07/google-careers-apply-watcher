# Google Careers Apply-Button Watcher

Watches one or more Google Careers job postings until their **Apply** button
goes live, then notifies you. Useful for roles that are listed before
applications formally open.

## How it works

Live Google Careers postings server-render the apply link
(`<a id="apply-action-button" aria-label="Apply" href="./apply?jobId=…">`)
directly into the page HTML, so a plain HTTP fetch detects it — no browser
automation needed. A posting that's up but not yet accepting applications
simply lacks that link.

This repo contains three implementations — pick the one that fits how you
want to run it:

| Implementation | Runs on | Alerts via |
|----------------|---------|------------|
| [`aws/`](#cloud-mode-aws-lambda--imessage) | AWS Lambda (free tier) | **iMessage** via Photon Spectrum — the current MVP |
| [`apply_watcher.py`](#local-mode-macos) | Your Mac (stdlib only) | macOS notification |
| [`apply_notifier.py`](#apply_notifierpy-slack--email--webhook) | Anywhere with Python | Slack / email / webhook |

## Local mode (macOS)

```bash
python3 apply_watcher.py "<job URL>" "<another job URL>" --interval 120
# or keep the list in a file (one URL per line):
python3 apply_watcher.py --urls-file urls.txt
```

When a button goes live it pops a notification with sound, prints the direct
apply link, and opens the page. Dead/mistyped URLs are flagged and dropped
(Google serves a generic "Jobs search" shell for those, recognized by the
page title). Run under `caffeinate -i … | tee watch.log` to keep the Mac
awake and keep a timestamped log.

Python 3 standard library only — nothing to install.

## Cloud mode (AWS Lambda + iMessage)

Runs every 5 minutes on AWS free tier and **iMessages everyone in
`RECIPIENTS`** the moment a button appears — one DM per event per
recipient, sent through [Photon Spectrum](https://photon.codes/docs/spectrum-ts)
Cloud, deduped via SSM Parameter Store state.

One-time setup:

```bash
aws configure                      # your AWS access key + region
cd aws
cp .env.example .env               # fill in Photon creds + JOB_URLS + RECIPIENTS
./deploy.sh                        # needs bun installed (builds the bundle)
```

`deploy.sh` is idempotent — it creates (or updates) the IAM role, the
`apply-watcher` Lambda (nodejs22.x, bundled with bun), and a
`rate(5 minutes)` EventBridge schedule, then invokes it once as a smoke
test. Re-run it after changing `.env` or the code.

Photon: `PROJECT_ID` / `PROJECT_SECRET` come from your project Settings
on the [dashboard](https://app.photon.codes). Every number in
`RECIPIENTS` must be registered there as a project user (shared-pool
plan requirement). **`.env` is gitignored — never commit credentials.**

Watch it run:

```bash
aws logs tail /aws/lambda/apply-watcher --follow
```

Cost: ~8,600 invocations/month — comfortably inside the Lambda and
EventBridge free tiers; Photon's free plan covers the messaging.

## `apply_notifier.py` (Slack / email / webhook)

A more configurable watcher (from
[bartut22/google-apply-notifier](https://github.com/bartut22/google-apply-notifier))
that classifies the apply element into one of three states:

| Status     | Meaning                                                              |
|------------|-----------------------------------------------------------------------|
| `open`     | A live `<a aria-label="Apply">` with a working `href` is present.     |
| `disabled` | The element exists in the DOM but has no usable `href` (not yet active). |
| `missing`  | No apply element found at all (page structure changed, job closed, or blocked). |

A notification fires only on a transition **into** `open`, and state is
persisted to disk (`state.json`) so restarts don't re-send duplicate alerts.

Features:

- Robust selector (`aria-label="Apply"` + `href` check) instead of brittle, frequently-rotated Google CSS class names.
- Exponential backoff retries on network errors.
- Randomized jitter on the poll interval to avoid a fixed, bot-like cadence.
- Pluggable notifications: console (always on), Slack webhook, generic webhook (Discord/ntfy/etc.), and email/SMTP.
- Rotating log file plus stdout logging.
- Graceful shutdown on `Ctrl+C` / `SIGTERM`.

Setup:

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit with your job URLs
cp .env.example .env                 # optional: notification credentials
```

Usage:

```bash
# Using a config file
python apply_notifier.py --config config.yaml

# Or pass one or more URLs directly on the CLI
python apply_notifier.py --url "https://www.google.com/about/careers/applications/jobs/results/..." --interval 300

# Run a single check and exit (good for cron / CI instead of a long-running loop)
python apply_notifier.py --config config.yaml --once
```

Enable notification channels via environment variables (or `.env`):

| Channel  | Required env vars |
|----------|--------------------|
| Slack    | `SLACK_WEBHOOK_URL` |
| Webhook (Discord/ntfy/custom) | `WEBHOOK_URL` |
| Email    | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_EMAIL_TO` |

## Serving many subscribers (`platform/`)

The modes above are single-user. [`platform/`](platform/README.md) is the
multi-subscriber architecture: a signup page → API Gateway → Lambda →
DynamoDB intake with email double opt-in, and a scheduled watcher that
fans out through SQS to SES email and Twilio SMS workers. Built to serve
250k+ subscribers; see its README for deploy steps and the SES/Twilio
compliance checklist that scale requires. **Currently parked as the
phase-2 path** — distribution happens via social for now, and this stack
gets picked back up when signup opens to the public.

## Notes / limitations

- This only detects a DOM-level "Apply" link becoming active. If Google changes the markup or blocks automated requests (e.g., CAPTCHA), the watchers log a `missing` status rather than crash, but won't know a role opened until the selector is updated.
- Respect Google's Terms of Service and `robots.txt` when polling; keep intervals reasonable (default is 5 minutes) and avoid tight, aggressive loops.
- This is a monitoring tool only — it does not submit applications on your behalf.
