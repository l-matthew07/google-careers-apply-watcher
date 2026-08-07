# Google Careers Apply-Button Watcher

Polls a Google Careers job posting until its **Apply** button goes live, then:
- pops a macOS notification (with sound), and
- opens the posting in your browser so you can apply immediately.

## How it works

Live Google Careers postings server-render the apply link
(`<a href="./apply?jobId=…">` next to an "Apply" label) directly into the
page HTML, so a plain HTTP fetch detects it — no browser automation needed.
A posting that's up but not yet accepting applications simply lacks that link.

## Usage

```bash
python3 apply_watcher.py "<job posting URL>"
```

The URL is the full posting link, e.g.
`https://www.google.com/about/careers/applications/jobs/results/123456-software-engineer-iii-new-grad`

Options:
- `--interval 120` — seconds between checks (default 180, plus a little random jitter to be polite)
- `--once` — single check, then exit (exit code 0 = live or still waiting, 1 = URL doesn't resolve to a posting)

Run it so it survives your Mac sleeping / terminal closing:

```bash
caffeinate -i python3 apply_watcher.py "<job URL>" | tee watch.log
```

(`caffeinate -i` keeps the Mac from idle-sleeping while it watches.)

## What it reports

- **`No apply button yet`** — posting is up, button not live; keeps polling
- **`APPLY BUTTON IS LIVE!`** — prints the direct apply link, notifies, opens the page, exits
- **`URL doesn't resolve to a job posting`** — the posting was taken down or the URL is mistyped (Google serves a generic "Jobs search" shell for dead URLs); notifies and exits
- Transient network errors are retried; you get a notification if 10 checks in a row fail

Python 3 standard library only — nothing to install.
