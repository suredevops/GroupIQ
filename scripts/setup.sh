#!/bin/bash
set -euo pipefail

# GroupIQ — One-time setup after cloning
# Run this once: bash scripts/setup.sh

echo "============================================"
echo "  GroupIQ — Project Setup"
echo "============================================"
echo ""

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# 1. Create virtual environment
echo "[1/4] Creating Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "      ✓ Created .venv"
else
    echo "      ✓ .venv already exists"
fi

# 2. Activate and install dependencies
echo "[2/4] Installing dependencies (boto3, pytest, moto)..."
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r lambdas/common/requirements.txt
pip install --quiet pytest moto[all]
echo "      ✓ All packages installed"

# 3. Verify installation
echo "[3/4] Verifying installation..."
python -c "import boto3; print(f'      ✓ boto3 {boto3.__version__}')"
python -c "import pytest; print(f'      ✓ pytest {pytest.__version__}')"
python -c "import moto; print(f'      ✓ moto installed')"

# 4. Run tests
echo "[4/4] Running tests..."
pytest tests/ -q
echo ""

echo "============================================"
echo "  SETUP COMPLETE!"
echo "============================================"
echo ""
echo "  Activate venv:  source .venv/bin/activate"
echo "  Run tests:      pytest tests/ -v"
echo "  Deploy local:   bash scripts/deploy_local.sh"
echo "  Start server:   bash scripts/start_web.sh"
echo ""
echo "  Admin Portal:    http://localhost:5555"
echo "  Customer Portal: http://localhost:5555/customer.html"
echo "============================================"
