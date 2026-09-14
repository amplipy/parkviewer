#!/usr/bin/env bash
# Build the ParkViewer standalone directory on Linux.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python scripts/make_icon.py
./.venv/bin/pyinstaller --clean --noconfirm packaging/pyinstaller.spec

rm -f dist/ParkViewer-linux.tar.gz
tar -czf dist/ParkViewer-linux.tar.gz -C dist ParkViewer
echo "Built: dist/ParkViewer/  (+ dist/ParkViewer-linux.tar.gz)"