# Releasing ragnalog

A release is a `vX.Y.Z` tag on `main`. Pushing the tag runs `.github/workflows/release.yml`:
it runs the CI checks on that tag (Ubuntu and Windows), builds the wheel and sdist, creates
the GitHub release with the tag's CHANGELOG section as its notes, and pushes the container
image `ghcr.io/rdk/ragnalog:X.Y.Z` plus `:latest`.

CI does not run on pushes (see `ci.yml`), so check locally before every release.

## Steps

1. **Check.** All green, from the repository root:

   ```sh
   uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
   ```

   `pytest` includes the browser smoke tests (`tests/browser`, skipped without Node 22+ and
   Edge or Chrome) and `tests/test_version.py`, which fails until step 2 is complete.

2. **Bump the version** everywhere it is quoted:
   - `pyproject.toml` (`version`) and `src/ragnalog/_version.py`;
   - `uv.lock`: run `uv lock` (it rewrites the package's own `version` line);
   - `README.md`: the release badge, the Docker image tags, the `git+ssh …@vX.Y.Z` install,
     the `gh release download vX.Y.Z` command and the wheel's file name;
   - `docs/cli.md` (the `manifest.v1` and `version.v1` examples) and `docs/report.md`
     (`tool_version` in the payload example).

   `uv run pytest tests/test_version.py` names any quote that was missed.

3. **CHANGELOG.md:** rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, open a new empty
   `## [Unreleased]` above it, and at the bottom point `[Unreleased]` at
   `compare/vX.Y.Z...HEAD` and add `[X.Y.Z]: …/releases/tag/vX.Y.Z`. Say there when stored
   sets need `ragnalog upgrade --all` (a format, signature or masking version changed).

4. **Commit and push** `main`: `git commit -m "X.Y.Z: …"` then `git push origin main`.

5. **Tag and push the tag**, alone:

   ```sh
   git tag -a vX.Y.Z -m "X.Y.Z"
   git push origin vX.Y.Z
   ```

6. **Verify:** `gh run watch` on the Release run, then `gh release view vX.Y.Z` (wheel and
   sdist attached, marked Latest) and the image tags
   (`gh api user/packages/container/ragnalog/versions`).

7. **Screenshots**, when the UI changed: regenerate `docs/img` from the synthetic demo logs
   with `scripts/screenshots.mjs` (its header has the commands). Never from real logs.

## Re-running and repairing

- `gh workflow run release.yml -f tag=vX.Y.Z` re-runs the release of an existing tag: the
  release is kept and the image pushed again. Files already attached are **not** replaced;
  to replace them, `uv build` in a checkout of the tag and `gh release upload vX.Y.Z
  dist/* --clobber`.
- The newest release must finish last: every image push moves `:latest`.
- The image is built without a provenance attestation (one manifest per tag); ghcr refused
  to tag the attested image index.

## Pitfalls

- **Tags and push events:** GitHub creates no push event when more than three tags are
  pushed at once, so no release runs start; a single tag push starts its run. After a history
  rewrite, force-push the older tags together, then the newest tag alone, last (its run
  rebuilds `:latest`).
- **Old tags:** after the November 2026 history rewrites, `v0.2.0` … `v0.4.0` fail their own
  fixture digest tests (the fixture was re-masked under them). Do not re-run their release
  workflow; their releases and wheels are complete.
- **Real data:** fixtures, screenshots and docs use invented data only (AGENTS.md, "Data
  hygiene"); names of the real environment live outside the repository.
