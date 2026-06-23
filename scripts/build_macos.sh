#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
APP_NAME="proxy_tools"
APP_VERSION="v0.2.0-beta"
DIST_PATH="$PROJECT_ROOT/dist"
WORK_PATH="$PROJECT_ROOT/build"
ENTRY_POINT="$PROJECT_ROOT/scripts/pyinstaller_entry.py"
CONFIG_SOURCE="$PROJECT_ROOT/config"
ASSETS_SOURCE="$PROJECT_ROOT/assets"
ICON_SOURCE="$ASSETS_SOURCE/nightnodes_logo.icns"

cd "$PROJECT_ROOT"

if [[ ! -x "$VENV_PYTHON" ]]; then
  python3 -m venv "$PROJECT_ROOT/.venv"
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -e "$PROJECT_ROOT[build]"
"$VENV_PYTHON" -m playwright install chromium

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --windowed
  --name "$APP_NAME"
  --distpath "$DIST_PATH"
  --workpath "$WORK_PATH"
  --add-data "$CONFIG_SOURCE:config"
  --add-data "$ASSETS_SOURCE:assets"
  "$ENTRY_POINT"
)

if [[ -f "$ICON_SOURCE" ]]; then
  PYINSTALLER_ARGS=(--icon "$ICON_SOURCE" "${PYINSTALLER_ARGS[@]}")
fi

"$VENV_PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

APP_PATH="$DIST_PATH/$APP_NAME.app"
VERSIONED_APP_PATH="$DIST_PATH/${APP_NAME}_${APP_VERSION}.app"
DMG_PATH="$DIST_PATH/${APP_NAME}_${APP_VERSION}_macos.dmg"

rm -rf "$VERSIONED_APP_PATH"
mv "$APP_PATH" "$VERSIONED_APP_PATH"

if command -v hdiutil >/dev/null 2>&1; then
  rm -f "$DMG_PATH"
  hdiutil create -volname "${APP_NAME}_${APP_VERSION}" -srcfolder "$VERSIONED_APP_PATH" -ov -format UDZO "$DMG_PATH"
  echo "Built: $DMG_PATH"
else
  echo "Built: $VERSIONED_APP_PATH"
fi
