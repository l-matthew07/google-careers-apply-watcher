"""
Fan-out worker: one "job went live" message in -> per-subscriber
notification messages out.

Pages through the job's verified subscribers via GSI1 and batch-sends
them to the notify queue. If the Lambda nears its timeout mid-way, it
re-enqueues itself with a pagination cursor, so a 250k-subscriber job
fans out across as many invocations as it needs.
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
sqs = boto3.client("sqs")
FANOUT_QUEUE = os.environ["FANOUT_QUEUE_URL"]
NOTIFY_QUEUE = os.environ["NOTIFY_QUEUE_URL"]

SAFETY_MS = 30_000  # re-enqueue when less than this remains


def _batches(items, size=10):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fan_out(msg: dict, context) -> None:
    kwargs = {}
    if "cursor" in msg:
        kwargs["ExclusiveStartKey"] = msg["cursor"]

    while True:
        page = ddb.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(f"JOB#{msg['job_id']}"),
            **kwargs,
        )
        for batch in _batches(page["Items"]):
            sqs.send_message_batch(
                QueueUrl=NOTIFY_QUEUE,
                Entries=[{
                    "Id": str(i),
                    "MessageBody": json.dumps({
                        "job_id": msg["job_id"],
                        "job_url": msg["job_url"],
                        "apply_url": msg["apply_url"],
                        "email": item["email"],
                        "phone": item.get("phone", ""),
                        "channels": list(item.get("channels", ["email"])),
                    }),
                } for i, item in enumerate(batch)],
            )

        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return
        kwargs["ExclusiveStartKey"] = cursor
        if context.get_remaining_time_in_millis() < SAFETY_MS:
            sqs.send_message(
                QueueUrl=FANOUT_QUEUE,
                MessageBody=json.dumps({**msg, "cursor": cursor}),
            )
            print(f"re-enqueued with cursor for {msg['job_id']}")
            return


def lambda_handler(event, context):
    for record in event["Records"]:
        _fan_out(json.loads(record["body"]), context)
