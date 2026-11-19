# 1. Tujuan Pengujian
Pengujian implementasi basis data NoSQL pada proyek IF5101 Part 3 dilakukan untuk mengevaluasi kebenaran hasil operasi esensial serta kinerja eksekusinya. Pengujian fungsional bertujuan memverifikasi kesesuaian hasil operasi terhadap persyaratan yang telah diidentifikasi, sedangkan pengujian kinerja bertujuan mengukur latensi operasi basis data pada kondisi pengujian yang ditentukan.

Pengujian dilakukan terhadap operasi yang relevan dengan peruntukan masing-masing teknologi basis data. Oleh karena itu, hasil kinerja tidak dimaksudkan sebagai perbandingan langsung antara keseluruhan kemampuan kedua DBMS, melainkan sebagai evaluasi terhadap kemampuannya dalam mendukung operasi tertentu.

# 2. Dataset Acuan
Seluruh implementasi menggunakan satu canonical dataset yang dibangkitkan dari generator data sintetis pada proyek Part 2. Generator tersebut dipertahankan untuk menjaga konsistensi struktur data konseptual, hubungan antardata, serta distribusi dan aturan bisnis yang telah digunakan sebelumnya.

Dataset dibangkitkan dengan parameter berikut.
|Parameter	| Nilai|
|-----------|-------|
|Generator	|Generator sintetis proyek Part 2|
|Profil dasar|	Large|
|Faktor skala|	2|
|Jumlah service orders|	2.000.000|
|Jumlah customers|	200.000|
|Jumlah driver partners|	100.000|
|Jumlah locations|	400.000|
|Jumlah merchant outlets|	60.000|
|Random seed|	42|
|Periode data|	1 Mei–1 November 2026 (UTC)|
|Format dataset acuan|	CSV|

Jumlah data pada tabel lainnya mengikuti mekanisme pembangkitan pada generator asli.

Setiap DBMS menggunakan data acuan yang sama, tetapi dapat menerapkan transformasi berbeda sesuai rancangan struktur logisnya. Kesetaraan data ditentukan berdasarkan makna informasi, identitas entitas, dan keterhubungan antardata, bukan keharusan memiliki jumlah objek fisik yang identik dengan tabel relasional. Hasil pembangkitan didokumentasikan melalui manifest yang memuat jumlah baris, nama kolom, serta checksum SHA-256 dari setiap berkas.

# 3. Lingkungan Pengujian
Seluruh pengujian dilakukan pada lingkungan yang didokumentasikan secara eksplisit, meliputi sistem operasi, spesifikasi CPU dan RAM, versi DBMS, versi pustaka client, konfigurasi Docker, serta versi kode implementasi.

Untuk setiap eksperimen, dicatat pula konfigurasi yang relevan, seperti jumlah instance DBMS, indeks atau view yang digunakan, dan batas penggunaan memori. Pengujian dilakukan dengan meminimalkan proses lain yang berpotensi mengganggu pengukuran.

Perbedaan lingkungan pengujian harus dipertimbangkan ketika menginterpretasikan hasil kinerja.

# 4. Skenario Pengujian
Setiap skenario pengujian memiliki identitas unik (operation_id) dan dipetakan ke satu atau beberapa persyaratan fungsional atau nonfungsional yang relevan. Skenario mencakup operasi pembuatan, pembacaan, pembaruan, penghapusan, dan pengolahan data sesuai kemampuan yang dirancang pada masing-masing DBMS.

Setiap skenario terdiri atas empat tahap:
1. Setup: Menyiapkan data dan kondisi awal yang diperlukan.

2. Execution: Menjalankan operasi basis data yang akan diukur.

3. Validation: Memverifikasi hasil operasi terhadap keluaran atau perubahan keadaan yang diharapkan.

4. Teardown: Mengembalikan lingkungan ke kondisi awal atau membersihkan data pengujian sementara.
Untuk operasi yang mengubah data, kondisi awal yang ekuivalen disiapkan kembali sebelum setiap pengulangan. Pengujian penghapusan dilakukan pada data yang memang memenuhi ketentuan penghapusan sehingga tidak merusak integritas dataset acuan.

# 5. Prosedur Pengukuran Kinerja
Setiap skenario dijalankan sebanyak empat kali, terdiri atas satu kali warm-up dan tiga kali pengulangan pengukuran. Hasil warm-up tidak dimasukkan ke dalam analisis kinerja.

