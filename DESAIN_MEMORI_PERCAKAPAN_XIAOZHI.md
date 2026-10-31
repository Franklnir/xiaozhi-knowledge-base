# Rancangan Memori Percakapan XiaoZhi

## Tujuan

XiaoZhi dapat menyimpan percakapan secara otomatis, memahami konteks yang baru terjadi beberapa detik lalu, dan menemukan kembali percakapan sampai setidaknya satu tahun sebelumnya. Ingatan harus terisolasi per akun dan per slot. Timestamp, sumber, serta hubungan antara ucapan, jawaban, dan hasil tool harus dapat ditelusuri.

Riwayat lengkap disimpan untuk pencarian dan audit. Model tidak menerima seluruh riwayat pada setiap prompt; ia menerima konteks terbaru dan potongan lama yang relevan agar batas token dan biaya tetap terkendali.

## Kondisi proyek sekarang

- `chat_history` sudah menyimpan `owner_id`, `slot_number`, `device_mac`, `request_id`, waktu, pesan user, jawaban XiaoZhi, serta payload request/response.
- MCP menyediakan `save_chat_history` dan `recall_chat_memory`.
- Penyimpanan transcript belum dijamin otomatis per giliran karena `save_chat_history` bergantung pada pemanggilan tool oleh model.
- Riwayat semantik dapat dicari, tetapi pemanggilan recall masih bergantung pada alur tool/prompt; belum menjadi langkah retrieval wajib sebelum setiap jawaban.

Implementasi sebaiknya memperluas jalur pencatatan dan retrieval yang ada, bukan membuat memori global baru yang tidak terhubung ke `chat_history`.

## Identitas dan batas isolasi

Gunakan kunci lingkup berikut untuk setiap read/write/delete memori:

```text
memory_scope = (owner_id, slot_number)
```

`device_mac`, `token_hash`, dan sumber koneksi disimpan sebagai metadata asal, bukan pengganti `owner_id + slot_number`. Slot harus ditentukan dari token/sesi yang sudah terautentikasi. Jangan menerima `owner_id` atau `slot_number` bebas dari isi pesan atau payload tool.

Setiap entri memiliki:

- `conversation_id`: UUID percakapan/sesi, dapat bertahan lintas beberapa turn jika sesi yang sama dilanjutkan.
- `turn_id`: UUID satu giliran percakapan, idempoten saat retry.
- `request_id`: ID dari gateway/MCP jika tersedia; buat UUID di backend bila tidak ada dan teruskan melalui API, tool, penyimpanan, dan log.
- `sequence_no`: urutan event di dalam percakapan.
- `owner_id`, `slot_number`: wajib pada semua query.

Kombinasi akun dan slot juga wajib menjadi bagian indeks pencarian semantik. Hasil yang ditemukan di slot lain tidak boleh masuk ke prompt.

## Waktu dan urutan

Simpan waktu dalam UTC dengan presisi milidetik atau lebih baik:

- `event_at`: waktu ucapan/event berlangsung jika perangkat menyediakan timestamp yang valid.
- `received_at`: waktu server menerima event; selalu tersedia dan menjadi dasar urutan fallback.
- `completed_at`: waktu jawaban atau tool selesai.
- Simpan zona waktu tampilan sebagai preferensi akun; tampilkan waktu lokal, misalnya `Asia/Jakarta`.

Jangan mempercayai jam perangkat tanpa validasi. Jika timestamp perangkat terlalu jauh dari waktu server, tetap simpan sebagai metadata tetapi urutkan menggunakan `received_at` dan tandai `clock_skew`.

## Siklus pencatatan setiap giliran

1. Saat request percakapan dimulai, backend membuat atau menerima `conversation_id`, `turn_id`, dan `request_id`.
2. Partial ASR boleh dipakai untuk konteks real-time selama sesi aktif, tetapi versi final transkripsi segera ditulis ke penyimpanan agar tidak hilang saat koneksi terputus.
3. Jawaban akhir XiaoZhi ditulis sebagai event terpisah pada `turn_id` yang sama.
4. Setiap pemanggilan tool dan hasilnya ditautkan ke `turn_id`, dengan status sukses/gagal, waktu, dan ukuran payload.
5. Semua write memakai upsert/idempotency key supaya retry tidak membuat transcript ganda.
6. Pencatatan dilakukan oleh backend/gateway secara otomatis. `save_chat_history` tetap dapat dipakai sebagai kompatibilitas/fallback, bukan satu-satunya cara menyimpan percakapan.

