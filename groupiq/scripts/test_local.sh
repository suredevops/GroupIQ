#!/bin/bash
set -euo pipefail

# GroupIQ — Run local tests directly (no Docker required)
# Invokes Lambda handlers as plain Python functions

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "  GroupIQ — Local Integration Test"
echo "============================================"
echo ""

export AWS_ACCESS_KEY_ID=testing
export AWS_SECRET_ACCESS_KEY=testing
export AWS_DEFAULT_REGION=us-east-1
export AWS_REGION=us-east-1
export BOOKINGS_TABLE=groupiq-bookings-test
export PRICING_TABLE=groupiq-pricing-rules-test
export NEGOTIATIONS_TABLE=groupiq-negotiations-test
export PROPOSALS_BUCKET=groupiq-proposals-test
export BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
export MAX_DISCOUNT_PERCENT=15
export ESCALATION_TOPIC_ARN=arn:aws:sns:us-east-1:123456789012:test
export SES_SENDER_EMAIL=test@groupiq.local
export ENVIRONMENT=test

cd "$PROJECT_ROOT"

python3 -c "
import sys, json, os
sys.path.insert(0, 'lambdas/common')
sys.path.insert(0, 'lambdas/intake')

from utils import generate_booking_id, utc_now_iso, build_response, BookingStatus

# Test 1: Utility functions
print('[TEST 1] Utility functions...')
bid = generate_booking_id()
assert bid.startswith('GRP-'), f'Bad booking ID: {bid}'
ts = utc_now_iso()
assert 'T' in ts, f'Bad timestamp: {ts}'
resp = build_response(200, {'ok': True})
assert resp['statusCode'] == 200
print(f'  PASS — Booking ID: {bid}')
print(f'  PASS — Timestamp: {ts}')
print(f'  PASS — Response format correct')

# Test 2: Validation
print()
print('[TEST 2] Inquiry validation...')
from handler import validate_inquiry

valid_inquiry = {
    'contact_name': 'Sarah Johnson',
    'contact_email': 'sarah@test.com',
    'event_type': 'conference',
    'event_date': '2026-09-15',
    'num_rooms': 75,
    'num_nights': 3,
    'property_id': 'MRIOTT-NYC-001',
}
errors = validate_inquiry(valid_inquiry)
assert errors == [], f'Valid inquiry should pass: {errors}'
print('  PASS — Valid inquiry accepted')

invalid_inquiry = {'contact_name': 'Test'}
errors = validate_inquiry(invalid_inquiry)
assert len(errors) > 0, 'Invalid inquiry should fail'
print(f'  PASS — Invalid inquiry rejected ({len(errors)} errors)')

low_rooms = {**valid_inquiry, 'num_rooms': 5}
errors = validate_inquiry(low_rooms)
assert any('minimum 10' in e for e in errors)
print('  PASS — Minimum rooms check works')

# Test 3: Status constants
print()
print('[TEST 3] Status constants...')
assert BookingStatus.INQUIRY_RECEIVED == 'INQUIRY_RECEIVED'
assert BookingStatus.ACCEPTED == 'ACCEPTED'
assert BookingStatus.ESCALATED == 'ESCALATED'
print('  PASS — All booking statuses defined')

print()
print('============================================')
print('  ALL LOCAL TESTS PASSED')
print('============================================')
"
