# IF5101 NoSQL smoke-test starter

Runs a small isolated CouchDB CRUD test and Memcached set/get/update/delete test.
The CouchDB test uses a random temporary DB and deletes it when finished.

## Local setup

Requires Docker Compose and Python 3.12+ (standard library only).

```bash
cp .env.example .env
# edit .env with credentials for local development
docker compose --env-file .env -f docker/compose.yaml up -d
```

Load `.env` values for the Python process before testing:

```bash
# Linux / macOS
set -a; . ./.env; set +a
python tests/smoke_test.py
```

```powershell
# PowerShell (from repo root)
Get-Content .env | Where-Object { $_ -match '^[^#=]+=.*' } | ForEach-Object {
  $parts = $_ -split '=', 2
  [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
}
python tests/smoke_test.py
```

Stop local containers (keep CouchDB data):

```bash
docker compose -f docker/compose.yaml down
```

The workflow `.github/workflows/nosql-smoke.yml` runs automatically on PRs to `main`, pushes to `main`, and manual dispatch. It creates disposable services on a GitHub-hosted runner. CI credentials are for CI-only disposable containers, never production.

**Scope:** This is an infrastructure + basic operations smoke test, not a benchmark or complete validation of all IF5101 functional and nonfunctional requirements.
