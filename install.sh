#!/usr/bin/env bash
set -euo pipefail

APP_NAME="vps-traffic-monitor"
REPO_RAW="https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main"
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
DEFAULT_PORT="8899"
DEFAULT_RESET_DAY="1"
PORT="${DEFAULT_PORT}"
RESET_DAY="${DEFAULT_RESET_DAY}"
INTERFACES=""
ACTION=""
PURGE="0"
INTERACTIVE="1"
IMPORT_CURRENT="0"

usage() {
  cat <<EOF
Usage: bash install.sh [options]

Without options, this script opens an interactive menu:
  1. Install traffic monitor
  2. Update traffic monitor
  3. Uninstall traffic monitor

Options:
  --action install|update|uninstall
  --port PORT              Web panel port, default: ${DEFAULT_PORT}
  --reset-day DAY          Monthly reset day 1-31, default: ${DEFAULT_RESET_DAY}
  --interfaces LIST        Interfaces to count, for example eth0,ens3. Empty means auto.
  --import-current         Import current interface counters into this cycle
  --purge                  With uninstall, also remove config and traffic database
  --yes                    Non-interactive mode, use provided/default values
  --help                   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --action)
      ACTION="${2:-}"; INTERACTIVE="0"; shift 2 ;;
    --port)
      PORT="${2:-}"; INTERACTIVE="0"; shift 2 ;;
    --reset-day)
      RESET_DAY="${2:-}"; INTERACTIVE="0"; shift 2 ;;
    --interfaces)
      INTERFACES="${2:-}"; INTERACTIVE="0"; shift 2 ;;
    --import-current)
      IMPORT_CURRENT="1"; INTERACTIVE="0"; shift ;;
    --purge)
      PURGE="1"; INTERACTIVE="0"; shift ;;
    --yes|-y)
      INTERACTIVE="0"; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run as root: sudo bash install.sh" >&2
    exit 1
  fi
}

check_number() {
  local value="$1" name="$2" min="$3" max="$4"
  if ! [[ "${value}" =~ ^[0-9]+$ ]] || (( value < min || value > max )); then
    echo "${name} must be a number from ${min} to ${max}" >&2
    exit 1
  fi
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

install_python_hint() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "Install it with: apt-get update && apt-get install -y python3 curl"
  elif command -v dnf >/dev/null 2>&1; then
    echo "Install it with: dnf install -y python3 curl"
  elif command -v yum >/dev/null 2>&1; then
    echo "Install it with: yum install -y python3 curl"
  elif command -v apk >/dev/null 2>&1; then
    echo "Install it with: apk add python3 curl"
  fi
}

prompt_menu() {
  echo
  echo "VPS Traffic Monitoring"
  echo "1. Install traffic monitor"
  echo "2. Update traffic monitor"
  echo "3. Uninstall traffic monitor"
  echo
  read -r -p "Choose an option [1-3]: " choice
  case "${choice}" in
    1) ACTION="install" ;;
    2) ACTION="update" ;;
    3) ACTION="uninstall" ;;
    *) echo "Invalid option." >&2; exit 1 ;;
  esac
}

prompt_install_config() {
  local input=""
  read -r -p "Web panel port [${DEFAULT_PORT}]: " input
  PORT="${input:-${DEFAULT_PORT}}"
  read -r -p "Monthly reset day 1-31 [${DEFAULT_RESET_DAY}]: " input
  RESET_DAY="${input:-${DEFAULT_RESET_DAY}}"
  read -r -p "Interfaces to count, for example eth0,ens3. Press Enter for auto: " input
  INTERFACES="${input:-__AUTO__}"
  read -r -p "Import current interface accumulated traffic? [y/N]: " input
  case "${input}" in
    y|Y|yes|YES) IMPORT_CURRENT="1" ;;
    *) IMPORT_CURRENT="0" ;;
  esac
}

prompt_uninstall_config() {
  local input=""
  read -r -p "Also remove config and traffic database? [y/N]: " input
  case "${input}" in
    y|Y|yes|YES) PURGE="1" ;;
    *) PURGE="0" ;;
  esac
}

check_linux_runtime() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This script only supports Linux VPS systems." >&2
    exit 1
  fi
  require_cmd systemctl
}

