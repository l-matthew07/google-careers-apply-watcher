# Apply-notifier platform (250k+ subscribers)

Serverless architecture for serving apply-button alerts to a large
subscriber base. Cost of *watching* scales with the number of distinct
jobs (small); cost of *notifying* scales with subscribers per event.

```
signup page (S3/CloudFront)
      │ POST /subscribe
      ▼
API Gateway ── intake Lambda ──► DynamoDB (subscriptions, double opt-in via SES email)
                                    ▲
EventBridge (5 min) ── watcher ─────┘ (job STATE items)
                          │ job went live (1 msg)
                          ▼
                    fan-out queue ── fanout Lambda (pages GSI, self-continues)
                          │ 1 msg per subscriber
                          ▼
                    notify queue ── notifier Lambda ──► SES email / Twilio SMS
                          │ (reserved concurrency = SMS rate valve)
                          ▼
                        DLQ (failed sends, 14-day retention)
```

## Key design points

- **Double opt-in.** `/subscribe` stores an unverified row and emails a
  confirmation link; only confirmed rows get GSI keys, so fan-out never
  sees unverified subscribers. Required for TCPA-sane SMS anyway.
- **Exactly-one-notification intent.** A `SENT#job/SUB#email` item dedupes
  across SQS's at-least-once delivery (check → send → mark, so a failure
  retries rather than silently dropping).
- **Fan-out self-continuation.** One "job live" event; the fanout worker
  pages through subscribers and re-enqueues itself with a cursor before
  timing out — 250k rows fan out across however many invocations needed.
- **SMS throughput valve.** `NotifierConcurrency` caps parallel sends;
  set it to match your registered Twilio throughput (10DLC or short code).
  Excess simply waits in the queue.

## Deploy

```bash
cd platform
sam build
sam deploy --guided     # prompts for SES sender, Twilio creds, CORS origin
```

Then host `site/index.html` anywhere static (S3 + CloudFront), replacing
`%%API_BASE_URL%%` with the `ApiBaseUrl` stack output.

## Before this serves real traffic

1. **SES production access** — sandbox only sends to verified addresses.
   Verify your domain (SPF/DKIM), request production access, and wire
   bounce/complaint SNS notifications to a suppression handler.
2. **Twilio A2P registration** — an unregistered number cannot send at
   volume. Register a 10DLC campaign or get a short code; set
   `NotifierConcurrency` to the resulting throughput. Handle inbound
   `STOP` via a Twilio webhook (unsubscribe the number).
3. **Cost awareness** — one SMS blast to 250k US numbers is ~$2,000 in
   Twilio fees. Email via SES is ~$25 per 250k. Consider email-default,
   SMS opt-in.
4. **Abuse controls** — add API Gateway throttling (per-IP) and a CAPTCHA
   on the signup page before publicizing the URL.
