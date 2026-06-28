#!/usr/bin/env bash
# commit-as.sh — commit + push with a specific or random identity.
# Author, committer, and pusher are all synced to the chosen identity
# because push uses the SSH host alias from ~/.ssh/config.
#
# Usage:
#   ./scripts/commit-as.sh zetryn   "feat: commit message"
#   ./scripts/commit-as.sh cry      "fix: commit message"
#   ./scripts/commit-as.sh aldirrss "chore: commit message"
#   ./scripts/commit-as.sh cdexio   "feat: commit message"
#   ./scripts/commit-as.sh lema     "feat: commit message"
#   ./scripts/commit-as.sh random   "feat: commit message"
#
# Optional: specify branch (default: main)
#   ./scripts/commit-as.sh zetryn "message" some-branch
#
# Convention (see ../docs/plans/README.md):
#   - Major + minor version release commits (vX.0.0 / v1.X.0) → use 'zetryn'
#   - Patch release commits (v1.0.X) → 'random' is OK
#   - General development commits → 'random' is OK
#
# Mirror of zetryn-ai/ai-agent/scripts/commit-as.sh. Keep in sync if the
# script in the framework repo evolves.

set -euo pipefail

IDENTITY="${1:-}"
MESSAGE="${2:-}"
BRANCH="${3:-main}"

ALL_IDENTITIES=(zetryn cry aldirrss cdexio lema)

if [[ -z "$IDENTITY" || -z "$MESSAGE" ]]; then
    echo "Usage: $0 <identity> <commit message> [branch]"
    echo "  identity: zetryn | cry | aldirrss | cdexio | lema | random"
    exit 1
fi

if [[ "$IDENTITY" == "random" ]]; then
    IDENTITY="${ALL_IDENTITIES[$((RANDOM % ${#ALL_IDENTITIES[@]}))]}"
    echo "==> Random identity selected: $IDENTITY"
fi

case "$IDENTITY" in
    zetryn)
        AUTHOR_NAME="zetryn"
        AUTHOR_EMAIL="zetrynai@gmail.com"
        SSH_HOST="github-zetryn"
        ;;
    cry)
        AUTHOR_NAME="cryptowave3142"
        AUTHOR_EMAIL="cryptowave3142@gmail.com"
        SSH_HOST="github-cry"
        ;;
    aldirrss)
        AUTHOR_NAME="aldirrss"
        AUTHOR_EMAIL="aldialputra@gmail.com"
        SSH_HOST="github-aldi"
        ;;
    cdexio)
        AUTHOR_NAME="cdexio"
        AUTHOR_EMAIL="cdexioagent@gmail.com"
        SSH_HOST="github_cdexio"
        ;;
    lema)
        AUTHOR_NAME="lemacore"
        AUTHOR_EMAIL="lemacoreofficial@gmail.com"
        SSH_HOST="github_lema"
        ;;
    *)
        echo "Unknown identity: $IDENTITY"
        echo "Options: zetryn | cry | aldirrss | cdexio | lema | random"
        exit 1
        ;;
esac

# Extract repo path (owner/repo) from any available named remote
REMOTE_URL=""
for remote in origin aldi cry zetryn cdexio lema; do
    if git remote get-url "$remote" &>/dev/null; then
        REMOTE_URL=$(git remote get-url "$remote")
        break
    fi
done

if [[ -z "$REMOTE_URL" ]]; then
    echo "Error: no named remote found. Add one with: git remote add <name> git@<host>:<owner>/<repo>.git"
    exit 1
fi

# Extract owner/repo from either SSH or HTTPS remote URL format
REPO_PATH=$(echo "$REMOTE_URL" | sed -E 's|.*[:/]([^/:]+/[^/]+)(\.git)?$|\1|')

PUSH_URL="git@${SSH_HOST}:${REPO_PATH}"

echo "==> Identity  : $AUTHOR_NAME <$AUTHOR_EMAIL>"
echo "==> Push URL  : $PUSH_URL ($BRANCH)"
echo "==> Message   : $MESSAGE"
echo ""

GIT_AUTHOR_NAME="$AUTHOR_NAME" \
GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
GIT_COMMITTER_NAME="$AUTHOR_NAME" \
GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
git commit -m "$MESSAGE"

echo ""
echo "==> Pushing as $AUTHOR_NAME ..."
git push "$PUSH_URL" "$BRANCH"

echo ""
echo "Done. Author + committer + pusher: $AUTHOR_NAME <$AUTHOR_EMAIL>"
