"""
Lightweight Semantic Vector Engine and User Persona Service for Xiaozhi.
Designed for ultra-low memory footprint (< 1 MB RAM) and sub-millisecond execution.
Provides semantic similarity scoring, concept clustering, and user persona profiling.
"""

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple


# Semantic concept taxonomy for Indonesian language context
# Groups related terms so queries like "keuangan" naturally match "biaya skripsi", "uang kos", "hutang", etc.
CONCEPT_TAXONOMY: Dict[str, List[str]] = {
    "keuangan": [
        "uang", "duit", "biaya", "kos", "skripsi", "ukt", "anggaran", "finansial",
        "bayar", "rekening", "tabungan", "gaji", "penghasilan", "utang", "hutang",
        "pinjaman", "cicilan", "investasi", "modal", "dompet", "tagihan"
    ],
    "akademik": [
        "kuliah", "kampus", "dosen", "mahasiswa", "pelajaran", "ujian", "uts", "uas",
        "skripsi", "thesis", "tugas", "pr", "nilai", "ipk", "semester", "matematika",
        "fisika", "kimia", "biologi", "sekolah", "jurusan", "kelas", "belajar", "kursus"
    ],
    "pekerjaan": [
        "kerja", "kantor", "bos", "atasan", "proyek", "karir", "gaji", "klien",
        "lembur", "meeting", "rapat", "wawancara", "interview", "cv", "resume",
        "perusahaan", "startup", "bisnis", "omset", "loker", "magang"
    ],
    "kesehatan_fisik": [
        "sakit", "dokter", "obat", "rumah sakit", "demam", "flu", "batuk", "pusing",
        "gejala", "sehat", "olahraga", "diet", "tidur", "insomnia", "lelah", "capek",
        "tensi", "darah", "vitamin", "gizi", "imun"
    ],
    "kesehatan_mental": [
        "stres", "stress", "cemas", "anxiety", "depresi", "sedih", "galau", "takut",
        "bingung", "overthinking", "burnout", "tenang", "meditasi", "curhat", "emosi",
        "mental", "patah hati", "kesepian"
    ],
    "teknologi": [
        "koding", "coding", "program", "python", "javascript", "komputer", "laptop",
        "server", "database", "sql", "bug", "error", "api", "ai", "software",
        "hardware", "internet", "wifi", "jaringan", "aplikasi", "web"
    ],
    "hobi_hiburan": [
        "game", "gaming", "musik", "lagu", "gitar", "nyanyi", "film", "movie",
        "nonton", "anime", "manga", "buku", "novel", "baca", "jalan-jalan", "traveling",
        "fotografi", "olahraga", "sepeda", "lari", "gym"
    ],
    "makanan_minuman": [
        "makan", "minum", "masak", "resep", "kopi", "teh", "sarapan", "makan siang",
        "makan malam", "pedas", "manis", "asin", "restoran", "kafe", "kuliner", "jajan"
    ],
    "keluarga_hubungan": [
        "orang tua", "ibu", "ayah", "mama", "papa", "anak", "istri", "suami",
        "pacar", "gebetan", "teman", "sahabat", "keluarga", "saudara", "kakak", "adik",
        "nikah", "cinta", "sayang", "hubungan", "komitmen"
    ],
    "spiritual_agama": [
        "sholat", "shalat", "salat", "solat", "doa", "puasa", "ibadah", "masjid", "gereja", "tuhan", "allah",
        "quran", "alkitab", "hadits", "hadis", "pahala", "dosa", "syukur", "dzikir", "zikir", "sedekah", "zakat", "wudhu", "wudu"
    ],
}

# Stopwords to filter out syntactic noise
INDONESIAN_STOPWORDS: Set[str] = {
    "yang", "di", "dan", "dari", "ini", "itu", "untuk", "pada", "ke", "karena",
    "oleh", "dengan", "saya", "aku", "kamu", "dia", "mereka", "kita", "kami",
    "adalah", "ada", "akan", "sudah", "bisa", "dapat", "atau", "juga", "hanya",
    "lagi", "tadi", "nih", "dong", "sih", "lah", "ya", "kan", "pun", "apa",
    "kok", "kenapa", "mengapa", "bagaimana", "kapan", "siapa", "mana"
}


