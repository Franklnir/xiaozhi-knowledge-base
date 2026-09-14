"""Study service for Xiaozhi: Problem solver, concept explainer, quiz, formulas, and academic English helper."""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.services.study_service")

# Database Kamus Rumus Ilmiah (Fisika, Matematika, Kimia, Ekonomi)
FORMULA_DATABASE = [
    {
        "keywords": ["lorentz", "gaya lorentz", "magnetik"],
        "name": "Gaya Lorentz",
        "subject": "Fisika (Elektromagnetisme)",
        "formula": "F = B · I · L · sin(θ)  atau  F = q · v · B · sin(θ)",
        "variables": {
            "F": "Gaya Lorentz (Newton, N)",
            "B": "Kuat medan magnet (Tesla, T)",
            "I": "Kuat arus listrik (Ampere, A)",
            "L": "Panjang kawat penghantar (meter, m)",
            "q": "Muatan listrik (Coulomb, C)",
            "v": "Kecepatan muatan (m/s)",
            "θ": "Sudut antara arah arus/kecepatan dengan medan magnet (derajat)",
        },
        "description": "Gaya yang timbul akibat adanya muatan listrik bergerak atau kawat berarus di dalam medan magnet.",
        "contoh": "Kawat 2 m berarus 5 A tegak lurus medan magnet 0.4 T mengalami gaya F = 0.4 * 5 * 2 * sin(90°) = 4 N.",
    },
    {
        "keywords": ["newton 2", "hukum newton", "gaya", "percepatan"],
        "name": "Hukum II Newton",
        "subject": "Fisika (Dinamika)",
        "formula": "ΣF = m · a",
        "variables": {
            "ΣF": "Resultan gaya (Newton, N)",
            "m": "Massa benda (kilogram, kg)",
            "a": "Percepatan benda (m/s²)",
        },
        "description": "Percepatan yang dialami suatu benda berbanding lurus dengan resultan gaya dan berbanding terbalik dengan massanya.",
        "contoh": "Benda bermassa 5 kg ditarik gaya 20 N menghasilkan percepatan a = F / m = 20 / 5 = 4 m/s².",
    },
    {
        "keywords": ["gerak parabola", "tinggi maksimum", "jarak maksimum", "kinematika"],
        "name": "Gerak Parabola / Kinematika",
        "subject": "Fisika (Mekanika)",
        "formula": "h_max = (v0² · sin²(θ)) / (2g)  dan  x_max = (v0² · sin(2θ)) / g",
        "variables": {
            "v0": "Kecepatan awal (m/s)",
            "θ": "Sudut elevasi penembakan (derajat)",
            "g": "Percepatan gravitasi (~9.8 atau 10 m/s²)",
            "h_max": "Ketinggian maksimum (meter, m)",
            "x_max": "Jarak jangkauan terjauh (meter, m)",
        },
        "description": "Perpaduan gerak lurus beraturan (GLB) pada sumbu X dan gerak lurus berubah beraturan (GLBB) pada sumbu Y.",
        "contoh": "Bola ditembakkan v0=20 m/s, θ=30°, g=10 m/s². Tinggi maks h_max = (20² * sin²(30°)) / (2 * 10) = (400 * 0.25) / 20 = 5 meter.",
    },
    {
        "keywords": ["energi kinetik", "energi potensial", "energi mekanik"],
        "name": "Energi Mekanik, Kinetik & Potensial",
        "subject": "Fisika (Energi)",
        "formula": "Ek = 1/2 · m · v²  |  Ep = m · g · h  |  Em = Ek + Ep",
        "variables": {
            "Ek": "Energi kinetik (Joule, J)",
            "Ep": "Energi potensial gravitasi (Joule, J)",
            "m": "Massa (kg)",
            "v": "Kecepatan (m/s)",
            "h": "Ketinggian dari acuan (meter)",
            "g": "Gravitasi (m/s²)",
        },
        "description": "Hukum Kekekalan Energi Mekanik menyatakan Em awal = Em akhir jika tidak ada gaya luar/gesek.",
        "contoh": "Benda 2 kg bergerak dengan kecepatan 6 m/s memiliki Ek = 0.5 * 2 * 6² = 36 Joule.",
    },
    {
        "keywords": ["hukum ohm", "tegangan", "hambatan", "daya listrik"],
        "name": "Hukum Ohm & Daya Listrik",
        "subject": "Fisika (Listrik Dinamis)",
        "formula": "V = I · R  dan  P = V · I = I² · R = V² / R",
        "variables": {
            "V": "Tegangan listrik (Volt, V)",
            "I": "Kuat arus listrik (Ampere, A)",
            "R": "Hambatan listrik (Ohm, Ω)",
            "P": "Daya listrik (Watt, W)",
        },
        "description": "Arus listrik yang mengalir sebanding dengan beda potensial dan berbanding terbalik dengan hambatan kawat penghantar.",
        "contoh": "Resistor 10 Ω diberi tegangan 12 V, maka arus I = 12 / 10 = 1.2 A dan daya P = 12 * 1.2 = 14.4 Watt.",
    },
    {
        "keywords": ["kuadrat", "rumus abc", "akar kuadrat", "determinan", "diskriminan"],
        "name": "Rumus Kuadrat (Rumus ABC) & Diskriminan",
        "subject": "Matematika (Aljabar)",
        "formula": "x1,2 = (-b ± √(b² - 4ac)) / (2a)  dan  D = b² - 4ac",
        "variables": {
            "a, b, c": "Koefisien persamaan kuadrat a·x² + b·x + c = 0",
            "D": "Diskriminan: D > 0 (2 akar real beda), D = 0 (kembar), D < 0 (imajiner)",
            "x1, x2": "Akar-akar persamaan kuadrat",
        },
        "description": "Metode penyelesaian pasti untuk mencari nilai x pembuat nol dari fungsi/persamaan kuadrat.",
        "contoh": "Untuk x² - 5x + 6 = 0: a=1, b=-5, c=6. x1,2 = (5 ± √(25 - 24)) / 2 = (5 ± 1)/2 -> x1=3, x2=2.",
    },
    {
        "keywords": ["integral parsial", "kalkulus", "integral"],
        "name": "Integral Parsial",
        "subject": "Matematika (Kalkulus)",
        "formula": "∫ u dv = u · v - ∫ v du",
        "variables": {
            "u": "Fungsi yang mudah diturunkan (pilih berdasarkan prioritas: LIATE - Logaritmik, Invers trigonometri, Aljabar, Trigonometri, Eksponensial)",
            "dv": "Fungsi yang mudah diintegralkan",
            "du": "Turunan dari u (du/dx · dx)",
            "v": "Integral dari dv",
        },
        "description": "Teknik pengintegralan hasil kali dua fungsi yang tidak dapat diselesaikan dengan substitusi langsung.",
        "contoh": "∫ x·e^x dx: Misal u = x -> du = dx; dv = e^x dx -> v = e^x. Maka ∫ x·e^x dx = x·e^x - ∫ e^x dx = x·e^x - e^x + C.",
    },
    {
        "keywords": ["elastisitas", "elastisitas permintaan", "ekonomi"],
        "name": "Elastisitas Permintaan (Price Elasticity of Demand)",
        "subject": "Ekonomi (Mikroekonomi)",
        "formula": "Ed = (ΔQ / ΔP) · (P1 / Q1)  atau  Ed = (%ΔQ) / (%ΔP)",
        "variables": {
            "Ed": "Koefisien elastisitas permintaan (|Ed| > 1 elastis, |Ed| < 1 inelastis, |Ed| = 1 uniter)",
            "ΔQ": "Perubahan jumlah barang yang diminta (Q2 - Q1)",
            "ΔP": "Perubahan harga barang (P2 - P1)",
            "P1, Q1": "Harga awal dan kuantitas awal",
        },
        "description": "Derajat kepekaan perubahan jumlah barang yang diminta konsumen akibat perubahan harga barang tersebut.",
        "contoh": "Harga naik dari Rp 10.000 ke Rp 12.000 (naik 20%), permintaan turun dari 100 ke 80 unit (turun 20%). Ed = -20% / 20% = -1 (Uniter).",
    },
    {
        "keywords": ["stoikiometri", "mol", "molaritas", "avogadro", "kimia"],
        "name": "Konsep Mol & Molaritas",
        "subject": "Kimia Dasar (Stoikiometri)",
        "formula": "n = massa / Mr  |  M = n / V  |  Jumlah Partikel = n · 6.022×10²³",
        "variables": {
            "n": "Jumlah zat (mol)",
            "massa": "Massa zat dalam gram (g)",
            "Mr": "Massa molekul relatif (g/mol)",
            "M": "Konsentrasi molaritas (Molar, M atau mol/L)",
            "V": "Volume larutan dalam Liter (L)",
        },
        "description": "Hubungan kuantitatif antara massa, partikel zat, dan volume larutan dalam reaksi kimia.",
        "contoh": "18 gram H2O (Mr=18). Jumlah mol n = 18 / 18 = 1 mol H2O.",
    }
]

