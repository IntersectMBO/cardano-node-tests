---
name: dep-sync
description: >-
  Sync cardano-node-tests to a specific cardonnay or cardano-clusterlib
  branch/ref so a real test run actually exercises it, instead of a stale
  PyPI release. Use whenever making or verifying changes in the cardonnay
  repo (cluster scripts, genesis spec files, cost models) or the
  cardano-clusterlib repo (transaction building, CLI wrappers, query
  helpers) that need to be exercised via a cardano-node-tests test run, or
  whenever a test result needs to be trusted before reporting it as
  "verified" or "passing."
---

# Syncing cardano-node-tests to a dependency branch

`cardano-node-tests` depends on `cardonnay` and `cardano-clusterlib` as
normal Python packages, pinned in `pyproject.toml` to version ranges and
locked in `uv.lock` to specific PyPI releases.

This matters for every environment built from those files, because such a
build has no idea a local `/path/to/cardonnay` or
`/path/to/cardano-clusterlib-py` clone exists, let alone an editable install
of it. Without the git-ref pin below it runs the stale PyPI release,
whatever is checked out locally.

## The three environments

| How it is built | venv | Contents | Who uses it |
| --- | --- | --- | --- |
| `runner/regression.sh`, `runner/node_upgrade.sh` | `$WORKDIR/.venv`, wiped and recreated each run | `uv sync --no-dev` from `pyproject.toml` + `uv.lock` | CI, full test runs |
| `make test-env` | `dev_workdir/.venv`, wiped and recreated | same, it sources the same `runner/setup_venv.sh` | product devs re-running one E2E test, without a full dev setup |
| `make install` | `./.venv` | `uv sync --group docs --group dev` | test authors' dev environment |

The first two are the ones the git-ref pin exists for: they are rebuilt from
`pyproject.toml` + `uv.lock` every time and ignore editable installs
completely. `make test-env` is not a lighter `make install`, it builds the
CI-style environment under `dev_workdir` and never touches `./.venv`. It
does `rm -rf dev_workdir` first, so don't run it over a dev cluster whose
state is wanted.

The third, `./.venv`, is where test authors put editable installs
(`make reinstall-editable repo=...`), so it may already track the clone's
working tree. Check for that before rebuilding it, see "Editable install"
below.

## Supported dependencies

| Package | `pyproject.toml` dependency name | Git URL |
| --- | --- | --- |
| cardonnay | `cardonnay` | `https://github.com/IntersectMBO/cardonnay.git` |
| cardano-clusterlib | `cardano-clusterlib` | `https://github.com/input-output-hk/cardano-clusterlib-py.git` |

Note the clusterlib repo name (`cardano-clusterlib-py`) differs from its
package name (`cardano-clusterlib`). Both can be synced at the same time, in
which case do every step below for each of them.

## When this skill is invoked, do this immediately

1. Work out which dependency (or both) is being synced. If the invocation
   argument or the surrounding conversation names one, use that. If it is
   ambiguous, ask, do not guess.
2. If a branch/ref was given as an argument, use it. Otherwise ask the user
   which branch, tag, or commit to sync to before doing anything else. Do
   not guess or default to `master` silently, confirm it.
3. Run the sync procedure below against that ref, in `cardano-node-tests`.
4. Verify it actually took effect (step 4 of the procedure), don't just
   report success because the commands didn't error.
5. Report back the resolved commit hash and version string for each synced
   package so the user can see exactly what got pinned.
6. Remind the user this is a temporary, local-only change (see step 6) and
   confirm whether they want it left in place for an upcoming test run or
   reverted now.

Do not report any cardano-node-tests result as verifying a cardonnay or
cardano-clusterlib change unless the code under test was actually the code
in question, i.e. either this sync ran and the venv the test used was built
from the synced `uv.lock`, or an editable install pointing at the right
clone at the right ref was in place. If a test result is already reported
from before either of those was established, treat it as unverified. A `git
pull` in the dependency clone or "I have the right branch checked out" are
not sufficient by themselves.

## Editable install

Check this before touching `./.venv`. Run from the `cardano-node-tests`
root:

