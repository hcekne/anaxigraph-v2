# Publishing AnaxiGraph 1.x

The existing PyPI project is `anaxigraph`. Version 1.0.0 replaces its 0.9.x implementation with
this repository's core. Keep the version in `pyproject.toml`, `src/anaxigraph/__init__.py` and
`uv.lock` consistent. Update the README and `docs/releases/<version>.md` together.

Commit to main and wait for its exact-source CI to pass. Create an immutable annotated
`v<version>` tag, then dispatch this repository's `release.yml` from that tag with the same `tag`
input. The workflow requires the matching successful CI, builds one wheel and source archive,
checks their complete package contents and README, exercises the wheel, and attests the bundle.
`source-manifest.json` records the source repository, full commit SHA and both file hashes;
`SHA256SUMS` accompanies the same files on the GitHub release.

The GitHub release remains marked prerelease until PyPI publication and installation are verified.
This is a publication state, not a different Python version. Do not move its tag or replace assets.

## Existing trusted publisher

PyPI currently trusts `hcekne/anaxigraph`, workflow `release.yml`, environment `pypi`.
The 1.x publishing bridge uses that same identity without importing or executing the old runtime.
Dispatch that workflow directly on main with `v2_version`, `v2_source_sha`, `v2_wheel_sha256` and
`v2_sdist_sha256` from the verified V2 manifest. Leave the legacy `tag` input empty.

The unprivileged download job reads only the fixed public V2 release, validates its source
identity, package metadata and exact hashes, then retains an immutable workflow artifact.
The separate PyPI job publishes those unchanged distribution files using short-lived OIDC.
Its public verification job compares PyPI hashes and installs the package. PyPI's publisher
attestation therefore names the original workflow; the V2 manifest and GitHub build attestations
identify this source repository explicitly. No environment policy or API token change is needed.

After publication, independently install with `uvx --refresh --from anaxigraph==<version>` outside
the checkout, check CLI/dashboard/MCP behavior, and confirm the public JSON artifact hashes.
Update the release record and GitHub release body with actual receipts, then mark the GitHub
release stable/latest. Package publication does not restart existing services or deploy a container.

A future direct publisher for `hcekne/anaxigraph-v2` can replace this bridge after registration
in the project's PyPI publishing settings. A reusable caller is not interchangeable with the
registered publishing identity. If an upload partly succeeds, retain its hashes and upload only
the missing verified files; never replace a published distribution or silently advance the version.
