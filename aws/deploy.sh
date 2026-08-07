#!/usr/bin/env bash
# Deploys the apply-watcher Lambda + a 5-minute EventBridge schedule.
# Idempotent: re-run after editing lambda_function.py or .env to update.
#
# Prereqs:
#   1. aws configure          (your AWS access key, e.g. region us-east-1)
#   2. cp .env.example .env   and fill in Twilio creds + job URLs
set -euo pipefail
cd "$(dirname "$0")"

FUNC=apply-watcher
ROLE=apply-watcher-role
RULE=apply-watcher-every-5min
STATE_PARAM=/apply-watcher/state

[ -f .env ] || { echo "Missing aws/.env — copy .env.example and fill it in."; exit 1; }
set -a; source .env; set +a
: "${JOB_URLS:?}" "${TWILIO_ACCOUNT_SID:?}" "${TWILIO_AUTH_TOKEN:?}" "${TWILIO_FROM:?}" "${TWILIO_TO:?}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
echo "Deploying to account $ACCOUNT, region $REGION"

# --- IAM role ---------------------------------------------------------------
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "Created role $ROLE (waiting for it to propagate...)"; sleep 10
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name ssm-state --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{\"Effect\": \"Allow\",
    \"Action\": [\"ssm:GetParameter\", \"ssm:PutParameter\"],
    \"Resource\": \"arn:aws:ssm:$REGION:$ACCOUNT:parameter$STATE_PARAM\"}]}"

# --- Lambda -----------------------------------------------------------------
rm -f function.zip && zip -q function.zip lambda_function.py

ENV_JSON=$(python3 - <<'PY'
import json, os
print(json.dumps({"Variables": {k: os.environ[k] for k in
  ["JOB_URLS","TWILIO_ACCOUNT_SID","TWILIO_AUTH_TOKEN","TWILIO_FROM","TWILIO_TO"]}
  | {"STATE_PARAM": os.environ.get("STATE_PARAM", "/apply-watcher/state")}))
PY
)

if aws lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNC" \
    --zip-file fileb://function.zip >/dev/null
  aws lambda wait function-updated --function-name "$FUNC"
  aws lambda update-function-configuration --function-name "$FUNC" \
    --environment "$ENV_JSON" --timeout 60 >/dev/null
else
  aws lambda create-function --function-name "$FUNC" \
    --runtime python3.12 --handler lambda_function.lambda_handler \
    --role "arn:aws:iam::$ACCOUNT:role/$ROLE" \
    --zip-file fileb://function.zip \
    --environment "$ENV_JSON" --timeout 60 >/dev/null
fi
aws lambda wait function-updated --function-name "$FUNC"
echo "Lambda $FUNC deployed."

# --- Schedule ---------------------------------------------------------------
aws events put-rule --name "$RULE" --schedule-expression "rate(5 minutes)" >/dev/null
aws lambda add-permission --function-name "$FUNC" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:$REGION:$ACCOUNT:rule/$RULE" >/dev/null 2>&1 || true
aws events put-targets --rule "$RULE" --targets \
  "Id=1,Arn=arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNC" >/dev/null
echo "Scheduled: every 5 minutes."

# --- Smoke test -------------------------------------------------------------
echo "Invoking once now..."
aws lambda invoke --function-name "$FUNC" --payload '{}' /dev/stdout | cat
echo
echo "Done. Logs: aws logs tail /aws/lambda/$FUNC --follow"