# Kamus Vocabulary Akademik (Bahasa Inggris Sehari-hari -> Istilah Akademis/Formal)
ACADEMIC_VOCAB_MAP = {
    "big": ["substantial", "significant", "considerable", "extensive", "prominent"],
    "small": ["marginal", "negligible", "minimal", "minute"],
    "good": ["favorable", "advantageous", "beneficial", "optimal", "exemplary"],
    "bad": ["adverse", "detrimental", "unfavorable", "suboptimal"],
    "show": ["demonstrate", "illustrate", "indicate", "exhibit", "reveal", "delineate"],
    "make": ["generate", "synthesize", "construct", "formulate", "establish"],
    "look at": ["examine", "investigate", "analyze", "scrutinize", "evaluate"],
    "get": ["obtain", "acquire", "derive", "attain"],
    "give": ["provide", "yield", "contribute", "furnish", "allocate"],
    "important": ["crucial", "pivotal", "paramount", "imperative", "fundamental"],
    "a lot of": ["a multitude of", "numerous", "substantial amount of", "an abundance of"],
    "find out": ["ascertain", "determine", "discern", "uncover"],
    "think": ["postulate", "hypothesize", "contemplate", "conceptualize"],
    "change": ["modify", "alter", "transform", "fluctuate", "adjust"],
    "help": ["facilitate", "assist", "expedite", "reinforce"],
    "start": ["initiate", "commence", "embark upon"],
    "stop": ["terminate", "cease", "halt"],
    "problem": ["impediment", "dilemma", "quandary", "complication"],
    "answer": ["resolution", "countermeasure", "explanation"],
}


