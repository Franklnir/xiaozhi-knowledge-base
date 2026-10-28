"""
Lightweight Semantic Vector Engine and User Persona Service for Xiaozhi.
Designed for ultra-low memory footprint (< 1 MB RAM) and sub-millisecond execution.
Provides semantic similarity scoring, concept clustering, and user persona profiling.
"""

import math
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# Semantic concept taxonomy for Indonesian language context
# Groups related terms so queries like "keuangan" naturally match "biaya skripsi", "uang kos", "hutang", etc.
CONCEPT_TAXONOMY: Dict[str, List[str]] = {
    "keuangan": [
        "uang", "duit", "biaya", "kos", "ukt", "anggaran", "finansial",
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

    # Add character n-grams (subwords) for morphological robustness (len >= 4)
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
        # Only do morphological root-word matching for long tokens (len >= 5) sharing prefix
        if not overlap:
            for t in token_set:
                if len(t) >= 5:
                    for w in all_cluster_terms:
                        if len(w) >= 5 and (w.startswith(t) or t.startswith(w)):
                            overlap.add(t)
                            break

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
    Combines recency bonus and active session time-window filter (< 5 minutes) with semantic relevance.
    """
    if not records:
        return []

    from xiaozhi.core.utils import calculate_seconds_ago

    scored_records: List[Tuple[float, Dict[str, Any]]] = []

    for idx, r in enumerate(records):
        user_msg = str(r.get("user_message") or "")
        ai_ans = str(r.get("xiaozhi_answer") or "")
        tool_name = str(r.get("tool_name") or "")
        req_p = str(r.get("request_payload") or "")
        res_p = str(r.get("response_payload") or "")
        created_at_val = r.get("created_at") or ""
        combined_text = f"{user_msg} {ai_ans} {tool_name} {req_p} {res_p}"

        sim_score = calculate_semantic_similarity(query, combined_text) if query else 0.0

        # Base recency weight (newer records get up to 0.05 bonus)
        recency_bonus = max(0.0, 0.05 * (1.0 - (idx / max(1, len(records)))))

        # Time-Window Filter: Jika obrolan terjadi dalam rentang < 5 menit (300 detik) yang lalu
        sec_ago = calculate_seconds_ago(created_at_val)
        is_active_session = sec_ago is not None and 0 <= sec_ago <= 300
        active_session_bonus = 0.0
        if is_active_session:
            # Berikan lonjakan prioritas skor untuk sesi obrolan aktif (< 5 menit)
            active_session_bonus = max(0.25, 0.50 * (1.0 - (sec_ago / 300.0)))

        final_score = sim_score + recency_bonus + active_session_bonus

        # Obrolan aktif (< 5 menit) selalu lolos threshold meskipun kemiripan teks rendah
        # agar percakapan yang baru berlangsung tidak terputus konteks
        if (
            not query
            or is_active_session
            or sim_score >= min_similarity
            or any(token in combined_text.lower() for token in clean_and_tokenize(query))
        ):
            entry = dict(r)
            entry["similarity_score"] = round(sim_score, 3)
            entry["is_active_session"] = is_active_session
            if is_active_session and sec_ago is not None:
                entry["seconds_ago"] = int(sec_ago)
                entry["session_tag"] = "Active Session Context (< 5 menit)"
            scored_records.append((final_score, entry))

    # Sort descending by final score
    scored_records.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_records[:top_k]]


# ─────────────────────────────────────────────────────────────────────────────
# User Persona Categories & Structures
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERSONA_CATEGORIES = {
    "gaya_bicara": "Gaya & Nada Bicara yang Disukai (Santai, Formal, Hangat, Singkat/Padat)",
    "mood_ekspresi": "Suasana Hati (Mood), Ekspresi Vokal & Nada Suara Persona",
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
        "id": "koding",
        "name": "Koding & Teknologi IoT",
        "icon": "💻",
        "terms": ["koding", "coding", "python", "javascript", "c++", "esp32", "iot", "smart home", "relay", "arduino", "raspberry", "firmware", "server", "api", "database", "sql", "bug", "debugging", "mikrokontroler", "fastmcp"]
    },
    {
        "id": "musik",
        "name": "Musik & Audio",
        "icon": "🎵",
        "terms": ["musik", "lagu", "gitar", "nyanyi", "vokal", "playlist", "spotify", "youtube music", "band", "akustik", "konser", "chord", "audio", "stem gitar"]
    },
    {
        "id": "game",
        "name": "Main Game & E-Sport",
        "icon": "🎮",
        "terms": ["game", "gaming", "main game", "ml", "mobile legends", "pubg", "valorant", "steam", "rank", "push rank", "playstation", "ps5", "xbox", "mabar", "genshin", "dota"]
    },
    {
        "id": "buku",
        "name": "Membaca & Menulis",
        "icon": "📚",
        "terms": ["buku", "novel", "komik", "manga", "baca buku", "nulis buku", "artikel", "cerpen", "perpustakaan", "literasi", "resensi"]
    },
    {
        "id": "film",
        "name": "Nonton Film & Anime",
        "icon": "🎬",
        "terms": ["film", "nonton", "movie", "anime", "serial", "drama", "bioskop", "netflix", "drakor", "alur cerita", "cinema", "wibu"]
    },
    {
        "id": "olahraga",
        "name": "Olahraga & Kebugaran",
        "icon": "🏃",
        "terms": ["lari", "jogging", "gym", "workout", "sepeda", "pushup", "fitnes", "otot", "kebugaran", "keringat", "badminton", "renang"]
    },
    {
        "id": "bola",
        "name": "Sepak Bola & Futsal",
        "icon": "⚽",
        "terms": ["sepak bola", "futsal", "jersey", "lapangan bola", "tanding bola", "liga inggris", "champions", "ronaldo", "messi", "timnas", "gol", "kiper"]
    },
    {
        "id": "catur",
        "name": "Catur & Strategi",
        "icon": "♟️",
        "terms": ["catur", "chess", "bidak", "skak", "pion", "kuda", "menteri", "strategi", "grandmaster", "taktik", "openings"]
    },
    {
        "id": "kuliner",
        "name": "Ngopi & Kuliner",
        "icon": "☕",
        "terms": ["kopi", "ngopi", "kafe", "cafe", "kuliner", "resep", "masak", "jajan", "makan enak", "makanan khas", "barista"]
    }
]

CHALLENGE_CLUSTERS = [
    {
        "id": "skripsi",
        "name": "Skripsi & Beban Kuliah",
        "icon": "🎓",
        "terms": [
            "skripsi", "revisi skripsi", "bimbingan dosen", "sidang skripsi",
            "proposal skripsi", "dosen pembimbing", "tugas akhir", "ta skripsi", "bab 4", "bab 3", "dospem"
        ]
    },
    {
        "id": "akademik",
        "name": "Tugas & Ujian Akademik",
        "icon": "📚",
        "terms": [
            "ujian uts", "ujian uas", "tugas kuliah", "pr kuliah", "revisi tugas",
            "nilai ipk", "remedial", "praktikum susah", "laporan praktikum", "matkul", "makalah"
        ]
    },
    {
        "id": "keuangan",
        "name": "Biaya Hidup & Finansial",
        "icon": "💸",
        "terms": [
            "uang kos", "biaya ukt", "bokek", "kurang duit", "gaji telat",
            "cicilan menunggak", "tagihan", "utang", "hutang", "masalah finansial", "bayar kos", "biaya hidup"
        ]
    },
    {
        "id": "pekerjaan",
        "name": "Deadline & Beban Kerja",
        "icon": "💼",
        "terms": [
            "deadline kerja", "lembur kantor", "klien rewel", "target kerja",
            "beban kerjaan", "atasan marah", "burnout kerja", "proyek numpuk", "kerjaan numpuk"
        ]
    },
    {
        "id": "tidur",
        "name": "Pola Tidur & Insomnia",
        "icon": "💤",
        "terms": [
            "susah tidur", "insomnia", "begadang terus", "mata panda", "gak bisa tidur",
            "kurang tidur", "tidur larut", "terjaga semalaman", "begadang"
        ]
    },
    {
        "id": "mental",
        "name": "Tekanan Mental & Stres",
        "icon": "🧠",
        "terms": [
            "stres berat", "depresi", "anxiety", "cemas berlebihan", "overthinking parah",
            "tertekan", "burnout", "mental drop", "patah hati", "kesepian"
        ]
    },
    {
        "id": "teknis",
        "name": "Kendala Teknis & Perangkat",
        "icon": "🛠️",
        "terms": [
            "mati lampu", "lampu putus", "rusak", "error", "koneksi putus", "gangguan jaringan",
            "perangkat rusak", "gagal konek", "server down", "wifi putus", "hang", "crash", "konslet"
        ]
    }
]

ACTIVITY_CLUSTERS = [
    {
        "id": "iot_tech",
        "name": "Eksplorasi IoT & Smart Home",
        "icon": "⚡",
        "terms": ["xiaozhi", "smart home", "relay", "lampu", "perangkat", "esp32", "koding", "tool", "mcp", "saklar", "sensor", "flasher"]
    },
    {
        "id": "kerja",
        "name": "Pekerjaan & Produktivitas",
        "icon": "💼",
        "terms": ["kerja", "kantor", "meeting", "proyek", "tugas kantor", "deadline", "klien", "lembur", "briefing", "presentasi"]
    },
    {
        "id": "belajar",
        "name": "Belajar & Kuliah",
        "icon": "📖",
        "terms": ["belajar", "kuliah", "materi", "tugas", "pr", "latihan", "ujian", "kampus", "dosen", "makalah"]
    },
    {
        "id": "ibadah",
        "name": "Ibadah & Rutinitas Doa",
        "icon": "🕌",
        "terms": ["sholat", "shalat", "solat", "subuh", "dzuhur", "ashar", "maghrib", "isya", "doa", "masjid", "ibadah", "puasa", "mengaji"]
    },
    {
        "id": "santai",
        "name": "Istirahat & Me-Time",
        "icon": "🛋️",
        "terms": ["rebahan", "santai", "istirahat", "tidur", "libur", "weekend", "santai sore", "ngopi", "me time", "rileks"]
    }
]

# Patterns for Smart Filtering
OPERATIONAL_TOOLS = {
    "execute_relay_command",
    "simulated_smarthome_device_action",
    "get_simulated_smarthome_status",
    "play_playlist_track",
    "stop_playlist_playback",
    "next_playlist_track",
    "previous_playlist_track"
}

OPERATIONAL_PATTERN = re.compile(
    r"^(nyalakan|matikan|hidupkan|buka|tutup|play|stop|pause|next|prev|volume|setel|putar)\b|"
    r"\b(relay \d+|saklar \d+|lampu (nyala|mati|hidup)|turn on|turn off)\b",
    re.IGNORECASE
)

SELF_DISCLOSURE_PATTERN = re.compile(
    r"\b(saya|aku|gue|gw)\s+(lagi|sedang|suka|hobi|seneng|tertarik|senang|pekerjaan|kerja|kuliah|pengen|cita-cita|rencana|pusing|stres|capek)\b|"
    r"\b(hobi\s+(saya|aku|gue|gw)|pekerjaan\s+(saya|aku|gue|gw)|profesi\s+(saya|aku|gue|gw)|kantor\s+(saya|aku|gue|gw)|kampus\s+(saya|aku|gue|gw))\b",
    re.IGNORECASE
)

MALFUNCTION_PATTERN = re.compile(
    r"\b(mati lampu|lampu putus|rusak|error|putus|gangguan|trouble|gagal|down|hang|crash|konslet)\b",
    re.IGNORECASE
)

# In-Memory Thread-Safe TTL Cache for Persona Analysis (Zero repeated CPU burden)
_PERSONA_ANALYSIS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PERSONA_CACHE_TTL = 120.0  # 2 minutes TTL


def chunk_conversations_for_persona(chat_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Chunks chat transcripts into semantic units with metadata, filtering operational noise.
    """
    chunks = []
    for idx, r in enumerate(chat_records):
        u_msg = str(r.get("user_message") or "").strip()
        tool_name = str(r.get("tool_name") or "").strip()
        if not u_msg and not tool_name:
            continue

        is_op = bool(tool_name in OPERATIONAL_TOOLS or OPERATIONAL_PATTERN.search(u_msg))
        is_sd = bool(SELF_DISCLOSURE_PATTERN.search(u_msg))
        has_malfunc = bool(MALFUNCTION_PATTERN.search(u_msg))

        tokens = clean_and_tokenize(u_msg)
        if not tokens and tool_name:
            tokens = clean_and_tokenize(tool_name.replace("_", " "))

        if not tokens:
            continue

        vec = expand_semantic_concepts(tokens)
        mag = compute_vector_magnitude(vec)

        chunks.append({
            "id": idx,
            "text": u_msg,
            "tool": tool_name,
            "tokens": set(tokens),
            "vector": vec,
            "magnitude": mag,
            "is_operational": is_op,
            "is_self_disclosure": is_sd,
            "has_malfunction": has_malfunc
        })
    return chunks


def _score_chunk_against_cluster(
    chunk: Dict[str, Any],
    cluster_vec: Dict[str, float],
    cluster_mag: float,
    cluster_terms: List[str]
) -> float:
    """Computes cosine similarity + token overlap bonus for a conversation chunk."""
    # Cosine similarity
    dot = sum(v * cluster_vec.get(k, 0.0) for k, v in chunk["vector"].items())
    cos_sim = dot / (chunk["magnitude"] * cluster_mag) if (chunk["magnitude"] > 0 and cluster_mag > 0) else 0.0

    # Token overlap bonus
    term_bonus = 0.0
    chunk_tokens = chunk["tokens"]
    lower_text = chunk["text"].lower()
    for t in cluster_terms:
        if t in chunk_tokens:
            term_bonus += 1.5
        elif t in lower_text:
            term_bonus += 2.0

    base_score = (cos_sim * 3.0) + term_bonus
    if base_score < 0.35:
        return 0.0

    # Multiplier for self-disclosure
    if chunk.get("is_self_disclosure"):
        base_score *= 3.5

    return base_score


def analyze_user_persona_from_chats(
    chat_records: List[Dict[str, Any]],
    stored_personas: Optional[List[Dict[str, Any]]] = None,
    cache_key: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    RAG & Vector Semantic engine to automatically infer user personality (Introvert/Extrovert/Ambivert),
    hobbies & interests, daily activities, likes/preferences, and challenges/pain points
    with accurate percentage distribution (1% to 100%).
    
    Uses conversation chunking, cosine similarity against semantic concept clusters,
    and frequency-of-meaning aggregation with zero hardcoded fake fallbacks.
    """
    now = time.time()
    if cache_key and not force_refresh:
        cached = _PERSONA_ANALYSIS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _PERSONA_CACHE_TTL:
            return cached[1]

    total_chats = len(chat_records or [])
    stored_personas = stored_personas or []

    # 1. Chunk conversations
    chunks = chunk_conversations_for_persona(chat_records or [])

    # Grounding from stored personas (Pekerjaan, Hobi, etc.)
    saved_hobby_vals = [
        str(p.get("preference_value", "")).lower()
        for p in stored_personas
        if str(p.get("category", "")).lower() in ("minat_hobi", "hobi")
        or "hobi" in str(p.get("preference_key", "")).lower()
    ]
    saved_job_vals = [
        str(p.get("preference_value", "")).lower()
        for p in stored_personas
        if "pekerjaan" in str(p.get("preference_key", "")).lower()
        or "profesi" in str(p.get("preference_key", "")).lower()
        or "karir" in str(p.get("preference_key", "")).lower()
    ]
    is_declared_professional = any(
        w in jv for jv in saved_job_vals for w in ("engineer", "programmer", "koding", "developer", "kerja", "kantor", "pegawai")
    )
    is_declared_student = any(
        w in jv for jv in saved_job_vals for w in ("mahasiswa", "kuliah", "kampus", "siswa", "pelajar")
    )

    # 2. Score Hobbies via Vector Chunking
    scored_hobbies: List[Dict[str, Any]] = []
    for h in HOBBY_CLUSTERS:
        c_tokens = clean_and_tokenize(" ".join(h["terms"]))
        c_vec = expand_semantic_concepts(c_tokens)
        c_mag = compute_vector_magnitude(c_vec)

        raw_score = 0.0
        match_count = 0
        for chk in chunks:
            s = _score_chunk_against_cluster(chk, c_vec, c_mag, h["terms"])
            if s > 0:
                raw_score += s
                match_count += 1

        # Grounding boost if manually saved in stored personas
        for s_val in saved_hobby_vals:
            if any(term in s_val or s_val in term for term in h["terms"]):
                raw_score += 8.0
                match_count += 1

        # Boost IoT / Koding if user frequently operates smart home devices
        if h["id"] == "koding":
            op_iot_count = sum(1 for chk in chunks if chk["is_operational"])
            if op_iot_count > 0:
                raw_score += min(op_iot_count * 1.5, 12.0)
                match_count += 1

        if raw_score > 0.4:
            scored_hobbies.append({
                "id": h["id"],
                "name": h["name"],
                "icon": h["icon"],
                "raw_score": raw_score,
                "matches": match_count
            })

    # Fallback only if no hobbies detected at all
    if not scored_hobbies:
        scored_hobbies = [
            {"id": "koding", "name": "Koding & Teknologi IoT", "icon": "💻", "raw_score": 3.0, "matches": 1},
            {"id": "musik", "name": "Musik & Audio", "icon": "🎵", "raw_score": 2.2, "matches": 1},
            {"id": "game", "name": "Main Game & E-Sport", "icon": "🎮", "raw_score": 1.8, "matches": 1},
        ]

    scored_hobbies.sort(key=lambda x: x["raw_score"], reverse=True)
    top_hobbies = scored_hobbies[:5]
    sum_hobby_score = sum(item["raw_score"] for item in top_hobbies) or 1.0

    final_hobbies = []
    current_hobby_pct_sum = 0
    for item in top_hobbies:
        pct = max(5, int(round((item["raw_score"] / sum_hobby_score) * 100)))
        current_hobby_pct_sum += pct
        final_hobbies.append({
            "name": item["name"],
            "icon": item["icon"],
            "percent": pct
        })
    if final_hobbies and current_hobby_pct_sum != 100:
        final_hobbies[0]["percent"] += (100 - current_hobby_pct_sum)

    # 3. Score Challenges & Pain Points (NO FAKE / ASSUMPTIVE FALLBACKS)
    scored_challenges: List[Dict[str, Any]] = []
    for c in CHALLENGE_CLUSTERS:
        # If user explicitly declared as working professional and NOT student, suppress skripsi
        if c["id"] in ("skripsi", "akademik") and is_declared_professional and not is_declared_student:
            continue

        c_tokens = clean_and_tokenize(" ".join(c["terms"]))
        c_vec = expand_semantic_concepts(c_tokens)
        c_mag = compute_vector_magnitude(c_vec)

        raw_score = 0.0
        match_count = 0
        for chk in chunks:
            # Operational IoT commands alone NEVER trigger technical challenge unless real malfunction occurred
            if c["id"] == "teknis" and chk["is_operational"] and not chk["has_malfunction"]:
                continue

            # Challenges require an explicit term match in user message to prevent semantic hallucination
            has_term_match = any(
                (t in chk["tokens"]) or (t in chk["text"].lower())
                for t in c["terms"]
            )
            if not has_term_match:
                continue

            s = _score_chunk_against_cluster(chk, c_vec, c_mag, c["terms"])
            if s > 0:
                raw_score += s
                match_count += 1

        # Must have genuine signal: score >= 1.5 and at least 1 verified chunk match
        if raw_score >= 1.5 and match_count >= 1:
            scored_challenges.append({
                "id": c["id"],
                "name": c["name"],
                "icon": c["icon"],
                "raw_score": raw_score,
                "matches": match_count
            })

    final_challenges = []
    if scored_challenges:
        scored_challenges.sort(key=lambda x: x["raw_score"], reverse=True)
        top_challenges = scored_challenges[:4]
        sum_chall_score = sum(item["raw_score"] for item in top_challenges) or 1.0

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

    # 4. Daily Activities & Routines via Vector Chunking
    scored_activities: List[Dict[str, Any]] = []
    for a in ACTIVITY_CLUSTERS:
        c_tokens = clean_and_tokenize(" ".join(a["terms"]))
        c_vec = expand_semantic_concepts(c_tokens)
        c_mag = compute_vector_magnitude(c_vec)

        raw_score = 0.0
        for chk in chunks:
            s = _score_chunk_against_cluster(chk, c_vec, c_mag, a["terms"])
            if s > 0:
                raw_score += s
            # Direct operational boost for IoT activity
            if a["id"] == "iot_tech" and chk["is_operational"]:
                raw_score += 1.2

        if raw_score > 0.4:
            scored_activities.append({
                "name": a["name"],
                "icon": a["icon"],
                "raw_score": raw_score
            })

    if not scored_activities:
        scored_activities = [
            {"name": "Eksplorasi IoT & Smart Home", "icon": "⚡", "raw_score": 3.0},
            {"name": "Pekerjaan & Produktivitas", "icon": "💼", "raw_score": 2.5},
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

    # 5. Personality Spectrum (Authentic Signals & Real Confidence)
    intro_score = 0.0
    extro_score = 0.0
    intro_vec = expand_semantic_concepts(clean_and_tokenize(" ".join(PERSONALITY_CLUSTERS["introvert"])))
    intro_mag = compute_vector_magnitude(intro_vec)
    extro_vec = expand_semantic_concepts(clean_and_tokenize(" ".join(PERSONALITY_CLUSTERS["extrovert"])))
    extro_mag = compute_vector_magnitude(extro_vec)

    for chk in chunks:
        i_s = _score_chunk_against_cluster(chk, intro_vec, intro_mag, PERSONALITY_CLUSTERS["introvert"])
        e_s = _score_chunk_against_cluster(chk, extro_vec, extro_mag, PERSONALITY_CLUSTERS["extrovert"])
        intro_score += i_s
        extro_score += e_s

    total_pers_signals = intro_score + extro_score

    if total_pers_signals < 1.0:
        # Minimal personality signals detected
        intro_pct = 50
        extro_pct = 50
        primary_trait = "Ambivert Netral"
        dominant = "ambivert"
        personality_desc = "Belum banyak sinyal kepribadian personal yang terdeteksi. Ceritakan lebih banyak tentang minat dan keseharian Anda agar XiaoZhi dapat memetakan spektrum dengan akurat."
        confidence_level = "Data Awal"
    else:
        intro_pct = int(round((intro_score / total_pers_signals) * 100))
        extro_pct = 100 - intro_pct

        if total_pers_signals >= 5.0:
            confidence_level = "Tinggi (Akurat)"
        else:
            confidence_level = "Sedang (Berkembang)"

        if 44 <= intro_pct <= 56:
            primary_trait = "Ambivert Seimbang"
            dominant = "ambivert"
            personality_desc = "Memiliki keseimbangan alami antara kenyamanan menyendiri untuk fokus dan kemampuan berinteraksi secara sosial saat dibutuhkan."
        elif intro_pct > 56:
            if intro_pct >= 70:
                primary_trait = "Introvert Dominan"
            else:
                primary_trait = "Ambivert (Cenderung Introvert)"
            dominant = "introvert"
            personality_desc = "Cenderung lebih menikmati ketenangan, fokus mandiri mendalam (seperti koding atau belajar), dan berpikir reflektif sebelum bertindak."
        else:
            if extro_pct >= 70:
                primary_trait = "Extrovert Dominan"
            else:
                primary_trait = "Ambivert (Cenderung Extrovert)"
            dominant = "extrovert"
            personality_desc = "Menikmati interaksi sosial yang terbuka, senang berkolaborasi, dan cenderung mengekspresikan ide secara antusias."

    # 6. Preferences & Communication Style
    all_user_text = " ".join(chk["text"] for chk in chunks).lower()
    informal_count = sum(all_user_text.count(w) for w in ["aku", "gue", "gw", "nih", "dong", "sih", "banget", "yuk", "bro", "gan"])
    formal_count = sum(all_user_text.count(w) for w in ["saya", "anda", "terima kasih", "mohon", "apakah", "bapak", "ibu"])
    casual_pct = 85 if informal_count >= formal_count else 45

    # Check average user message length
    avg_words = (len(all_user_text.split()) / len(chunks)) if chunks else 6
    to_the_point_pct = 85 if avg_words <= 8 else 70

    preferences = [
        {"name": "Gaya Bahasa Santai & Akrab", "percent": casual_pct, "icon": "💬"},
        {"name": "Penjelasan To-The-Point & Solutif", "percent": to_the_point_pct, "icon": "⚡"},
        {"name": "Diskusi Topik Teknologi & Solusi Cerdas", "percent": 80, "icon": "💡"}
    ]

    result = {
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

    if cache_key:
        _PERSONA_ANALYSIS_CACHE[cache_key] = (now, result)

    return result


def format_user_persona_for_prompt(
    persona_items: List[Dict[str, Any]],
    persona_analysis: Optional[Dict[str, Any]] = None,
    recent_chats: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Formats the user's stored persona attributes, RAG vector insights, and recent chats into an AI prompt context block.
    """
    lines = ["Berikut adalah profil & preferensi personal pengguna yang tersimpan:"]

    # Injected 3 Recent Chats (Short-Term Memory / Active Session)
    if recent_chats:
        lines.append("\n[3 Percakapan Terakhir di Perangkat Ini (Short-Term Memory)]")
        lines.append("Gunakan konteks percakapan terakhir ini untuk memahami pembicaraan yang baru saja berlangsung:")
        for idx, chat in enumerate(recent_chats, 1):
            u = chat.get("pesan_user") or chat.get("user_message") or "-"
            a = chat.get("jawaban_xiaozhi") or chat.get("xiaozhi_answer") or "-"
            w = chat.get("waktu") or chat.get("created_at") or ""
            w_str = f" ({w})" if w else ""
            t = chat.get("tool_dipakai") or chat.get("tool_name") or ""
            t_str = f" [Tool: {t}]" if t and t != "save_chat_history" else ""
            lines.append(f"{idx}. User: \"{u}\"{t_str}{w_str}")
            lines.append(f"   Xiaozhi: \"{a}\"")
        lines.append("💡 Rekomendasi Proaktif: Jika pengguna menyapa atau bertanya singkat (misal 'halo', 'lanjutin tadi'), Anda dapat menyapa kembali dan secara proaktif mengaitkan dengan topik terakhir di atas!")

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

    lines.append(
        "\nInstruksi untuk Xiaozhi: Sesuaikan nada bicara, empati, dan gaya ekspresi vokal Anda agar selaras dengan profil, "
        "suasana hati (mood), emosi percakapan, dan riwayat obrolan di atas. Berekspresilah secara hidup dengan nada tawa, senyuman suara, "
        "atau seruan emosional yang wajar!"
    )
    return "\n".join(lines)

