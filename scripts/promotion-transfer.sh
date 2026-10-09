#!/usr/bin/env bash
# Host side of the "Promote unstable work" workflow. Snapshots cross from the
# unstable host to production only as a bundle encrypted to a key pair whose
# private half never leaves production. Binary data goes to stdout; progress to
# stderr. Nothing printed here may contain user data: Actions logs are public.
#   key RUN        (production) print the run's public key, creating it once
#   capture RUN    (unstable)   public key on stdin; snapshot, encrypt, bundle to stdout
#   receive RUN    (production) bundle on stdin; decrypt into /root/launch-snapshots
set -euo pipefail
[[ $# == 2 && "$2" =~ ^[0-9]+$ ]] || { echo 'Usage: promotion-transfer.sh key|capture|receive RUN_ID' >&2; exit 1; }
command=$1 run=$2
cd "$(dirname "$0")/.."
environment=$(cat .deployment-environment)
snapshots=/root/launch-snapshots
keys=/root/launch-promotion/$run
umask 077
log() { echo "$@" >&2; }
# The base is the production snapshot whose database dump unstable was refreshed from.
find_base() {
  python3 - "$1" "$snapshots" <<'PY'
import json, sys
from pathlib import Path
digest, root = sys.argv[1], Path(sys.argv[2])
for meta in sorted(root.glob('*/snapshot.json')):
    data = json.loads(meta.read_text())
    if data.get('files', {}).get('database.dump') == digest and data.get('environment', 'production') == 'production':
        print(meta.parent)
        break
PY
}
case "$command" in
  key)
    [[ "$environment" == production ]] || { log 'Keys are created on production'; exit 1; }
    mkdir -p "$keys"
    [[ -f "$keys/private.pem" ]] || openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$keys/private.pem" 2>/dev/null
    openssl pkey -in "$keys/private.pem" -pubout
    ;;
  capture)
    [[ "$environment" == unstable ]] || { log 'Capture runs on unstable'; exit 1; }
    work=$(mktemp -d)
    trap 'rm -rf -- "$work"' EXIT
    cat > "$work/public.pem"
    openssl pkey -pubin -in "$work/public.pem" -noout
    [[ -f .deploy-state/last-refresh.json ]] || { log 'Unstable has no recorded refresh; there is no base to merge against'; exit 1; }
    base_digest=$(python3 -c 'import json; print(json.load(open(".deploy-state/last-refresh.json"))["snapshot"]["files"]["database.dump"])')
    destination=$snapshots/unstable-promotion-$run
    bash scripts/snapshot.sh --promotion-source "$destination" >&2
    mkdir -p "$work/payload"
    cp -a "$destination" "$work/payload/unstable"
    base=$(find_base "$base_digest")
    if [[ -n "$base" ]]; then
      cp -a "$base" "$work/payload/base"
      log 'Refresh base found on unstable; included.'
    else
      log 'Refresh base not on unstable; production must hold it.'
    fi
    printf '%s\n' "$base_digest" > "$work/payload/base-digest"
    openssl rand 32 > "$work/secret"
    tar -C "$work/payload" -cf - . | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$work/secret" > "$work/bundle.enc"
    openssl pkeyutl -encrypt -pubin -inkey "$work/public.pem" -pkeyopt rsa_padding_mode:oaep -in "$work/secret" > "$work/secret.enc"
    tar -C "$work" -cf - bundle.enc secret.enc
    ;;
  receive)
    [[ "$environment" == production ]] || { log 'Receive runs on production'; exit 1; }
    [[ -f "$keys/private.pem" ]] || { log 'No key for this run; run key first'; exit 1; }
    work=$(mktemp -d)
    trap 'rm -rf -- "$work"' EXIT
    tar -C "$work" -xf - bundle.enc secret.enc
    openssl pkeyutl -decrypt -inkey "$keys/private.pem" -pkeyopt rsa_padding_mode:oaep -in "$work/secret.enc" > "$work/secret"
    mkdir -p "$work/payload"
    openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$work/secret" < "$work/bundle.enc" | tar -C "$work/payload" --no-same-owner -xf -
    unstable=$snapshots/unstable-promotion-$run
    [[ ! -e "$unstable" ]] || { log "$unstable already exists"; exit 1; }
    mkdir -p "$snapshots"
    mv "$work/payload/unstable" "$unstable"
    base=$(find_base "$(cat "$work/payload/base-digest")")
    if [[ -z "$base" && -d "$work/payload/base" ]]; then
      base=$snapshots/refresh-base-$run
      mv "$work/payload/base" "$base"
    fi
    [[ -n "$base" ]] || { log 'The refresh base snapshot is on neither host'; exit 1; }
    printf '%s\n' "$base" > "$keys/base"
    log "Received unstable snapshot $unstable; base $base"
    ;;
  *) log 'Unknown command'; exit 1 ;;
esac
