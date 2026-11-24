#!/bin/bash
# Build Duo.app with the source inside it, and package it as a .dmg.
# Run on a Mac from the source folder:  bash scripts/make-dmg.sh
# Optional env: DEVELOPER_ID="Developer ID Application: Name (TEAMID)" to sign,
#               NOTARY_PROFILE="name" to notarize (needs a stored notarytool profile).
# Not yet run end to end: the first real run will show what needs adjusting.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"                       # the _duo source folder
VERSION="$(cat "$SRC/VERSION" | tr -d '[:space:]')"
OUT="$SRC/dist"
STAGE="$OUT/stage"
APP="$SRC/app/src-tauri/target/release/bundle/macos/Duo.app"

echo "Building Duo.app ($VERSION)"
(cd "$SRC/app" && npx --yes @tauri-apps/cli@^2 build --bundles app)
[ -d "$APP" ] || { echo "Duo.app was not built at $APP"; exit 1; }

echo "Putting the source inside the app"
RES="$APP/Contents/Resources/_duo"
rm -rf "$RES"
mkdir -p "$RES"
(cd "$SRC" && tar --exclude=.git --exclude=__pycache__ --exclude=target --exclude='.duo*' --exclude=dist --exclude=app/src-tauri/target -cf - .) | tar -xf - -C "$RES"

if [ -n "${DEVELOPER_ID:-}" ]; then
  echo "Signing"
  codesign --deep --force --options runtime --sign "$DEVELOPER_ID" "$APP"
fi

echo "Making the disk image"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="$OUT/Duo-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -volname "Duo $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

if [ -n "${DEVELOPER_ID:-}" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "Notarizing"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi

echo "Done: $DMG"
