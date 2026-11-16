#!/usr/bin/env bash
# Push main to the authoritative GitHub repository, then refresh its bare mirror.
set -euo pipefail
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --sync-only ) ]]; then
    echo 'Usage: bash bin/push_repo.sh [--sync-only]' >&2
    exit 2
fi
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
mirror_root="$(dirname -- "$project_root")/hydro_ops_sync"
source_url=https://github.com/fallspinach/hydro_ops.git
destination_url=https://github.com/CW3E/hydro_ops_cw3e.git

# Refuse a different repository or an ordinary working checkout as the mirror.
[[ -d "$mirror_root" ]] || { echo 'Missing sibling hydro_ops_sync bare clone; see docs/project_relocation.md.' >&2; exit 1; }
exec 9>"$mirror_root/hydro-ops-sync.lock"
flock -n 9 || { echo 'Another repository push/sync is running.' >&2; exit 1; }
[[ $(git -C "$project_root" remote get-url origin) == "$source_url" ]]
[[ $(git -C "$project_root" remote get-url --push origin) == "$source_url" ]]
[[ $(git -C "$mirror_root" rev-parse --is-bare-repository) == true ]]
[[ $(git -C "$mirror_root" remote get-url origin) == "$source_url" ]]
[[ $(git -C "$mirror_root" remote get-url destination) == "$destination_url" ]]
[[ $(git -C "$mirror_root" remote get-url --push destination) == "$destination_url" ]]
[[ $(git -C "$mirror_root" config --get-all remote.origin.fetch) == '+refs/*:refs/*' ]]

if [[ ${1:-} != --sync-only ]]; then
    git -C "$project_root" push origin main
fi
# Never mirror the working checkout: only refs freshly fetched from origin.
git -C "$mirror_root" fetch --prune origin
git -C "$mirror_root" push --mirror --atomic destination

source_refs=$(git -C "$mirror_root" ls-remote --refs origin | LC_ALL=C sort)
destination_refs=$(git -C "$mirror_root" ls-remote --refs destination | LC_ALL=C sort)
if [[ "$source_refs" != "$destination_refs" ]]; then
    echo 'Remote refs differ after synchronization. Inspect concurrent pushes and retry --sync-only.' >&2
    exit 1
fi
echo 'Verified: source and destination Git refs match.'
