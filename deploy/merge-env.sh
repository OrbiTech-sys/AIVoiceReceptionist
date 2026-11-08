#!/usr/bin/env bash
# Merge NAME=value lines into a .env file without touching any other line.
#
#   merge-env.sh OVERRIDES_FILE ENV_FILE [BACKUP_FILE]
#
# - Blank lines and lines starting with # in OVERRIDES_FILE are ignored, and so is anything that is not NAME=value.
# - A NAME that already exists in ENV_FILE is replaced in place (a NAME repeated in ENV_FILE is collapsed into that one
#   line); a new NAME is appended at the end. Every other line, comments included, stays exactly as it was.
# - These names are never changed, whatever the overrides say, because changing them breaks an existing install:
#   VR_SECRET_KEY (it encrypts stored secrets) and DB_PASSWORD (PostgreSQL fixes its password at first start).
#   Override the list with PROTECTED_KEYS="NAME1 NAME2".
# - Values are never printed. The only output is one summary line of counts, on stderr.
#
# Used by .github/workflows/deploy.yml on the server (the extra variables come from the SERVER_ENV secret); it can
# also be run by hand.
set -euo pipefail

overrides=${1:?usage: merge-env.sh OVERRIDES_FILE ENV_FILE [BACKUP_FILE]}
envfile=${2:?usage: merge-env.sh OVERRIDES_FILE ENV_FILE [BACKUP_FILE]}
backup=${3:-}

if [ ! -s "$overrides" ]; then
  echo "merge-env: no extra variables to apply" >&2
  exit 0
fi
[ -f "$envfile" ] || : > "$envfile"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

awk -v protected="${PROTECTED_KEYS:-VR_SECRET_KEY DB_PASSWORD}" '
function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
BEGIN {
  n = split(protected, p, " ")
  for (i = 1; i <= n; i++) prot[p[i]] = 1
  n = 0
}
FNR == NR {                                   # first file: the overrides
  sub(/\r$/, "")
  line = trim($0)
  if (line == "" || substr(line, 1, 1) == "#") next
  eq = index(line, "=")
  key = (eq > 1) ? trim(substr(line, 1, eq - 1)) : ""
  if (key !~ /^[A-Za-z_][A-Za-z0-9_]*$/) { bad++; next }
  if (key in prot) { skipped++; next }
  value = substr(line, eq + 1)
  sub(/^[ \t]+/, "", value)
  if (!(key in val)) order[++n] = key
  val[key] = key "=" value
  next
}
{                                             # second file: the existing .env
  key = ""
  if (match($0, /^[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*=/)) key = trim(substr($0, RSTART, RLENGTH - 1))
  if (key != "" && (key in val)) {
    if (!(key in done)) { print val[key]; done[key] = 1; replaced++ } else dropped++
    next
  }
  print
}
END {
  for (i = 1; i <= n; i++) if (!(order[i] in done)) { print val[order[i]]; added++ }
  printf "merge-env: %d replaced, %d added, %d repeated line(s) dropped, %d protected skipped, %d malformed ignored\n", replaced + 0, added + 0, dropped + 0, skipped + 0, bad + 0 > "/dev/stderr"
}
' "$overrides" "$envfile" > "$tmp"

[ -s "$tmp" ] || { echo "merge-env: refusing to write an empty file" >&2; exit 1; }

if [ -n "$backup" ] && [ -s "$envfile" ]; then
  cp -p "$envfile" "$backup"
  chmod 600 "$backup"
fi
cat "$tmp" > "$envfile"
chmod 600 "$envfile"
