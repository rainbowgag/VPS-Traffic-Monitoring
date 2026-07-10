#!/usr/bin/env bash
set -euo pipefail

APP_NAME="vps-traffic-monitor"
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
PURGE="0"

usage() {
  cat <<EOF
Usage: bash uninstall.sh [options]

Options:
  --purge      Also remove config and traffic database
  --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)
      PURGE="1"; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash uninstall.sh" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl stop "${APP_NAME}" >/dev/null 2>&1 || true
  systemctl disable "${APP_NAME}" >/dev/null 2>&1 || true
fi

rm -f "${SERVICE_FILE}"

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl reset-failed "${APP_NAME}" >/dev/null 2>&1 || true
fi

rm -rf "${INSTALL_DIR}"

if [[ "${PURGE}" == "1" ]]; then
  rm -rf "${CONFIG_DIR}" "${DATA_DIR}"
  echo "VPS Traffic Monitoring uninstalled. Config and data were removed."
else
  echo "VPS Traffic Monitoring uninstalled. Config and data were kept:"
  echo "  ${CONFIG_DIR}"
  echo "  ${DATA_DIR}"
  echo "Run with --purge to remove them too."
fi
