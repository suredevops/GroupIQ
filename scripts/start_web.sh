#!/bin/bash
# GroupIQ Web Server Startup Script
# Configures Gmail SMTP for email notifications

cd "$(dirname "$0")/../web" || exit 1
source ../.venv/bin/activate

# Gmail SMTP Configuration
# To enable real email notifications:
# 1. Enable 2-Step Verification on your Google Account
# 2. Go to https://myaccount.google.com/apppasswords
# 3. Generate an App Password for "Mail"
# 4. Set it below or pass as environment variable

export SMTP_USERNAME="${SMTP_USERNAME:-gsureshkrishna001@gmail.com}"
export SMTP_PASSWORD="${SMTP_PASSWORD:-}"
export SES_SENDER_EMAIL="${SES_SENDER_EMAIL:-gsureshkrishna001@gmail.com}"
export SMTP_HOST="${SMTP_HOST:-smtp.gmail.com}"
export SMTP_PORT="${SMTP_PORT:-587}"

if [ -z "$SMTP_PASSWORD" ]; then
    echo "============================================"
    echo "  EMAIL NOTIFICATIONS: NOT CONFIGURED"
    echo "============================================"
    echo ""
    echo "  To enable real Gmail notifications:"
    echo "  1. Generate App Password: https://myaccount.google.com/apppasswords"
    echo "  2. Run: SMTP_PASSWORD='your-app-password' bash scripts/start_web.sh"
    echo ""
    echo "  Emails will be logged to console only."
    echo "============================================"
    echo ""
fi

# Kill existing server if running
kill $(lsof -t -i :5555) 2>/dev/null || true
sleep 1

echo "[GroupIQ] Starting web server on http://localhost:5555"
echo "[GroupIQ] Admin Portal:    http://localhost:5555"
echo "[GroupIQ] Customer Portal: http://localhost:5555/customer.html"
echo ""

python server.py