```sh
./.venv/bin/python - <<'PY'
import importlib.metadata as md, json
for pkg in ("cardonnay", "cardano-clusterlib"):
    try:
        dist = md.distribution(pkg)
    except md.PackageNotFoundError:
        print(f"{pkg}: NOT INSTALLED")
        continue
    raw = dist.read_text("direct_url.json") or "{}"
    info = json.loads(raw)
    if info.get("dir_info", {}).get("editable"):
        print(f"{pkg}: EDITABLE {dist.version} <- {info['url']}")
    else:
        print(f"{pkg}: registry {dist.version}")
PY
```

Only `./.venv` is worth checking. The `runner/setup_venv.sh` environments
are recreated from `uv.lock` on every run, so they are never editable.

For a package reported `EDITABLE`:

- **Skip the `./.venv` rebuild in step 5.** `./.venv` already imports that
  clone's working tree, so a dev-environment run exercises it with no
  rebuild at all. Say that this is why no rebuild happened, don't skip it
  silently.
- **Never run `make install` to "make sure".** It is a
  `uv sync` into `./.venv`, which reinstalls the dependency from `uv.lock`
  and drops the editable install, i.e. destroys the dev setup the user
  wants kept. Recovering it means another `make reinstall-editable
  repo=...`, see README.
- **Confirm the clone matches the target ref.** An editable install
  exercises whatever is checked out in that working tree right now, so check
  the printed path is the intended clone, that the target ref is what's
  checked out there, and that the tree is clean enough that "the ref" is a
  meaningful description of it.
- **The URL may point outside this worktree.** With several
  `cardano-node-tests` worktrees around, an editable install done from one
  of them can be what the others resolve to. Report the path, don't assume
  it is the clone the user has open.
- **The pin in steps 2-4 is still required for the other two
  environments.** A CI entry point and `make test-env` build their own venv
  and ignore the editable install entirely. Do the pin whenever one of them
  is how the change gets exercised. `make test-env` is safe to run
  alongside an editable install, it leaves `./.venv` alone, but the
  environment it produces will not use it.

## The sync procedure

1. Confirm the branch/ref exists and is pushed to the git URL from the table
   above (or use `master` if it's already merged there but not yet released
   to PyPI). If the user asked for a branch, use exactly that branch, not
   `master`. Note the dependency clones may have several remotes (personal
   forks as `origin`, the canonical repo as `upstream`), so check that the
   ref is reachable from the URL you are about to put in `pyproject.toml`,
   not merely present locally.

2. In `cardano-node-tests/pyproject.toml`, temporarily change the dependency
   line from a version pin to a git reference:

   ```toml
   "cardonnay @ git+https://github.com/IntersectMBO/cardonnay.git@<branch-or-master>",
   ```

   ```toml
   "cardano-clusterlib @ git+https://github.com/input-output-hk/cardano-clusterlib-py.git@<branch-or-master>",
   ```

   If the ref lives only on a fork, point the URL at that fork instead.

3. Regenerate the lock file so it actually resolves that ref:

   ```sh
   uv lock
   ```

4. Confirm the lock file actually changed, don't just assume the command
   succeeded silently. `uv` prints a line like:

   ```text
   Updated cardonnay v0.4.1 -> v0.4.2.dev8+gca7c6fb56 (ca7c6fb5)
   ```

   ```text
   Updated cardano-clusterlib v0.10.5 -> v0.10.6.dev3+g1f2e3d4a (1f2e3d4a)
   ```

   The commit hash at the end must match the tip of the branch you pushed.
   If the version number didn't change, the wiring didn't take, stop and find
   out why before running anything. When syncing both packages, check both
   lines are present.

5. Run the actual test.

   - Via a CI entry point (`runner/regression.sh`, `runner/node_upgrade.sh`)
     or `make test-env`: nothing more to do. Each builds its own fresh venv
     from `pyproject.toml` + `uv.lock`, so it picks the synced ref up on its
     own.
   - In the dev environment (`make cluster-scripts`, `make start-cluster`, a
     bare `pytest`, all with `./.venv` activated): `./.venv` is never
     reconciled with `pyproject.toml` on its own. If the dependency is
     installed editable there, it is already correct, leave it alone.
     Otherwise `make install` rebuilds `./.venv` from the synced lock file.

6. Treat this `pyproject.toml`/`uv.lock` edit as strictly temporary, local
   only. Never commit or push it as part of a real PR. Once the dependency
   change is confirmed working and merged, revert both files back to the
   normal version-pin form (`git checkout -- pyproject.toml uv.lock`, or
   re-run `uv lock` after reverting the dependency line). A real
   version bump only happens once the dependency cuts an actual PyPI release.
