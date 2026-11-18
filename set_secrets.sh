#!/bin/bash
# Run by the owner (not Claude). Sets every secret the public repo needs. Values are never printed.
set -e
unset GITHUB_TOKEN GH_TOKEN; cd "$(dirname "$0")"; REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
F=~/.secrets/api-keys.env
DIR="$PWD"
val() { grep -E "^(export )?$1=" "$F" | tail -1 | sed -E "s/^(export )?$1=//; s/^[\"']//; s/[\"']$//"; }
put() { if [ -z "$2" ]; then echo "MISSING $1"; exit 1; fi; printf '%s' "$2" | gh secret set "$1" -R "$REPO"; echo "set $1"; }
put AIRTABLE_TOKEN "$(val AIRTABLE_TOKEN)"
put RESEND_API_KEY "$(val RESEND_API_KEY)"
put STATE_KEY "$(val DM_STATE_KEY)"
put PRIVATE_CONFIG "$(cat "$DIR/private_config.json")"
echo
echo "Paste the NEW Tailscale auth key (tskey-auth-...), then press Enter. It will not show on screen:"
read -rs TS; echo
case "$TS" in tskey-*) put TS_AUTHKEY "$TS";; *) echo "That did not look like a Tailscale key (should start tskey-). TS_AUTHKEY not set."; exit 1;; esac
gh secret list -R "$REPO"
