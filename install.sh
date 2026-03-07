#!/bin/bash
# Macroa Installer — Linux / macOS
# Usage: bash install.sh [--dev] [--no-web] [--dir <path>]
set -euo pipefail

BOLD='\033[1m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}◈${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }
step() { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

# ── Defaults ──────────────────────────────────────────────────────────────────

INCLUDE_WEB=1
DEV_MODE=0
INSTALL_DIR="${HOME}/.macroa"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# ── Parse args ────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-web)   INCLUDE_WEB=0 ;;
        --dev)      DEV_MODE=1 ;;
        --dir)      INSTALL_DIR="$2"; shift ;;
        --help|-h)
            echo "Usage: bash install.sh [--no-web] [--dev] [--dir <path>]"
            echo ""
            echo "  --no-web   Skip web API dependencies (fastapi, uvicorn)"
            echo "  --dev      Install dev dependencies (pytest, ruff, mypy)"
            echo "  --dir      Custom data directory (default: ~/.macroa)"
            exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${CYAN}  ◈ Macroa Installer${NC}"
echo -e "${DIM}  Personal AI OS${NC}"
echo ""

# ── OS detection ──────────────────────────────────────────────────────────────

step "[1/5] Detecting environment"

OS="unknown"
case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *)      err "Unsupported OS: $(uname -s)"; exit 1 ;;
esac
ok "OS: $OS ($(uname -m))"

# ── Python version check ──────────────────────────────────────────────────────

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
            local major minor
            IFS='.' read -r major minor <<< "$ver"
            if [[ "${major:-0}" -ge "$MIN_PYTHON_MAJOR" && "${minor:-0}" -ge "$MIN_PYTHON_MINOR" ]]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if ! PYTHON="$(find_python)"; then
    err "Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} is required but not found."
    echo ""
    echo "  Install it with your package manager:"
    if [[ "$OS" == "linux" ]]; then
        echo "    sudo dnf install python3.11   # Fedora"
        echo "    sudo apt install python3.11   # Debian/Ubuntu"
    else
        echo "    brew install python@3.11"
    fi
    exit 1
fi

PYTHON_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
ok "Python: $PYTHON_VER ($PYTHON)"

# ── Choose install method ─────────────────────────────────────────────────────

step "[2/5] Choosing install method"

METHOD=""
VENV_DIR=""

# Prefer uv (fast, handles extras safely)
if command -v uv &>/dev/null; then
    METHOD="uv"
    ok "Using: uv $(uv --version 2>/dev/null | head -1)"

# Fall back to pipx (isolated venv, proper PATH management)
elif command -v pipx &>/dev/null; then
    METHOD="pipx"
    ok "Using: pipx $(pipx --version 2>/dev/null)"

# Fall back to pip + venv in ~/.macroa/venv
else
    METHOD="venv"
    VENV_DIR="${HOME}/.local/share/macroa-venv"
    warn "uv and pipx not found — installing into ${VENV_DIR}"
    info "Consider installing pipx for cleaner management: https://pipx.pypa.io"
fi

# ── Build the extras spec ─────────────────────────────────────────────────────

EXTRAS=""
if [[ "$INCLUDE_WEB" == "1" && "$DEV_MODE" == "0" ]]; then
    EXTRAS="[all]"
elif [[ "$DEV_MODE" == "1" ]]; then
    EXTRAS="[dev]"
fi

SPEC="${SOURCE_DIR}${EXTRAS}"

# ── Install ───────────────────────────────────────────────────────────────────

step "[3/5] Installing Macroa"

case "$METHOD" in
    uv)
        if [[ "$DEV_MODE" == "1" ]]; then
            info "Installing in editable mode with dev extras..."
            uv pip install -e "${SOURCE_DIR}${EXTRAS}" --system 2>/dev/null \
                || uv tool install --editable "${SOURCE_DIR}${EXTRAS}"
        else
            uv tool install "${SPEC}" --force
        fi
        ok "Installed via uv tool"
        ;;

    pipx)
        if [[ "$DEV_MODE" == "1" ]]; then
            warn "For development, use: pip install -e '${SOURCE_DIR}[dev]' in a venv"
            warn "Continuing with pipx install (no dev extras)..."
        fi
        pipx install "${SPEC}" --force
        ok "Installed via pipx"
        ;;

    venv)
        info "Creating virtual environment at ${VENV_DIR}..."
        "$PYTHON" -m venv "$VENV_DIR"
        VENV_PIP="${VENV_DIR}/bin/pip"
        "$VENV_PIP" install --quiet --upgrade pip
        "$VENV_PIP" install --quiet "${SPEC}"
        ok "Installed in venv: ${VENV_DIR}"

        # Install wrapper script to ~/.local/bin
        WRAPPER_DIR="${HOME}/.local/bin"
        mkdir -p "$WRAPPER_DIR"
        WRAPPER="${WRAPPER_DIR}/macroa"
        cat > "$WRAPPER" <<WRAPPER_SCRIPT
