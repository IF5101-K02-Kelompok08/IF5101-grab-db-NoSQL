# Query Mango

Isi folder ini adalah semua query Mango dari `notebook/couchdb_mango_queries.ipynb`, satu file per query.
Nama file mengikuti ID Part 2 (`R01`–`R10`, `W01`–`W10`) atau ID operasi (`PF-01-Q01`, `PF-03-Q01`,
`PF-07-Q01`, `PF-07-Q02`). Kalau satu operasi butuh beberapa query, nomor langkahnya ada di nama file,
misalnya `W10_1_cari_area.json`, `W10_2_cari_outlet.json`, dan `W10_3_cek_order_berjalan.json`.

CouchDB menolak key tambahan di body `_find` (`comment`, `_comment`, dan `$comment` semuanya error), jadi
komentarnya tidak bisa ditaruh di dalam query. Setiap file isinya seperti ini:

| Key | Isi |
|---|---|
| `operasi` | ID operasi dan langkah ke berapa |
| `database` | database tempat query dijalankan |
| `keterangan` | query ini untuk apa |
| `catatan` | penjelasan bagian-bagian query, index yang dipakai, dan langkah sesudahnya |
| `hanya_explain` | `true` kalau query-nya harus scan 2 juta order, jadi cukup dicek dengan `_explain` |
| `query` | query Mango yang dikirim ke `_find` |

## Cara menjalankan

Di Fauxton (`http://127.0.0.1:5984/_utils`), pilih database sesuai key `database`, buka Run A Query with
Mango, lalu tempel isi key `query`.

Dari terminal, ambil `query`-nya pakai `jq` lalu kirim ke `_find`:

```bash
set -a; . ./.env; set +a
F=couchdb/mango/R10_2_outlet_open.json
curl -s -u "$COUCHDB_USER:$COUCHDB_PASSWORD" -X POST \
  "http://127.0.0.1:5984/$(jq -r .database $F)/_find" \
  -H 'Content-Type: application/json' -d "$(jq .query $F)"
```

Untuk file yang `hanya_explain`-nya `true`, ganti `_find` dengan `_explain`. Query untuk operasi tulis
(`W02_2` sampai `W10_2` dan `PF-07-*_1`) mencari data uji benchmark (ID `...9999...`), jadi hasilnya kosong
kecuali data uji sedang dibuat, misalnya saat notebook dijalankan.
