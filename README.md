# google-apply-notifier

Watches Google Careers job postings and sends an alert the instant the **Apply** button becomes clickable — useful for roles that are listed before applications formally open.

## How it works

Google Careers job cards render an apply element like:

```html
<a id="apply-action-button" aria-label="Apply" href="./apply?jobId=...">
```

The script polls each configured job URL, parses the returned HTML, and classifies the apply element into one of three states:

| Status     | Meaning                                                              |
|------------|-----------------------------------------------------------------------|
| `open`     | A live `<a aria-label="Apply">` with a working `href` is present.     |
| `disabled` | The element exists in the DOM but has no usable `href` (not yet active). |
| `missing`  | No apply element found at all (page structure changed, job closed, or blocked). |

A notification fires only on a transition **into** `open`, and state is persisted to disk so restarts don't re-send duplicate alerts.

## Features

- Robust selector (`aria-label="Apply"` + `href` check) instead of brittle, frequently-rotated Google CSS class names.
- Exponential backoff retries on network errors.
- Randomized jitter on the poll interval to avoid a fixed, bot-like cadence.
- De-duplicated, state-persisted alerts (`state.json`).
- Pluggable notifications: console (always on), Slack webhook, generic webhook (Discord/ntfy/etc.), and email/SMTP.
- Rotating log file plus stdout logging.
- Graceful shutdown on `Ctrl+C` / `SIGTERM`.
- Supports watching multiple job postings at once.

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit with your job URLs
```

## Usage

```bash
# Using a config file
python apply_notifier.py --config config.yaml

# Or pass one or more URLs directly on the CLI
python apply_notifier.py --url "https://www.google.com/about/careers/applications/jobs/results/..." --interval 300

# Run a single check and exit (good for cron / CI instead of a long-running loop)
python apply_notifier.py --config config.yaml --once
```

## Notifications

Enable any combination via environment variables:

| Channel  | Required env vars |
|----------|--------------------|
| Slack    | `SLACK_WEBHOOK_URL` |
| Webhook (Discord/ntfy/custom) | `WEBHOOK_URL` |
| Email    | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_EMAIL_TO` |

Console logging is always on regardless of the above.

## Notes / limitations

- This only detects a DOM-level "Apply" link becoming active. If Google changes the markup or blocks automated requests (e.g., CAPTCHA), the script will log `missing` status rather than crash, but won't know a role opened until the selector is updated.
- Respect Google's Terms of Service and `robots.txt` when polling; keep intervals reasonable (default is 5 minutes) and avoid tight, aggressive loops.
- This is a monitoring tool only — it does not submit applications on your behalf.

## License

MIT
