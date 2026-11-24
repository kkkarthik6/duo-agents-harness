#!/bin/sh
# Duo installer. One command:
#   sh install.sh                 (from a checkout or an unpacked archive)
#   curl -fsSL <url>/install.sh | sh    (once the repo is public; set DUO_REPO below)
#
# What it does:
#   1. Checks for Python 3.13, tmux, and at least one agent CLI (claude or codex).
#   2. Installs the source as a sealed core in ~/Duo/core/<version>/ and sets up ~/Duo/user/ (your features).
#      Run it again with a newer source to update: the new core is tested with your features before it is used.
#   3. Writes ~/Duo/bin/duo (it runs the current core) and adds it to the PATH line in your shell profile.
#   4. Prints the command to start Duo.
set -eu

DUO_HOME="${DUO_HOME:-$HOME/Duo}"
DUO_REPO="${DUO_REPO:-}"            # git URL, used only when this script runs outside a source tree
SRC="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"

say() { printf '%s\n' "$*"; }
fail() { say "Install stopped: $*"; exit 1; }

# 1. Prerequisites
PY=""
if [ "$(uname -s)" = "Darwin" ]; then PYHOW="brew install python@3.13"; TMHOW="brew install tmux"
else PYHOW="sudo apt install python3.13   (or: uv python install 3.13)"; TMHOW="sudo apt install tmux"; fi
for c in "${DUO_PYTHON:-}" /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 "$(command -v python3.13 || true)" "$(command -v python3 || true)"; do
  [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 13))' 2>/dev/null && PY="$c" && break
done
[ -n "$PY" ] || fail "Python 3.13 or newer is not installed. Run: $PYHOW"
command -v tmux >/dev/null 2>&1 || fail "tmux is not installed. Run: $TMHOW"

# 2. Source
if [ ! -f "$SRC/bin/duo" ]; then
  [ -n "$DUO_REPO" ] || fail "Run this script from the Duo source folder, or set DUO_REPO to the git URL."
  command -v git >/dev/null 2>&1 || fail "git is needed to fetch $DUO_REPO"
  TMP="$(mktemp -d)"
  git clone --depth 1 "$DUO_REPO" "$TMP/duo"
  SRC="$TMP/duo"
fi
PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$PY" -c 'from engine.agents import detect; raise SystemExit(0 if detect() else 1)' || {
  say "Note: no agent CLI found yet. Duo uses the ones you already have: install and sign in to Claude Code, Codex or Antigravity, then run duo."
}
mkdir -p "$DUO_HOME/bin" "$DUO_HOME/skills" "$DUO_HOME/codex-home"
"$PY" "$SRC/scripts/install_core.py" "$SRC" "$DUO_HOME" || {
  code=$?
  [ "$code" = 3 ] && fail "The new core did not pass its tests. Your current Duo still runs."
  fail "Duo could not install its core (exit $code)."
}

# 3. Launcher on the PATH. It always runs the current core named in ~/Duo/core/state.json.
rm -f "$DUO_HOME/bin/duo"
cat > "$DUO_HOME/bin/duo" <<'SHEOF'
#!/bin/sh
D="${DUO_HOME:-$HOME/Duo}"
C=$(sed -n 's/.*"current": *"\([^"]*\)".*/\1/p' "$D/core/state.json" | head -1)
[ -n "$C" ] && [ -x "$D/core/$C/bin/duo" ] || { echo "Duo has no core installed. Run install.sh again." >&2; exit 1; }
exec "$D/core/$C/bin/duo" "$@"
SHEOF
chmod +x "$DUO_HOME/bin/duo"
if [ "$(uname -s)" = "Darwin" ]; then
  "$PY" "$SRC/scripts/update_desktop_launcher.py" "$DUO_HOME" "$SRC"
fi
for c in "$DUO_HOME"/core/*/bin/duo; do [ -f "$c" ] && chmod +x "$c"; done
LINE='export PATH="$HOME/Duo/bin:$PATH"'
for rc in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do
  if [ -f "$rc" ] && ! grep -qF "$LINE" "$rc"; then printf '\n# Duo\n%s\n' "$LINE" >> "$rc"; fi
done
if [ "$(uname -s)" = "Darwin" ] || case "${SHELL:-}" in *zsh) true;; *) false;; esac; then
  [ -f "$HOME/.zshrc" ] || printf '# Duo\n%s\n' "$LINE" >> "$HOME/.zshrc"
else
  [ -f "$HOME/.bashrc" ] || printf '# Duo\n%s\n' "$LINE" >> "$HOME/.bashrc"
fi

# 4. Done
say "Duo is installed."
say "  core:     $DUO_HOME/core      (sealed; only install.sh and duo update change it)"
say "  features: $DUO_HOME/user      (your features; Feedback and Roll up change it)"
say "Open a new terminal, then run:  duo serve"
say "Or start it now:  $DUO_HOME/bin/duo serve"