#!/bin/bash
exec "${VENV_DIR}/bin/macroa" "\$@"
WRAPPER_SCRIPT
        chmod +x "$WRAPPER"
        ok "Wrapper installed: ${WRAPPER}"
        ;;
esac

# ── PATH check ────────────────────────────────────────────────────────────────

step "[4/5] Verifying installation"

MACROA_BIN=""
if command -v macroa &>/dev/null; then
    MACROA_BIN="$(command -v macroa)"
    MACROA_VER="$(macroa --help 2>&1 | head -1 || true)"
    ok "macroa found: ${MACROA_BIN}"
else
    warn "macroa not found in PATH."
    echo ""
    echo "  Add one of the following to your shell config (~/.zshrc or ~/.bashrc):"
    echo ""
    case "$METHOD" in
        uv)
            echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
            echo "    # or run: source \"\$(uv tool dir --bin)\""
            ;;
        pipx)
            echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
            echo "    # or run: pipx ensurepath"
            ;;
        venv)
            echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
            ;;
    esac
fi

# ── System service setup ──────────────────────────────────────────────────────

step "[5/5] Setting up always-on daemon service"

SERVICE_OK=0

if [[ -n "$MACROA_BIN" ]]; then
    if [[ "$OS" == "linux" ]]; then
        SYSTEMD_DIR="${HOME}/.config/systemd/user"
        SERVICE_FILE="${SYSTEMD_DIR}/macroa.service"
        mkdir -p "$SYSTEMD_DIR"
        cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Macroa Personal AI OS
After=network-online.target

[Service]
Type=simple
ExecStart=${MACROA_BIN} daemon run --no-web
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
SERVICE
        ok "systemd user service written: ${SERVICE_FILE}"

        if command -v systemctl &>/dev/null && systemctl --user daemon-reload &>/dev/null 2>&1; then
            systemctl --user enable --now macroa &>/dev/null 2>&1 && {
                ok "systemd service enabled and started"
                SERVICE_OK=1
            } || warn "systemctl enable failed — you can start it manually (see below)"
        else
            warn "systemctl not available — enable the service manually (see below)"
        fi

    elif [[ "$OS" == "macos" ]]; then
        LAUNCH_DIR="${HOME}/Library/LaunchAgents"
        PLIST_FILE="${LAUNCH_DIR}/io.macroa.daemon.plist"
        mkdir -p "$LAUNCH_DIR"
        cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>          <string>io.macroa.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>${MACROA_BIN}</string>
    <string>daemon</string>
    <string>run</string>
    <string>--no-web</string>
  </array>
  <key>RunAtLoad</key>      <true/>
  <key>KeepAlive</key>      <true/>
  <key>StandardOutPath</key><string>${HOME}/.macroa/logs/daemon.log</string>
  <key>StandardErrorPath</key><string>${HOME}/.macroa/logs/daemon.log</string>
</dict>
</plist>
PLIST
        ok "launchd plist written: ${PLIST_FILE}"

        if command -v launchctl &>/dev/null; then
            launchctl load "$PLIST_FILE" &>/dev/null 2>&1 && {
                ok "launchd agent loaded"
                SERVICE_OK=1
            } || warn "launchctl load failed — you can load it manually (see below)"
        else
            warn "launchctl not available"
        fi
    fi

    # Verify socket appears within 6 s
    if [[ "$SERVICE_OK" == "1" ]]; then
        SOCK_PATH="${HOME}/.macroa/macroa.sock"
        info "Waiting for daemon socket…"
        for i in $(seq 1 30); do
            if [[ -S "$SOCK_PATH" ]]; then
                ok "Daemon socket ready: ${SOCK_PATH}"
                break
            fi
            sleep 0.2
        done
        if [[ ! -S "$SOCK_PATH" ]]; then
            warn "Socket not yet visible — daemon may still be starting"
        fi
    fi
else
    warn "macroa not in PATH — skipping service setup. Re-run install.sh after adding macroa to PATH."
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}  Installation complete.${NC}"
echo ""
echo -e "  ${BOLD}macroa${NC}              — start the REPL (auto-attaches to daemon)"
echo -e "  ${BOLD}macroa setup${NC}        — configure API key / models"
echo -e "  ${BOLD}macroa daemon status${NC} — daemon health + socket path"
echo -e "  ${BOLD}macroa serve${NC}        — HTTP API (port 8000)"
echo ""

if [[ "$SERVICE_OK" == "0" && -n "$MACROA_BIN" ]]; then
    echo -e "  ${YELLOW}To enable always-on daemon manually:${NC}"
    if [[ "$OS" == "linux" ]]; then
        echo -e "    systemctl --user daemon-reload"
        echo -e "    systemctl --user enable --now macroa"
    elif [[ "$OS" == "macos" ]]; then
        echo -e "    launchctl load ~/Library/LaunchAgents/io.macroa.daemon.plist"
    fi
    echo ""
fi

if [[ -z "$MACROA_BIN" ]]; then
    echo -e "  ${YELLOW}Reload your shell first:${NC} exec \$SHELL"
    echo -e "  ${YELLOW}Then re-run install.sh to set up the always-on daemon service.${NC}"
    echo ""
fi