Jika sesi terputus di tengah jalan, transcript user dan status turn `interrupted` tetap tersimpan. Jangan menandai jawaban yang belum selesai sebagai jawaban final.

## Pembentukan konteks model

Sebelum membuat jawaban, backend membentuk konteks dengan urutan prioritas:

1. Transcript final dari sesi aktif dan event berdekatan dalam jendela real-time sekitar 3–5 detik, memakai `turn_id`, urutan event, dan timestamp.
2. Giliran terbaru pada slot aktif, misalnya 10–20 turn, dibatasi oleh anggaran token yang ditetapkan per model.
3. Ringkasan percakapan terdahulu untuk slot itu.
4. Hasil pencarian semantik/teks atas arsip satu tahun terakhir, hanya jika ada petunjuk bahwa pengguna merujuk ke topik atau kejadian lama.
5. Untuk hasil lama yang dipilih, ambil satu atau dua giliran sebelum/sesudah hasil agar jawaban tetap memiliki konteks kronologis.

Retrieval riwayat lama sebaiknya dilakukan otomatis di backend untuk pertanyaan seperti “yang kemarin”, “tiga detik lalu”, atau “waktu saya cerita tentang ...”. Tool `recall_chat_memory` dapat tetap tersedia untuk permintaan eksplisit, tetapi ketepatan dasar tidak boleh bergantung pada model yang ingat memanggil tool tersebut.

Saat batas token hampir tercapai, ringkas giliran lama secara bertahap. Ringkasan memuat periode waktu dan referensi ke `turn_id` sumber. Jangan mengganti atau menghapus transcript asli dengan ringkasan.

## RAG untuk arsip percakapan

RAG (Retrieval-Augmented Generation) adalah cara mengambil bukti dari arsip yang relevan lalu memberikannya ke model sebelum model menjawab. RAG dipakai untuk riwayat lama; konteks beberapa detik terakhir tetap diberikan langsung dari sesi aktif karena lebih akurat dan lebih cepat daripada pencarian vektor.

### Kondisi retrieval sekarang

Proyek sudah memiliki pencarian riwayat yang disebut semantik. `rank_chat_history_semantically` memperluas token melalui taksonomi konsep, menghitung kemiripan cosine pada fitur token tersebut, dan memberi bobot waktu. Ini berguna sebagai pencarian kata/konsep, tetapi belum merupakan pipeline embedding dense/vector index yang mengubah seluruh arsip menjadi representasi semantik umum. Rancangan RAG di bawah ini melengkapi pencarian tersebut, bukan menganggap vector RAG sudah tersedia.

### Alur ingest dan retrieval

1. Setelah turn tersimpan, bentuk dokumen retrieval dari pasangan pesan user dan jawaban, waktu, judul/topik, tool yang dipakai, serta ringkasan hasil tool. Simpan referensi ke `turn_id` dan payload asli.
2. Pecah dokumen pada batas turn/topik yang wajar; jangan memisahkan pertanyaan dari jawaban terkait. Untuk transkrip panjang, gunakan potongan kecil dengan overlap konteks. Abaikan retry transport, heartbeat, dan data operasional yang bukan percakapan.
3. Buat embedding secara asinkron. Simpan `embedding_model`, versinya, waktu pembuatan, `owner_id`, `slot_number`, `conversation_id`, `turn_id`, dan `event_at` bersama setiap embedding. Perubahan model embedding harus memungkinkan re-index tanpa kehilangan arsip sumber.
4. Saat pertanyaan baru masuk, tentukan apakah perlu pencarian lama berdasarkan kata waktu/topik atau ketidakjelasan rujukan. Cari gabungan full-text, konsep/token saat ini, dan embedding dense.
5. Terapkan batas `(owner_id, slot_number)` **di dalam query pencarian sebelum ranking/top-k**, bukan setelah hasil dicampur. Tambahkan filter waktu jika user menyebut “kemarin”, tanggal tertentu, atau “setahun lalu”.
6. Gabungkan kandidat dengan skor relevansi, kecocokan waktu, dan keragaman percakapan. Ambil beberapa hasil terbaik beserta giliran di sekitarnya; hilangkan duplikasi dari ringkasan dan transcript.
7. Berikan hasil ke model sebagai kutipan memori dengan timestamp dan sumber `turn_id`. Model diminta menyatakan jika bukti tidak ditemukan atau tidak pasti, bukan mengarang detail.
8. Catat skor/sumber retrieval dan referensi turn yang digunakan tanpa menulis konten pribadi ke log operasional.