def clean_and_tokenize(text: str) -> List[str]:
    """Normalize text into lowercase alphanumeric tokens."""
    if not text:
        return []
    # Replace punctuation with space
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]
    return [t for t in tokens if t not in INDONESIAN_STOPWORDS]


def expand_semantic_concepts(tokens: List[str]) -> Dict[str, float]:
    """
    Expand token list with related semantic concepts from CONCEPT_TAXONOMY.
    Returns a weighted feature dictionary.
    """
    features: Dict[str, float] = {}
    token_set = set(tokens)

    # Base token weight
    for t in tokens:
        features[t] = features.get(t, 0.0) + 1.0

    # Add character n-grams (subwords) for morphological robustness (misal: "keuangan", "beruang", "uang")
    for t in token_set:
        if len(t) >= 4:
            for i in range(len(t) - 2):
                ngram = t[i:i + 3]
                features[f"ng_{ngram}"] = features.get(f"ng_{ngram}", 0.0) + 0.35

    # Check matches against semantic taxonomy
    matched_clusters: Set[str] = set()
    for cluster_name, cluster_words in CONCEPT_TAXONOMY.items():
        all_cluster_terms = set(cluster_words + [cluster_name] + cluster_name.split("_"))
        overlap = token_set.intersection(all_cluster_terms)
        # Also check substring match (e.g. "keuangan" contains "uang")
        if not overlap:
            for t in token_set:
                if any(w in t or t in w for w in all_cluster_terms if len(w) >= 4):
                    overlap.add(t)

        if overlap:
            matched_clusters.add(cluster_name)
            cluster_strength = len(overlap) * 2.0
            features[f"sem_{cluster_name}"] = cluster_strength
            # Boost related cluster keywords with a decay
            for w in cluster_words[:10]:
                features[f"rel_{w}"] = features.get(f"rel_{w}", 0.0) + 0.8

    return features


def compute_vector_magnitude(vector: Dict[str, float]) -> float:
    """Calculate Euclidean norm (L2 magnitude) of feature vector."""
    sum_squares = sum(v * v for v in vector.values())
    return math.sqrt(sum_squares) if sum_squares > 0 else 1e-9


def calculate_semantic_similarity(query_text: str, document_text: str) -> float:
    """
    Computes semantic cosine similarity between query and candidate text.
    Returns a score between 0.0 and 1.0.
    """
    if not query_text or not document_text:
        return 0.0

    q_tokens = clean_and_tokenize(query_text)
    d_tokens = clean_and_tokenize(document_text)

    if not q_tokens or not d_tokens:
        return 0.0

    q_vec = expand_semantic_concepts(q_tokens)
    d_vec = expand_semantic_concepts(d_tokens)

    # Dot product
    dot_product = 0.0
    for k, q_val in q_vec.items():
        if k in d_vec:
            dot_product += q_val * d_vec[k]

    # Magnitudes
    q_mag = compute_vector_magnitude(q_vec)
    d_mag = compute_vector_magnitude(d_vec)

    score = dot_product / (q_mag * d_mag)
    return max(0.0, min(1.0, float(score)))