def solve_study_problem_handler(problem_statement: str, subject: str = "") -> Dict[str, Any]:
    """
    Format and structure a step-by-step educational problem solver response.
    Ensures that AI does not simply blur out the final answer, but provides
    a structured pedagogical breakdown.
    """
    cleaned = problem_statement.strip()
    if not cleaned:
        return {"success": False, "message": "Soal tidak boleh kosong."}

    # Detect subject if empty
    detected_subject = subject
    lower_prob = cleaned.lower()
    if not detected_subject:
        if any(w in lower_prob for w in ["integral", "turunan", "matriks", "vektor", "limit", "sin", "cos", "tan", "aljabar", "persamaan", "kuadrat"]):
            detected_subject = "Matematika"
        elif any(w in lower_prob for w in ["kecepatan", "percepatan", "gaya", "energi", "tegangan", "arus", "massa", "gravitasi", "suhu", "panas", "frekuensi"]):
            detected_subject = "Fisika"
        elif any(w in lower_prob for w in ["mol", "reaksi", "larutan", "asam", "basa", "senyawa", "unsur", "elektron", "ikatan"]):
            detected_subject = "Kimia"
        elif any(w in lower_prob for w in ["inflasi", "permintaan", "penawaran", "elastisitas", "biaya", "pendapatan", "laba"]):
            detected_subject = "Ekonomi"
        elif any(w in lower_prob for w in ["algoritma", "fungsi", "loop", "array", "python", "oop", "database", "query"]):
            detected_subject = "Informatika / Pemrograman"
        else:
            detected_subject = "Sains & Analitika"

    # Match potential formula hints from local DB
    formula_hints = []
    for f in FORMULA_DATABASE:
        if any(k in lower_prob for k in f["keywords"]):
            formula_hints.append({
                "nama": f["name"],
                "rumus": f["formula"],
                "variabel": f["variables"]
            })

    return {
        "success": True,
        "subject": detected_subject,
        "problem_statement": cleaned,
        "matched_formula_hints": formula_hints[:2],
        "framework_pembahasan": {
            "1_identifikasi": "Tuliskan apa saja besaran/data yang DIKETAHUI beserta satuannya dan apa yang DITANYAKAN.",
            "2_rumus_konsep": "Sebutkan rumus atau teori ilmiah yang digunakan.",
            "3_langkah_pengerjaan": "Tuliskan langkah kalkulasi/penalaran bertahap (step-by-step) secara runtut dan jelas.",
            "4_jawaban_akhir": "Tuliskan kesimpulan hasil akhir dengan satuan (SI) yang tepat.",
            "5_tips_jebakan": "Berikan 1 tips penting agar siswa tidak salah konsep atau keliru menghitung di soal serupa."
        },
        "instruksi_xiaozhi": (
            "JAWABLAH SECARA EDUKATIF DAN SISTEMATIS menggunakan kerangka 5 langkah di atas. "
            "Gunakan bahasa Indonesia yang jelas, ramah, dan mendidik layaknya seorang dosen atau tutor terbaik. "
            "Jika ada perhitungan numerik, periksa kembali perkalian dan pembagian agar akurat 100%."
        )
    }


def explain_concept_handler(concept: str, subject: str = "") -> Dict[str, Any]:
    """
    Provide a dual-level explanation:
    1. Formal academic definition (ideal for exams/papers).
    2. Real-world analogy (ELI5 - Explain Like I'm 5).
    3. Practical real-world example.
    """
    cleaned = concept.strip()
    if not cleaned:
        return {"success": False, "message": "Konsep yang ingin dijelaskan tidak boleh kosong."}

    return {
        "success": True,
        "concept": cleaned,
        "subject": subject or "Akademik / Pengetahuan",
        "dual_level_guidance": {
            "level_1_akademik_resmi": "Definisi formal, istilah teknis yang tepat, dan latar belakang teori yang valid untuk tugas/skripsi/ujian.",
            "level_2_analogi_eli5": "Analogi visual dunia nyata sehari-hari yang sangat mudah dipahami bahkan oleh orang awam.",
            "level_3_contoh_nyata": "Studi kasus konkret bagaimana konsep ini bekerja di industri, alam, atau kehidupan nyata."
        },
        "instruksi_xiaozhi": (
            f"Jelaskan konsep '{cleaned}' secara runtut: "
            "1) Berikan definisi formal akademisnya. "
            "2) Jelaskan ulang memakai analogi sederhana (ELI5). "
            "3) Sebutkan 1 contoh nyata penerapannya."
        )
    }


def quiz_me_handler(topic: str, action: str = "get_question", user_answer: str = "", question_id: str = "") -> Dict[str, Any]:
    """
    Interactive study quiz partner.
    Generates tailored questions on a topic or evaluates a student's answer.
    """
    topic_clean = topic.strip()
    if not topic_clean:
        topic_clean = "Pengetahuan Umum / Sains"

    if action == "check_answer":
        return {
            "success": True,
            "mode": "evaluasi_jawaban",
            "topic": topic_clean,
            "user_answer": user_answer,
            "evaluasi_guide": {
                "status": "Tentukan apakah jawaban benar, mendekati, atau keliru.",
                "apresiasi": "Berikan kalimat pujian/semangat ramah.",
                "pembahasan": "Jelaskan fakta atau solusi yang benar dan mengapa demikian.",
                "tindak_lanjut": "Tanyakan apakah ingin lanjut ke soal berikutnya atau ingin penjelasan lebih dalam."
            },
            "instruksi_xiaozhi": f"Evaluasi jawaban pengguna '{user_answer}' untuk topik '{topic_clean}'. Nilai dengan adil, edukatif, dan beri pembahasan ringkas yang jelas."
        }
    else:
        return {
            "success": True,
            "mode": "buat_soal",
            "topic": topic_clean,
            "panduan_soal": {
                "format": "Berikan 1 pertanyaan kuis interaktif yang jelas (bisa pilihan ganda A/B/C/D atau uraian singkat).",
                "tingkat_kesulitan": "Menengah (menguji konsep dasar dan pemahaman logika).",
                "instruksi_ke_user": "Minta pengguna menjawab secara langsung melalui suara."
            },
            "instruksi_xiaozhi": f"Buatkan 1 soal kuis menarik tentang '{topic_clean}'. Sampaikan soalnya ke pengguna dan katakan 'Coba tebak atau sebutkan jawabannya!'."
        }


def lookup_formula_handler(topic_or_keyword: str) -> Dict[str, Any]:
    """
    Instant formula and SI unit reference lookup.
    """
    query = topic_or_keyword.lower().strip()
    matched = []

    for item in FORMULA_DATABASE:
        if any(k in query for k in item["keywords"]) or query in item["name"].lower() or query in item["subject"].lower():
            matched.append(item)

    if matched:
        return {
            "success": True,
            "query": topic_or_keyword,
            "found_in_database": True,
            "results": matched,
            "instruksi_xiaozhi": "Bacakan rumus utama, arti lambang variabelnya, serta satuan SI-nya secara ringkas dan teratur."
        }

    return {
        "success": True,
        "query": topic_or_keyword,
        "found_in_database": False,
        "instruksi_xiaozhi": (
            f"Sajikan rumus lengkap untuk '{topic_or_keyword}' dengan format: "
            "1) Rumus Utama, 2) Keterangan Lambang & Satuan Standar Internasional (SI), "
            "3) Kapan rumus ini digunakan, dan 4) Satu contoh perhitungan singkat."
        )
    }