Untuk PostgreSQL, implementasi dapat memakai `pgvector` bila extension tersedia, plus full-text search yang sudah dipakai database. Jika extension belum tersedia, mulai dengan full-text dan pencarian konsep yang ada; antarmuka retrieval harus memungkinkan vector backend ditambahkan tanpa mengubah format transcript. Hindari menambah layanan vector database terpisah kecuali volume atau kebutuhan throughput membenarkannya.

### Kebijakan jawaban memory-first dan fallback tools

Setiap pertanyaan diproses dengan alur berikut sebelum XiaoZhi menyusun jawaban:

1. Tentukan lingkup akun/slot dari sesi tepercaya dan klasifikasikan maksud: melanjutkan percakapan, mengingat topik/waktu tertentu, meminta data akun/aplikasi, atau menanyakan informasi eksternal/terkini.
2. Jika ada kemungkinan rujukan ke obrolan lama, cari dulu dalam konteks aktif dan arsip percakapan pada slot itu. Ambil bukti yang relevan, timestamp, dan referensi turn.
3. Jika kecocokan riwayat cukup kuat, berikan konteks tersebut ke model dan izinkan respons alami, misalnya: “Kayaknya kita pernah bahas topik ini—waktu itu kita membicarakan ... sekitar [waktu]. Itu yang kamu maksud?” Pernyataan waktu/topik hanya boleh dibuat jika didukung hasil retrieval.
4. Jika kecocokan lemah atau beberapa percakapan mirip ditemukan, XiaoZhi menyampaikan bahwa ia menemukan kemungkinan pembahasan, merangkum pembeda dan waktu secara singkat, lalu meminta konfirmasi. Jangan menyatakan “saya ingat” sebagai fakta tanpa sumber.
5. Jika riwayat tidak menemukan jawaban, pilih tool lain berdasarkan kebutuhan: database/API untuk data aplikasi, pencarian web untuk informasi publik yang berubah/eksternal, atau tool domain khusus yang tersedia. Tool hanya dipanggil jika sesuai dengan pertanyaan.
6. Jika riwayat dan tool tidak menghasilkan bukti, katakan bahwa informasi belum ditemukan dan ajukan pertanyaan klarifikasi yang diperlukan. Jangan mengisi kekosongan dengan ingatan palsu.
7. Jawaban dan tool result dicatat kembali pada `turn_id` yang sama agar konteks selanjutnya memiliki jejak waktu dan sumber.

Konteks yang diberikan ke model harus membedakan sumber dengan label eksplisit, misalnya `[RIWAYAT PERCAKAPAN]`, `[HASIL TOOL/API]`, dan `[HASIL PENCARIAN WEB]`. Semua retrieval memori menerapkan filter akun dan slot sebelum ranking; hasil dari slot berbeda tidak boleh digunakan walaupun topiknya cocok.

### JSON besar di RAG

Jangan membuat satu embedding dari JSON mentah yang sangat panjang. Simpan dokumen asli di arsip payload, lalu indekskan ringkasan dan potongan bermakna seperti item, tanggal, nama field, error, atau hasil. Sertakan pointer field/path dan rentang item. Jika pengguna meminta angka/detail persis, ambil nilai tersebut dari JSON sumber dan tunjukkan waktu/sumbernya ke model. Dengan begitu indeks semantik tetap ringan tanpa menghilangkan nilai asli.

## Perapihan payload JSON riwayat mingguan

Jalankan compactor terjadwal untuk mengurangi payload JSON operasional yang besar pada `chat_history`. Ini proses terpisah dari RAG dan **tidak menyentuh database/tabel memori jangka panjang, persona, ringkasan memori, embedding, atau indeks semantik**.

Aturan compaction:

