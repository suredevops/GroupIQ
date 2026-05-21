#!/bin/bash
set -euo pipefail

# GroupIQ — Seed DynamoDB with sample pricing rules

AWS_REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
TABLE_NAME="groupiq-pricing-rules-${ENVIRONMENT}"

echo "=== Seeding pricing rules into $TABLE_NAME ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/sample_data.json"

# Extract and load each pricing rule
python3 -c "
import json, boto3, sys
from decimal import Decimal

with open('$CONFIG_FILE') as f:
    data = json.load(f)

dynamodb = boto3.resource('dynamodb', region_name='$AWS_REGION')
table = dynamodb.Table('$TABLE_NAME')

for rule in data['sample_pricing_rules']:
    item = json.loads(json.dumps(rule), parse_float=Decimal)
    table.put_item(Item=item)
    print(f'  Loaded: {rule[\"property_id\"]} / {rule[\"rule_type\"]}')

print('Done — all pricing rules seeded.')
"
