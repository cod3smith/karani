#!/bin/sh
# karani watchdog — a dead-man's switch that shares NOTHING with karani.
#
# karani once died for 8 days in silence: a disk cleanup deleted the
# virtualenv, which killed both the hourly hunt and the daily agent whose
# job was to notice. Any check living inside that venv dies with it, so
# this one is POSIX sh + curl and reads a single file: the marker an
# hourly pass rewrites when it completes. Stale marker => alert.
#
# Deliberately does not import karani, activate a venv, or touch the
# database. Placeholders are rendered by `karani schedule`.
set -u

MARKER="__MARKER__"
MAX_AGE_H="__MAX_AGE_H__"
ENV_FILE="__ENV_FILE__"
CHANNEL="__CHANNEL__"

# Slack bot token lives in the environment file, never in this script.
if [ -f "$ENV_FILE" ]; then
    TOKEN=$(sed -n 's/^[[:space:]]*SLACK_BOT_TOKEN=//p' "$ENV_FILE" \
            | tr -d '"'"'"' \r' | tail -1)
fi
: "${TOKEN:=${SLACK_BOT_TOKEN:-}}"

now=$(date +%s)
if [ -f "$MARKER" ]; then
    # stat -f on BSD/macOS, stat -c on GNU/Linux.
    last=$(stat -f %m "$MARKER" 2>/dev/null || stat -c %Y "$MARKER" 2>/dev/null)
    age_h=$(( (now - last) / 3600 ))
    [ "$age_h" -lt "$MAX_AGE_H" ] && exit 0
    text="karani watchdog: no completed hunt in ${age_h}h (threshold ${MAX_AGE_H}h). The scheduler looks dead — check: launchctl list | grep karani"
else
    text="karani watchdog: no pass marker at ${MARKER} — the scheduler has never completed a pass, or the install moved. Check: launchctl list | grep karani"
fi

if [ -z "${TOKEN:-}" ] || [ -z "$CHANNEL" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $text (slack unconfigured)" >&2
    exit 1
fi

curl -s -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-type: application/json; charset=utf-8' \
    --data "$(printf '{"channel":"%s","text":"%s"}' "$CHANNEL" "$text")" \
    >/dev/null
echo "$(date '+%Y-%m-%d %H:%M:%S') alerted: $text" >&2
