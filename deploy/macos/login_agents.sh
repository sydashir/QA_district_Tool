#!/usr/bin/env bash
# Start the API and the web app at login on the Mac, and restart them if they die.
#
# WHY: Postgres runs as a Homebrew login service since the move out of Docker (2026-09-14), but the
# API and the web app were started by hand. After any reboot the product was dead — the page would
# not load, or would load and show nothing — with no warning. A product that silently stops working
# after a restart is not finished.
#
# Two launchd user agents, written with this machine's real paths:
#   local.district-auditor.api  python3 -m uvicorn server.api:app on 127.0.0.1:8099
#   local.district-auditor.web  npm run dev (Vite) on localhost:5173, proxying /api to 8099
# RunAtLoad starts them at login; KeepAlive restarts them if they exit.
#
# The web agent runs the Vite DEV server on purpose, not a built bundle: a bundle goes stale the
# moment web/src changes and nothing says so — the same trap as the stale Docker image.
#
# Usage:  deploy/macos/login_agents.sh install     # write, load, and wait until both answer
#         deploy/macos/login_agents.sh status
#         deploy/macos/login_agents.sh uninstall
# Logs:   ~/Library/Logs/district-auditor/{api,web}.log
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/district-auditor"
DOMAIN="gui/$(id -u)"
API_LABEL="local.district-auditor.api"
WEB_LABEL="local.district-auditor.web"

write_agent() {   # label workdir logname program [args...]
  local label="$1" workdir="$2" log="$3"; shift 3
  local args="" a
  for a in "$@"; do args+="    <string>${a}</string>"$'\n'; done
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
${args}  </array>
  <key>WorkingDirectory</key><string>${workdir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${TOOL_PATH}</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>${LOGS}/${log}.log</string>
  <key>StandardErrorPath</key><string>${LOGS}/${log}.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$AGENTS/$label.plist" >/dev/null
}

load_agent() {
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist"
}

wait_for() {      # name url
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "$2"; then echo "  $1 answering at $2"; return 0; fi
    sleep 1
  done
  echo "  $1 NOT answering at $2 after 60s — see $LOGS" >&2
  return 1
}

status() {
  for label in "$API_LABEL" "$WEB_LABEL"; do
    if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$label" | awk -v l="$label" '/^\tstate =|^\tpid =|last exit code/ {gsub(/^\t/,""); printf "  %s: %s\n", l, $0}'
    else
      echo "  $label: not loaded"
    fi
  done
}

case "${1:-}" in
  install)
    PY="$(command -v python3)"; NPM="$(command -v npm)"
    [ -n "$PY" ] && [ -n "$NPM" ] || { echo "python3 and npm must be on PATH" >&2; exit 1; }
    TOOL_PATH="$(dirname "$PY"):$(dirname "$NPM"):/usr/bin:/bin:/usr/sbin:/sbin"
    mkdir -p "$AGENTS" "$LOGS"
    write_agent "$API_LABEL" "$REPO" api "$PY" -m uvicorn server.api:app --host 127.0.0.1 --port 8099
    write_agent "$WEB_LABEL" "$REPO/web" web "$NPM" run dev
    load_agent "$API_LABEL"
    load_agent "$WEB_LABEL"
    wait_for API http://127.0.0.1:8099/api/health
    wait_for "web app" http://localhost:5173/
    status
    ;;
  status)
    status
    curl -sf -o /dev/null http://127.0.0.1:8099/api/health && echo "  API answering" || echo "  API NOT answering"
    curl -sf -o /dev/null http://localhost:5173/ && echo "  web app answering" || echo "  web app NOT answering"
    ;;
  uninstall)
    for label in "$API_LABEL" "$WEB_LABEL"; do
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
    done
    echo "  removed both agents"
    ;;
  *)
    echo "usage: $0 install | status | uninstall" >&2; exit 2 ;;
esac