def academic_english_helper_handler(text: str, mode: str = "all") -> Dict[str, Any]:
    """
    Academic English proofreader and vocabulary enhancer.
    Identifies conversational words and provides elevated academic alternatives.
    """
    cleaned = text.strip()
    if not cleaned:
        return {"success": False, "message": "Teks bahasa Inggris tidak boleh kosong."}

    words = re.findall(r'\b\w+\b', cleaned.lower())
    suggested_upgrades = {}

    for w in words:
        if w in ACADEMIC_VOCAB_MAP:
            suggested_upgrades[w] = ACADEMIC_VOCAB_MAP[w]

    return {
        "success": True,
        "original_text": cleaned,
        "mode": mode,
        "suggested_academic_replacements": suggested_upgrades,
        "panduan_koreksi": {
            "1_analisis_grammar": "Periksa Subject-Verb Agreement, Tenses, Preposition, dan Plural/Singular.",
            "2_teks_revisi": "Berikan versi kalimat yang sudah diperbaiki tata bahasanya.",
            "3_upgrade_akademik": "Berikan versi formal alternatif yang sangat cocok untuk jurnal, paper, esai kuliah, atau TOEFL/IELTS.",
            "4_penjelasan_singkat": "Jelaskan aturan grammar yang diperbaiki dengan bahasa yang mudah dipahami."
        },
        "instruksi_xiaozhi": (
            "Periksa teks bahasa Inggris tersebut secara mendalam. Tampilkan: "
            "1) Apakah kalimat sudah benar atau ada error grammar, "
            "2) Kalimat versi perbaikan (jika ada salah), "
            "3) Versi Academic/Formal yang lebih elegan untuk tulisan ilmiah."
        )
    }


# ═════════════════════════════════════════════════════════════════════════════
# 1. FILSAFAT & LOGIKA: DATABASE & HANDLER KESESATAN BERPIKIR (LOGICAL FALLACIES)
# ═════════════════════════════════════════════════════════════════════════════

