#!/usr/bin/env bash
# Build "Image Explorer.app" and a .dmg on macOS.
#
#   ./build_mac.sh
#
# Optional environment variables:
#   PYTHON=python3.12            interpreter used to create the build venv
#   SKIP_TESTS=1                 don't run the test suite first
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#                                sign with your Developer ID (default: ad-hoc)
#   NOTARY_PROFILE=my-profile    notarize + staple, using a profile saved with
#                                `xcrun notarytool store-credentials my-profile`
#
# The app is built for this Mac's architecture (arm64 on Apple Silicon,
# x86_64 on Intel). Build on each kind of Mac you want to support.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv-build"
APP_NAME="Image Explorer"
APP="dist/${APP_NAME}.app"

step() { printf '\n==> %s\n' "$*"; }

step "Setting up build environment in ${VENV}"
if [ ! -d "${VENV}" ]; then
    "${PYTHON}" -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements-build.txt

VERSION="$(python -c 'import app; print(app.__version__)')"
ARCH="$(uname -m)"
echo "Image Explorer ${VERSION} for macOS ${ARCH}, $(python --version)"

if [ -z "${SKIP_TESTS:-}" ]; then
    step "Running tests"
    python -m pytest -q
fi

step "Building with PyInstaller"
rm -rf build dist
# The spec reads CODESIGN_IDENTITY and signs every binary with it (hardened
# runtime); without it PyInstaller applies an ad-hoc signature.
python -m PyInstaller --noconfirm --clean packaging/image_explorer.spec

step "Self-test of the built app"
"${APP}/Contents/MacOS/${APP_NAME}" --self-test

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    step "Verifying signature"
    codesign --verify --deep --strict --verbose=2 "${APP}"
fi

if [ -n "${NOTARY_PROFILE:-}" ]; then
    if [ -z "${CODESIGN_IDENTITY:-}" ]; then
        echo "NOTARY_PROFILE needs CODESIGN_IDENTITY (a Developer ID) as well." >&2
        exit 1
    fi
    step "Notarizing (this can take a few minutes)"
    ditto -c -k --keepParent "${APP}" "dist/notarize.zip"
    xcrun notarytool submit "dist/notarize.zip" --keychain-profile "${NOTARY_PROFILE}" --wait
    xcrun stapler staple "${APP}"
    rm -f "dist/notarize.zip"
fi

step "Creating disk image"
DMG="dist/Image-Explorer-${VERSION}-macOS-${ARCH}.dmg"
STAGING="$(mktemp -d)"
cp -R "${APP}" "${STAGING}/"
ln -s /Applications "${STAGING}/Applications"  # drag-to-install shortcut
hdiutil create -volname "${APP_NAME}" -srcfolder "${STAGING}" -ov -format UDZO "${DMG}" >/dev/null
rm -rf "${STAGING}"
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    codesign --sign "${CODESIGN_IDENTITY}" --timestamp "${DMG}"
fi

step "Done"
du -sh "${APP}" "${DMG}"
echo "App: ${APP}"
echo "DMG: ${DMG}"
if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    echo
    echo "Note: this build is only ad-hoc signed. On other Macs, Gatekeeper will block it"
    echo "until the user right-clicks the app > Open (or runs: xattr -cr \"/Applications/${APP_NAME}.app\")."
fi
