#!/bin/bash
set -euo pipefail

# GroupIQ — Deploy to LocalStack (local AWS emulation)
# Prerequisites: Docker running, LocalStack container up (docker-compose up -d)

LOCALSTACK_URL="http://localhost:4566"
REGION="us-east-1"
ENVIRONMENT="local"

echo "============================================"
echo "  GroupIQ — Local Deployment (LocalStack)"
echo "============================================"
echo ""

# Check LocalStack is running
echo "[*] Checking LocalStack..."
if ! curl -s "$LOCALSTACK_URL/_localstack/health" > /dev/null 2>&1; then
    echo "    LocalStack is not running. Starting it..."
    docker-compose up -d
    echo "    Waiting for LocalStack to be ready..."
    sleep 10
fi
echo "    LocalStack is running."

export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=$REGION

# 1. Create DynamoDB tables
echo ""
echo "[1/6] Creating DynamoDB tables..."

aws --endpoint-url=$LOCALSTACK_URL dynamodb create-table \
    --table-name groupiq-bookings-$ENVIRONMENT \
    --key-schema AttributeName=booking_id,KeyType=HASH AttributeName=version,KeyType=RANGE \
    --attribute-definitions \
        AttributeName=booking_id,AttributeType=S \
        AttributeName=version,AttributeType=N \
        AttributeName=status,AttributeType=S \
        AttributeName=event_date,AttributeType=S \
    --global-secondary-indexes \
        "IndexName=status-index,KeySchema=[{AttributeName=status,KeyType=HASH},{AttributeName=event_date,KeyType=RANGE}],Projection={ProjectionType=ALL}" \
    --billing-mode PAY_PER_REQUEST \
    2>/dev/null || echo "    (bookings table already exists)"

aws --endpoint-url=$LOCALSTACK_URL dynamodb create-table \
    --table-name groupiq-negotiations-$ENVIRONMENT \
    --key-schema AttributeName=booking_id,KeyType=HASH AttributeName=turn_number,KeyType=RANGE \
    --attribute-definitions \
        AttributeName=booking_id,AttributeType=S \
        AttributeName=turn_number,AttributeType=N \
    --billing-mode PAY_PER_REQUEST \
    2>/dev/null || echo "    (negotiations table already exists)"

aws --endpoint-url=$LOCALSTACK_URL dynamodb create-table \
    --table-name groupiq-pricing-rules-$ENVIRONMENT \
    --key-schema AttributeName=property_id,KeyType=HASH AttributeName=rule_type,KeyType=RANGE \
    --attribute-definitions \
        AttributeName=property_id,AttributeType=S \
        AttributeName=rule_type,AttributeType=S \
    --billing-mode PAY_PER_REQUEST \
    2>/dev/null || echo "    (pricing-rules table already exists)"

echo "    DynamoDB tables created."

# 2. Create S3 bucket
echo ""
echo "[2/6] Creating S3 bucket..."
aws --endpoint-url=$LOCALSTACK_URL s3 mb s3://groupiq-proposals-$ENVIRONMENT 2>/dev/null || echo "    (bucket already exists)"
echo "    S3 bucket created."

# 3. Create SNS topic
echo ""
echo "[3/6] Creating SNS topic..."
TOPIC_ARN=$(aws --endpoint-url=$LOCALSTACK_URL sns create-topic --name groupiq-escalation-$ENVIRONMENT --query 'TopicArn' --output text)
echo "    Topic ARN: $TOPIC_ARN"

# 3b. Verify SES sender email (required for sending reminders/notifications)
echo "    Verifying SES sender email..."
aws --endpoint-url=$LOCALSTACK_URL ses verify-email-identity --email-address test@groupiq.local 2>/dev/null
echo "    SES sender verified: test@groupiq.local"

# 4. Seed pricing rules
echo ""
echo "[4/6] Seeding pricing rules..."
python3 -c "
import boto3, json
from decimal import Decimal

dynamodb = boto3.resource('dynamodb', endpoint_url='$LOCALSTACK_URL', region_name='$REGION',
    aws_access_key_id='test', aws_secret_access_key='test')
table = dynamodb.Table('groupiq-pricing-rules-$ENVIRONMENT')

rules = [
    {
        'property_id': 'MRIOTT-NYC-001',
        'rule_type': 'room_rate',
        'base_rate': Decimal('299'),
        'peak_rate': Decimal('399'),
        'floor_rate': Decimal('254'),
    },
    {
        'property_id': 'MRIOTT-NYC-001',
        'rule_type': 'fnb_pricing',
        'breakfast_per_person': Decimal('45'),
        'lunch_per_person': Decimal('65'),
        'dinner_per_person': Decimal('95'),
    },
    {
        'property_id': 'MRIOTT-NYC-001',
        'rule_type': 'negotiation_bounds',
        'max_room_discount_percent': Decimal('15'),
        'max_fnb_discount_percent': Decimal('10'),
    },
]

