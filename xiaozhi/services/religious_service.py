"""
Religious and Spiritual Service for Xiaozhi:
Comprehensive, accurate, respectful, and reverent guides for:
- Islam, Christianity & Catholicism, Hinduism, Buddhism, Confucianism, and Judaism.
Covers prayers (doa), rituals & worship procedures (tata cara ibadah), and scriptures / chapters / verses.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.services.religious_service")

# ═════════════════════════════════════════════════════════════════════════════
# 1. DATABASE KITAB SUCI & AYAT-AYAT KANONIKAL
# ═════════════════════════════════════════════════════════════════════════════

SCRIPTURE_CANON_DATABASE = [
    # ── ISLAM ──────────────────────────────────────────────────────────────
    {
        "religion": "Islam",
        "scripture_name": "Al-Qur'an & Hadits",
        "items": [
            {
                "identifier": "Al-Fatihah (Surat ke-1)",
                "aliases": ["al fatihah", "alfatihah", "fatihah", "surat 1", "pembuka", "ummul quran"],
                "total_verses": 7,
                "classification": "Makkiyah",
                "meaning_name": "Pembukaan",
                "sample_verse": {
                    "verse": 1,
                    "arabic": "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
                    "transliteration": "Bismillāhir-raḥmānir-raḥīm",
                    "translation": "Dengan nama Allah Yang Maha Pengasih, Maha Penyayang."
                },
                "key_teachings": "Intisari seluruh ajaran Al-Qur'an mengenai tauhid, permohonan petunjuk jalan yang lurus (Sirathal Mustaqim), dan puji-pujian hanya kepada Allah SWT."
            },
            {
                "identifier": "Ayat Kursi (Al-Baqarah: 255)",
                "aliases": ["ayat kursi", "kursi", "al baqarah 255", "baqarah 255"],
                "surah": "Al-Baqarah (Surat ke-2)",
                "verse": 255,
                "arabic": "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَّهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ...",
                "transliteration": "Allāhu lā ilāha illā huwal-ḥayyul-qayyūm, lā ta'khużuhū sinatuw wa lā na'ūm...",
                "translation": "Allah, tidak ada tuhan selain Dia. Yang Mahahidup, Yang terus-menerus mengurus makhluk-Nya. Tidak mengantuk dan tidak tidur...",
                "key_teachings": "Ayat paling agung dalam Al-Qur'an tentang kekuasaan mutlak Allah atas langit dan bumi serta perlindungan paripurna."
            },
            {
                "identifier": "Surat Al-Ikhlas (Surat ke-112)",
                "aliases": ["al ikhlas", "ikhlas", "surat 112", "qul huwallahu ahad"],
                "total_verses": 4,
                "classification": "Makkiyah",
                "meaning_name": "Memurnikan Keesaan Allah",
                "arabic": "قُلْ هُوَ اللَّهُ أَحَدٌ ۝ اللَّهُ الصَّمَدُ ۝ لَمْ يَلِدْ وَلَمْ يُولَدْ ۝ وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ",
                "transliteration": "Qul huwallāhu aḥad. Allāhuṣ-ṣamad. Lam yalid wa lam yūlad. Wa lam yakul lahū kufuwan aḥad.",
                "translation": "Katakanlah: Dialah Allah, Yang Maha Esa. Allah tempat meminta segala sesuatu. Allah tidak beranak dan tidak pula diperanakkan. Dan tidak ada sesuatu yang setara dengan Dia.",
                "key_teachings": "Kemurnian tauhid Islam yang menolak segala bentuk sekutu atau antropomorfisme Tuhan."
            },
            {
                "identifier": "Surat Yasin (Surat ke-36)",
                "aliases": ["yasin", "ya sin", "surat 36", "jantung al-quran"],
                "total_verses": 83,
                "classification": "Makkiyah",
                "key_teachings": "Sering disebut sebagai jantung Al-Qur'an, menegaskan kerasulan Nabi Muhammad SAW, kepastian hari kebangkitan, dan tanda-tanda kebesaran ciptaan Allah di alam semesta."
            }
        ]
    },

    # ── KRISTEN & KATOLIK ──────────────────────────────────────────────────
    {
        "religion": "Kristen & Katolik",
        "scripture_name": "Alkitab (Holy Bible: Perjanjian Lama & Perjanjian Baru)",
        "items": [
            {
                "identifier": "Mazmur 23 (Tuhan adalah Gembalaku)",
                "aliases": ["mazmur 23", "psalm 23", "tuhan adalah gembalaku", "gembalaku yang baik"],
                "section": "Perjanjian Lama - Kitab Mazmur",
                "author": "Raja Daud",
                "text_content": (
                    "Tuhan adalah gembalaku, takkan kekurangan aku. Ia membaringkan aku di padang yang berumput hijau, "
                    "Ia membimbing aku ke air yang tenang; Ia menyegarkan jiwaku. Ia menuntun aku di jalan yang benar oleh karena nama-Nya. "
                    "Sekalipun aku berjalan dalam lembah kekelaman, aku tidak takut bahaya, sebab Engkau besertaku; gada-Mu dan tongkat-Mu, itulah yang menghibur aku."
                ),
                "key_teachings": "Ungkapan iman dan kepasrahan mendalam akan pemeliharaan, bimbingan, ketenangan jiwa, dan perlindungan Allah dalam setiap musim kehidupan."
            },
            {
                "identifier": "Yohanes 3:16",
                "aliases": ["yohanes 3:16", "john 3:16", "karena begitu besar kasih allah"],
                "section": "Perjanjian Baru - Injil Yohanes",
                "text_content": (
                    "Karena begitu besar kasih Allah akan dunia ini, sehingga Ia telah mengaruniakan Anak-Nya yang tunggal, "
                    "supaya setiap orang yang percaya kepada-Nya tidak binasa, melainkan beroleh hidup yang kekal."
                ),
                "key_teachings": "Ayat sentral kekristenan mengenai kasih karunia keselamatan penebusan dosa dan janji kehidupan kekal melalui Yesus Kristus."
            },
            {
                "identifier": "1 Korintus 13 (Kidung Kasih Agape)",
                "aliases": ["1 korintus 13", "korintus 13", "kidung kasih", "kasih itu sabar"],
                "section": "Perjanjian Baru - Surat Paulus",
                "text_content": (
                    "Kasih itu sabar; kasih itu murah hati; ia tidak cemburu. Ia tidak memegahkan diri dan tidak sombong. "
                    "Ia tidak melakukan yang tidak sopan dan tidak mencari keuntungan diri sendiri. Ia tidak pemarah dan tidak menyimpan kesalahan orang lain..."
                ),
                "key_teachings": "Hakikat kasih ilahi yang sejati (Agape) yang lebih tinggi dari segala karunia atau kepandaian manusia."
            }
        ]
    },

    # ── HINDU ──────────────────────────────────────────────────────────────
    {
        "religion": "Hindu",
        "scripture_name": "Catur Weda & Bhagavad Gita",
        "items": [
            {
                "identifier": "Bhagavad Gita Bab 2 Sloka 47 (Karma Yoga)",
                "aliases": ["bhagavad gita 2.47", "gita 2 47", "karma yoga", "karmanye vadhikaraste"],
                "section": "Bhagavad Gita - Adhyaya 2 (Sankhya Yoga)",
                "sanskrit": "कर्मण्येवाधिकारस्ते मा फलेषु कदाचन। मा कर्मफलहेतुर्भूर्मा ते सङ्गोऽस्त्वकर्मणि॥",
                "transliteration": "Karmany evādhikāras te mā phaleṣu kadācana, mā karma-phala-hetur bhūr mā te saṅgo 'stv akarmaṇi.",
                "translation": "Engkau hanya berhak atas pekerjaan/kewajibanmu, namun jangan sekali-kali mengharapkan hasil/pahalanya. Jangan jadikan pahala sebagai motifmu bertindak, dan jangan pula engkau terikat pada ketidakaktifan (kemalasan).",
                "key_teachings": "Filsafat luhur 'Niskama Karma' (Bekerja tulus tanpa pamrih sebagai persembahan suci kepada Ida Sang Hyang Widhi Wasa)."
            },
            {
                "identifier": "Gayatri Mantra (Rigweda III.62.10)",
                "aliases": ["gayatri mantra", "mantra gayatri", "om bhur bhuwah"],
                "section": "Rigweda Mandala 3",
                "sanskrit": "ॐ भूर्भुवः स्वः तत्सवितुर्वरेण्यं भर्गो देवस्य धीमहि धियो यो नः प्रचोदयात्॥",
                "transliteration": "Om bhūr bhuvaḥ svaḥ tat savitur vareṇyaṁ bhargo devasya dhīmahi dhiyo yo naḥ pracodayāt.",
                "translation": "Om, cahaya suci dari ketiga alam (bumi, langit, dan surga), kemuliaan Savitar (Sang Sumber Kehidupan) yang patut dipuja, kami memediasikan kecemerlangan Ilahi itu agar menerangi dan membimbing kecerdasan budi nurani kami.",
                "key_teachings": "Mantram pemujaan tertinggi untuk memohon pencerahan budi pekerti, kebijaksanaan, dan pembersihan batin."
            }
        ]
    },

    # ── BUDDHA ─────────────────────────────────────────────────────────────
    {
        "religion": "Buddha",
        "scripture_name": "Tipitaka & Dhammapada",
        "items": [
            {
                "identifier": "Dhammapada Bait 1 & 2 (Yamaka Vagga)",
                "aliases": ["dhammapada bait 1", "dhammapada 1", "manopubbangama", "yamaka vagga"],
                "section": "Khuddaka Nikaya - Dhammapada",
                "pali": "Mano-pubbaṅgamā dhammā, mano-seṭṭhā mano-mayā...",
                "translation": "Pikiran adalah pelopor dari segala sesuatu; pikiran adalah pemimpin; pikiran adalah pembentuk. Bila seseorang berbicara atau bertindak dengan pikiran jahat, penderitaan akan mengikutinya bagai roda pedati mengikuti jejak kaki lembu penariknya. Bila seseorang berbicara atau berbuat dengan pikiran murni, kebahagiaan akan menyertainya laksana bayang-bayang yang tak pernah meninggalkan dirinya.",
                "key_teachings": "Hukum Karma dan sentralitas pikiran: Kondisi hidup kita adalah cerminan dari kemurnian niat dan pikiran kita sendiri."
            },
            {
                "identifier": "Karaniya Metta Sutta (Khotbah Cinta Kasih)",
                "aliases": ["metta sutta", "karaniya metta sutta", "cinta kasih buddha", "sabbe satta"],
                "section": "Sutta Nipata & Khuddakapatha",
                "pali_phrase": "Sabbe sattā bhavantu sukhitattā.",
                "translation": "Semoga semua makhluk hidup berbahagia, bebas dari penderitaan, aman, dan tenteram.",
                "key_teachings": "Pancaran cinta kasih tanpa batas (Metta) kepada seluruh makhluk hidup di alam semesta tanpa diskriminasi, laksana kasih seorang ibu yang melindungi anak tunggalnya dengan nyawanya."
            }
        ]
    },

    # ── KONGHUCU ───────────────────────────────────────────────────────────
    {
        "religion": "Konghucu",
        "scripture_name": "Kitab Suci Si Shu (Yang Empat) & Wu Jing (Yang Lima)",
        "items": [
            {
                "identifier": "Sabda Suci / Lun Yu (Analects of Confucius)",
                "aliases": ["lun yu", "sabda suci", "analekta", "ajaran konghucu"],
                "key_teachings_and_quotes": [
                    "Ren (Cinta Kasih): 'Jangan lakukan kepada orang lain apa yang engkau sendiri tidak suka orang lain lakukan kepadamu' (Ji suo bu yu, wu shi yu ren - Golden Rule).",
                    "Li (Kesusilaan & Tata Krama): Menjaga keharmonisan dalam keluarga dan masyarakat melalui adab yang santun.",
                    "Xiao (Bakti): Menghormati, merawat, dan berbakti kepada orang tua dan leluhur sebagai akar dari segala kebajikan moral."
                ]
            },
            {
                "identifier": "Kitab Da Xue (Ajaran Besar)",
                "aliases": ["da xue", "ajaran besar", "ta hsueh"],
                "core_concept": "Membina diri sendiri terlebih dahulu, barulah merapikan keluarga, mengatur negara, dan mendamaikan dunia (Ge Wu, Zhi Zhi, Cheng Yi, Zheng Xin, Xiu Shen, Qi Jia, Zhi Guo, Ping Tian Xia)."
            }
        ]
    },

    # ── YAHUDI ─────────────────────────────────────────────────────────────
    {
        "religion": "Yahudi (Yudaisme)",
        "scripture_name": "Tanakh (Torah / Taurat, Nevi'im, Ketuvim)",
        "items": [
            {
                "identifier": "Shema Yisrael (Devarim / Ulangan 6:4)",
                "aliases": ["shema", "shema yisrael", "tanakh", "ulangan 6:4"],
                "section": "Torah - Sefer Devarim (Kitab Ulangan)",
                "hebrew": "שְׁמַע יִשְׂרָאֵל יְהוָה אֱלֹהֵינוּ יְהוָה אֶחָד",
                "transliteration": "Sh'ma Yisra'el, Adonai Eloheinu, Adonai Eḥad.",
                "translation": "Dengarlah, hai orang Israel: TUHAN adalah Allah kita, TUHAN itu Esa!",
                "key_teachings": "Pernyataan iman monoteisme mutlak Yudaisme yang diucapkan setiap pagi dan malam hari."
            }
        ]
    }
]

# ═════════════════════════════════════════════════════════════════════════════
# 2. DATABASE DOA HARIAN & PANDUAN TATA CARA IBADAH
# ═════════════════════════════════════════════════════════════════════════════

PRAYER_AND_WORSHIP_DATABASE = [
    # ── ISLAM ──────────────────────────────────────────────────────────────
    {
        "religion": "Islam",
        "category": "Doa Harian & Ibadah",
        "practices": [
            {
                "name": "Tata Cara Sholat Fardhu (5 Waktu)",
                "aliases": ["tata cara sholat", "panduan sholat", "sholat", "rukun sholat"],
                "tahapan": [
                    "1. Niat dan Berdiri Tegak menghadap Kiblat.",
                    "2. Takbiratul Ihram ('Allahu Akbar') sambil mengangkat tangan sejajar telinga/bahu.",
                    "3. Membaca Doa Iftitah (Sunnah).",
                    "4. Membaca Surat Al-Fatihah (Rukun wajib di setiap rakaat).",
                    "5. Membaca salah satu surat pendek Al-Qur'an (di rakaat 1 & 2).",
                    "6. Ruku' dengan thuma'ninah (membaca 'Subhana Rabbiyal 'Azimi wa bihamdih' 3x).",
                    "7. I'tidal (bangkit dari ruku', membaca 'Sami'allahu liman hamidah...').",
                    "8. Sujud pertama (membaca 'Subhana Rabbiyal A'la wa bihamdih' 3x).",
                    "9. Duduk di antara dua sujud ('Rabbighfirli warhamni...').",
                    "10. Sujud kedua.",
                    "11. Bangkit untuk rakaat berikutnya hingga rakaat terakhir.",
                    "12. Duduk Tasyahhud Akhir (Membaca Tasyahhud dan Shalawat Nabi).",
                    "13. Salam menoleh ke kanan dan ke kiri ('Assalamu'alaikum warahmatullah')."
                ]
            },
            {
                "name": "Tata Cara Berwudhu",
                "aliases": ["tata cara wudhu", "wudhu", "bersuci"],
                "tahapan": [
                    "1. Niat dalam hati dan membaca Basmalah.",
                    "2. Membasuh kedua telapak tangan hingga pergelangan.",
                    "3. Berkumur-kumur (Madhmadha) 3 kali.",
                    "4. Membasuh dan menghirup air ke hidung lalu mengeluarkannya (Istinsyaq) 3 kali.",
                    "5. Membasuh seluruh wajah secara merata 3 kali (Rukun Wajib).",
                    "6. Membasuh kedua tangan sampai siku dimulai dari kanan lalu kiri 3 kali (Rukun Wajib).",
                    "7. Mengusap sebagian/seluruh kepala dengan air 3 kali (Rukun Wajib).",
                    "8. Mengusap kedua telinga bagian luar dan dalam 3 kali.",
                    "9. Membasuh kedua kaki hingga mata kaki dimulai dari kaki kanan 3 kali (Rukun Wajib).",
                    "10. Tertib (berurutan) dan diakhiri dengan Doa setelah wudhu."
                ]
            },
            {
                "name": "Doa Kedua Orang Tua",
                "aliases": ["doa orang tua", "doa ibu bapak", "rabbighfirli waliwalidayya"],
                "arabic": "رَبِّ اغْفِرْ لِي وَلِوَالِدَيَّ وَارْحَمْهُمَا كَمَا رَبَّيَانِي صَغِيرًا",
                "transliteration": "Rabbighfir lī wa liwālidayya warḥamhumā kamā rabbayānī ṣaghīrā.",
                "translation": "Wahai Tuhanku, ampunilah aku dan kedua orang tuaku, dan sayangilah mereka berdua sebagaimana mereka telah mendidikku di waktu kecil."
            },
            {
                "name": "Doa Sapu Jagat (Kebaikan Dunia & Akhirat)",
                "aliases": ["doa sapu jagat", "rabbana atina", "kebaikan dunia akhirat"],
                "arabic": "رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً وَفِي الْآخِرَةِ حَسَنَةً وَقِنَا عَذَابَ النَّارِ",
                "transliteration": "Rabbanā ātinā fid-dunyā ḥasanataw wa fil-ākhirati ḥasanataw wa qinā 'ażāban-nār.",
                "translation": "Ya Tuhan kami, berilah kami kebaikan di dunia dan kebaikan di akhirat, dan lindungilah kami dari siksa api neraka."
            }
        ]
    },

    # ── KRISTEN & KATOLIK ──────────────────────────────────────────────────
    {
        "religion": "Kristen & Katolik",
        "category": "Doa Harian & Liturgi Ibadah",
        "practices": [
            {
                "name": "Doa Bapa Kami (The Lord's Prayer)",
                "aliases": ["doa bapa kami", "bapa kami", "our father", "doa tuhan yesus"],
                "text": (
                    "Bapa kami yang di sorga, Dikuduskanlah nama-Mu, datanglah Kerajaan-Mu, "
                    "jadilah kehendak-Mu di bumi seperti di sorga. "
                    "Berikanlah kami pada hari ini makanan kami yang secukupnya "
                    "dan ampunilah kami akan kesalahan kami, seperti kami juga mengampuni orang yang bersalah kepada kami; "
                    "dan janganlah membawa kami ke dalam pencobaan, tetapi lepaskanlah kami dari pada yang jahat. "
                    "[Karena Engkaulah yang empunya Kerajaan dan kuasa dan kemuliaan sampai selama-lamanya. Amin.]"
                )
            },
            {
                "name": "Doa Salam Maria (Tradisi Katolik)",
                "aliases": ["salam maria", "doa salam maria", "hail mary", "ave maria"],
                "text": (
                    "Salam Maria, penuh rahmat, Tuhan sertamu; "
                    "terpujilah engkau di antara wanita, dan terpujilah buah tubuhmu, Yesus. "
                    "Santa Maria, bunda Allah, doakanlah kami yang berdosa ini, sekarang dan waktu kami mati. Amin."
                )
            },
            {
                "name": "Tata Ibadah Kebaktian Kristen Protestan",
                "aliases": ["tata ibadah kebaktian", "kebaktian minggu", "liturgi gereja"],
                "tahapan": [
                    "1. Panggilan Beribadah & Saat Teduh.",
                    "2. Votum dan Salam (Penyerahan ibadah dalam nama Allah Tritunggal).",
                    "3. Nyanyian Pujian Jemaat.",
                    "4. Pengakuan Dosa & Berita Anugerah Pengampunan.",
                    "5. Pelayanan Firman Tuhan (Doa Epiklese, Pembacaan Alkitab, dan Khotbah).",
                    "6. Pengakuan Iman Rasuli (Credo).",
                    "7. Persembahan Syukur & Doa Syafaat.",
                    "8. Pengutusan dan Berkat Damai Sejahtera Tuhan."
                ]
            },
            {
                "name": "Tata Perayaan Ekaristi (Misa Kudus Katolik)",
                "aliases": ["misa", "ekaristi", "misa kudus", "liturgi ekaristi"],
                "tahapan": [
                    "1. Ritus Pembuka: Perarakan, Tanda Salib, Tobat (Kyrie), dan Madah Kemuliaan (Gloria).",
                    "2. Liturgi Sabda: Bacaan Pertama, Mazmur Tanggapan, Bacaan Kedua, Bait Pengantar Injil, Homili, dan Syahadat.",
                    "3. Liturgi Ekaristi: Persiapan Persembahan, Doa Syukur Agung (Prefasi, Kudus/Sanctus, Konsekrasi), dan Komuni Kudus.",
                    "4. Ritus Penutup: Pengumuman, Berkat Imam, dan Pengutusan umat melayani sesama."
                ]
            }
        ]
    },

    # ── HINDU ──────────────────────────────────────────────────────────────
    {
        "religion": "Hindu",
        "category": "Mantram & Tata Cara Sembahyang",
        "practices": [
            {
                "name": "Puja Tri Sandhya (Dilakukan 3 Kali Sehari)",
                "aliases": ["tri sandhya", "puja tri sandhya", "trisandya", "sembahyang hindu"],
                "penjelasan": "Dilakukan pada pergantian waktu: Pagi hari (Brahma Muhurta/Fajar), Siang hari (Tepat jam 12), dan Senja hari (Sandhya Kala).",
                "bait_utama": [
                    "Bait 1 (Gayatri): 'Om Bhur Bhuwah Swah, Tat Sawitur Warenyam, Bhargo Dewasya Dhimahi, Dhiyo Yo Nah Pracodayat.'",
                    "Bait 2 (Narayan): 'Om Narayana Ewedam Sarwam, Yad Bhutam Yac Ca Bhawyam...'",
                    "Bait 3: 'Om Twam Siwah Twam Mahadewah, Iswarah Parameswarah...'",
                    "Bait 6 (Ksamaprarthana/Pengampunan): 'Om Ksamaswa Mam Mahadewa, Sarwaprani Hitankara... Om Santih Santih Santih Om.'"
                ]
            },
            {
                "name": "Kramaning Sembah (Panca Sembah)",
                "aliases": ["panca sembah", "kramaning sembah", "sembahyang pura"],
                "tahapan": [
                    "1. Sembah Puyung (Tangan kosong di ubun-ubun): Ditujukan kepada Sang Hyang Atma / Siwa Raditya.",
                    "2. Sembah dengan Bunga Putih: Ditujukan kepada Sang Hyang Surya / Sang Hyang Siwa Raditya sebagai saksi agung.",
                    "3. Sembah dengan Kawangen/Bunga Campur: Ditujukan kepada Ida Sang Hyang Widhi Wasa dalam manifestasi Ista Dewata di Pura tersebut.",
                    "4. Sembah dengan Kawangen/Bunga Campur: Ditujukan kepada Samodaya Dewata (Dewata Samodaya) memohon anugerah dan kerahayuan.",
                    "5. Sembah Puyung Penutup: Menyampaikan rasa syukur atas anugerah yang telah dilimpahkan, diakhiri dengan nunas Tirtha dan Bija."
                ]
            }
        ]
    },

    # ── BUDDHA ─────────────────────────────────────────────────────────────
    {
        "religion": "Buddha",
        "category": "Paritta & Kebaktian Puja Bakti",
        "practices": [
            {
                "name": "Kebaktian Puja Bakti & Penghormatan Triratna",
                "aliases": ["kebaktian buddha", "puja bakti", "sembahyang vihara", "paritta"],
                "tahapan": [
                    "1. Penyalaan Lilin dan Dupa (Dupa lambang keharuman Dhamma, lilin lambang penerang kegelapan batin).",
                    "2. Namaskara (Bersujud 3 kali dengan 5 titik menyentuh lantai menghormat Buddha, Dhamma, dan Sangha).",
                    "3. Pembacaan Namakkara Gatha & Vandana ('Namo Tassa Bhagavato Arahato Sammasambuddhassa' 3x).",
                    "4. Tisarana (Pernyataan berlindung pada Triratna: Buddha, Dhamma, Sangha).",
                    "5. Pancasila Buddhis (Tekad menjaga 5 sila moralitas).",
                    "6. Meditasi Samatha Bhavana (Mengamati keluar masuknya nafas / Anapanasati) untuk menenangkan batin.",
                    "7. Pembacaan Dhammasavanna (Mendengarkan uraian Dhamma).",
                    "8. Pattidana (Pelimpahan jasa kebajikan kepada sanak keluarga dan semua makhluk)."
                ]
            },
            {
                "name": "Mantra Maha Karuna Dharani (Welang Asih Agung)",
                "aliases": ["maha karuna dharani", "ta pei cou", "mantra welas asih", "dewi kwan im"],
                "penjelasan": "Mantra suci Bodhisattva Avalokitesvara (Guan Yin) untuk memancarkan welas asih agung tanpa syarat dan membersihkan karma buruk."
            }
        ]
    },

    # ── KONGHUCU ───────────────────────────────────────────────────────────
    {
        "religion": "Konghucu",
        "category": "Doa & Tata Ibadah Kebajikan",
        "practices": [
            {
                "name": "Tata Cara Sembahyang kepada Huang Tian Shang Di (Tuhan Yang Maha Esa)",
                "aliases": ["sembahyang tian", "sembahyang dupa", "ibadah konghucu", "litang"],
                "tahapan": [
                    "1. Cuci tangan dan membersihkan diri (Zheng Xin - Menjernihkan Hati).",
                    "2. Menyalakan Dupa (Hio) dengan sikap khidmat memegang dupa dengan kedua tangan di depan dada (Sikap Ba De / Delapan Kebajikan).",
                    "3. Menghadap ke Meja Altar Tian (Langit), membungkuk memberi hormat (Zuo Yi).",
                    "4. Berlutut (Gui) dengan tulus menyampaikan doa syukur dan permohonan bimbingan moral hidup.",
                    "5. Menancapkan dupa di Hio-louw (tempat dupa) dengan tangan kanan ditopang tangan kiri.",
                    "6. Dilanjutkan dengan sembahyang penghormatan kepada Nabi Agung Kongzi dan para leluhur."
                ]
            },
            {
                "name": "Salam Keagamaan Konghucu",
                "aliases": ["salam konghucu", "wei de dong tian", "xian you yi de"],
                "penjelasan": "Umat Konghucu saling menyapa dengan merangkapkan kedua tangan di depan dada (Bao Quan) sambil mengucapkan: 'Wei De Dong Tian' (Hanya Kebajikan Yang Berkenan Pada Tian) dan dijawab 'Xian You Yi De' (Peganglah Satu Kebajikan)."
            }
        ]
    },

    # ── YAHUDI ─────────────────────────────────────────────────────────────
    {
        "religion": "Yahudi (Yudaisme)",
        "category": "Doa Harian & Shabbat",
        "practices": [
            {
                "name": "Penyambutan Shabbat (Kiddush)",
                "aliases": ["shabbat", "sabat yahudi", "kiddush", "doa shabbat"],
                "tahapan": [
                    "1. Menyalakan lilin Shabbat 18 menit sebelum matahari terbenam hari Jumat oleh wanita/ibu rumah tangga.",
                    "2. Membaca doa berkat penyalaan lilin: 'Barukh atah Adonai Eloheinu, Melekh ha-olam...'",
                    "3. Kiddush: Doa berkat atas cangkir anggur yang melambangkan sukacita pemeliharaan Allah.",
                    "4. Mencuci tangan secara ritual (Netilat Yadayim).",
                    "5. Doa berkat atas Roti Challah (Hamotzi): 'Barukh atah Adonai... ha-motzi lekhem min ha-aretz' (Yang menumbuhkan roti dari bumi).",
                    "6. Santap malam keluarga penuh kehangatan, kidung pujian Shalom Aleichem, dan berkat bagi anak-anak."
                ]
            },
            {
                "name": "Doa Modeh Ani (Doa Bangun Pagi)",
                "aliases": ["modeh ani", "doa bangun tidur yahudi"],
                "hebrew": "מוֹדֶה אֲנִי לְפָנֶיךָ מֶלֶךְ חַי וְקַיָּם שֶׁהֶחֱזַרְתָּ בִּי נִשְׁמָתִי בְּחֶמְלָה, רַבָּה אֱמוּנָתֶךָ",
                "transliteration": "Modeh ani lifanekha, melekh ḥai v'kayam, sheheḥezarta bi nishmati b'ḥemlah, rabah emunatekha.",
                "translation": "Aku bersyukur kepada-Mu, ya Raja Yang Hidup dan Kekal, karena Engkau telah mengembalikan jiwaku ke dalam diriku dengan penuh belas kasih; sungguh besar kesetiaan-Mu."
            }
        ]
    }
]


def lookup_scripture_and_verse_handler(religion: str = "", book_or_surah: str = "", verse_or_chapter: str = "", query: str = "") -> Dict[str, Any]:
    """
    Search and retrieve holy scriptures, chapters, surahs, and verses across religions.
    """
    search_term = (query or book_or_surah).lower().strip()
    target_rel = religion.lower().strip()

    matched_results = []

    for group in SCRIPTURE_CANON_DATABASE:
        group_rel = group["religion"].lower()
        if target_rel and target_rel not in group_rel and group_rel not in target_rel:
            continue

        for item in group["items"]:
            # Check aliases or identifier
            aliases = item.get("aliases", [])
            identifier = item.get("identifier", "").lower()
            if search_term:
                if any(search_term in a for a in aliases) or any(a in search_term for a in aliases) or search_term in identifier:
                    matched_results.append({
                        "agama": group["religion"],
                        "nama_kitab": group["scripture_name"],
                        "detail": item
                    })
            else:
                matched_results.append({
                    "agama": group["religion"],
                    "nama_kitab": group["scripture_name"],
                    "detail": item
                })

    if matched_results:
        return {
            "success": True,
            "agama": religion or "Lintas Agama",
            "pencarian": query or book_or_surah,
            "ayat_pasal": verse_or_chapter,
            "results": matched_results[:3],
            "instruksi_xiaozhi": (
                "Sampaikan ayat suci atau kutipan kitab tersebut dengan penuh kesantunan, takzim, dan keagungan. "
                "Bacakan teks lafal / transliterasi aslinya, terjemahan resminya dalam bahasa Indonesia, "
                "serta jelaskan makna hikmah spiritual atau asbabun nuzul/konteks ajaran moralnya secara menyejukkan batin."
            )
        }

    return {
        "success": True,
        "agama": religion or "Umum / Lintas Agama",
        "pencarian": query or book_or_surah,
        "ayat_pasal": verse_or_chapter,
        "panduan_penyusunan": {
            "1_identitas_ayat": f"Sebutkan kitab suci, nama surat/pasal ({book_or_surah}), dan nomor ayat ({verse_or_chapter}).",
            "2_teks_dan_lafal": "Sajikan lafal teks asli / transliterasi fonetik yang benar.",
            "3_terjemahan_resmi": "Sajikan terjemahan resmi bahasa Indonesia yang diakui lembaga keagamaan terkait.",
            "4_makna_hikmah": "Uraikan hikmah, pesan moral, atau konteks spiritual bagi kehidupan manusia secara damai dan toleran."
        },
        "instruksi_xiaozhi": (
            f"Bacakan ayat atau bagian kitab suci '{book_or_surah} {verse_or_chapter}' untuk agama '{religion or 'terkait'}'. "
            "Gunakan rujukan teologis resmi yang valid, lafal yang santun, dan jangan mengubah substansi makna aslinya."
        )
    }


def get_prayer_and_worship_guide_handler(religion: str = "", ritual_or_prayer_name: str = "", occasion: str = "") -> Dict[str, Any]:
    """
    Provide prayer recitations and step-by-step worship/ritual guides for any religion.
    """
    search_term = ritual_or_prayer_name.lower().strip()
    target_rel = religion.lower().strip()

    matched_guides = []

    for group in PRAYER_AND_WORSHIP_DATABASE:
        group_rel = group["religion"].lower()
        if target_rel and target_rel not in group_rel and group_rel not in target_rel:
            continue

        for p in group["practices"]:
            aliases = p.get("aliases", [])
            name = p.get("name", "").lower()
            if search_term:
                if any(search_term in a for a in aliases) or any(a in search_term for a in aliases) or search_term in name:
                    matched_guides.append({
                        "agama": group["religion"],
                        "kategori": group["category"],
                        "panduan": p
                    })
            else:
                matched_guides.append({
                    "agama": group["religion"],
                    "kategori": group["category"],
                    "panduan": p
                })

    if matched_guides:
        return {
            "success": True,
            "agama": religion or "Lintas Agama",
            "perihal": ritual_or_prayer_name,
            "kesempatan": occasion,
            "results": matched_guides[:2],
            "instruksi_xiaozhi": (
                "Bimbinglah pengguna dengan bahasa yang sangat santun, tenang, dan runtut. "
                "Bacakan lafal doa beserta artinya, atau jelaskan urutan tata cara ibadah langkah-demi-langkah "
                "agar pengguna dapat beribadah atau berdoa dengan khusyuk dan benar."
            )
        }

    return {
        "success": True,
        "agama": religion or "Umum / Lintas Agama",
        "perihal": ritual_or_prayer_name,
        "kesempatan": occasion,
        "panduan_sistematika": {
            "1_tujuan_dan_niat": "Awali dengan niat tulus, kebersihan lahir-batin, dan suasana hening/khusyuk.",
            "2_bacaan_doa": "Sajikan bacaan doa (teks asli/lafal dan terjemahan bahasa Indonesia).",
            "3_urutan_tata_cara": "Jelaskan langkah ibadah secara berurutan dari awal sampai penutup.",
            "4_pesan_kedamaian": "Tutup dengan doa restu, kedamaian, dan harapan berkah Ilahi."
        },
        "instruksi_xiaozhi": (
            f"Sajikan bacaan doa atau tata cara ibadah '{ritual_or_prayer_name}' dalam tradisi agama '{religion or 'terkait'}'. "
            "Pastikan urutannya benar sesuai rukun/liturgi resmi masing-masing agama dan sampaikan dengan nada menenangkan."
        )
    }