FALLACY_DATABASE = [
    {
        "name": "Ad Hominem",
        "latin": "Argumentum ad Hominem",
        "keywords": ["ad hominem", "pribadi", "menyerang orang", "karakter", "umur", "latar belakang", "bocah", "tahu apa", "masih kecil", "bego", "bodoh", "siapa kamu", "tidak sekolah", "kamu kan", "ngomong apa"],
        "definisi": "Menyerang karakter, kepribadian, penampilan, atau latar belakang lawan bicara alih-alih membantah substansi argumennya.",
        "contoh": "'Pendapat Anda tentang ekonomi tidak valid karena Anda sendiri belum punya rumah.'",
        "cara_identifikasi": "Apakah alasan penolakan berfokus pada siapa yang berbicara, bukan apa yang dikatakan?",
        "solusi_rekontruksi": "Fokus murni pada data, bukti, dan validitas logis premis tanpa menyinggung pribadi pembicara."
    },
    {
        "name": "Straw Man (Manusia Jerami)",
        "latin": "Ignoratio Elenchi",
        "keywords": ["straw man", "strawman", "memelintir", "melebih-lebihkan", "distorsi", "maksudmu", "jadi kamu mau", "kamu bilang", "berarti kamu ingin"],
        "definisi": "Memelintir, melebih-lebihkan, atau menyederhanakan argumen lawan secara tidak adil agar mudah diserang atau dihancurkan.",
        "contoh": "A: 'Kita perlu mengurangi polusi dengan bersepeda.' B: 'Jadi Anda ingin industri otomotif bangkrut dan semua karyawan di-PHK?'",
        "cara_identifikasi": "Apakah posisi lawan bicara diwakilkan secara ekstrem dan tidak akurat sebelum dibantah?",
        "solusi_rekontruksi": "Gunakan teknik 'Steel-manning': gambarkan argumen lawan dalam bentuk terbaik dan paling rasional sebelum mengevaluasinya."
    },
    {
        "name": "False Dilemma (Dikotomi Palsu / Hitam-Putih)",
        "latin": "Bifurcatio",
        "keywords": ["false dilemma", "hitam putih", "hanya dua pilihan", "jika tidak", "bifurkasi", "kalau gak", "pilihannya cuma", "antara", "hanya ada 2"],
        "definisi": "Menyajikan situasi seolah-olah hanya ada dua pilihan mutlak (hitam-putih), padahal kenyataannya ada opsi perantara atau alternatif lain.",
        "contoh": "'Jika Anda tidak mendukung kebijakan kami 100%, berarti Anda adalah musuh negara.'",
        "cara_identifikasi": "Apakah spektrum kemungkinan dipangkas secara paksa menjadi hanya dua kutub ekstrem?",
        "solusi_rekontruksi": "Eksplorasi jalan tengah, kompromi, atau spektrum opsi alternatif ketiga dan keempat."
    },
    {
        "name": "Slippery Slope (Lereng Licin)",
        "latin": "Argumentum in Terrorem",
        "keywords": ["slippery slope", "lereng licin", "pasti berujung", "rantai bencana", "bencana", "nanti bakal", "ujung-ujungnya pasti"],
        "definisi": "Mengasumsikan bahwa satu langkah awal kecil yang diambil pasti secara otomatis memicu rangkaian peristiwa bencana ekstrem tanpa bukti rantai kausalitas yang nyata.",
        "contoh": "'Jika kita mengizinkan murid memakai ponsel di kelas, mereka tidak akan belajar, ujian gagal, tidak dapat kerja, dan peradaban hancur.'",
        "cara_identifikasi": "Apakah ada lompatan logis tanpa pembuktian bahwa tahap A niscaya memicu tahap Z?",
        "solusi_rekontruksi": "Tunjukkan bukti empiris untuk setiap mata rantai sebab-akibat, jangan melompat ke kesimpulan paling ekstrem."
    },
    {
        "name": "Circular Reasoning (Penalaran Melingkar)",
        "latin": "Petitio Principii / Begging the Question",
        "keywords": ["circular reasoning", "melingkar", "petitio principii", "begging the question", "karena ya karena", "sudah pasti benar"],
        "definisi": "Argumen di mana kesimpulan yang ingin dibuktikan sudah secara implisit atau eksplisit diasumsikan benar di dalam premisnya.",
        "contoh": "'Buku ini selalu benar karena buku ini mengatakan bahwa dirinya sempurna tanpa kesalahan.'",
        "cara_identifikasi": "Apakah premis pendukung hanya merupakan pengulangan kata dari klaim itu sendiri?",
        "solusi_rekontruksi": "Gunakan bukti independen eksternal yang tidak bergantung pada klaim itu sendiri untuk memvalidasi kebenaran."
    },
    {
        "name": "Post Hoc Ergo Propter Hoc (Korelasi Dianggap Kausalitas)",
        "latin": "Post Hoc Ergo Propter Hoc",
        "keywords": ["post hoc", "kebetulan", "setelah ini maka karena ini", "korelasi", "kausalitas", "gara-gara", "semenjak"],
        "definisi": "Menyimpulkan bahwa peristiwa A adalah penyebab peristiwa B murni hanya karena peristiwa B terjadi setelah peristiwa A.",
        "contoh": "'Ayam berkokok sebelum matahari terbit, jadi kokokan ayam yang menyebabkan matahari terbit.'",
        "cara_identifikasi": "Apakah urutan waktu semata disalahartikan sebagai hubungan sebab-akibat langsung?",
        "solusi_rekontruksi": "Uji variabel pengganggu (confounding variables) dan lakukan uji kontrol ilmiah untuk membuktikan mekanisme kausalitas."
    },
    {
        "name": "Bandwagon / Appeal to Popularity",
        "latin": "Argumentum ad Populum",
        "keywords": ["bandwagon", "mayoritas", "banyak orang", "populer", "semua orang", "semua juga tahu", "lagi tren"],
        "definisi": "Mengklaim suatu keyakinan atau tindakan pasti benar dan bermoral hanya karena mayoritas orang mempercayai atau melakukannya.",
        "contoh": "'Semua orang memakai cara ini untuk menyontek, jadi ini bukan kesalahan besar.'",
        "cara_identifikasi": "Apakah popularitas dijadikan tolok ukur kebenaran objektif?",
        "solusi_rekontruksi": "Ingat bahwa kebenaran ilmiah dan etika tidak ditentukan oleh pemungutan suara (konsensus mayoritas bisa keliru)."
    },
    {
        "name": "Tu Quoque (Whataboutism)",
        "latin": "Tu Quoque ('Kamu juga')",
        "keywords": ["tu quoque", "whataboutism", "kamu juga", "munafik", "kamu sendiri", "situ sendiri", "ngaca dulu"],
        "definisi": "Menepis kritik atau argumen valid lawan dengan menuduh bahwa lawan bicara pernah atau sedang melakukan kesalahan yang sama (hipokrisi).",
        "contoh": "Dokter: 'Merokok merusak paru-paru Anda.' Pasien: 'Tapi dokter sendiri masih merokok, jadi nasihat dokter salah.'",
        "cara_identifikasi": "Apakah tanggapan mengalihkan topik pembicaraan ke perilaku si pengkritik?",
        "solusi_rekontruksi": "Jawab substansi kritikan terlebih dahulu secara mandiri sebelum membahas konsistensi pihak lain."
    },
    {
        "name": "Appeal to Emotion",
        "latin": "Argumentum ad Passiones",
        "keywords": ["appeal to emotion", "emosi", "kasihan", "ketakutan", "memancing amarah", "iba", "tega sekali"],
        "definisi": "Memanipulasi respon emosional pendengar (kasihan, takut, amarah) sebagai pengganti argumen logis yang sah.",
        "contoh": "'Tolong luluskan saya dari ujian ini, jika saya tidak lulus orang tua saya akan sangat sedih dan sakit.'",
        "cara_identifikasi": "Apakah argumen menggunakan penderitaan atau rasa takut untuk menutupi ketiadaan bukti?",
        "solusi_rekontruksi": "Pisahkan antara empati kemanusiaan dan standar penilaian objektif logis."
    },
    {
        "name": "Cherry Picking (Texas Sharpshooter)",
        "latin": "Falasia Seleksi Data",
        "keywords": ["cherry picking", "pilih data", "mengabaikan bukti", "bias seleksi", "hanya ambil yang enak", "anekdot"],
        "definisi": "Hanya memilih segelintir data atau contoh yang mendukung klaim, sambil secara sengaja mengabaikan porsi besar data yang membantah klaim tersebut.",
        "contoh": "'Kakek saya merokok setiap hari dan hidup sampai umur 95 tahun, jadi rokok sama sekali tidak berbahaya.'",
        "cara_identifikasi": "Apakah kesimpulan ditarik dari bukti anekdot yang menolak konsensus data statistik makro?",
        "solusi_rekontruksi": "Gunakan tinjauan sistematis (systematic review) dan sampel data representatif."
    }
]


def detect_logical_fallacy_handler(argument: str, context: str = "") -> Dict[str, Any]:
    """
    Analisis cacat logika (logical fallacies) dalam suatu argumen atau teks debat.
    """
    cleaned = argument.strip()
    if not cleaned:
        return {"success": False, "message": "Argumen tidak boleh kosong."}

    lower_arg = cleaned.lower()
    matched_fallacies = []

    for f in FALLACY_DATABASE:
        if any(k in lower_arg for k in f["keywords"]):
            matched_fallacies.append({
                "nama": f["name"],
                "nama_latin": f["latin"],
                "definisi": f["definisi"],
                "contoh": f["contoh"],
                "cara_identifikasi": f["cara_identifikasi"],
                "solusi_rekontruksi": f["solusi_rekontruksi"]
            })

    return {
        "success": True,
        "argumen_diuji": cleaned,
        "konteks": context or "Debat / Argumen Filosofis",
        "kandidat_fallacy_tercocok": matched_fallacies[:3],
        "metodologi_bedah_logika": {
            "1_dekonstruksi_premis": "Pecah argumen menjadi Premis Mayor, Premis Minor, dan Kesimpulan (Konklusi).",
            "2_uji_validitas": "Uji apakah kesimpulan niscaya mengikuti premis secara validitas formal (syllogism).",
            "3_uji_kebenaran_materiil": "Uji apakah fakta-fakta dalam premis memang benar dan terverifikasi secara empiris.",
            "4_identifikasi_cacat": "Tentukan nama cacat logika yang terjadi dan di bagian mana letak kekeliruannya.",
            "5_rekonstruksi_dialektika": "Rumuskan kembali argumen tersebut menjadi argumen yang rasional, sehat, dan kokoh."
        },
        "instruksi_xiaozhi": (
            "Bedahlah argumen tersebut dengan ketajaman analisis filsafat dan logika penalaran kritis. "
            "Sebutkan: 1) Apakah argumen tersebut mengandung cacat logika (fallacy), "
            "2) Jelaskan letak kecacatan berpikirnya dengan analogi yang cerdas, "
            "3) Berikan cara menyusun argumen tandingan atau perbaikan yang valid tanpa cacat logika."
        )
    }


