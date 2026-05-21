#!/bin/bash
set -euo pipefail

# GroupIQ — Build and Package Lambda Functions for Deployment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LAMBDAS_DIR="$PROJECT_ROOT/lambdas"

echo "=== GroupIQ Lambda Packaging ==="

# Package the common layer
echo "[1/5] Packaging common layer..."
cd "$LAMBDAS_DIR/common"
rm -rf build layer.zip
mkdir -p build/python
pip install -r requirements.txt -t build/python/ --quiet
cp utils.py build/python/
cd build && zip -r ../layer.zip python/ > /dev/null
cd "$LAMBDAS_DIR/common" && rm -rf build
echo "  -> common/layer.zip created"

# Package each Lambda function
for FUNC in intake proposal_generator negotiation_agent notification; do
    echo "[*] Packaging $FUNC..."
    cd "$LAMBDAS_DIR/$FUNC"
    rm -rf build package.zip
    mkdir -p build
    pip install -r requirements.txt -t build/ --quiet
    cp handler.py build/
    cd build && zip -r ../package.zip . > /dev/null
    cd "$LAMBDAS_DIR/$FUNC" && rm -rf build
    echo "  -> $FUNC/package.zip created"
done

echo ""
echo "=== All packages built successfully ==="
echo "Run 'cd terraform && terraform apply' to deploy."