1. Pilih record yang berumur lebih dari 7 hari dan melewati ambang ukuran payload yang dapat dikonfigurasi. Jangan memproses seluruh riwayat tanpa syarat.
2. Pertahankan kolom percakapan utama (`user_message`, `xiaozhi_answer`), identitas akun/slot, waktu, source/tool, `request_id`, dan relasi `turn_id` apa adanya.
3. Sebelum mengganti payload besar di `request_payload`/`response_payload`, simpan salinan asli yang persis ke arsip operasional terkompresi di penyimpanan persisten. Simpan pointer, ukuran asli, checksum SHA-256, dan status archive di record riwayat.
4. Ganti hanya payload inline dengan ringkasan JSON skema tetap: status, nama tool, waktu/rentang, jumlah item, identifier penting, field utama, dan error. Hilangkan field transport berulang yang tidak punya nilai percakapan. Jangan mengubah nilai angka/tanggal atau mengarang field.
5. Jika perlu menelusuri kembali detail, backend dapat memuat payload asli dari arsip berdasarkan pointer/checksum. Bila arsip belum terverifikasi, jangan menghapus atau mengganti payload inline.
6. Proses harus idempoten, batch kecil, memiliki batas waktu/CPU, dan mencatat `compacted_at`, `original_size_bytes`, `summary_size_bytes`, serta status berhasil/gagal. Kesalahan satu payload tidak boleh menghentikan batch berikutnya.
7. Record di bawah ambang ukuran dibiarkan. Transcript percakapan tetap dapat dicari tanpa membaca blob JSON penuh.

Jadwalkan harian untuk mencari record yang melewati usia 7 hari; tidak perlu menunggu job mingguan besar. Simpan arsip JSON operasional setidaknya sepanjang masa retensi riwayat yang dipilih. Kebijakan dan operasi ini hanya berlaku pada payload riwayat chat, bukan database memori yang harus dibiarkan utuh.

### Kualitas dan keamanan retrieval

- Retrieval harus selalu dibatasi akun dan slot; skor tinggi tidak boleh mengalahkan batas isolasi.
- Gunakan timestamp asli serta rentang waktu untuk membedakan percakapan yang mirip tetapi terjadi pada tanggal berbeda.
- Terapkan batas token terhadap hasil retrieval dan prioritaskan bukti yang dapat menjawab pertanyaan.
- Simpan versi embedding agar hasil dari versi lama bisa diidentifikasi dan diindeks ulang.
- Ukur precision/recall dengan contoh pertanyaan tentang beberapa detik lalu, topik seminggu/bulan/tahun lalu, waktu spesifik, JSON detail, dan percakapan identik di slot berbeda.
- Hapus embedding/chunk/ringkasan/payload saat sumber turn dihapus atau masa retensi berakhir.

## JSON dan payload tool yang panjang

Payload JSON penuh tidak boleh otomatis ditempel seluruhnya ke prompt.

- Simpan payload asli tanpa perubahan untuk kebutuhan audit/retrieval, beserta `content_type`, ukuran byte, checksum SHA-256, status parsing, dan referensi ke turn/tool.
- Untuk payload kecil, simpan pada record payload terstruktur di PostgreSQL (`JSONB` bila sesuai). Payload besar disimpan terkompresi pada penyimpanan persisten bersama, lalu database menyimpan pointer, ukuran, dan checksum.
- Buat representasi ringkas untuk model: nama tool, hasil/status, field penting, jumlah item, rentang waktu, dan error. Pertahankan nama field dan angka penting; jangan merangkum dengan mengubah nilai.
- Jika JSON berupa daftar besar, pecah menurut item/grup logis dan indeks field penting agar dapat dicari. Ambil hanya potongan yang relevan dengan query.
- Jika perlu nilai persis yang tidak ada dalam ringkasan, backend mengambil potongan arsip asli berdasarkan path/field atau rentang item. Jangan menebak nilai yang tidak ikut diambil.
- Catat `payload_size_bytes`, `summary_status`, dan `truncated=false/true`. Jika ada batas penyimpanan atau kegagalan, beri status eksplisit; jangan memotong diam-diam.
- Redaksi kredensial, token, cookie, dan secret sebelum payload masuk ke prompt atau log. Akses arsip penuh tetap dibatasi ke pemilik slot dan admin yang berwenang.