def rank_chat_history_semantically(
    query: str,
    records: List[Dict[str, Any]],
    top_k: int = 5,
    min_similarity: float = 0.12
) -> List[Dict[str, Any]]:
    """
    Rank chat history entries using semantic similarity score.
    Combines recency bonus with semantic relevance.
    """
    if not query or not records:
        return records[:top_k]

    scored_records: List[Tuple[float, Dict[str, Any]]] = []

    for idx, r in enumerate(records):
        user_msg = str(r.get("user_message") or "")
        ai_ans = str(r.get("xiaozhi_answer") or "")
        tool_name = str(r.get("tool_name") or "")
        req_p = str(r.get("request_payload") or "")
        res_p = str(r.get("response_payload") or "")
        combined_text = f"{user_msg} {ai_ans} {tool_name} {req_p} {res_p}"

        sim_score = calculate_semantic_similarity(query, combined_text)

        # Small recency weight (newer records get up to 0.05 bonus)
        recency_bonus = max(0.0, 0.05 * (1.0 - (idx / max(1, len(records)))))
        final_score = sim_score + recency_bonus

        if sim_score >= min_similarity or any(token in combined_text.lower() for token in clean_and_tokenize(query)):
            entry = dict(r)
            entry["similarity_score"] = round(sim_score, 3)
            scored_records.append((final_score, entry))

    # Sort descending by final score
    scored_records.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_records[:top_k]]


# ─────────────────────────────────────────────────────────────────────────────
# User Persona Categories & Structures
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERSONA_CATEGORIES = {
    "gaya_bicara": "Gaya & Nada Bicara yang Disukai (Santai, Formal, Hangat, Singkat/Padat)",
    "minat_hobi": "Hobi, Minat & Ketertarikan Pribadi",
    "tujuan_belajar": "Target Akademik, Karir & Fokus Pembelajaran",
    "informasi_pribadi": "Identitas & Fakta Penting (Panggilan, Kampus/Sekolah, Domisili)",
    "kebiasaan": "Rutinitas, Jadwal & Pola Keseharian"
}

# Cluster definition for Vector Persona Profiling
PERSONALITY_CLUSTERS = {
    "introvert": [
        "kamar", "sendiri", "menyendiri", "baca", "hening", "game solo", "overthinking",
        "lelah", "capek", "malam", "tenang", "nulis", "melamun", "me time", "rebahan",
        "privat", "diam", "sunyi", "istirahat", "pribadi", "sepi", "nyaman sendiri"
    ],
    "extrovert": [
        "nongkrong", "teman", "kawan", "kumpul", "ramai", "jalan", "party", "ngobrol",
        "organisasi", "meetup", "liburan", "curhat bareng", "tim", "festival", "konser",
        "keluar", "sosialisasi", "reuni", "acara", "hangout", "rame"
    ]
}

HOBBY_CLUSTERS = [
    {
        "id": "bola",
        "name": "Sepak Bola & Futsal",
        "icon": "⚽",
        "terms": ["bola", "sepak bola", "futsal", "jersey", "lapangan", "tanding", "liga", "ronaldo", "messi", "timnas", "gol", "kiper", "striker"]
    },
    {
        "id": "game",
        "name": "Main Game & E-Sport",
        "icon": "🎮",
        "terms": ["game", "gaming", "main game", "ml", "mobile legends", "pubg", "valorant", "steam", "rank", "push rank", "playstation", "ps5", "xbox", "mabar", "genshin"]
    },
    {
        "id": "catur",
        "name": "Catur & Strategi",
        "icon": "♟️",
        "terms": ["catur", "chess", "bidak", "skak", "pion", "kuda", "menteri", "strategi", "grandmaster", "taktik", "openings"]
    },
    {
        "id": "musik",
        "name": "Musik & Audio",
        "icon": "🎵",
        "terms": ["musik", "lagu", "gitar", "nyanyi", "vokal", "playlist", "spotify", "youtube music", "band", "akustik", "konser", "chord"]
    },
    {
        "id": "koding",
        "name": "Koding & Teknologi IoT",
        "icon": "💻",
        "terms": ["koding", "coding", "python", "javascript", "program", "programmer", "esp32", "iot", "smart home", "relay", "lampu", "server", "api", "database", "sql", "bug", "error", "bot"]
    },
    {
        "id": "buku",
        "name": "Membaca & Menulis",
        "icon": "📚",
        "terms": ["buku", "novel", "komik", "manga", "baca", "nulis", "artikel", "cerita", "perpustakaan", "literasi"]
    },
    {
        "id": "film",
        "name": "Nonton Film & Anime",
        "icon": "🎬",
        "terms": ["film", "nonton", "movie", "anime", "serial", "drama", "bioskop", "netflix", "drakor", "alur cerita"]
    },
    {
        "id": "olahraga",
        "name": "Olahraga & Kebugaran",
        "icon": "🏃",
        "terms": ["lari", "jogging", "gym", "workout", "sepeda", "pushup", "fitnes", "otot", "kebugaran", "keringat"]
    },
    {
        "id": "kuliner",
        "name": "Ngopi & Kuliner",
        "icon": "☕",
        "terms": ["kopi", "ngopi", "kafe", "cafe", "kuliner", "resep", "masak", "jajan", "makan enak", "makanan"]
    }
]

