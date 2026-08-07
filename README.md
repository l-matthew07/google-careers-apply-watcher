# Google Careers Apply-Button Watcher

Watches one or more Google Careers job postings until their **Apply** button
goes live, then notifies you — locally via macOS notification, or from the
cloud via **Twilio SMS** (AWS Lambda, checks every 5 minutes even while your
laptop is closed).

## How it works

Live Google Careers postings server-render the apply link
(`<a href="./apply?jobId=…">` next to an "Apply" label) directly into the
page HTML, so a plain HTTP fetch detects it — no browser automation needed.
A posting that's up but not yet accepting applications simply lacks that link.

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

## Cloud mode (AWS Lambda + Twilio SMS)

Runs every 5 minutes on AWS free tier and **texts you** the moment a button
appears — one SMS per event, deduped via SSM Parameter Store state.

One-time setup:

```bash
aws configure                      # your AWS access key + region
cd aws
cp .env.example .env               # fill in Twilio creds + JOB_URLS
./deploy.sh
```

`deploy.sh` is idempotent — it creates (or updates) the IAM role, the
`apply-watcher` Lambda (python3.12, stdlib-only zip), and a
`rate(5 minutes)` EventBridge schedule, then invokes it once as a smoke
test. Re-run it after changing `.env` or the code.

Twilio: create an account at twilio.com, buy a number (~$1/mo, trial credit
covers it), and copy the Account SID + Auth Token from the console into
`aws/.env`. **`.env` is gitignored — never commit credentials.**

Watch it run:

```bash
aws logs tail /aws/lambda/apply-watcher --follow
```

Cost: ~8,600 invocations/month — comfortably inside the Lambda and
EventBridge free tiers; the only real cost is the Twilio number.
