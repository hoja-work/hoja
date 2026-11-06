# Dependency security policy

Dependency advisories are build signals, not ambient noise. Each surface is
checked according to its ecosystem and failure severity:

| Surface | Checked by | Fails on |
| --- | --- | --- |
| `frontend/` npm tree | `npm audit --audit-level=high` | high, critical |
| Python lock (`uv.lock`) | `pip-audit -r` on `uv export` | any known advisory |
| Container base images | `docker compose build --pull` | stale base layers requiring review |

CI runs the npm and Python checks on every push and pull request, plus a weekly
schedule (`.github/workflows/security-audit.yml`). Dependabot opens grouped
patch/minor pull requests for npm, pip, Docker, and GitHub Actions.

## Deployed images

A container image freezes dependencies at build time, so an audited source tree
can still produce an image with stale packages. Rebuild with updated base layers
and audit the resulting environment during deployment review:

```bash
docker compose build --pull
docker compose run --rm --no-deps app python -m pip list --format=freeze \
  > /tmp/hoja-image-freeze.txt
uvx pip-audit -r /tmp/hoja-image-freeze.txt
```

Remove local packages that are not published to PyPI from the exported freeze
list before running `pip-audit`. Investigate findings and upgrade or document a
reviewed exception in the pull request; do not commit point-in-time audit output
to the repository.

## Local checks

```bash
# Frontend dependencies
cd frontend && npm ci && npm audit --audit-level=high

# Python lock, not the ambient environment
uv export --frozen --no-emit-project --format requirements-txt -o /tmp/hoja-requirements.txt
uvx pip-audit -r /tmp/hoja-requirements.txt
```