Waktu eksekusi diukur menggunakan time.perf_counter_ns() pada Python. Interval pengukuran dimulai tepat sebelum pemanggilan operasi DBMS dan berakhir setelah respons operasi diterima oleh client.

Pengukuran tidak mencakup proses pembangkitan atau pemuatan dataset, persiapan kondisi awal, validasi hasil, maupun pembersihan data pengujian. Dengan demikian, metrik yang dilaporkan merupakan latensi operasi basis data yang diamati dari sisi client, termasuk komunikasi dengan DBMS.

Waktu eksekusi dilaporkan dalam milidetik (ms), dengan nilai setiap pengulangan serta rata-rata dan median.

# 6. Validasi Kebenaran Hasil
Setiap skenario dinyatakan berhasil apabila operasi dapat dieksekusi tanpa kesalahan teknis dan hasilnya sesuai dengan perilaku yang diharapkan.

Untuk operasi pembacaan, hasil diverifikasi terhadap nilai yang diketahui dari dataset acuan atau keluaran referensi yang ekuivalen. Untuk operasi modifikasi, validasi dilakukan dengan memeriksa perubahan keadaan data setelah operasi selesai.

Kegagalan eksekusi dan kegagalan validasi dibedakan agar hasil pengujian dapat menunjukkan apakah masalah berasal dari proses eksekusi atau ketidaksesuaian hasil.

# 7. Pengujian Cache
Untuk operasi yang menggunakan penyimpanan sementara, pengujian membedakan kondisi cache hit dan cache miss.

Cache hit terjadi ketika informasi yang diminta tersedia dalam penyimpanan sementara, sedangkan cache miss terjadi ketika informasi yang diminta tidak tersedia. Kedua kondisi diuji secara terpisah agar perbedaan latensinya dapat dianalisis.

Selain latensi, pengujian dapat mencakup perilaku pembaruan, penghapusan atau invalidasi, dan kedaluwarsa data berdasarkan Time to Live (TTL).

Apabila dilakukan simulasi rangkaian permintaan, cache hit rate dihitung sebagai jumlah permintaan yang menghasilkan hit dibagi jumlah keseluruhan permintaan, sedangkan cache miss rate dihitung dengan cara serupa menggunakan jumlah miss. Proporsi tersebut hanya merepresentasikan beban kerja simulasi yang digunakan, bukan tingkat keberhasilan cache pada sistem produksi.

# 8. Format Keluaran Pengujian
Keluaran pengujian disimpan dalam dua format, yaitu JSON dan CSV.

Format JSON digunakan untuk menyimpan identitas operasi, persyaratan yang diuji, DBMS, status keberhasilan, hasil validasi, serta ringkasan waktu eksekusi. Format CSV digunakan untuk menyimpan waktu eksekusi setiap pengulangan secara terpisah.

Kolom CSV meliputi:

operation_id, dbms, scenario, dataset_size, run, execution_time_ms, success, validation_passed, cache_hit

Nilai dataset_size menyatakan jumlah pesanan pada dataset acuan. Nilai tersebut tidak selalu sama dengan jumlah dokumen atau entri fisik yang tersimpan pada masing-masing DBMS.

# 9. Analisis dan Keterbatasan Pengujian
Analisis dilakukan dengan mengidentifikasi operasi yang memiliki latensi relatif tinggi atau rendah, memeriksa kebenaran hasil, serta menjelaskan faktor yang mungkin memengaruhi kinerja, seperti rancangan struktur data, indeks, penggunaan cache, dan karakteristik operasi.

Hasil pengujian juga digunakan untuk mengidentifikasi peluang optimasi serta mengevaluasi kesesuaian implementasi dengan persyaratan sistem.

Pengukuran dengan tiga pengulangan memiliki keterbatasan untuk memperkirakan distribusi latensi pada beban kerja produksi. Selain itu, hasil dapat dipengaruhi kondisi cache, konfigurasi DBMS, jumlah data aktual yang dimuat, dan lingkungan perangkat keras. Pengujian ini juga tidak secara langsung membuktikan ketersediaan tinggi, ketahanan terhadap gangguan, atau skalabilitas terdistribusi tanpa eksperimen tambahan yang secara khusus dirancang untuk persyaratan tersebut.