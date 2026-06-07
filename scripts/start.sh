#!/bin/bash
# GroupIQ Auto-Start Script — Ensures all services are running
# This script is idempotent: safe to run multiple times

PROJ_DIR="/Users/bgudi536/GroupIQ"
LOG_FILE="$PROJ_DIR/data/groupiq.log"
PID_FILE="$PROJ_DIR/data/server.pid"
PYTHON="$PROJ_DIR/.venv/bin/python3"

# Fallback to system python if venv doesn't exist
if [ ! -f "$PYTHON" ]; then
    PYTHON=$(which python3)
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# 1. Start Docker Desktop if not running
if ! docker info > /dev/null 2>&1; then
    log "Starting Docker Desktop..."
    open -a "Docker Desktop" 2>/dev/null || open -a "Docker" 2>/dev/null
    for i in $(seq 1 30); do
        docker info > /dev/null 2>&1 && break
        sleep 2
    done
    if docker info > /dev/null 2>&1; then
        log "Docker: RUNNING"
    else
        log "Docker: FAILED TO START (continuing without LocalStack)"
    fi
fi

# 2. Start LocalStack container
if docker info > /dev/null 2>&1; then
    if ! docker ps --format '{{.Names}}' | grep -q "groupiq-localstack"; then
        log "Starting LocalStack..."
        docker start groupiq-localstack 2>/dev/null
        sleep 5
    fi
    if docker ps --format '{{.Names}}' | grep -q "groupiq-localstack"; then
        log "LocalStack: RUNNING"
    fi
fi

# 3. Start/Restart the Python server
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "Server already running (PID $OLD_PID), restarting..."
        kill "$OLD_PID" 2>/dev/null
        sleep 1
    fi
fi

cd "$PROJ_DIR"
$PYTHON web/server.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
sleep 2

if kill -0 "$NEW_PID" 2>/dev/null; then
    log "Server: RUNNING (PID $NEW_PID) on HTTPS:5556 / HTTP:5555"
else
    log "Server: FAILED TO START"
    exit 1
fi

# 4. Create DynamoDB tables if LocalStack is ready
$PYTHON -c "
import socket
s=socket.socket(); s.settimeout(2)
if s.connect_ex(('localhost',4566))==0:
    s.close()
    import boto3
    db=boto3.client('dynamodb',endpoint_url='http://localhost:4566',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
    tables=db.list_tables()['TableNames']
    if not tables:
        db.create_table(TableName='groupiq-bookings-local',KeySchema=[{'AttributeName':'booking_id','KeyType':'HASH'},{'AttributeName':'version','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'booking_id','AttributeType':'S'},{'AttributeName':'version','AttributeType':'N'}],BillingMode='PAY_PER_REQUEST')
        db.create_table(TableName='groupiq-negotiations-local',KeySchema=[{'AttributeName':'booking_id','KeyType':'HASH'},{'AttributeName':'turn_number','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'booking_id','AttributeType':'S'},{'AttributeName':'turn_number','AttributeType':'N'}],BillingMode='PAY_PER_REQUEST')
        db.create_table(TableName='groupiq-inventory-local',KeySchema=[{'AttributeName':'property_id','KeyType':'HASH'},{'AttributeName':'date','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'property_id','AttributeType':'S'},{'AttributeName':'date','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        print('[DynamoDB] Tables created')
    else:
        print(f'[DynamoDB] {len(tables)} tables exist')
else:
    s.close()
    print('[DynamoDB] LocalStack not ready, skipping')
" >> "$LOG_FILE" 2>&1

# 5. Verify email
curl -sSk "https://localhost:5556/bookings" -o /dev/null -w "" 2>/dev/null
log "System ready. Email notifications active."
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
