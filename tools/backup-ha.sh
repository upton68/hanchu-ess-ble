#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: backup-ha.sh [options]

Download the current Home Assistant integration directory via SFTP and
archive it into a timestamped local backup file (<timestamp>.tar.gz).
Requires the `tar` command.
Runs on macOS, Linux, and Windows (Git Bash).

Options:
  -H, --host HOST              Target host (default: $HA_HOST or homeassistant.local)
  -u, --user USER              SSH user (default: root)
  -P, --port PORT              SSH port (default: 22)
  -r, --remote-dir PATH        Remote target directory (default: homeassistant/custom_components/hanchu_ess_ble)
  -b, --backup-root PATH       Local backup root (default: <repo>/.ha-deploy-backups)
  -p, --password PASSWORD      Password for non-interactive mode (requires sshpass)
      --help                   Show this help

Environment:
  HA_HOST                      Default target host
  HANCHU_BLE_SFTP_PASSWORD     Alternative to --password (requires sshpass)

Example:
  bash tools/backup-ha.sh --host 192.168.1.50 --user root
EOF
}

if ! command -v sftp >/dev/null 2>&1; then
  echo "Error: sftp not found in PATH." >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "Error: tar not found in PATH." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="${HA_HOST:-homeassistant.local}"
USER="root"
PORT="22"
REMOTE_DIR="homeassistant/custom_components/hanchu_ess_ble"
BACKUP_ROOT="$REPO_ROOT/.ha-deploy-backups"
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
    -b|--backup-root)
      BACKUP_ROOT="$2"
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

REMOTE_DIR_SFTP="${REMOTE_DIR//\\//}"
REMOTE_DIR_NAME="${REMOTE_DIR_SFTP##*/}"
REMOTE_DIR_PARENT="${REMOTE_DIR_SFTP%/*}"
if [[ "$REMOTE_DIR_PARENT" == "$REMOTE_DIR_SFTP" ]]; then
  REMOTE_DIR_PARENT="."
fi

mkdir -p "$BACKUP_ROOT"
BACKUP_ROOT_SFTP="$(cd "$BACKUP_ROOT" && pwd)"

BATCH_FILE="$(mktemp)"
DOWNLOAD_DIR="$(mktemp -d)"
cleanup() {
  rm -f "$BATCH_FILE"
  rm -rf "$DOWNLOAD_DIR"
}
trap cleanup EXIT

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

STAMP="$(date +%Y%m%d-%H%M%S)"
TAR_PATH="$BACKUP_ROOT_SFTP/$STAMP.tar.gz"

cat > "$BATCH_FILE" <<EOF
lcd "$DOWNLOAD_DIR"
cd "$REMOTE_DIR_PARENT"
get -r "$REMOTE_DIR_NAME"
EOF

echo "Downloading remote backup..."
run_sftp_batch "$BATCH_FILE"

tar -czf "$TAR_PATH" -C "$DOWNLOAD_DIR" "$REMOTE_DIR_NAME"

echo "Created backup: $TAR_PATH"

