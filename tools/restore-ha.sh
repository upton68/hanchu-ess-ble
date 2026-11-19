#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: restore-ha.sh --restore-from PATH [options]

Restore the Home Assistant integration directory via SFTP from a local backup.
PATH may be a timestamped <timestamp>.tar.gz produced by backup-ha.sh (requires
`tar`), or a legacy backup directory.
Runs on macOS, Linux, and Windows (Git Bash).

Options:
  -H, --host HOST              Target host (default: $HA_HOST or homeassistant.local)
  -u, --user USER              SSH user (default: root)
  -P, --port PORT              SSH port (default: 22)
  -r, --remote-dir PATH        Remote target directory (default: homeassistant/custom_components/hanchu_ess_ble)
      --restore-from PATH      Local backup .tar.gz file or directory to restore from (required)
  -p, --password PASSWORD      Password for non-interactive mode (requires sshpass)
      --help                   Show this help

Environment:
  HA_HOST                      Default target host
  HANCHU_BLE_SFTP_PASSWORD     Alternative to --password (requires sshpass)

Example:
  bash tools/restore-ha.sh --host 192.168.1.50 --user root --restore-from "$HOME/hanchu-ess-ble/.ha-deploy-backups/20260703-230000.tar.gz"
EOF
}

if ! command -v sftp >/dev/null 2>&1; then
  echo "Error: sftp not found in PATH." >&2
  exit 1
fi

HOST="${HA_HOST:-homeassistant.local}"
USER="root"
PORT="22"
REMOTE_DIR="homeassistant/custom_components/hanchu_ess_ble"
RESTORE_FROM=""
PASSWORD="${HANCHU_BLE_SFTP_PASSWORD:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host)
      HOST="$2"
      shift 2
      ;;
    -u|--user)
      USER="$2"
      shift 2
      ;;
    -P|--port)
      PORT="$2"
      shift 2
      ;;
    -r|--remote-dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    --restore-from)
      RESTORE_FROM="$2"
      shift 2
      ;;
    -p|--password)
      PASSWORD="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$RESTORE_FROM" ]]; then
  echo "Error: --restore-from is required." >&2
  usage >&2
  exit 1
fi

if [[ ! -e "$RESTORE_FROM" ]]; then
  echo "Error: restore path does not exist: $RESTORE_FROM" >&2
  exit 1
fi

REMOTE_DIR_SFTP="${REMOTE_DIR//\\//}"
REMOTE_DIR_NAME="${REMOTE_DIR_SFTP##*/}"

resolve_restore_source() {
  local restore_from="$1"
  local nested="$restore_from/$REMOTE_DIR_NAME"
  if [[ -d "$nested" ]]; then
    (cd "$nested" && pwd)
  else
    (cd "$restore_from" && pwd)
  fi
}

EXTRACT_DIR=""
BATCH_FILE="$(mktemp)"
cleanup() {
  rm -f "$BATCH_FILE"
  if [[ -n "$EXTRACT_DIR" ]]; then
    rm -rf "$EXTRACT_DIR"
  fi
}
trap cleanup EXIT

if [[ -f "$RESTORE_FROM" ]]; then
  if ! command -v tar >/dev/null 2>&1; then
    echo "Error: tar not found in PATH." >&2
    exit 1
  fi
  EXTRACT_DIR="$(mktemp -d)"
  tar -xzf "$RESTORE_FROM" -C "$EXTRACT_DIR"
  RESTORE_FROM="$EXTRACT_DIR"
fi

RESTORE_SOURCE_SFTP="$(resolve_restore_source "$RESTORE_FROM")"

cat > "$BATCH_FILE" <<EOF
lcd "$RESTORE_SOURCE_SFTP"
cd "$REMOTE_DIR_SFTP"
put -r *
EOF

run_sftp_batch() {
  local batch_file="$1"
  local target="${USER}@${HOST}"
  local sftp_args=(-oBatchMode=no -P "$PORT" -b "$batch_file" "$target")
  if [[ -n "$PASSWORD" ]]; then
    if ! command -v sshpass >/dev/null 2>&1; then
      echo "Error: --password/HANCHU_BLE_SFTP_PASSWORD requires sshpass." >&2
      echo "Install sshpass or omit password to use normal interactive prompting." >&2
      exit 1
    fi
    SSHPASS="$PASSWORD" sshpass -e sftp "${sftp_args[@]}"
  else
    sftp "${sftp_args[@]}"
  fi
}

echo "Restoring remote directory from: $RESTORE_SOURCE_SFTP"
run_sftp_batch "$BATCH_FILE"

