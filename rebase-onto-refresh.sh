#!/usr/bin/env bash
# rebase-onto-refresh.sh — recover from the push race, on the HUMAN side.
#
# CLAUDE.md documents how the refresh workflows recover when a human pushes
# underneath them: .github/actions/commit-refreshed-db replays the one table
# that workflow owns. This is the mirror image, for when a refresh lands while
# you are mid-edit and `git push` is rejected. It has happened twice in one
# week, and the manual recovery is the same five steps every time.
#
# WHY NOT `git pull --rebase`. denovo.db is binary, so it conflicts on every
# refresh, and a textual merge of denovo.sql cannot be trusted. The workable
# move is the same one the composite action makes: each refresh workflow is the
# SOLE WRITER of exactly one table, so their rows can simply be replayed onto
# your database and everything else survives untouched.
#
# WHY UPSERT AND NOT REPLACE. An earlier manual recovery nearly got this wrong.
# Their publication_impact had 351 rows and mine had 352, because I had just
# added a paper their refresh predated. Deleting the table and inserting theirs
# would have silently dropped that row. INSERT OR REPLACE keyed on the primary
# key gives the right semantics: their refreshed values win where the two
# overlap, and rows only I have survive.
#
# It stops short of committing, because the commit message should say what was
# replayed. It prints the exact command to finish.
#
#   ./rebase-onto-refresh.sh          # replay, reset, restage
#   ./rebase-onto-refresh.sh --check  # report only, touch nothing

set -euo pipefail
cd "$(dirname "$0")"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# The four tables a refresh workflow may own. Anything else upstream touched is
# a human's commit, and replaying rows is then the wrong tool: stop and let a
# person decide.
BUILDER_TABLES="repository_metrics publication_impact publication_citation journal_impact"

say() { printf '  %s\n' "$*"; }
die() { printf 'rebase-onto-refresh: %s\n' "$*" >&2; exit 1; }

git fetch origin --quiet

behind=$(git rev-list --count HEAD..origin/main)
ahead=$(git rev-list --count origin/main..HEAD)

if [ "$behind" -eq 0 ]; then
  say "origin/main has nothing new. Just push."
  exit 0
fi
if [ "$ahead" -eq 0 ]; then
  say "Nothing local to rebase; $behind commit(s) to pull."
  say "Run: git merge --ff-only origin/main"
  exit 0
fi
[ "$ahead" -eq 1 ] || die "$ahead local commits. This squashes them into one working
tree, which would lose history. Rebase by hand, or soft-reset to a single commit first."

echo "upstream: $behind commit(s) landed while you worked"
git log --oneline HEAD..origin/main | sed 's/^/    /'

# --- refuse anything that is not a pure refresh -----------------------------
others=$(git diff --name-only HEAD...origin/main | grep -vx -e 'denovo\.db' -e 'denovo\.sql' || true)
[ -z "$others" ] || die "upstream also changed files a table replay cannot fix:
$(printf '%s\n' "$others" | sed 's/^/    /')
Rebase by hand."

changed=$(git diff HEAD...origin/main -- denovo.sql \
          | grep -oE '^[+-]INSERT INTO [a-z_]+' \
          | awk '{print $NF}' | sort -u)
[ -n "$changed" ] || die "upstream changed denovo.db but no INSERT rows differ. Rebase by hand."

echo "tables they changed:"
for t in $changed; do
  case " $BUILDER_TABLES " in
    *" $t "*) say "$t (builder-owned, replayable)" ;;
    *) die "$t is not owned by any refresh workflow. A row replay is the wrong
tool for it, because two writers may have edited the same rows. Rebase by hand." ;;
  esac
done

if [ "$CHECK_ONLY" -eq 1 ]; then
  say "--check: nothing written."
  exit 0
fi

# --- replay -----------------------------------------------------------------
cp denovo.db "denovo.db.before-rebase"
say "backup: denovo.db.before-rebase"

pattern=$(printf '%s|' $changed); pattern="^INSERT INTO (${pattern%|}) "
git show origin/main:denovo.sql \
  | grep -E "$pattern" \
  | sed 's/^INSERT INTO /INSERT OR REPLACE INTO /' > .replay.sql
say "replaying $(wc -l < .replay.sql) row(s)"
sqlite3 -bail denovo.db < .replay.sql
rm -f .replay.sql

# --- verify: every upstream row must now be present -------------------------
git show origin/main:denovo.sql | grep -E "$pattern" | sort > .theirs.sql
for t in $changed; do sqlite3 denovo.db ".dump $t" | grep "^INSERT INTO $t "; done | sort > .mine.sql
missing=$(comm -23 .theirs.sql .mine.sql | wc -l)
extra=$(comm -13 .theirs.sql .mine.sql | wc -l)
rm -f .theirs.sql .mine.sql
[ "$missing" -eq 0 ] || die "$missing upstream row(s) did not apply. denovo.db.before-rebase holds the original."
say "verified: 0 upstream rows missing, $extra row(s) only yours (rows their refresh predates)"

# --- restage on top of origin/main ------------------------------------------
# --mixed, not --soft: denovo.sql must be regenerated from the replayed .db
# before it is staged, or the dump and the binary disagree.
git reset --mixed origin/main >/dev/null
sqlite3 denovo.db .dump > denovo.sql
[ -f scripts/check_counts.py ] && python3 scripts/check_counts.py --fix --quiet || true
# Stage what the replay and the count refresh touched, NOT `git add -A`: that
# swept this script's own 7 MB backup, denovo.db.before-rebase, into a commit
# that was then pushed. Files the local commit changed are restaged by name.
git add denovo.db denovo.sql
git diff --name-only ORIG_HEAD origin/main -- . ':!denovo.db' ':!denovo.sql' \
  | while read -r f; do [ -e "$f" ] && git add -- "$f" || git rm -q --cached --ignore-unmatch -- "$f"; done
for doc in $(python3 scripts/check_counts.py --list-files 2>/dev/null); do git add -- "$doc"; done

echo
say "Replayed and restaged on origin/main. Your commit message is preserved."
say "Finish with:"
say "    git commit -C ORIG_HEAD   # or -c to edit it, e.g. to mention the replay"
say "    git push origin main"
say "Then delete denovo.db.before-rebase once you are happy."
