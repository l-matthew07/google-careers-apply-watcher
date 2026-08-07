"""
EventBridge (every 5 min) -> check each watched job -> on transition to
"live", record it and drop ONE fan-out message on the fan-out queue.

Cost scales with the number of distinct jobs (small), never with the
number of subscribers.
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

from common import check

ddb = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
sqs = boto3.client("sqs")
FANOUT_QUEUE = os.environ["FANOUT_QUEUE_URL"]


def watched_jobs():
    # STATE items only — one per distinct job, so this scan stays tiny.
    kwargs = {}
    while True:
        page = ddb.scan(
            FilterExpression=Key("sk").eq("STATE"), **kwargs)
        yield from page["Items"]
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def lambda_handler(event, context):
    results = {}
    for job in watched_jobs():
        job_id, url = job["pk"].removeprefix("JOB#"), job["job_url"]
        prev = job.get("status", "waiting")
        status, apply_url = check(url)
        results[job_id] = status

        if status in ("waiting", "error"):
            continue
        if status == prev:  # already handled this transition
            continue

        ddb.update_item(
            Key={"pk": job["pk"], "sk": "STATE"},
            UpdateExpression="SET #s = :s, apply_url = :a",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":a": apply_url or ""},
        )
        if status == "live":
            sqs.send_message(
                QueueUrl=FANOUT_QUEUE,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "job_url": url,
                    "apply_url": apply_url or url,
                }),
            )
            print(f"LIVE: {job_id} — fan-out queued")

    print(json.dumps(results))
    return results
