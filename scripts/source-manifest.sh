#!/usr/bin/env sh
#
# source-manifest.sh — keep SOURCE_MANIFEST.sha256 telling the truth about the tree.
#
#   ./scripts/source-manifest.sh write   regenerate it from the tracked files
#   ./scripts/source-manifest.sh check   fail if the committed one disagrees
#
# The manifest is what a recipient hashes a delivery against, so a stale copy is
# worse than none: it reads like verification. write and check stay separate on
# purpose — a gate that regenerated the file it was about to verify could never go red.
set -eu
cd "$(dirname "$0")/.."
git rev-parse --show-toplevel >/dev/null

MANIFEST="${SOURCE_MANIFEST_PATH:-SOURCE_MANIFEST.sha256}"
TAB="$(printf '\t')"

digests() {
  # release-evidence/ is generated per run, so it is delivery output rather than
  # source; and a manifest can never contain its own hash. Paths are taken after the
  # digest so that a name with spaces survives.
  git ls-files -z | sort -z | xargs -0 sha256sum \
    | awk -v self="$(basename "$MANIFEST")" '
        { hash = $1; path = substr($0, length(hash) + 3)
          if (path == "SOURCE_MANIFEST.sha256" || path == self) next
          if (substr(path, 1, 16) == "release-evidence/") next
          print path "\t" hash }' \
    | sort
}

list_of() {
  # The committed manifest uses "./path"; git reports "path". Normalise both sides
  # so a rename of the convention is not read as every file having gone missing.
  if [ -f "$1" ]; then
    awk 'NF>=2 { hash=$1; path=substr($0, length(hash) + 3); sub(/^\.\//, "", path); print path"\t"hash }' "$1" | sort
  else
    :
  fi
}

case "${1:-check}" in
  write)
    digests | awk -F'\t' '{print $2"  "$1}' > "$MANIFEST.tmp"
    mv "$MANIFEST.tmp" "$MANIFEST"
    echo "wrote $(wc -l < "$MANIFEST" | tr -d ' ') entries to $MANIFEST"
    ;;
  check)
    current=$(mktemp)
    committed=$(mktemp)
    trap 'rm -f "$current" "$committed"' EXIT INT TERM
    digests > "$current"
    list_of "$MANIFEST" > "$committed"
    awk -F'\t' -v manifest_file="$committed" '
      FILENAME == manifest_file { have[$1] = $2; next }
      {
        tracked++
        cur[$1] = $2
        if (!($1 in have)) { missing++; if (first_missing == "") first_missing = $1 }
        else if (have[$1] != $2) mismatch++
      }
      END {
        listed = 0
        gone = 0
        for (k in have) { listed++; if (!(k in cur)) gone++ }
        printf "tracked=%d listed=%d missing=%d stale=%d mismatched=%d\n", tracked, listed, missing, gone, mismatch
        if (tracked == 0) {
          print "no tracked files were enumerated; refusing to certify an empty denominator"
          exit 1
        }
        if (missing + gone + mismatch > 0) {
          print "the manifest does not describe this tree; run ./scripts/source-manifest.sh write"
          if (first_missing != "") printf "first unlisted file: %s\n", first_missing
          exit 1
        }
      }
    ' "$committed" "$current"
    ;;
  *)
    echo "usage: $0 {write|check}" >&2
    exit 2
    ;;
esac
