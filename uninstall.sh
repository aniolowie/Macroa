#!/bin/bash
# Macroa Uninstaller — Linux / macOS
# Usage: bash uninstall.sh [--keep-data]
set -euo pipefail

BOLD='\033[1m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}◈${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
step() { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

KEEP_DATA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data) KEEP_DATA=1 ;;
        --help|-h)
            echo "Usage: bash uninstall.sh [--keep-data]"
            echo ""
            echo "  --keep-data   Keep ~/.macroa (memory, sessions, config)"
            exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

echo ""
echo -e "${BOLD}${CYAN}  ◈ Macroa Uninstaller${NC}"
echo ""

# ── Stop daemon ───────────────────────────────────────────────────────────────

step "[1/3] Stopping daemon"

OS="unknown"
case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
esac

# Kill running daemon via macroa CLI if available
if command -v macroa &>/dev/null; then
    macroa daemon stop &>/dev/null 2>&1 && ok "Daemon stopped" || true
fi

# Disable system service
if [[ "$OS" == "linux" ]]; then
    SERVICE_FILE="${HOME}/.config/systemd/user/macroa.service"
    if systemctl --user is-active --quiet macroa 2>/dev/null; then
        systemctl --user stop macroa 2>/dev/null && ok "systemd service stopped" || true
    fi
    if systemctl --user is-enabled --quiet macroa 2>/dev/null; then
        systemctl --user disable macroa 2>/dev/null && ok "systemd service disabled" || true
    fi
    if [[ -f "$SERVICE_FILE" ]]; then
        rm -f "$SERVICE_FILE"
        systemctl --user daemon-reload 2>/dev/null || true
        ok "Service file removed: $SERVICE_FILE"
    fi

elif [[ "$OS" == "macos" ]]; then
    PLIST_FILE="${HOME}/Library/LaunchAgents/io.macroa.daemon.plist"
    if [[ -f "$PLIST_FILE" ]]; then
        launchctl unload "$PLIST_FILE" 2>/dev/null && ok "launchd agent unloaded" || true
        rm -f "$PLIST_FILE"
        ok "Plist removed: $PLIST_FILE"
    fi
fi

# Remove stale socket
rm -f "${HOME}/.macroa/macroa.sock"

# ── Remove package ────────────────────────────────────────────────────────────

step "[2/3] Removing package"

REMOVED=0

if command -v uv &>/dev/null && uv tool list 2>/dev/null | grep -q "^macroa"; then
    uv tool uninstall macroa && ok "Removed via uv tool" && REMOVED=1
fi

if [[ "$REMOVED" == "0" ]] && command -v pipx &>/dev/null && pipx list 2>/dev/null | grep -q "macroa"; then
    pipx uninstall macroa && ok "Removed via pipx" && REMOVED=1
fi

VENV_DIR="${HOME}/.local/share/macroa-venv"
if [[ -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    ok "Removed venv: $VENV_DIR"
    REMOVED=1
fi

WRAPPER="${HOME}/.local/bin/macroa"
if [[ -f "$WRAPPER" ]]; then
    rm -f "$WRAPPER"
    ok "Removed wrapper: $WRAPPER"
fi

if [[ "$REMOVED" == "0" ]]; then
    warn "Package not found via uv, pipx, or venv — may have been installed another way."
    warn "Run manually: pip uninstall macroa"
fi

# ── Remove data ───────────────────────────────────────────────────────────────

step "[3/3] Removing data"

MACROA_DIR="${HOME}/.macroa"

if [[ "$KEEP_DATA" == "1" ]]; then
    info "Keeping data directory: ${MACROA_DIR}  (--keep-data)"
elif [[ -d "$MACROA_DIR" ]]; then
    rm -rf "$MACROA_DIR"
    ok "Removed data directory: ${MACROA_DIR}"
else
    info "Data directory not found — nothing to remove."
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}  Uninstall complete.${NC}"
echo ""
if [[ "$KEEP_DATA" == "1" ]]; then
    echo -e "  Config and memory kept at: ${CYAN}${MACROA_DIR}${NC}"
    echo -e "  Remove manually with: ${BOLD}rm -rf ${MACROA_DIR}${NC}"
    echo ""
fi
