#!/usr/bin/env bash
# Install the Apigee X skills, rules, and subagents into an AI agent environment.
#
#   ./install.sh                      # Antigravity global plugin: ~/.gemini/config/plugins/apigee-skills (symlink)
#   ./install.sh --project DIR        # Antigravity / .agents workspace: DIR/.agents/{skills,rules,agents} (copy)
#   ./install.sh --claude DIR         # Claude Code: DIR/.claude/{skills,rules,agents} (use ~ for user scope)
#   ./install.sh --copilot DIR        # GitHub Copilot: DIR/.github/{skills,instructions,agents} (or ~/.copilot when DIR=~)
#   ./install.sh --gemini DIR         # Gemini CLI: DIR/.gemini/{skills,rules,agents} (use ~ for user scope)
#   ./install.sh --codex DIR          # OpenAI Codex / Cursor: DIR/.agents/skills (use ~ for user scope)
#   ./install.sh --target DIR         # Custom target: copy skills/* directly into DIR/<skill-name>
#   Add --copy to copy instead of symlink for the global plugin, --force to overwrite existing files.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=plugin; DEST=""; COPY=false; FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --antigravity|--plugin) MODE=plugin ;;
    --project|--agents)     MODE=project; DEST="$2"; shift ;;
    --claude)               MODE=claude;  DEST="$2"; shift ;;
    --copilot)              MODE=copilot; DEST="$2"; shift ;;
    --gemini)               MODE=gemini;  DEST="$2"; shift ;;
    --codex|--cursor)       MODE=codex;   DEST="$2"; shift ;;
    --target)               MODE=target;  DEST="$2"; shift ;;
    --copy)                 COPY=true ;;
    --force)                FORCE=true ;;
    -h|--help)              sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 1 ;;
  esac; shift
done

prepare_dst() {
  local dst="$1"
  if [[ -e "$dst" || -L "$dst" ]]; then
    $FORCE || { echo "exists: $dst (use --force)" >&2; exit 1; }
    rm -rf "$dst"
  fi
  mkdir -p "$(dirname "$dst")"
}

put() {  # put SRC DST
  local src="$1" dst="$2"
  prepare_dst "$dst"
  if [[ "$MODE" == plugin && "$COPY" == false ]]; then ln -s "$src" "$dst"; else cp -R "$src" "$dst"; fi
  echo "installed $dst"
}

put_portable_agent() {  # strip Antigravity-only tool/flag keys for Claude Code & GitHub Copilot
  local src="$1" dst="$2"
  prepare_dst "$dst"
  awk '
    NR==1 && $0=="---" { in_fm=1; print; next }
    in_fm && $0=="---" { in_fm=0; skip_list=0; print; next }
    in_fm {
      if ($0 ~ /^(tools|mainAgent|subagent):/) { skip_list=($0 ~ /^tools:/); next }
      if (skip_list && $0 ~ /^[[:space:]]+-/) { next }
      skip_list=0
    }
    { print }
  ' "$src" > "$dst"
  echo "installed $dst"
}

subroot() {  # subroot BASE DOTDIR -> avoids BASE/.claude/.claude when user passes ~/.claude
  local base="${1%/}" dot="$2"
  if [[ "$(basename "$base")" == "$dot" ]]; then echo "$base"; else echo "$base/$dot"; fi
}

case "$MODE" in
  plugin)
    put "$REPO" "$HOME/.gemini/config/plugins/apigee-skills" ;;
  project)
    ROOT="$(subroot "$DEST" ".agents")"
    for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done
    for r in "$REPO"/rules/*.md; do put "$r" "$ROOT/rules/$(basename "$r")"; done
    for a in "$REPO"/agents/*.md; do put "$a" "$ROOT/agents/$(basename "$a")"; done ;;
  claude)
    ROOT="$(subroot "$DEST" ".claude")"
    for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done
    for r in "$REPO"/rules/*.md; do put "$r" "$ROOT/rules/$(basename "$r")"; done
    for a in "$REPO"/agents/*.md; do put_portable_agent "$a" "$ROOT/agents/$(basename "$a")"; done ;;
  copilot)
    if [[ "${DEST%/}" == "${HOME%/}" || "$(basename "${DEST%/}")" == ".copilot" ]]; then
      ROOT="$(subroot "$DEST" ".copilot")"
      for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done
      for a in "$REPO"/agents/*.md; do
        put_portable_agent "$a" "$ROOT/agents/$(basename "$a" .md).agent.md"
      done
    else
      ROOT="$(subroot "$DEST" ".github")"
      for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done
      for r in "$REPO"/rules/*.md; do
        put "$r" "$ROOT/instructions/$(basename "$r" .md).instructions.md"
      done
      for a in "$REPO"/agents/*.md; do
        put_portable_agent "$a" "$ROOT/agents/$(basename "$a" .md).agent.md"
      done
    fi ;;
  gemini)
    ROOT="$(subroot "$DEST" ".gemini")"
    for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done
    for r in "$REPO"/rules/*.md; do put "$r" "$ROOT/rules/$(basename "$r")"; done
    for a in "$REPO"/agents/*.md; do put "$a" "$ROOT/agents/$(basename "$a")"; done ;;
  codex)
    ROOT="$(subroot "$DEST" ".agents")"
    for s in "$REPO"/skills/*/; do put "${s%/}" "$ROOT/skills/$(basename "$s")"; done ;;
  target)
    for s in "$REPO"/skills/*/; do put "${s%/}" "$DEST/$(basename "$s")"; done ;;
esac
echo "Done. Skills: $(ls "$REPO/skills" | tr '\n' ' ')"