for rule in rules:
    table.put_item(Item=rule)
    print(f'    Loaded: {rule[\"property_id\"]} / {rule[\"rule_type\"]}')
"
echo "    Pricing rules seeded."

# 5. Create Lambda functions (using local executor)
echo ""
echo "[5/6] Creating Lambda functions..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

for FUNC in intake proposal_generator negotiation_agent notification reminder tipai_governance; do
    echo "    Creating $FUNC..."
    cd "$PROJECT_ROOT/lambdas/$FUNC"

    rm -rf /tmp/groupiq-$FUNC && mkdir -p /tmp/groupiq-$FUNC
    cp handler.py /tmp/groupiq-$FUNC/
    cp "$PROJECT_ROOT/lambdas/common/utils.py" /tmp/groupiq-$FUNC/
    chmod 644 /tmp/groupiq-$FUNC/*.py
    cd /tmp/groupiq-$FUNC && zip -r /tmp/groupiq-$FUNC.zip . > /dev/null

    # Delete existing function first to avoid Pending state conflicts
    aws --endpoint-url=$LOCALSTACK_URL lambda delete-function \
        --function-name groupiq-$FUNC-$ENVIRONMENT > /dev/null 2>&1 || true

    aws --endpoint-url=$LOCALSTACK_URL lambda create-function \
        --function-name groupiq-$FUNC-$ENVIRONMENT \
        --runtime python3.12 \
        --handler handler.lambda_handler \
        --role arn:aws:iam::000000000000:role/groupiq-lambda \
        --zip-file fileb:///tmp/groupiq-$FUNC.zip \
        --environment "Variables={BOOKINGS_TABLE=groupiq-bookings-$ENVIRONMENT,PRICING_TABLE=groupiq-pricing-rules-$ENVIRONMENT,NEGOTIATIONS_TABLE=groupiq-negotiations-$ENVIRONMENT,PROPOSALS_BUCKET=groupiq-proposals-$ENVIRONMENT,BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0,MAX_DISCOUNT_PERCENT=15,ESCALATION_TOPIC_ARN=$TOPIC_ARN,SES_SENDER_EMAIL=test@groupiq.local,REMINDER_DAYS_BEFORE=2,ENVIRONMENT=$ENVIRONMENT}" \
        --timeout 120 \
        > /dev/null
done
echo "    All Lambda functions deployed."

# 6. Create API Gateway
echo ""
echo "[6/6] Creating API Gateway..."
API_ID=$(aws --endpoint-url=$LOCALSTACK_URL apigatewayv2 create-api \
    --name groupiq-api-$ENVIRONMENT \
    --protocol-type HTTP \
    --query 'ApiId' --output text 2>/dev/null || echo "")

if [ -n "$API_ID" ]; then
    # Create routes
    INTAKE_INT=$(aws --endpoint-url=$LOCALSTACK_URL apigatewayv2 create-integration \
        --api-id $API_ID \
        --integration-type AWS_PROXY \
        --integration-uri "arn:aws:lambda:$REGION:000000000000:function:groupiq-intake-$ENVIRONMENT" \
        --payload-format-version "2.0" \
        --query 'IntegrationId' --output text)

    aws --endpoint-url=$LOCALSTACK_URL apigatewayv2 create-route \
        --api-id $API_ID \
        --route-key "POST /inquiries" \
        --target "integrations/$INTAKE_INT" > /dev/null

    NEGOTIATE_INT=$(aws --endpoint-url=$LOCALSTACK_URL apigatewayv2 create-integration \
        --api-id $API_ID \
        --integration-type AWS_PROXY \
        --integration-uri "arn:aws:lambda:$REGION:000000000000:function:groupiq-negotiation_agent-$ENVIRONMENT" \
        --payload-format-version "2.0" \
        --query 'IntegrationId' --output text)

    aws --endpoint-url=$LOCALSTACK_URL apigatewayv2 create-route \
        --api-id $API_ID \
        --route-key "POST /inquiries/{bookingId}/negotiate" \
        --target "integrations/$NEGOTIATE_INT" > /dev/null

    echo "    API Gateway created: $LOCALSTACK_URL/restapis/$API_ID/local/_user_request_"
fi

echo ""
echo "============================================"
echo "  LOCAL DEPLOYMENT COMPLETE"
echo "============================================"
echo ""
echo "  DynamoDB:  $LOCALSTACK_URL"
echo "  S3:        $LOCALSTACK_URL"
echo "  Lambda:    $LOCALSTACK_URL"
echo "  API:       $LOCALSTACK_URL/restapis/$API_ID/local/_user_request_/inquiries"
echo ""
echo "  Test with:"
echo "    curl -X POST $LOCALSTACK_URL/restapis/$API_ID/local/_user_request_/inquiries \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"contact_name\":\"Test User\",\"contact_email\":\"test@test.com\",\"event_type\":\"conference\",\"event_date\":\"2026-09-15\",\"num_rooms\":50,\"num_nights\":3,\"property_id\":\"MRIOTT-NYC-001\"}'"
echo ""
