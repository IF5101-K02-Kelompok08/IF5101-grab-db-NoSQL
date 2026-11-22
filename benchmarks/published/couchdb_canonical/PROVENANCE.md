# Asal-usul file di folder ini

Bukti ini berasal dari dataset canonical (2.000.000 order) di CouchDB 3.5.3, Mac ini (Apple M5,
16 GB RAM). Checksum SHA-256 setiap file bukti tercatat di `MANIFEST.json`. Output lengkapnya juga
tersimpan di `notebook/couchdb_queries.ipynb`.

| File | Cara dibuat |
|---|---|
| `environment.json`, `verify_report.json`, `operations_once.csv`, `tests.txt`, `benchmark_summary.csv`, `analysis_timing.csv`, `analysis_summary.md`, `figures/01_…png` s.d. `figures/04_…png`, `figures/05_faktor_baris_dan_request.png`, `MANIFEST.json` | satu run notebook `notebook/couchdb_queries.ipynb` pada commit `954367b` tanpa perubahan yang belum di-commit (2026-11-22 01:43–01:44 UTC), dengan `RUN_LOAD = False`, `RUN_BENCHMARK = False`, `RUN_TESTS = True` |
| `results.json`, `runs.csv` | `benchmarks.run` dengan adapter CouchDB (1 warm-up + 3 run terukur per operasi) pada 2026-11-21 23:05 UTC, saat HEAD di `51e8a45` dan dengan file reference `data/reference/couchdb_reference_canonical.json`. Kode `couchdb/`, `benchmarks/adapters/`, `benchmarks/run.py` dan `tests/` sama persis antara commit itu dan `954367b`; yang berubah hanya nama project Compose di `docker/compose.yaml` |
| `load_report.json` | salinan identik `benchmarks/results/couchdb_load_report.json` yang ditulis `couchdb.load` saat pemuatan data canonical (2026-11-21 22:31–22:56 UTC) |

File berikut TIDAK tercakup di `MANIFEST.json`:

| File | Keterangan |
|---|---|
| `PROVENANCE.md` | file ini, ditulis setelah run notebook |
| `figures/05_baris_dikirim_couchdb.png`, `analysis_extra/` | versi lama gambar 05. Jumlah barisnya dihitung dari CSV dengan `analysis_extra/view_rows.py`, bukan diukur. Digantikan oleh `figures/05_faktor_baris_dan_request.png`, yang memakai kolom `couch_rows` dan `requests` hasil pengukuran di `operations_once.csv`. Disimpan untuk perbandingan |

`couchdb_reference_canonical.json` (hasil yang diharapkan, 63,6 MB) tercatat di `MANIFEST.json`
tetapi tidak dipublikasikan. File ini dibuat ulang dengan `python -m couchdb.reference --data-dir
data/canonical --output data/reference/couchdb_reference_canonical.json`.
