#!/usr/bin/env bash
# Install the Apigee skills into an agent environment.
#
#   ./install.sh                      # global plugin: ~/.gemini/config/plugins/apigee-skills (symlink)
#   ./install.sh --project DIR        # project-scoped: DIR/.gemini/{skills,rules,agents} (copy)
#   ./install.sh --claude DIR         # Claude Code: DIR/.claude/skills (copy); use ~ for user scope
#   ./install.sh --target DIR         # any agent: copy skills/ into DIR (e.g. DIR=.agents/skills)
#   add --copy to copy instead of symlink for the global plugin, --force to overwrite.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=plugin; DEST=""; COPY=false; FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) MODE=project; DEST="$2"; shift ;;
    --claude)  MODE=claude;  DEST="$2"; shift ;;
    --target)  MODE=target;  DEST="$2"; shift ;;
    --copy)    COPY=true ;;
    --force)   FORCE=true ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown arg $1"; exit 1 ;;
  esac; shift
done

put() {  # put SRC DST
  local src="$1" dst="$2"
  if [[ -e "$dst" || -L "$dst" ]]; then
    $FORCE || { echo "exists: $dst (use --force)"; exit 1; }
    rm -rf "$dst"
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ "$MODE" == plugin && "$COPY" == false ]]; then ln -s "$src" "$dst"; else cp -R "$src" "$dst"; fi
  echo "installed $dst"
}

case "$MODE" in
  plugin)
    put "$REPO" "$HOME/.gemini/config/plugins/apigee-skills" ;;
  project)
    for s in "$REPO"/skills/*/; do put "${s%/}" "$DEST/.gemini/skills/$(basename "$s")"; done
    for r in "$REPO"/rules/*.md; do put "$r" "$DEST/.gemini/rules/$(basename "$r")"; done
    for a in "$REPO"/agents/*.md; do put "$a" "$DEST/.gemini/agents/$(basename "$a")"; done ;;
  claude)
    for s in "$REPO"/skills/*/; do put "${s%/}" "$DEST/.claude/skills/$(basename "$s")"; done ;;
  target)
    for s in "$REPO"/skills/*/; do put "${s%/}" "$DEST/$(basename "$s")"; done ;;
esac
echo "Done. Skills: $(ls "$REPO/skills" | tr '\n' ' ')"
