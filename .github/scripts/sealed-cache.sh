#!/usr/bin/env bash
# Seal a directory before it goes into the Actions cache, or open it after a restore.
#
#   .github/scripts/sealed-cache.sh seal <dir> <box>    pack <dir> into <box>/ (the path given to actions/cache)
#   .github/scripts/sealed-cache.sh open <dir> <box>    unpack <box>/ back into <dir>, then remove <box>/
#
# Why: in a public repository a workflow started by a pull request (from a fork too) can restore the caches of the
# default branch. These caches hold publishers' listing descriptions, photo links and held photos, which must not
# become public. With the REALIST_CACHE_KEY secret set, the archive is encrypted (gpg, AES-256, integrity-checked);
# secrets are not given to pull-request runs from forks, so such a run sees only ciphertext. Without the secret the
# archive is plain (the old behaviour) and a warning says so.
set -euo pipefail
mode=${1:?seal or open} dir=${2:?directory} box=${3:?cache path}
parent=$(dirname "$dir") name=$(basename "$dir")
gpg_args=(--batch --quiet --yes --pinentry-mode loopback --passphrase-fd 3)

case "$mode" in
  seal)
    rm -rf "$box" && mkdir -p "$box"
    if [ ! -d "$dir" ]; then echo "Nothing to seal: $dir does not exist."; exit 0; fi
    if [ -n "${REALIST_CACHE_KEY:-}" ]; then
      if tar -C "$parent" -czf - "$name" \
          | gpg "${gpg_args[@]}" --symmetric --cipher-algo AES256 --output "$box/data.tgz.gpg" 3<<<"$REALIST_CACHE_KEY"; then
        echo "Sealed $dir (encrypted)."
      else
        rm -rf "$box" && mkdir -p "$box"
        echo "::warning::Could not encrypt $dir; it is not cached this run."
      fi
    else
      echo "::warning::REALIST_CACHE_KEY is not set, so $dir is cached unencrypted and pull-request workflows can read it. Add the secret (see README, Security)."
      tar -C "$parent" -czf "$box/data.tgz" "$name"
    fi
    ;;
  open)
    if [ -f "$box/data.tgz.gpg" ]; then
      if [ -z "${REALIST_CACHE_KEY:-}" ]; then
        echo "::warning::The $dir cache is encrypted but REALIST_CACHE_KEY is not available; starting without it."
      elif ! gpg "${gpg_args[@]}" --decrypt "$box/data.tgz.gpg" 3<<<"$REALIST_CACHE_KEY" | tar -C "$parent" --no-same-owner -xzf -; then
        echo "::warning::Could not open the $dir cache (key changed?); starting without it."
        rm -rf "$dir"
      fi
    elif [ -f "$box/data.tgz" ]; then
      tar -C "$parent" --no-same-owner -xzf "$box/data.tgz"
    fi
    rm -rf "$box"
    ;;
  *) echo "usage: $0 seal|open <dir> <box>" >&2; exit 2 ;;
esac
