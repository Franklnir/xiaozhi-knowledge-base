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
        "sholat", "doa", "puasa", "ibadah", "masjid", "gereja", "tuhan", "allah",
        "quran", "alkitab", "hadits", "pahala", "dosa", "syukur", "dzikir", "sedekah"
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
        combined_text = f"{user_msg} {ai_ans}"

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


def format_user_persona_for_prompt(persona_items: List[Dict[str, Any]]) -> str:
    """
    Formats the user's stored persona attributes into an AI prompt context block.
    """
    if not persona_items:
        return "Belum ada catatan preferensi personal khusus untuk pengguna ini."

    lines = ["Berikut adalah profil & preferensi personal pengguna yang tersimpan:"]
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

    lines.append("\nInstruksi: Sesuaikan nada bicara, saran, dan pendekatan Anda agar selaras dengan profil di atas.")
    return "\n".join(lines)
