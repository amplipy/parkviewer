#!/usr/bin/env bash
# Build ParkViewer.app on macOS. Requires Xcode CLT + python3.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
python3 scripts/make_icon.py
./.venv/bin/pyinstaller --clean --noconfirm packaging/pyinstaller.spec

# Ship the .app (PyInstaller BUNDLE output) zipped with mac metadata preserved.
rm -f dist/ParkViewer-macos.zip
ditto -c -k --keepParent dist/ParkViewer.app dist/ParkViewer-macos.zip
echo "Built: dist/ParkViewer.app  (+ dist/ParkViewer-macos.zip)"