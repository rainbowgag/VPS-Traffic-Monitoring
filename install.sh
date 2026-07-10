#!/usr/bin/env bash
set -euo pipefail

APP_NAME="vps-traffic-monitor"
REPO_RAW="https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main"
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
PORT="8088"
RESET_DAY="1"
INTERFACES=""

usage() {
  cat <<EOF
Usage: bash install.sh [options]

Options:
  --port PORT              Web panel port, default: 8088
  --reset-day DAY          Monthly reset day 1-31, default: 1
  --interfaces LIST        Interfaces to count, for example eth0,ens3. Empty means auto.
  --help                   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="${2:-}"; shift 2 ;;
    --reset-day)
      RESET_DAY="${2:-}"; shift 2 ;;
    --interfaces)
      INTERFACES="${2:-}"; shift 2 ;;
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

need_root
check_number "${PORT}" "port" 1 65535
check_number "${RESET_DAY}" "reset day" 1 31

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer only supports Linux VPS systems." >&2
  exit 1
fi

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

require_cmd systemctl

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
if interfaces.strip():
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
echo "VPS Traffic Monitoring installed successfully."
echo "Panel: http://${PUBLIC_IP:-YOUR_VPS_IP}:${PORT}"
echo "Config: ${CONFIG_DIR}/config.json"
echo "Data: ${DATA_DIR}/traffic.db"
echo
echo "Useful commands:"
echo "  systemctl status ${APP_NAME}"
echo "  journalctl -u ${APP_NAME} -f"
echo "  bash <(curl -fsSL ${REPO_RAW}/uninstall.sh)"