## Skema data yang disarankan

Tahap pertama dapat memperluas `chat_history` yang ada dengan `conversation_id`, `turn_id`, `sequence_no`, `event_at`, `received_at`, dan metadata status/ukuran. Untuk hasil tool yang besar, pisahkan ke tabel payload:

```text
conversation_payloads
  id, owner_id, slot_number, conversation_id, turn_id, request_id
  tool_name, payload_kind, payload_json_or_pointer, summary
  byte_size, sha256, parse_status, created_at
```

Semua tabel diberi foreign key atau constraint yang menjaga lingkup akun/slot. Indeks utama:

```text
(owner_id, slot_number, received_at DESC)
(owner_id, slot_number, conversation_id, sequence_no)
(owner_id, slot_number, turn_id)
```

Jika PostgreSQL dan SQLite sama-sama didukung, migrasi serta implementasi store harus mempertahankan perilaku isolasi yang sama pada keduanya.

## Retensi, privasi, dan pemulihan

- Tetapkan masa retensi arsip sekurang-kurangnya satu tahun; buat konfigurasi eksplisit dan dokumentasikan apakah data yang lebih lama dihapus atau tetap disimpan.
- Ringkasan jangka panjang juga dihapus saat pengguna menghapus riwayat slot, agar fakta turunan tidak tertinggal.
- Sediakan ekspor dan hapus memori per slot, bukan hanya per akun.
- Backup database dan payload store harus mencakup data yang sama dan diuji pemulihannya.
- Terapkan enkripsi saat transit dan batasi akses pada pemilik akun/slot.

## Observabilitas

Log terstruktur untuk setiap tahap harus memuat `request_id`, `conversation_id`, `turn_id`, `owner_id`, `slot_number`, jumlah byte/token, sumber retrieval, jumlah hasil retrieval, dan status simpan. Jangan tulis isi percakapan, token, atau payload mentah ke log operasional.

Tambahkan metrik: turn diterima/tersimpan/gagal, keterlambatan persist, retrieval hit/miss, jumlah token konteks, ukuran arsip, payload yang dipindah ke penyimpanan eksternal, dan kegagalan akses lintas slot (harus nol).

## Tahapan implementasi

1. **Pemetaan dan backfill:** inventarisasi field historis, isi slot yang hilang secara aman, ukur payload terbesar, dan pastikan timestamp/source yang tersedia.
2. **Pencatatan otomatis:** tulis transcript user/assistant dan event tool pada setiap turn, dengan ID stabil dan idempotensi.
3. **Jaminan isolasi:** tambahkan filter `owner_id + slot_number` ke setiap pembacaan, retrieval semantik, ringkasan, ekspor, dan penghapusan.
4. **Context builder:** ambil jendela percakapan terbaru, ringkasan, dan arsip relevan berdasarkan anggaran token.
5. **Penanganan JSON besar:** ringkasan terstruktur, chunk/index, pointer payload penuh, redaksi rahasia, dan status ukuran/truncation.
6. **Retensi dan kontrol pengguna:** kebijakan satu tahun, ekspor, hapus per slot, dan pemeriksaan backup.
7. **Rollout bertahap:** aktifkan untuk satu slot internal, bandingkan kelengkapan transcript dan kebocoran lintas slot, lalu perluas dengan metrik dan rollback.

## Kriteria penerimaan

- Setiap turn yang selesai atau terputus memiliki transcript user dan status jawaban yang dapat ditelusuri dengan ID stabil.
- Pesan dalam jendela 3–5 detik yang sama masuk ke konteks sesuai urutan waktunya.
- Pertanyaan tentang percakapan lama dapat menemukan pesan berdasarkan waktu, kata kunci, atau makna hingga sekurang-kurangnya satu tahun.
- Tidak ada isi atau hasil retrieval dari slot lain dalam prompt, meskipun akun memiliki beberapa slot/perangkat.
- JSON besar dapat disimpan penuh, diringkas untuk konteks, dan ditelusuri kembali ke payload asli tanpa silent truncation.
- Penghapusan satu slot menghapus transcript, ringkasan, indeks, dan payload turunannya untuk slot tersebut.
- Log dapat menghubungkan satu jawaban dengan input, tool, retrieval, slot, dan waktu tanpa menampilkan konten sensitif.
