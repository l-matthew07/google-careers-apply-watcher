"""
POST /subscribe  {email, job_url, phone?, channels?: ["email","sms"]}
GET  /confirm?token=...

Writes a pending subscription to DynamoDB and emails a confirmation link
(double opt-in). Only confirmed subscriptions are notified.

Table layout (single table, on-demand):
    SUB#<email>   JOB#<jobId>   subscription (gsi1pk=JOB#<jobId> once verified)
    JOB#<jobId>   STATE         job watch state, shared by all subscribers
    TOKEN#<tok>   TOKEN         pending-confirmation pointer (TTL'd)
"""

import json
import os
import secrets
import time

import boto3

from common import EMAIL_RE, PHONE_RE, job_id_from_url, send_email

ddb = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
ses = boto3.client("sesv2")

CONFIRM_TTL_SECONDS = 48 * 3600


def _resp(status: int, body: dict, content_type="application/json"):
    payload = json.dumps(body) if content_type == "application/json" else body
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        },
        "body": payload,
    }


def _subscribe(event):
    try:
        data = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid JSON"})

    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip().replace(" ", "").replace("-", "")
    job_url = (data.get("job_url") or "").strip()
    channels = data.get("channels") or ["email"]

    if not EMAIL_RE.match(email):
        return _resp(400, {"error": "invalid email"})
    if phone and not PHONE_RE.match(phone):
        return _resp(400, {"error": "phone must be E.164, e.g. +15551234567"})
    if "sms" in channels and not phone:
        return _resp(400, {"error": "sms channel requires a phone number"})
    job_id = job_id_from_url(job_url)
    if not job_id or "google.com/about/careers" not in job_url:
        return _resp(400, {"error": "not a Google Careers job URL"})

    token = secrets.token_urlsafe(32)
    now = int(time.time())

    ddb.put_item(Item={
        "pk": f"SUB#{email}", "sk": f"JOB#{job_id}",
        "email": email, "phone": phone, "channels": channels,
        "job_url": job_url, "job_id": job_id,
        "verified": False, "created": now,
    })
    ddb.put_item(Item={
        "pk": f"TOKEN#{token}", "sk": "TOKEN",
        "email": email, "job_id": job_id, "ttl": now + CONFIRM_TTL_SECONDS,
    })
    # Ensure the job itself is being watched (idempotent).
    try:
        ddb.put_item(
            Item={"pk": f"JOB#{job_id}", "sk": "STATE",
                  "job_url": job_url, "status": "waiting", "created": now},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        pass

    confirm_url = f"{os.environ['API_BASE_URL']}/confirm?token={token}"
    send_email(
        ses, email,
        "Confirm your Google Careers apply alert",
        f"You (or someone using this address) asked to be alerted when this "
        f"Google job opens for applications:\n\n  {job_url}\n\n"
        f"Confirm to activate the alert:\n\n  {confirm_url}\n\n"
        f"If this wasn't you, ignore this email and nothing will be sent.",
    )
    return _resp(200, {"ok": True, "message": "confirmation email sent"})


def _confirm(event):
    token = (event.get("queryStringParameters") or {}).get("token", "")
    got = ddb.get_item(Key={"pk": f"TOKEN#{token}", "sk": "TOKEN"}).get("Item")
    if not got or got.get("ttl", 0) < time.time():
        return _resp(400, "<h1>Link expired or invalid.</h1>", "text/html")

    email, job_id = got["email"], got["job_id"]
    # gsi1 keys are only set on verified items, so the watcher's fan-out
    # query never sees unconfirmed subscribers.
    ddb.update_item(
        Key={"pk": f"SUB#{email}", "sk": f"JOB#{job_id}"},
        UpdateExpression="SET verified = :t, gsi1pk = :j, gsi1sk = :s",
        ExpressionAttributeValues={
            ":t": True, ":j": f"JOB#{job_id}", ":s": f"SUB#{email}",
        },
    )
    ddb.delete_item(Key={"pk": f"TOKEN#{token}", "sk": "TOKEN"})
    return _resp(200, "<h1>Alert confirmed ✓</h1><p>You can close this tab.</p>",
                 "text/html")


def lambda_handler(event, context):
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {})
    if path.endswith("/subscribe") and method == "POST":
        return _subscribe(event)
    if path.endswith("/confirm") and method == "GET":
        return _confirm(event)
    return _resp(404, {"error": "not found"})
