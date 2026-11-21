# Release Workers Bridge MCP

Releases are published to PyPI by
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml) when a
version tag such as `v0.5.0` is pushed. The workflow:

1. runs the Python tests;
2. verifies that the tag exactly matches
   `mcp_workers_bridge.__version__`;
3. builds and validates the sdist and wheel;
4. uploads those exact files to PyPI from a protected `pypi` environment.

Trusted Publishing uses a short-lived OpenID Connect identity from GitHub, so
the repository does not store a PyPI API token.

The project is distributed under the MIT License. Before the first release,
confirm that the copyright holder and year in `LICENSE` are correct.

## One-time trusted publisher setup

Create the `pypi` environment under the repository's **Settings →
Environments**. Require approval from a trusted maintainer for every deployment.
For extra protection, restrict deployment tags to the `v*` pattern and limit
which branches or tags can deploy to the environment.

Then configure this pending Trusted Publisher in PyPI under **Account settings
→ Publishing**, or under the existing project's **Publishing** settings:

| Field | Value |
| --- | --- |
| PyPI project name | `mcp-workers-bridge` |
| Owner | `yuitof` |
| Repository | `mcp-workers-bridge` |
| Workflow name | `publish.yml` |
| Environment | `pypi` |

The workflow filename must be exactly `publish.yml`. Configure the environment
name only if that field is also present on the trusted publisher. See the
[PyPI Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/) for
the current account flow.

After the publisher is saved, the first successful tagged release creates or
claims the PyPI project.

## Publish a release

1. Update `__version__` in `src/mcp_workers_bridge/__init__.py`.
2. Run `uv lock`, `make check`, and verify that the built package version is the
   intended release.
3. Commit the release changes and merge them to `main`.
4. Create and push the matching tag:

   ```bash
   git tag v0.5.0
   git push origin v0.5.0
   ```

5. Approve the `pypi` environment deployment in GitHub Actions.
6. Open <https://pypi.org/project/mcp-workers-bridge/> and verify the version,
   files, metadata, README, and install command.

PyPI does not allow replacing an uploaded version. If anything is wrong after
publication, fix it and release a new patch version rather than moving or
reusing the tag.

## Local artifact check

Build into a temporary directory so existing artifacts are left untouched:

```bash
dist_dir="$(mktemp -d)"
uv build --out-dir "$dist_dir"
uvx twine check "$dist_dir"/*
```
