# Asal-usul file analisis di folder ini

File bukti utama (`MANIFEST.json`, `environment.json`, `load_report.json`, `verify_report.json`,
`results.json`, `runs.csv`, `benchmark_summary.csv`, `operations_once.csv`, `tests.txt`) dihasilkan
oleh notebook `notebook/couchdb_queries.ipynb` (commit `51e8a45`) di Mac ini pada 2026-11-21/22
dan checksum-nya tercatat di `MANIFEST.json`.

File berikut ditambahkan sesudahnya dan TIDAK tercakup di `MANIFEST.json`:

| File | Cara dibuat |
|---|---|
| `load_report.json` | salinan identik `benchmarks/results/couchdb_load_report.json` yang ditulis `couchdb.load` saat pemuatan data canonical (2026-11-21 22:31–22:56 UTC) |
| `analysis_timing.csv`, `analysis_summary.md`, `figures/01_…png` s.d. `figures/04_…png` | sel analisis bagian 8 notebook (commit `d4b1024`), dijalankan ulang di atas file bukti di atas tanpa mengubahnya |
| `figures/05_baris_dikirim_couchdb.png` | `analysis_extra/fig05_view_rows.py` dengan waktu dari `analysis_timing.csv` dan jumlah baris dari `analysis_extra/view_rows_from_dataset.json` |
| `analysis_extra/view_rows_from_dataset.json` | `python3 analysis_extra/view_rows.py data/canonical`: jumlah baris view/dokumen yang dikirim CouchDB ke client per operasi baca, dihitung dari CSV dataset canonical (bukan diukur saat benchmark) |

Run notebook berikutnya mencatat jumlah ini langsung (kolom `couch_rows` dan `requests` di
`operations_once.csv`), sehingga gambar 05 bisa dibuat ulang dari pengukuran.