install_or_update() {
  local label="$1"
  check_number "${PORT}" "port" 1 65535
  check_number "${RESET_DAY}" "reset day" 1 31

  if [[ ! -r /proc/net/dev ]]; then
    echo "/proc/net/dev is not readable. Traffic counters are unavailable." >&2
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required." >&2
    install_python_hint
    exit 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required." >&2
    install_python_hint
    exit 1
  fi

  mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${DATA_DIR}"

  TMP_FILE="$(mktemp)"
  cleanup() {
    rm -f "${TMP_FILE}"
  }
  trap cleanup EXIT

  if [[ -f "./monitor.py" ]]; then
    cp "./monitor.py" "${TMP_FILE}"
  else
    curl -fsSL "${REPO_RAW}/monitor.py" -o "${TMP_FILE}"
  fi

  python3 -m py_compile "${TMP_FILE}"
  install -m 0755 "${TMP_FILE}" "${INSTALL_DIR}/monitor.py"

  python3 - "$CONFIG_DIR/config.json" "$PORT" "$RESET_DAY" "$INTERFACES" <<'PY'
import json
import os
import sys

path, port, reset_day, interfaces = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
config = {
    "host": "0.0.0.0",
    "port": port,
    "reset_day": reset_day,
    "interfaces": [],
    "exclude_interfaces": ["lo", "docker*", "br-*", "veth*", "virbr*", "zt*", "tailscale*", "wg*"],
    "sample_interval": 5,
    "database": "/var/lib/vps-traffic-monitor/traffic.db"
}
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as fh:
        old = json.load(fh)
    old.update({"port": port, "reset_day": reset_day})
    config.update(old)
if interfaces.strip() == "__AUTO__":
    config["interfaces"] = []
elif interfaces.strip():
    config["interfaces"] = [x.strip() for x in interfaces.split(",") if x.strip()]
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = f"{path}.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(config, fh, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PY

  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=VPS Traffic Monitoring
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/monitor.py --config ${CONFIG_DIR}/config.json
Restart=always
RestartSec=3
User=root
WorkingDirectory=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
EOF

  systemctl stop "${APP_NAME}" >/dev/null 2>&1 || true

  if [[ "${IMPORT_CURRENT}" == "1" ]]; then
    echo "Importing current interface counters..."
    python3 "${INSTALL_DIR}/monitor.py" --config "${CONFIG_DIR}/config.json" --import-current
  fi

  systemctl daemon-reload
  systemctl enable "${APP_NAME}" >/dev/null
  systemctl restart "${APP_NAME}"

  sleep 1
  if ! systemctl is-active --quiet "${APP_NAME}"; then
    systemctl status "${APP_NAME}" --no-pager || true
    echo "Service failed to start." >&2
    exit 1
  fi

  PUBLIC_IP="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "VPS Traffic Monitoring ${label} successfully."
  echo "Panel: http://${PUBLIC_IP:-YOUR_VPS_IP}:${PORT}"
  echo "Config: ${CONFIG_DIR}/config.json"
  echo "Data: ${DATA_DIR}/traffic.db"
  echo
  echo "Useful commands:"
  echo "  systemctl status ${APP_NAME}"
  echo "  journalctl -u ${APP_NAME} -f"
  echo "  bash <(curl -fsSL ${REPO_RAW}/install.sh)"
}

uninstall_monitor() {
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
  fi
}

need_root

if [[ "${INTERACTIVE}" == "1" ]]; then
  prompt_menu
  if [[ "${ACTION}" == "install" || "${ACTION}" == "update" ]]; then
    prompt_install_config
  elif [[ "${ACTION}" == "uninstall" ]]; then
    prompt_uninstall_config
  fi
fi

case "${ACTION}" in
  install)
    check_linux_runtime
    install_or_update "installed"
    ;;
  update)
    check_linux_runtime
    install_or_update "updated"
    ;;
  uninstall)
    check_linux_runtime
    uninstall_monitor
    ;;
  "")
    echo "No action selected." >&2
    usage
    exit 1
    ;;
  *)
    echo "Invalid action: ${ACTION}" >&2
    usage
    exit 1
    ;;
esac