# ═════════════════════════════════════════════════════════════════════════════
# 2. PSIKOLOGI KOGNITIF: DATABASE & HANDLER BIAS KOGNITIF & DISTORSI PIKIRAN
# ═════════════════════════════════════════════════════════════════════════════

COGNITIVE_BIAS_DATABASE = [
    {
        "name": "Confirmation Bias (Bias Konfirmasi)",
        "keywords": ["confirmation bias", "bias konfirmasi", "hanya mencari yang cocok", "membenarkan keyakinan", "cocoklogi", "sudah kubilang", "tuh kan benar", "cari pembenaran"],
        "definisi": "Kecenderungan manusia untuk secara aktif mencari, menafsirkan, dan mengingat informasi yang hanya membenarkan prasangka atau keyakinan awal mereka, sambil mengabaikan fakta sebaliknya.",
        "pemicu_psikologis": "Otak berupaya menghemat energi dan mempertahankan harga diri dengan menghindari disonansi kognitif (cognitive dissonance).",
        "pertanyaan_metakognisi": "'Bukti apa yang jika saya temukan, akan membuat saya bersedia mengubah pandangan ini?'",
        "reframing_cbt": "Secara sengaja jadilah 'Devil's Advocate' bagi diri sendiri: cari 3 argumen terbaik yang bertentangan dengan keyakinan Anda."
    },
    {
        "name": "Dunning-Kruger Effect",
        "keywords": ["dunning kruger", "dunning-kruger", "merasa paling tahu", "sok tahu", "overconfidence", "gampang banget", "itu kan mudah", "ah gampang", "ah sepele"],
        "definisi": "Fenomena kognitif di mana orang dengan keahlian minim pada suatu bidang menilai kemampuan dirinya jauh lebih tinggi dari kenyataan karena belum tahu luasnya kompleksitas bidang tersebut.",
        "pemicu_psikologis": "Ketidaktahuan terhadap batas pengetahuan sendiri (kurangnya metakognisi).",
        "pertanyaan_metakognisi": "'Seberapa banyak fakta mendalam dari topik ini yang belum pernah saya baca atau uji secara nyata?'",
        "reframing_cbt": "Adopsi 'Beginner's Mind' (Pikiran Pemula): Semakin banyak seseorang belajar, semakin ia menyadari betapa banyaknya hal yang belum ia ketahui."
    },
    {
        "name": "Sunk Cost Fallacy",
        "keywords": ["sunk cost", "sayang uangnya", "sayang waktunya", "sudah terlanjur", "biaya tertanam", "hangus", "sayang kalau", "rugi", "tambah modal", "nanggung"],
        "definisi": "Melanjutkan investasi, usaha, hubungan, atau keputusan yang sudah terbukti merugikan hanya karena merasa 'sayang' dengan waktu, uang, atau emosi yang sudah terlanjur dikeluarkan di masa lalu.",
        "pemicu_psikologis": "Keengganan menanggung rasa rugi (loss aversion) dan penolakan mengakui kegagalan masa lalu.",
        "pertanyaan_metakognisi": "'Jika saya baru memulai hari ini tanpa ada modal yang hangus di masa lalu, apakah saya tetap akan memilih opsi ini?'",
        "reframing_cbt": "Masa lalu tidak bisa ditarik kembali. Keputusan rasional hanya mempertimbangkan biaya masa depan vs manfaat masa depan."
    },
    {
        "name": "Availability Heuristic",
        "keywords": ["availability heuristic", "heuristik ketersediaan", "mudah diingat", "berita viral", "trauma", "baru nonton", "sering dengar"],
        "definisi": "Menilai probabilitas atau bahaya suatu peristiwa murni berdasarkan seberapa mudah contoh peristiwa tersebut terlintas di ingatan emosional.",
        "contoh": "Takut berlebihan naik pesawat setelah melihat berita kecelakaan, padahal secara statistik berkendara mobil jauh lebih berisiko.",
        "pertanyaan_metakognisi": "'Apakah hal ini sering terjadi secara statistik, atau hanya sering muncul di ingatan karena dramatis dan viral?'",
        "reframing_cbt": "Cek data angka dasar (base rate statistics) daripada mengandalkan intensitas emosi memori."
    },
    {
        "name": "Anchoring Bias",
        "keywords": ["anchoring", "jangkar", "angka pertama", "patokan pertama", "terpaku", "harga awal", "awalnya"],
        "definisi": "Kecenderungan terlalu bergantung pada informasi atau angka pertama yang didengar (jangkar) saat membuat estimasi atau keputusan berikutnya.",
        "contoh": "Harga jaket dicoret dari Rp 1.000.000 menjadi Rp 400.000 terasa sangat murah, padahal nilai riil barang tersebut mungkin hanya Rp 200.000.",
        "pertanyaan_metakognisi": "'Jika informasi pertama itu tidak pernah disebutkan, berapa nilai yang akan saya berikan secara objektif?'",
        "reframing_cbt": "Cari patokan pembanding dari sumber yang sama sekali independen sebelum mengambil keputusan."
    },
    {
        "name": "Fundamental Attribution Error",
        "keywords": ["attribution error", "atribusi", "menyalahkan karakter", "alasan situasi", "memang dasar orangnya", "sifat aslinya"],
        "definisi": "Kecenderungan menilai kegagalan orang lain sebagai cacat karakter/moral mereka ('dia malas/ceroboh'), namun menilai kegagalan diri sendiri semata-mata karena faktor situasi lingkungan ('cuaca buruk, sistem error').",
        "pemicu_psikologis": "Ketidaktahuan kita akan konteks penuh kehidupan orang lain vs kesadaran penuh akan kesulitan diri sendiri.",
        "pertanyaan_metakognisi": "'Tekanan situasi apa yang mungkin dialami orang tersebut sehingga mereka bertindak seperti itu?'",
        "reframing_cbt": "Gunakan Prinsip Hanlon's Razor: 'Jangan anggap sebagai niat jahat apa yang cukup dijelaskan oleh kelelahan atau ketidaktahuan.'"
    },
    {
        "name": "Catastrophizing (Membesar-besarkan Skenario Terburuk)",
        "keywords": ["catastrophizing", "skenario terburuk", "pasti hancur", "tamat riwayat", "cemas berlebihan", "gagal total", "hancur sudah", "kiamat"],
        "definisi": "Distorsi kognitif di mana seseorang secara otomatis meyakini bahwa hasil yang paling buruk dan bencana total pasti akan terjadi dari suatu peristiwa kecil.",
        "contoh": "'Saya tidak sengaja salah ketik saat presentasi, pasti bos mengira saya bodoh dan saya akan segera dipecat.'",
        "pertanyaan_metakognisi": "'Berapa persen kemungkinan skenario terburuk itu benar-benar terjadi? Jika terjadi, apa langkah solutif konkret saya?'",
        "reframing_cbt": "Petakan 3 skenario: 1) Skenario Terburuk (Worst Case), 2) Skenario Terbaik (Best Case), dan 3) Skenario Paling Realistis (Most Likely Case)."
    },
    {
        "name": "Overgeneralization (Generalisasi Berlebihan)",
        "keywords": ["overgeneralization", "selalu begini", "tidak pernah", "semua orang sama saja", "generalisasi", "selalu saja", "pasti gagal"],
        "definisi": "Menarik kesimpulan universal yang kaku hanya berdasarkan satu atau dua kejadian tunggal ('Saya gagal di wawancara ini, berarti saya tidak punya masa depan').",
        "pemicu_psikologis": "Upaya otak menyederhanakan realitas menjadi hukum absolut.",
        "pertanyaan_metakognisi": "'Apakah ada bukti pengecualian di mana hal ini tidak terjadi? Apakah kata 'selalu' atau 'tidak pernah' benar-benar akurat?'",
        "reframing_cbt": "Ganti kata absolut 'selalu' atau 'pasti' dengan kata kondisional: 'pada situasi kali ini', 'kadang-kadang'."
    }
]