CHALLENGE_CLUSTERS = [
    {
        "id": "skripsi",
        "name": "Skripsi & Beban Kuliah",
        "icon": "🎓",
        "terms": ["skripsi", "dosen", "bimbingan", "revisi", "judul", "sidang", "proposal", "uts", "uas", "tugas", "kuliah", "kampus", "nilai", "ipk", "pusing mikirin", "pusing"]
    },
    {
        "id": "keuangan",
        "name": "Biaya Hidup & Finansial",
        "icon": "💸",
        "terms": ["uang", "duit", "kos", "uang kos", "biaya", "ukt", "bayar", "tagihan", "bokek", "hutang", "utang", "cicilan", "gaji", "finansial", "dompet", "hemat"]
    },
    {
        "id": "tidur",
        "name": "Pola Tidur & Insomnia",
        "icon": "💤",
        "terms": ["tidur", "susah tidur", "insomnia", "begadang", "lelah", "capek", "ngantuk", "mata panda", "belum tidur", "larut malam"]
    },
    {
        "id": "mental",
        "name": "Tekanan Mental & Stres",
        "icon": "🧠",
        "terms": ["stres", "stress", "cemas", "anxiety", "overthinking", "galau", "bingung", "pusing", "burnout", "tertekan", "kesepian", "sedih"]
    },
    {
        "id": "teknis",
        "name": "Kendala Teknis & Perangkat",
        "icon": "🛠️",
        "terms": ["lampu", "mati", "rusak", "putus", "error", "koneksi", "mati lampu", "saklar", "relay", "gangguan", "trouble"]
    }
]

ACTIVITY_CLUSTERS = [
    {
        "id": "belajar",
        "name": "Belajar & Kuliah",
        "icon": "📖",
        "terms": ["belajar", "kuliah", "baca", "materi", "tugas", "pr", "latihan", "ujian", "kampus"]
    },
    {
        "id": "iot_tech",
        "name": "Eksplorasi IoT & Smart Home",
        "icon": "⚡",
        "terms": ["xiaozhi", "smart home", "relay", "lampu", "perangkat", "esp32", "koding", "tool", "mcp"]
    },
    {
        "id": "ibadah",
        "name": "Ibadah & Rutinitas Doa",
        "icon": "🕌",
        "terms": ["sholat", "shalat", "solat", "subuh", "dzuhur", "ashar", "maghrib", "isya", "doa", "masjid", "ibadah", "puasa"]
    },
    {
        "id": "santai",
        "name": "Istirahat & Me-Time",
        "icon": "🛋️",
        "terms": ["rebahan", "santai", "istirahat", "tidur", "libur", "weekend", "santai sore", "ngopi"]
    },
    {
        "id": "kerja",
        "name": "Pekerjaan & Produktivitas",
        "icon": "💼",
        "terms": ["kerja", "kantor", "meeting", "proyek", "tugas kantor", "deadline", "klien", "lembur"]
    }
]


