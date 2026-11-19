Sebelum mengimplementasikan query NoSQL, ada beberapa kesepakatan yang harus diputuskan.
Beberapa yang bisa dituliskan di dokumen ini per hari ini (19 Nov 2026), adalah

| Komponen | Kesepakatan |
|----------|-------------|
|ID Data   | Gunakan identifier yang konsisten dengan yang sudah ada di Part 2, misalnya kalo nanti di CouchDB mau bikin collection service_orders berarti setiap dokumennya kalo bisa pake order_id yang formatnya "ORDXXXXXXX". |
|Pembangkitan Dataset | Buat script transformator per DBMS (karena kan skema logisnya beda), yang meng-adjust dari satu canonical dataset yang ada di folder `/data`|
|Ukuran Dataset| Karena di dokumen [laporan](https://docs.google.com/document/d/1Nsd7lJaTReGwManm9biRvCI3lNn9IFct7Rzb5r5TREM/edit?tab=t.0) itu salah satu alasan NoSQL adalah karena volume data, jadi ukuran datasetnya kita buat 2x profile large di SQL (berarti 2mio service_orders sebagai corenya dan sisanya mengikuti)|
|Random seed| Semua random seed disamain aja pake 42|
|Query| Ketika membuat query cantumkan comment untuk mapping query tersebut menyelesaikan ID persyaratan PF atau PNF yang mana dan skenario operasi apa yang diuji|
|Benchmark| setiap query dijalankan 3x, analisisnya dilakukan dalam satuan milidetik (ms), dan dengan 1x warm-up. Khusus untuk memcached, akan diukur juga rate cache hit dan cache miss. Untuk operasi yang mengubah data, kondisi awal yang ekuivalen harus dipulihkan sebelum setiap pengulangan pengukuran agar hasil pengujian dapat dibandingkan secara adil. |
|Output Hasil Pengujian (atau benchmarking)| Hasilnya JSON yang contohnya ada di bawah. Hasil setiap run benchmark disimpan sebagai CSV |

## Contoh JSON Output Hasil Pengujian
```JSON

{
  "operation_id": "PF-03-01",
  "requirement_ids": ["PF-03", "PNF-03"],
  "dbms": "couchdb",
  "operation": "update_trip_status",
  "success": true,
  "result": {
    "trip_id": "TRP0000001",
    "trip_status": "COMPLETED"
  },
  "validation": {
    "passed": true
  }
}

```

success pada JSON berarti operasi berhasil dieksekusi secara teknis, sedangkan validation.passed berarti hasilnya sesuai ekspektasi.

## Contoh CSV Benchmark Runs

```CSV
operation_id,dbms,scenario,dataset_size,run,execution_time_ms
PF-03-Q01,couchdb,update,200000,1,12.41
PF-05-Q01,memcached,cache_hit,200000,1,0.83
PF-05-Q01,memcached,cache_miss,200000,1,1.12
```

## Masih Perlu Disesuaikan
Di file `/docker/compose.yaml` versi couchdb dan memcached sementara sudah dituliskan. Tapi kalau nanti para PIC untuk masing-masing DBMS mau mengubahnya tolong pastikan aja selaras dengan yang dituliskan di laporan.