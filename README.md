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

### Canonical Dataset Verification

Dataset acuan dibangkitkan menggunakan generator sintetis dari proyek Part 2 dengan profil `nosql` dan random seed 42. Untuk memastikan seluruh anggota kelompok menggunakan dataset yang identik, setiap file CSV divalidasi terhadap `data/canonical/manifest.json`.

Jalankan:

```bash
uv run python scripts/verify_canonical.py
```

Skrip akan memverifikasi keberadaan seluruh CSV, kesesuaian kolom, jumlah baris, ukuran file, dan checksum SHA-256. Proses verifikasi dilakukan secara streaming agar tidak memerlukan pemuatan seluruh dataset ke memori.

Pengujian dinyatakan berhasil apabila seluruh CSV sesuai dengan manifest yang tersimpan dalam repository.