def analyze_user_persona_from_chats(
    chat_records: List[Dict[str, Any]],
    stored_personas: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    RAG & Vector Semantic engine to automatically infer user personality (Introvert/Extrovert/Ambivert),
    hobbies & interests, daily activities, likes/preferences, and challenges/pain points
    with accurate percentage distribution (1% to 100%).
    """
    total_chats = len(chat_records or [])
    stored_personas = stored_personas or []

    # Combine all user texts & tool interactions into an analyzed corpus
    all_user_tokens: List[str] = []
    text_corpus_blocks: List[str] = []

    for r in chat_records:
        u_msg = str(r.get("user_message") or "").strip()
        tool_name = str(r.get("tool_name") or "").strip()
        ans = str(r.get("xiaozhi_answer") or "").strip()
        combined = f"{u_msg} {tool_name} {ans}".lower()
        text_corpus_blocks.append(combined)
        if u_msg:
            all_user_tokens.extend(clean_and_tokenize(u_msg))

    token_set = set(all_user_tokens)
    full_text = " ".join(text_corpus_blocks)

    def count_cluster_score(terms: List[str]) -> float:
        score = 0.0
        for term in terms:
            term_clean = term.lower().strip()
            # Exact token match
            if term_clean in token_set:
                score += 2.0
            # Phrase in full text
            count = full_text.count(term_clean)
            if count > 0:
                score += min(count * 1.2, 10.0)
            # Substring / n-gram match for Indonesian morph
            if len(term_clean) >= 4 and any(term_clean in t for t in token_set):
                score += 0.8
        return score

    # 1. Personality Spectrum (Introvert vs Extrovert vs Ambivert)
    intro_score = count_cluster_score(PERSONALITY_CLUSTERS["introvert"])
    extro_score = count_cluster_score(PERSONALITY_CLUSTERS["extrovert"])

    # Base bias towards neutral ambivert if data is minimal
    base_weight = 4.0
    total_pers_weight = (intro_score + base_weight) + (extro_score + base_weight)
    intro_pct = int(round(((intro_score + base_weight) / total_pers_weight) * 100))
    extro_pct = 100 - intro_pct

    # Determine trait label
    if 45 <= intro_pct <= 55:
        primary_trait = "Ambivert Seimbang"
        dominant = "ambivert"
        personality_desc = "Memiliki keseimbangan alami antara kenyamanan menyendiri untuk fokus dan kemampuan berinteraksi secara sosial saat dibutuhkan."
    elif intro_pct > 55:
        if intro_pct >= 72:
            primary_trait = "Introvert Kuat"
        else:
            primary_trait = "Ambivert (Cenderung Introvert)"
        dominant = "introvert"
        personality_desc = "Cenderung lebih menikmati ketenangan, aktivitas fokus mandiri (seperti game atau belajar), dan berpikir mendalam sebelum bertindak."
    else:
        if extro_pct >= 72:
            primary_trait = "Extrovert Aktif"
        else:
            primary_trait = "Ambivert (Cenderung Extrovert)"
        dominant = "extrovert"
        personality_desc = "Menikmati interaksi sosial, senang berkolaborasi, dan cenderung mengekspresikan ide secara terbuka dan antusias."

    # 2. Hobbies & Interests Breakdown (1 - 100%)
    scored_hobbies: List[Dict[str, Any]] = []
    # Check manually saved hobbies in user_persona to boost
    saved_hobby_vals = [
        str(p.get("preference_value", "")).lower()
        for p in stored_personas
        if str(p.get("category", "")).lower() in ("minat_hobi", "hobi")
        or "hobi" in str(p.get("preference_key", "")).lower()
    ]

    for h in HOBBY_CLUSTERS:
        raw_score = count_cluster_score(h["terms"])
        # Boost if manually declared in stored persona
        for s_val in saved_hobby_vals:
            if any(term in s_val or s_val in term for term in h["terms"]):
                raw_score += 6.0
        if raw_score > 0.5:
            scored_hobbies.append({
                "id": h["id"],
                "name": h["name"],
                "icon": h["icon"],
                "raw_score": raw_score
            })

    # If no specific hobby detected yet, fallback to balanced popular ones
    if not scored_hobbies:
        scored_hobbies = [
            {"id": "game", "name": "Main Game & E-Sport", "icon": "🎮", "raw_score": 3.0},
            {"id": "bola", "name": "Sepak Bola & Olahraga", "icon": "⚽", "raw_score": 2.5},
            {"id": "musik", "name": "Musik & Hiburan", "icon": "🎵", "raw_score": 2.0},
            {"id": "catur", "name": "Catur & Strategi", "icon": "♟️", "raw_score": 1.5},
        ]

    scored_hobbies.sort(key=lambda x: x["raw_score"], reverse=True)
    top_hobbies = scored_hobbies[:5]
    sum_hobby_score = sum(item["raw_score"] for item in top_hobbies) or 1.0

    final_hobbies = []
    current_hobby_pct_sum = 0
    for idx, item in enumerate(top_hobbies):
        pct = max(5, int(round((item["raw_score"] / sum_hobby_score) * 100)))
        current_hobby_pct_sum += pct
        final_hobbies.append({
            "name": item["name"],
            "icon": item["icon"],
            "percent": pct
        })
    # Normalize hobby percentage sum to 100%
    if final_hobbies and current_hobby_pct_sum != 100:
        final_hobbies[0]["percent"] += (100 - current_hobby_pct_sum)

    # 3. Challenges & Pain Points (Masalah yang Dihadapi)
    scored_challenges: List[Dict[str, Any]] = []
    for c in CHALLENGE_CLUSTERS:
        raw_score = count_cluster_score(c["terms"])
        if raw_score > 0.5:
            scored_challenges.append({
                "id": c["id"],
                "name": c["name"],
                "icon": c["icon"],
                "raw_score": raw_score
            })

    if not scored_challenges:
        scored_challenges = [
            {"id": "skripsi", "name": "Tugas & Kesibukan Kuliah", "icon": "🎓", "raw_score": 3.0},
            {"id": "keuangan", "name": "Manajemen Keuangan", "icon": "💸", "raw_score": 2.5},
            {"id": "tidur", "name": "Waktu Istirahat & Begadang", "icon": "💤", "raw_score": 1.5}
        ]

    scored_challenges.sort(key=lambda x: x["raw_score"], reverse=True)
    top_challenges = scored_challenges[:4]
    sum_chall_score = sum(item["raw_score"] for item in top_challenges) or 1.0

    final_challenges = []
    current_chall_pct_sum = 0
    for item in top_challenges:
        pct = max(8, int(round((item["raw_score"] / sum_chall_score) * 100)))
        current_chall_pct_sum += pct
        if pct >= 40:
            level = "Tinggi (Perlu Solusi)"
            level_badge = "danger"
        elif pct >= 25:
            level = "Sedang (Perlu Perhatian)"
            level_badge = "warning"
        else:
            level = "Ringan (Terkendali)"
            level_badge = "neutral"

        final_challenges.append({
            "name": item["name"],
            "icon": item["icon"],
            "percent": pct,
            "level": level,
            "badge": level_badge
        })
    if final_challenges and current_chall_pct_sum != 100:
        final_challenges[0]["percent"] += (100 - current_chall_pct_sum)

    # 4. Daily Activities & Routines
    scored_activities: List[Dict[str, Any]] = []
    for a in ACTIVITY_CLUSTERS:
        raw_score = count_cluster_score(a["terms"])
        if raw_score > 0.4:
            scored_activities.append({
                "name": a["name"],
                "icon": a["icon"],
                "raw_score": raw_score
            })

    if not scored_activities:
        scored_activities = [
            {"name": "Belajar & Kuliah", "icon": "📖", "raw_score": 3.0},
            {"name": "Eksplorasi IoT & Smart Home", "icon": "⚡", "raw_score": 2.5},
            {"name": "Ibadah & Rutinitas Doa", "icon": "🕌", "raw_score": 2.0},
            {"name": "Istirahat & Me-Time", "icon": "🛋️", "raw_score": 1.5}
        ]

    scored_activities.sort(key=lambda x: x["raw_score"], reverse=True)
    top_acts = scored_activities[:4]
    sum_act_score = sum(item["raw_score"] for item in top_acts) or 1.0

    final_activities = []
    act_pct_sum = 0
    for item in top_acts:
        pct = max(10, int(round((item["raw_score"] / sum_act_score) * 100)))
        act_pct_sum += pct
        final_activities.append({
            "name": item["name"],
            "icon": item["icon"],
            "percent": pct
        })
    if final_activities and act_pct_sum != 100:
        final_activities[0]["percent"] += (100 - act_pct_sum)

    # 5. Preferences & Communication Style
    informal_count = sum(full_text.count(w) for w in ["aku", "gue", "nih", "dong", "sih", "banget", "pusing", "yuk"])
    formal_count = sum(full_text.count(w) for w in ["saya", "anda", "terima kasih", "mohon", "apakah"])
    casual_pct = 85 if informal_count >= formal_count else 45

    preferences = [
        {"name": "Gaya Bahasa Santai & Akrab", "percent": casual_pct, "icon": "💬"},
        {"name": "Penjelasan To-The-Point & Solutif", "percent": 80, "icon": "⚡"},
        {"name": "Diskusi Topik Teknologi & Kehidupan", "percent": 75, "icon": "💡"}
    ]

    confidence_level = "Tinggi (Akurat)" if total_chats >= 20 else ("Sedang" if total_chats >= 5 else "Data Awal")

    return {
        "total_chats_analyzed": total_chats,
        "confidence_level": confidence_level,
        "personality": {
            "primary_trait": primary_trait,
            "introvert_percent": intro_pct,
            "extrovert_percent": extro_pct,
            "dominant": dominant,
            "description": personality_desc
        },
        "hobbies": final_hobbies,
        "challenges": final_challenges,
        "activities": final_activities,
        "preferences": preferences
    }


def format_user_persona_for_prompt(
    persona_items: List[Dict[str, Any]],
    persona_analysis: Optional[Dict[str, Any]] = None
) -> str:
    """
    Formats the user's stored persona attributes and RAG vector insights into an AI prompt context block.
    """
    lines = ["Berikut adalah profil & preferensi personal pengguna yang tersimpan:"]

    # Injected RAG Vector Persona insights
    if persona_analysis:
        pers = persona_analysis.get("personality", {})
        hobbies = persona_analysis.get("hobbies", [])
        challenges = persona_analysis.get("challenges", [])
        activities = persona_analysis.get("activities", [])

        lines.append("\n[Analisis Karakter & Persona Otomatis (RAG & Vektor Semantik)]")
        if pers:
            lines.append(f"- Spektrum Kepribadian: {pers.get('primary_trait')} ({pers.get('introvert_percent')}% Introvert / {pers.get('extrovert_percent')}% Extrovert)")
            lines.append(f"  Catatan Perilaku: {pers.get('description')}")
        if hobbies:
            hobby_str = ", ".join(f"{h.get('name')} ({h.get('percent')}%)" for h in hobbies[:3])
            lines.append(f"- Minat & Hobi Teratas: {hobby_str}")
        if challenges:
            chall_str = ", ".join(f"{c.get('name')} ({c.get('percent')}%)" for c in challenges[:3])
            lines.append(f"- Tantangan / Masalah yang Sedang Dihadapi: {chall_str}")
        if activities:
            act_str = ", ".join(f"{a.get('name')} ({a.get('percent')}%)" for a in activities[:3])
            lines.append(f"- Pola Rutinitas Utama: {act_str}")

    if persona_items:
        grouped: Dict[str, List[str]] = {}
        for item in persona_items:
            cat = str(item.get("category") or "informasi_pribadi")
            label = VALID_PERSONA_CATEGORIES.get(cat, cat.replace("_", " ").title())
            key = str(item.get("preference_key") or "").strip()
            val = str(item.get("preference_value") or "").strip()
            if key and val:
                grouped.setdefault(label, []).append(f"- {key}: {val}")

        for cat_label, items in grouped.items():
            lines.append(f"\n[{cat_label}]")
            lines.extend(items)

    lines.append("\nInstruksi untuk Xiaozhi: Sesuaikan nada bicara, empati, dan saran Anda agar selaras dengan profil dan masalah yang sedang dihadapi user di atas.")
    return "\n".join(lines)

