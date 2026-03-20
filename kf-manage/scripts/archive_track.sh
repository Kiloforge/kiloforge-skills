#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <track-id> [archive-reason]"
  exit 1
fi

TRACK_ID="$1"
REASON="${2:-Completed}"
TRACK_DIR=".agent/kf/tracks/$TRACK_ID"
ARCHIVE_DIR=".agent/kf/tracks/_archive/$TRACK_ID"
TRACK_YAML="$TRACK_DIR/track.yaml"
TRACKS_YAML=".agent/kf/tracks.yaml"

if [ ! -d "$TRACK_DIR" ]; then
  echo "Error: Track directory '$TRACK_DIR' does not exist."
  exit 1
fi

# Fetch the track title from track.yaml before moving
TITLE=$(sed -n 's/^title: *//p' "$TRACK_YAML" | head -1 | sed 's/^["'"'"']\(.*\)["'"'"']$/\1/')
TODAY=$(date '+%Y-%m-%d')
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "Archiving track: $TRACK_ID ($TITLE)..."

# 1. Update track.yaml status to 'archived', record reason & date.
python3 -c "
import sys
lines = open('$TRACK_YAML').read()

# Update or add fields
import re

def set_yaml_field(content, field, value):
    pattern = r'^' + field + r':.*$'
    replacement = field + ': ' + value
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        return content.rstrip() + '\n' + replacement + '\n'

lines = set_yaml_field(lines, 'status', 'archived')
lines = set_yaml_field(lines, 'archived', 'true')
lines = set_yaml_field(lines, 'archived_at', '\"$TIMESTAMP\"')
lines = set_yaml_field(lines, 'archive_reason', '\"$REASON\"')

with open('$TRACK_YAML', 'w') as f:
    f.write(lines)
"

# 2. Move to archive
mkdir -p .agent/kf/tracks/_archive
mv "$TRACK_DIR" "$ARCHIVE_DIR"

# 3. Update the master tracks.yaml registry via kf-track CLI
if [ -x "~/.kf/bin/kf-track.py" ]; then
  ~/.kf/bin/kf-track.py update "$TRACK_ID" --status archived
else
  # Fallback: update tracks.yaml directly if CLI not available
  if [ -f "$TRACKS_YAML" ]; then
    python3 -c "
import re
content = open('$TRACKS_YAML').read()

# Update the status field for this track in the YAML registry
# This handles a simple flat list format
pattern = r'(- id: $TRACK_ID\n(?:  \w.*\n)*?  status: )\w+'
replacement = r'\1archived'
updated = re.sub(pattern, replacement, content)

if updated == content:
    # Try alternate YAML format where track ID is a key
    pattern = r'($TRACK_ID:\n(?:  \w.*\n)*?  status: )\w+'
    replacement = r'\1archived'
    updated = re.sub(pattern, replacement, content)

with open('$TRACKS_YAML', 'w') as f:
    f.write(updated)
"
  fi
fi

# 4. Commit the changes
git add "$ARCHIVE_DIR" "$TRACKS_YAML"
git commit -m "chore(kf): archive track '$TRACK_ID' (Reason: $REASON)"

echo "Successfully archived $TRACK_ID!"
