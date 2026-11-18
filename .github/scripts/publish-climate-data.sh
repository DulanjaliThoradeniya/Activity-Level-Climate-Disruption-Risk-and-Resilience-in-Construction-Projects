#!/usr/bin/env bash
set -euo pipefail

message=$1
shift
base=$(git rev-parse HEAD)

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
if ! git diff --cached --quiet; then
  echo "::error::The index already contains staged changes; refusing to include them in the generated-data commit."
  exit 1
fi
git add -- "$@"
mapfile -d '' -t changed_files < <(git diff --cached --name-only -z)

if ((${#changed_files[@]})); then
  git commit -m "$message"
  generated_commit=$(git rev-parse HEAD)
fi

for attempt in 1 2 3; do
  git fetch origin main
  remote=$(git rev-parse refs/remotes/origin/main)

  if ! git merge-base --is-ancestor "$base" "$remote"; then
    echo "::error::Remote main no longer contains the checkout used to generate the data. Stop and investigate."
    exit 1
  fi

  for path in "${changed_files[@]}"; do
    if ! git diff --quiet "$base" "$remote" -- "$path" &&
       ! git diff --quiet "$generated_commit" "$remote" -- "$path"; then
      echo "::error file=$path::Remote main also changed this generated file. Regenerate from the latest main instead of overwriting or merging the snapshots."
      exit 1
    fi
  done

  if ! git rebase "$remote"; then
    echo "::error::Could not rebase the generated data onto the latest main; resolve the conflict and rerun."
    git rebase --abort || echo "::error::Could not abort the failed rebase."
    exit 1
  fi

  if [ "$(git rev-parse HEAD)" = "$remote" ]; then
    echo "The generated data is already current."
    exit 0
  fi

  if git push origin HEAD:refs/heads/main; then
    exit 0
  fi

  git fetch origin main
  if [ "$(git rev-parse refs/remotes/origin/main)" = "$remote" ]; then
    echo "::error::Push failed without a new remote commit; check the push error above."
    exit 1
  fi
  echo "Remote main advanced during push (attempt $attempt of 3); retrying."
done

echo "::error::Remote main kept advancing; retry the workflow later."
exit 1
