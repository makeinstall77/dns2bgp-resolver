#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_NAME="dns2bgp-resolver"
STAGING="${ROOT}/build/deb-staging"
DIST="${ROOT}/dist"
INSTALL=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [--install]

Build a .deb package from source.

  --install   build and install with dpkg -i (requires sudo)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) INSTALL=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd python3
need_cmd dpkg-deb

PYTHON="${PYTHON:-python3}"
PY_MINOR="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_MINOR%%.*}"
PY_MIN="${PY_MINOR#*.}"

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MIN" -lt 12 ]]; then
    echo "Python >= 3.12 required, found $("$PYTHON" --version)" >&2
    exit 1
fi

VERSION="$("$PYTHON" -c "
import pathlib, re
text = pathlib.Path('$ROOT/pyproject.toml').read_text()
m = re.search(r'^version\\s*=\\s*\"([^\"]+)\"', text, re.M)
if not m:
    raise SystemExit('version not found in pyproject.toml')
print(m.group(1))
")"

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
DEB_FILE="${DIST}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

echo "==> Building wheel (${VERSION})"
BUILD_VENV="${ROOT}/build/wheel-venv"
rm -rf "${ROOT}/build/wheel-build" "${STAGING}" "${BUILD_VENV}"
mkdir -p "${ROOT}/build/wheel-build" "${DIST}"
"$PYTHON" -m venv "${BUILD_VENV}"
"${BUILD_VENV}/bin/pip" install -q build
"${BUILD_VENV}/bin/python" -m build --wheel --outdir "${DIST}" "${ROOT}"
rm -rf "${BUILD_VENV}"

WHEEL="$(ls -1 "${DIST}"/dns2bgp_resolver-"${VERSION}"-py3-none-any.whl 2>/dev/null || ls -1 "${DIST}"/dns2bgp_resolver-"${VERSION}"*.whl | head -1)"
if [[ ! -f "$WHEEL" ]]; then
    echo "Wheel not found in ${DIST}" >&2
    exit 1
fi

echo "==> Preparing staging (${STAGING})"
mkdir -p \
    "${STAGING}/opt/dns2bgp" \
    "${STAGING}/etc/dns2bgp" \
    "${STAGING}/lib/systemd/system" \
    "${STAGING}/usr/share/doc/${PKG_NAME}" \
    "${STAGING}/DEBIAN"

"$PYTHON" -m venv "${STAGING}/opt/dns2bgp/.venv"
"${STAGING}/opt/dns2bgp/.venv/bin/pip" install -q --upgrade pip
"${STAGING}/opt/dns2bgp/.venv/bin/pip" install -q "$WHEEL"
sed -i '1s|.*|#!/opt/dns2bgp/.venv/bin/python3|' "${STAGING}/opt/dns2bgp/.venv/bin/dns2bgp"

install -m 0644 "${ROOT}/deploy/config.yaml" "${STAGING}/etc/dns2bgp/config.yaml"
install -m 0644 "${ROOT}/deploy/dns2bgp.service" "${STAGING}/lib/systemd/system/dns2bgp.service"
install -m 0644 "${ROOT}/deploy/bird.include.example.conf" "${STAGING}/usr/share/doc/${PKG_NAME}/bird.include.example.conf"
install -m 0755 "${ROOT}/packaging/deb/DEBIAN/postinst" "${STAGING}/DEBIAN/postinst"
install -m 0755 "${ROOT}/packaging/deb/DEBIAN/prerm" "${STAGING}/DEBIAN/prerm"
install -m 0755 "${ROOT}/packaging/deb/DEBIAN/postrm" "${STAGING}/DEBIAN/postrm"
install -m 0644 "${ROOT}/packaging/deb/DEBIAN/conffiles" "${STAGING}/DEBIAN/conffiles"

cat > "${STAGING}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.12), adduser
Recommends: bird
Maintainer: dns2bgp-resolver <local>
Description: Resolve domains into BGP routes for VPN traffic steering
 DNS resolver that publishes domain IP addresses as bird static routes.
 Includes CLI, web UI, and Telegram bot.
EOF

echo "==> Building ${DEB_FILE}"
dpkg-deb --root-owner-group --build "${STAGING}" "${DEB_FILE}"
rm -rf "${STAGING}"

echo "==> Done: ${DEB_FILE}"

if [[ "$INSTALL" == true ]]; then
    echo "==> Installing package"
    sudo dpkg -i "${DEB_FILE}"
fi
