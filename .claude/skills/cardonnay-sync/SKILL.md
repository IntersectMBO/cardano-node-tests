---
name: cardonnay-sync
description: >-
  Sync cardano-node-tests to a specific cardonnay branch/ref so a real test
  run actually exercises it, instead of a stale PyPI release. Use whenever
  making or verifying changes in the cardonnay repo (cluster scripts, genesis
  spec files, cost models) that need to be exercised via a cardano-node-tests
  test run, or whenever a test result needs to be trusted before reporting it
  as "verified" or "passing."
---

# Syncing cardano-node-tests to a cardonnay branch

`cardano-node-tests` depends on `cardonnay` as a normal Python package,
pinned in `pyproject.toml` to a version range and locked in `uv.lock` to one
specific PyPI release. Editing files in a local `cardonnay` git clone, even
an editable pip install of it, does not change what a real test run uses
unless you wire it to that ref explicitly. `runner/node_upgrade.sh` and
friends build a fresh, isolated venv from `pyproject.toml` + `uv.lock` only,
they have no idea a local `/path/to/cardonnay` clone exists.

## When this skill is invoked, do this immediately

1. If a branch/ref was given as an argument, use it. Otherwise ask the user
   which cardonnay branch, tag, or commit to sync to before doing anything
   else. Do not guess or default to `master` silently, confirm it.
2. Run the sync procedure below against that ref, in `cardano-node-tests`.
3. Verify it actually took effect (step 4 of the procedure), don't just
   report success because the commands didn't error.
4. Report back the resolved commit hash and version string so the user can
   see exactly what got pinned.
5. Remind the user this is a temporary, local-only change (see step 7) and
   confirm whether they want it left in place for an upcoming test run or
   reverted now.

Do not run any cardano-node-tests test (`runner/node_upgrade.sh`,
`make start-cluster`, etc.) on a cardonnay change without having gone through
this sync first in the same session. If a test result is already reported
from before this sync ran, treat it as unverified. An editable local
install, a `git pull` in the cardonnay clone, or "I have the right branch
checked out" are not sufficient by themselves.

## The sync procedure

1. Confirm the branch/ref exists and is pushed (or use `master` if it's
   already merged there but not yet released to PyPI). If the user asked for
   a branch, use exactly that branch, not `master`.

2. In `cardano-node-tests/pyproject.toml`, temporarily change the dependency
   line from a version pin to a git reference:

   ```
   "cardonnay @ git+https://github.com/IntersectMBO/cardonnay.git@<branch-or-master>",
   ```

3. Regenerate the lock file so it actually resolves that ref. This needs
   `uv`, which lives in the Nix dev shell, not necessarily on bare `PATH`:

   ```sh
   nix develop --accept-flake-config . -c bash -c "make update-uv-lock"
   ```

4. Confirm the lock file actually changed, don't just assume the command
   succeeded silently. `uv` prints a line like:

   ```
   Updated cardonnay v0.4.1 -> v0.4.2.dev8+gca7c6fb56 (ca7c6fb5)
   ```

   The commit hash at the end must match the tip of the branch you pushed.
   If the version number didn't change, the wiring didn't take, stop and find
   out why before running anything.

5. Clear any previously-built test venv before the next run, otherwise a
   stale one gets reused:

   ```sh
   rm -rf run_workdir/.venv
   ```

6. Now run the actual test (`runner/node_upgrade.sh`, or a plain
   `make cluster-scripts && make start-cluster` for a quicker smoke check).
   This is the first point where it's actually exercising the real change.

7. Treat this `pyproject.toml`/`uv.lock` edit as strictly temporary, local
   only. Never commit or push it as part of a real PR. Once the cardonnay
   change is confirmed working and merged, revert both files back to the
   normal version-pin form (`git checkout -- pyproject.toml uv.lock`, or
   re-run `make update-uv-lock` after reverting the dependency line). A real
   version bump only happens once cardonnay cuts an actual PyPI release.