def identify_cognitive_bias_handler(statement_or_scenario: str, context: str = "") -> Dict[str, Any]:
    """
    Identifikasi bias kognitif dan distorsi pola pikir psikologis dalam situasi atau kalimat seseorang.
    """
    cleaned = statement_or_scenario.strip()
    if not cleaned:
        return {"success": False, "message": "Pernyataan atau skenario tidak boleh kosong."}

    lower_text = cleaned.lower()
    matched_biases = []

    for b in COGNITIVE_BIAS_DATABASE:
        if any(k in lower_text for k in b["keywords"]):
            matched_biases.append({
                "nama": b["name"],
                "definisi": b["definisi"],
                "pertanyaan_metakognisi": b["pertanyaan_metakognisi"],
                "reframing_cbt": b["reframing_cbt"]
            })

    return {
        "success": True,
        "skenario_diuji": cleaned,
        "konteks": context or "Psikologi Kognitif & Perilaku",
        "kandidat_bias_tercocok": matched_biases[:3],
        "metodologi_cbt_reframing": {
            "1_deteksi_distorsi": "Identifikasi bentuk jebakan pikiran yang membuat emosi atau keputusan menjadi tidak rasional.",
            "2_uji_realitas": "Bandingkan asumsi subyektif dengan fakta obyektif (reality-testing).",
            "3_pertanyaan_sokratik": "Ajukan pertanyaan mendalam yang menantang akar keyakinan yang bias tersebut.",
            "4_reframing_solutif": "Ubah pola pikir menjadi lebih fleksibel, adaptif, dan sehat secara psikologis."
        },
        "disclaimer_etis": (
            "CATATAN EDUKATIF: Analisis ini bertujuan untuk wawasan psikologi kognitif dan pengembangan diri (self-reflection), "
            "bukan merupakan diagnosis psikiatri atau pengganti konsultasi klinis dengan psikolog profesional."
        ),
        "instruksi_xiaozhi": (
            "Analisis skenario atau pernyataan tersebut dari sudut pandang psikologi kognitif. "
            "Sebutkan: 1) Bias kognitif atau distorsi pikiran apa yang sedang bermain, "
            "2) Mengapa otak cenderung terjebak dalam bias tersebut, "
            "3) Berikan pertanyaan refleksi diri (pertanyaan sokratik) dan pembingkaian ulang (CBT reframing) yang menenangkan dan mencerahkan."
        )
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. DUNIA IT, PEMROGRAMAN & ARSITEKTUR SOFTWARE: DATABASE & HANDLER
# ═════════════════════════════════════════════════════════════════════════════

IT_PATTERNS_DATABASE = [
    {
        "name": "Singleton Pattern",
        "category": "Creational Design Pattern",
        "keywords": ["singleton", "satu instance", "global access"],
        "tujuan": "Memastikan suatu class hanya memiliki satu instance di seluruh siklus hidup aplikasi dan menyediakan titik akses global ke instance tersebut.",
        "kapan_dipakai": "Database connection pool, logging service, hardware driver client, configuration manager.",
        "perhatian": "Hati-hati dengan thread safety di lingkungan multithreading dan kesulitan dalam automated unit testing (mocking)."
    },
    {
        "name": "Factory Method & Abstract Factory",
        "category": "Creational Design Pattern",
        "keywords": ["factory", "pabrik", "abstract factory", "create object"],
        "tujuan": "Mendelegasikan proses pembuatan objek ke subclass atau class khusus tanpa mengekspos logika instansiasi langsung ke kode pemanggil.",
        "kapan_dipakai": "Saat sistem harus mendukung banyak format file (PDF, CSV, JSON), integrasi payment gateway (Midtrans, Stripe, Xendit), atau UI lintas platform."
    },
    {
        "name": "Observer Pattern",
        "category": "Behavioral Design Pattern",
        "keywords": ["observer", "pubsub", "publish subscribe", "event listener", "listener"],
        "tujuan": "Mendefinisikan relasi satu-ke-banyak di mana ketika suatu objek berubah status, semua objek pengamat (observers) akan diberitahu secara otomatis.",
        "kapan_dipakai": "Event-driven architecture, WebSocket messaging, UI state reactivity (React/Vue), notifikasi push."
    },
    {
        "name": "Repository Pattern",
        "category": "Architectural Pattern",
        "keywords": ["repository", "repo pattern", "akses database", "decoupling db"],
        "tujuan": "Mengisolasi lapisan domain bisnis dari lapisan akses data (database) sehingga logika aplikasi tidak bergantung langsung pada teknologi database tertentu.",
        "kapan_dipakai": "Clean Architecture, proyek skala menengah ke atas yang menggunakan ORM / SQL mentah agar mudah di-unit test."
    },
    {
        "name": "Clean Architecture (Hexagonal / Onion)",
        "category": "Software Architecture",
        "keywords": ["clean architecture", "hexagonal", "onion architecture", "domain driven design", "ddd"],
        "tujuan": "Menyusun sistem dalam lapisan konsentris di mana aturan bisnis inti (Entities & Use Cases) berada di tengah dan tidak memiliki dependensi terhadap framework eksternal, UI, atau DB.",
        "kapan_dipakai": "Aplikasi enterprise yang ditargetkan berumur panjang, mudah dimigrasi teknologinya, dan membutuhkan cakupan test (testability) tinggi."
    },
    {
        "name": "Microservices vs Monolith",
        "category": "System Design & Architecture",
        "keywords": ["microservice", "microservices", "monolith", "monolit", "arsitektur sistem"],
        "tujuan": "Perbandingan paradigma arsitektur: Monolith menggabungkan seluruh modul dalam satu deployment unit, sedangkan Microservices memecah sistem menjadi service independen via API/gRPC/Kafka.",
        "kapan_dipakai": "Monolith ideal untuk tahap startup/MVP dan tim kecil. Microservices ideal untuk skala organisasi besar dengan puluhan tim independen dan kebutuhan deployment terpisah."
    },
    {
        "name": "Database Caching & Cache Invalidation",
        "category": "Backend Performance",
        "keywords": ["cache", "caching", "redis", "memcached", "cache invalidation", "cache aside"],
        "tujuan": "Menyimpan data hasil query berat di memori RAM (in-memory) untuk mengurangi beban IO database dan memangkas latency dari ratusan milidetik menjadi sub-milidetik.",
        "kapan_dipakai": "Trafik tinggi membaca data yang jarang berubah (produk populer, data profil, token sesi pengguna)."
    }
]


def it_code_and_architecture_helper_handler(query_or_code: str, topic_type: str = "auto") -> Dict[str, Any]:
    """
    Panduan tajam untuk dunia IT: Analisis bug/kode, penentuan arsitektur software, design pattern, dan command line.
    """
    cleaned = query_or_code.strip()
    if not cleaned:
        return {"success": False, "message": "Pertanyaan atau kode IT tidak boleh kosong."}

    lower_query = cleaned.lower()
    matched_patterns = []

    for p in IT_PATTERNS_DATABASE:
        if any(k in lower_query for k in p["keywords"]):
            matched_patterns.append({
                "nama": p["name"],
                "kategori": p["category"],
                "tujuan": p["tujuan"],
                "kapan_dipakai": p["kapan_dipakai"]
            })

    # Detect topic
    detected_topic = topic_type
    if detected_topic == "auto":
        if any(w in lower_query for w in ["pattern", "arsitektur", "clean architecture", "microservice", "monolith", "solid", "dry"]):
            detected_topic = "Software Architecture & Design Patterns"
        elif any(w in lower_query for w in ["bug", "error", "traceback", "exception", "nullpointer", "segfault", "undefined"]):
            detected_topic = "Code Debugging & Root Cause Analysis"
        elif any(w in lower_query for w in ["docker", "git", "linux", "bash", "chmod", "regex", "grep", "curl", "nginx"]):
            detected_topic = "DevOps, CLI & Terminal Tools"
        elif any(w in lower_query for w in ["sql", "query", "nosql", "postgres", "mongodb", "indexing", "sharding", "join"]):
            detected_topic = "Database & Data Engineering"
        else:
            detected_topic = "Software Engineering & Best Practices"

    return {
        "success": True,
        "input_query_or_code": cleaned,
        "kategori_it": detected_topic,
        "matched_architectures": matched_patterns[:2],
        "metodologi_analisis_it": {
            "1_akar_masalah": "Identifikasi penyebab utama masalah (root cause) atau konsep kunci yang melatarbelakanginya.",
            "2_prinsip_rekayasa": "Terapkan prinsip rekayasa perangkat lunak standar (SOLID, DRY, KISS, YAGNI, POLA ASINCRONOUS).",
            "3_solusi_kode_konkret": "Sajikan contoh kode/perintah yang bersih, efisien, aman, dan berstandar produksi.",
            "4_aspek_performa_dan_tradeoff": "Jelaskan kelebihan, kekurangan, kompleksitas Big-O (waktu & memori), dan trade-off dari solusi tersebut."
        },
        "instruksi_xiaozhi": (
            "Berikan panduan teknis yang tajam, akurat, dan berstandar industri (Senior Software Engineer level). "
            "Jangan berikan kode asal jadi yang rapuh. Tampilkan: "
            "1) Analisis inti masalah atau arsitektur yang paling tepat, "
            "2) Solusi kode / perintah terminal yang bersih dan idiomatis, "
            "3) Penjelasan mengapa solusi ini lebih baik dari sisi performa, keamanan, dan pemeliharaan (clean code)."
        )
    }

