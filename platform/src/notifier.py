"""
Notify worker: consumes per-subscriber messages, sends email (SES) and/or
SMS (Twilio), exactly once per subscriber per job.

Dedup is a conditional put on a SENT item, so SQS's at-least-once
delivery never double-texts anyone. Failures are reported per-message
(partial batch response) so only the failed sends retry; after the queue's
retry budget they land in the dead-letter queue.

Reserved concurrency on this function is the SMS throughput valve — size
it to your registered Twilio sending rate (10DLC/short code).
"""

import json
import os

import boto3

from common import job_name, send_email, send_sms

ddb = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
ses = boto3.client("sesv2")


def _notify(msg: dict) -> None:
    """Check -> send -> mark. A crash between send and mark can cause a
    rare duplicate on retry; the reverse order would turn any failed send
    into a permanently lost notification, which is worse."""
    name = job_name(msg["job_url"])
    sent_key = {"pk": f"SENT#{msg['job_id']}", "sk": f"SUB#{msg['email']}"}
    if "Item" in ddb.get_item(Key=sent_key):
        return

    if "email" in msg["channels"]:
        send_email(
            ses, msg["email"],
            f"Apply now — {name} is open",
            f"The Google job you're watching is now accepting applications:\n\n"
            f"  {name}\n  {msg['apply_url']}\n\n"
            f"Good luck!",
        )
    if "sms" in msg["channels"] and msg.get("phone"):
        send_sms(
            msg["phone"],
            f"Google apply button is LIVE: {name}\n{msg['apply_url']}",
        )
    ddb.put_item(Item=sent_key)


def lambda_handler(event, context):
    failures = []
    for record in event["Records"]:
        try:
            _notify(json.loads(record["body"]))
        except Exception as e:  # report + retry just this message
            print(f"send failed: {e}")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
