#!/bin/sh
# claude CLI wrapper (same logic as the anchors app)
export CLAUDE_CONFIG_DIR=/claude-home/.claude
if [ -z "$CLAUDE_CODE_OAUTH_TOKEN" ] && [ -f /claude-home/.claude-token ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '\r\n' < /claude-home/.claude-token)"
  export CLAUDE_CODE_OAUTH_TOKEN
fi

has_p=0; has_turns=0; has_perm=0
for a in "$@"; do
  case "$a" in
    -p|--print) has_p=1 ;;
    --max-turns|--max-turns=*) has_turns=1 ;;
    --permission-mode|--permission-mode=*) has_perm=1 ;;
  esac
done

if [ "$has_p" = 1 ]; then
  [ "$has_turns" = 0 ] && set -- "$@" --max-turns 50
  [ "$has_perm" = 0 ] && set -- "$@" --permission-mode acceptEdits
fi

exec /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe "$@"
