"""Language Tutor Service for XiaoZhi.
Transforms XiaoZhi into an interactive, pedagogical, and encouraging personal language tutor.

Features:
1. Automated Vocabulary Flashcards (DB persistence with phonetics, nuances, and practical examples).
2. Interactive Vocabulary & Sentence Drills (Interactive voice quiz from stored materials or curated essentials).
3. Situational Conversation Roleplay (Cafe, Airport, Hotel, Directions, Job Interview, Shopping) with
   Feedback-First grammar corrections and real-world response prompts.
4. Grammar Doctor & Cultural Nuance Explainer (Formulas, Indonesian common pitfalls, and polite/informal nuances).
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.language_tutor")

# ── 1. Curated Essential Vocabulary Bank for Zero-Data Starter ──────────────

STARTER_VOCABULARY_BANK: Dict[str, List[Dict[str, str]]] = {
    "inggris": [
        {
            "word": "Accomplish",
            "translation": "Menyelesaikan / Mencapai keberhasilan",
            "pronunciation": "uh-KAHM-plish",
            "part_of_speech": "Verb (Kata Kerja)",
            "level": "B1",
            "example": "We can accomplish this project together before Monday.",
            "example_translation": "Kita bisa menyelesaikan proyek ini bersama sebelum hari Senin.",
            "notes": "Sering digunakan dalam konteks profesional atau pencapaian target kerja.",
        },
        {
            "word": "Convenient",
            "translation": "Praktis / Nyaman / Mudah dijangkau",
            "pronunciation": "kuhn-VEE-nyuhnt",
            "part_of_speech": "Adjective (Kata Sifat)",
            "level": "A2",
            "example": "Living near the train station is very convenient.",
            "example_translation": "Tinggal di dekat stasiun kereta sangatlah praktis.",
            "notes": "Digunakan untuk hal yang memudahkan aktivitas atau menghemat waktu.",
        },
        {
            "word": "Appreciate",
            "translation": "Menghargai / Sangat berterima kasih",
            "pronunciation": "uh-PREE-shee-ayt",
            "part_of_speech": "Verb (Kata Kerja)",
            "level": "A2",
            "example": "I really appreciate your kind assistance today.",
            "example_translation": "Saya sangat menghargai bantuan baik Anda hari ini.",
            "notes": "Ungkapan sopan yang lebih elegan dibanding hanya mengucapkan 'thank you'.",
        },
        {
            "word": "Hesitate",
            "translation": "Ragu-ragu / Bimbang",
            "pronunciation": "HEZ-i-tayt",
            "part_of_speech": "Verb (Kata Kerja)",
            "level": "B1",
            "example": "Do not hesitate to ask me if you have any questions.",
            "example_translation": "Jangan ragu untuk bertanya jika Anda punya pertanyaan apa pun.",
            "notes": "Frasa umum dalam email dan percakapan formal: 'Don't hesitate to...'.",
        },
        {
            "word": "Opportunity",
            "translation": "Kesempatan emas / Peluang",
            "pronunciation": "op-er-TOO-ni-tee",
            "part_of_speech": "Noun (Kata Benda)",
            "level": "A2",
            "example": "This internship is a great opportunity to learn edge AI.",
            "example_translation": "Magang ini adalah peluang bagus untuk mempelajari AI pada mikrokontroler.",
            "notes": "Berbeda dengan 'chance' yang lebih bermakna kebetulan, opportunity menekankan peluang yang menguntungkan.",
        },
    ],
    "jepang": [
        {
            "word": "Okaerinasai (おかえりなさい)",
            "translation": "Selamat datang kembali di rumah",
            "pronunciation": "O-ka-e-ri-na-sai",
            "part_of_speech": "Ungkapan Salam (Aisatsu)",
            "level": "N5 (Pemula)",
            "example": "Okaerinasai! Kyou no shigoto wa dou deshita ka?",
            "example_translation": "Selamat pulang! Bagaimana pekerjaanmu hari ini?",
            "notes": "Dibalas oleh orang yang baru tiba dengan ucapan 'Tadaima' (Saya pulang).",
        },
        {
            "word": "Otsukaresama deshita (お疲れ様でした)",
            "translation": "Terima kasih atas kerja kerasnya hari ini",
            "pronunciation": "O-tsu-ka-re-sa-ma de-shi-ta",
            "part_of_speech": "Ungkapan Salam Sopan",
            "level": "N5 (Pemula)",
            "example": "Kyou mo ichinichi, otsukaresama deshita!",
            "example_translation": "Terima kasih atas kerja kerasnya seharian ini!",
            "notes": "Ungkapan wajib dalam budaya kerja Jepang saat jam kerja selesai atau tugas tuntas.",
        },
        {
            "word": "Daijoubu (大丈夫)",
            "translation": "Tidak apa-apa / Aman / Baik-baik saja",
            "pronunciation": "Dai-jou-bu",
            "part_of_speech": "Na-Adjective",
            "level": "N5 (Pemula)",
            "example": "Daijoubu desu ka? Tetsudaimashou ka?",
            "example_translation": "Apakah kamu baik-baik saja? Mau saya bantu?",
            "notes": "Sangat fleksibel; bisa untuk menanyakan kabar, menenangkan orang, atau menolak tawaran dengan sopan.",
        },
    ],
    "mandarin": [
        {
            "word": "Xīn kǔ le (辛苦了)",
            "translation": "Terima kasih atas kerja keras dan usahanya",
            "pronunciation": "Shin koo luh (Nada: 1 - 3 - netral)",
            "part_of_speech": "Ungkapan Sopan",
            "level": "HSK 2",
            "example": "Dàjiā dōu xīn kǔ le, zǎodiǎn xiūxi ba!",
            "example_translation": "Semuanya sudah bekerja keras, istirahatlah lebih awal ya!",
            "notes": "Diucapkan kepada rekan kerja, kurir, atau siapa saja yang telah membantu kita bersusah payah.",
        },
        {
            "word": "Méi guānxi (没关系)",
            "translation": "Tidak masalah / Tidak apa-apa",
            "pronunciation": "May gwan-shee (Nada: 2 - 1 - netral)",
            "part_of_speech": "Ungkapan Balasan",
            "level": "HSK 1",
            "example": "Duìbuqǐ! — Méi guānxi, wǒ yě gāng dào.",
            "example_translation": "Maaf! — Tidak apa-apa, saya juga baru saja sampai.",
            "notes": "Balasan standar ketika seseorang mengucapkan 'Duìbuqǐ' (Maaf).",
        },
    ],
}

# ── 2. Situational Roleplay Scenarios Data ──────────────────────────────────

ROLEPLAY_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "kafe": {
        "title": "Memesan Kopi & Makanan di Kafe Modern",
        "roles": {"ai": "Barista Kafe Ramah", "user": "Pelanggan"},
        "opening_dialogue": {
            "inggris": "Hi there! Welcome to Brew & Co. What can I get started for you today?",
            "jepang": "Irasshaimase! Go-chuumon wa ikaga desu ka?",
            "mandarin": "Nǐ hǎo! Huānyíng guānglín. Jīntiān xiǎng hē diǎn shénme?",
        },
        "opening_translation": {
            "inggris": "Halo! Selamat datang di Brew & Co. Mau pesan apa hari ini?",
            "jepang": "Selamat datang! Anda ingin memesan apa?",
            "mandarin": "Halo! Selamat datang. Hari ini ingin minum apa?",
        },
        "response_suggestions": {
            "inggris": [
                "Could I have an iced latte with oat milk, please?",
                "Do you have any recommendations for iced coffee?",
                "Just an espresso and a croissant to go, please.",
            ],
            "jepang": [
                "Aisu kafe rate wo hitotsu onegaishimasu.",
                "Osusume no koohii wa arimasu ka?",
            ],
            "mandarin": [
                "Qǐng gěi wǒ yī bēi bīng nátiě (Minta segelas es latte).",
                "Yǒu shénme tuījiàn de kāfēi ma? (Ada rekomendasi kopi apa?)",
            ],
        },
    },
    "bandara": {
        "title": "Check-in Tiket & Bagasi di Bandara Internasional",
        "roles": {"ai": "Petugas Counter Maskapai", "user": "Penumpang Pesawat"},
        "opening_dialogue": {
            "inggris": "Good morning! Welcome to SkyWings airlines. May I please see your passport and booking reference?",
            "jepang": "Ohayou gozaimasu. Pasupooto to koukuuken wo misete itadakemasu ka?",
            "mandarin": "Zǎoshang hǎo! Qǐng chūshì nǐ de hùzhào hé jīpiào.",
        },
        "opening_translation": {
            "inggris": "Selamat pagi! Selamat datang di SkyWings. Boleh saya lihat paspor dan kode booking Anda?",
            "jepang": "Selamat pagi. Boleh saya lihat paspor dan tiket pesawat Anda?",
            "mandarin": "Selamat pagi! Tolong tunjukkan paspor dan tiket pesawat Anda.",
        },
        "response_suggestions": {
            "inggris": [
                "Here is my passport and reservation code.",
                "Could I please get a window seat if available?",
                "I have one suitcase to check in, and one carry-on bag.",
            ],
            "jepang": [
                "Kochira ga pasupooto desu.",
                "Madowari no seki wa aite imasu ka? (Apakah ada kursi dekat jendela?)",
            ],
            "mandarin": [
                "Zhè shì wǒ de hùzhào (Ini paspor saya).",
                "Yǒu kào chuāng de zuòwèi ma? (Ada kursi dekat jendela?)",
            ],
        },
    },
    "hotel": {
        "title": "Check-in & Tanya Fasilitas di Resepsionis Hotel",
        "roles": {"ai": "Resepsionis Hotel", "user": "Tamu Hotel"},
        "opening_dialogue": {
            "inggris": "Good afternoon, sir/ma'am! Welcome to Grand Palace Hotel. Are you checking in today?",
            "jepang": "Konnichiwa! Guran Paresu Hoteru e youkoso. Go-shukuhaku no go-yoyaku desu ka?",
            "mandarin": "Xiàwǔ hǎo! Huānyíng rùzhù Jiǔdiàn. Qǐngwèn nǐ yǒu yùdìng ma?",
        },
        "opening_translation": {
            "inggris": "Selamat siang! Selamat datang di Grand Palace Hotel. Apakah Anda ingin check-in hari ini?",
            "jepang": "Selamat siang! Selamat datang. Apakah Anda sudah memiliki reservasi penginapan?",
            "mandarin": "Selamat siang! Selamat datang di hotel. Apakah Anda memiliki reservasi?",
        },
        "response_suggestions": {
            "inggris": [
                "Yes, I have a reservation under the name Frank.",
                "What time is breakfast served in the morning?",
                "Could you give me the Wi-Fi password, please?",
            ],
            "jepang": [
                "Hai, yoyaku shita Frank desu.",
                "Asagohan wa nanji kara desu ka? (Sarapan mulai jam berapa?)",
            ],
            "mandarin": [
                "Shì de, wǒ yǒu yùdìng (Ya, saya punya reservasi).",
                "Zǎocān jǐ diǎn kāishǐ? (Sarapan mulai jam berapa?)",
            ],
        },
    },
    "wawancara": {
        "title": "Simulasi Wawancara Kerja (Job Interview Practice)",
        "roles": {"ai": "Interviewer / HR Manager", "user": "Kandidat Pelamar Kerja"},
        "opening_dialogue": {
            "inggris": "Hello! Thank you for taking the time to meet with us today. To start off, could you briefly tell me about yourself and your background?",
            "jepang": "Hajimemashite. Kyou wa omensetsu ni kite itadaki arigatou gozaimasu. Mazu wa kantan ni jikoshoukai wo onegai dekimasu ka?",
            "mandarin": "Nǐ hǎo! Gǎnxiè nǐ jīntiān lái miànshì. Shǒuxiān, qǐng jiǎndān jièshào yīxià nǐ zìjǐ.",
        },
        "opening_translation": {
            "inggris": "Halo! Terima kasih telah meluangkan waktu bertemu hari ini. Untuk memulai, bisakah ceritakan singkat tentang diri Anda dan latar belakang Anda?",
            "jepang": "Senang bertemu Anda. Terima kasih sudah datang wawancara hari ini. Bisakah Anda perkenalkan diri secara singkat?",
            "mandarin": "Halo! Terima kasih sudah hadir wawancara hari ini. Pertama, tolong perkenalkan diri Anda secara singkat.",
        },
        "response_suggestions": {
            "inggris": [
                "Certainly! My name is Frank, and I have a background in IoT engineering and embedded AI.",
                "I have been working with microcontroller platforms like ESP32 and building voice-controlled systems.",
                "I am very passionate about edge computing and excited about this opportunity.",
            ],
            "jepang": [
                "Watashi wa Frank to moushimasu. IoT to AI no kaihatsu wo shite imasu.",
            ],
            "mandarin": [
                "Wǒ jiào Frank, wǒ de zhuānyè shì wùliánwǎng hé réngōng zhìnéng.",
            ],
        },
    },
    "tanya_arah": {
        "title": "Menanyakan Arah Jalan & Transportasi Umum",
        "roles": {"ai": "Warga Lokal yang Ramah", "user": "Wisatawan"},
        "opening_dialogue": {
            "inggris": "Excuse me! You look like you might need some help. Where are you trying to go?",
            "jepang": "Sumimasen! Michi ni mayotte imasu ka? Doko e ikitai desu ka?",
            "mandarin": "Qǐngwèn, nǐ xūyào bāngzhù ma? Nǐ xiǎng qù nǎlǐ?",
        },
        "opening_translation": {
            "inggris": "Permisi! Anda terlihat seperti butuh bantuan. Mau pergi ke mana?",
            "jepang": "Permisi! Apakah Anda tersesat? Mau pergi ke mana?",
            "mandarin": "Permisi, butuh bantuan? Anda ingin pergi ke mana?",
        },
        "response_suggestions": {
            "inggris": [
                "Excuse me, could you tell me how to get to the central station?",
                "Is it within walking distance, or should I take the subway?",
                "Thank you so much for your kind help!",
            ],
            "jepang": [
                "Eki wa dochira desu ka? (Stasiun ke arah mana ya?)",
                "Aruite ikemasu ka? (Bisakah ditempuh jalan kaki?)",
            ],
            "mandarin": [
                "Qǐngwèn dìtiě zhàn zài nǎlǐ? (Permisi stasiun subway di mana?)",
            ],
        },
    },
}

# ── 3. Grammar Nuance Rules & Pitfalls ──────────────────────────────────────

GRAMMAR_TOPIC_EXPLANATIONS: Dict[str, Dict[str, Any]] = {
    "present_perfect_vs_past_simple": {
        "title": "Present Perfect (have/has + V3) vs Past Simple (V2)",
        "language": "Inggris",
        "core_concept": (
            "Past Simple digunakan saat waktu kejadian SPESIFIK dan SUDAH SELESAI di masa lalu (ada keterangan waktu: yesterday, last week, in 2020).\n"
            "Present Perfect digunakan saat waktu kejadian TIDAK PENTING/BELUM SELESAI, tetapi DAMPAKNYA masih terasa sekarang (pengalaman hidup, baru saja terjadi, atau berlanjut)."
        ),
        "examples": [
            {
                "correct": "I lost my key. (Past Simple - kemarin hilang, tapi sekarang mungkin sudah ketemu).",
                "nuance": "Menyatakan fakta sejarah/lampau.",
            },
            {
                "correct": "I have lost my key! (Present Perfect - kuncinya hilang dan SEKARANG saya masih belum bisa masuk rumah).",
                "nuance": "Menekankan akibat langsung pada saat ini.",
            },
        ],
        "common_pitfall": "Kesalahan umum penutur Indonesia: menggunakan 'I have gone yesterday' (SALAH ❌ karena ada kata 'yesterday'). Seharusnya: 'I went yesterday' (BENAR ✔️).",
    },
    "wa_vs_ga_particles": {
        "title": "Perbedaan Partikel Wa (は) vs Ga (が) dalam Bahasa Jepang",
        "language": "Jepang",
        "core_concept": (
            "Partikel Wa (は) berfungsi sebagai TOPIK pembicaraan umum (artinya: 'Bicara tentang X, maka...'). Informasi baru ada di belakang Wa.\n"
            "Partikel Ga (が) berfungsi sebagai SUBJEK SPESIFIK / PENEGASAN (artinya: 'X inilah pelakunya!'). Informasi pentingnya ada pada kata sebelum Ga."
        ),
        "examples": [
            {
                "correct": "Watashi wa Frank desu. (Topiknya saya, memperkenalkan nama).",
                "nuance": "Fokus pada nama 'Frank'.",
            },
            {
                "correct": "Dare ga kimasu ka? — Frank ga kimasu. (Siapa yang datang? — Frank-lah yang datang!).",
                "nuance": "Fokus menjawab penegasan siapa orangnya.",
            },
        ],
        "common_pitfall": "Untuk kata tanya (Dare, Nani, Doko), selalu gunakan Ga (bukan Wa). Contoh: 'Dare ga' bukan 'Dare wa'.",
    },
    "mandarin_tones": {
        "title": "Empat Nada (Four Tones) Intonasi Bahasa Mandarin",
        "language": "Mandarin",
        "core_concept": (
            "Satu kata ejaan sama bisa memiliki arti berbeda 180 derajat tergantung tinggi-rendah nada:\n"
            "• Nada 1 (Mā - Datar tinggi 55): Ibu 👩\n"
            "• Nada 2 (Má - Naik dari tengah ke tinggi 35): Rami / Mati rasa 🌿\n"
            "• Nada 3 (Mǎ - Turun lalu naik 214): Kuda 🐎\n"
            "• Nada 4 (Mà - Menukik tajam dari tinggi ke bawah 51): Memarahi 😠"
        ),
        "examples": [
            {
                "correct": "Wǒ xiǎng wèn nǐ (Saya ingin BERTANYA padamu - wèn nada 4).",
                "nuance": "Bertanya sopan.",
            },
            {
                "correct": "Wǒ xiǎng wěn nǐ (Saya ingin MENCIUM kamu - wěn nada 3!).",
                "nuance": "Hati-hati! Salah nada dari 4 ke 3 bisa berakibat fatal.",
            },
        ],
        "common_pitfall": "Jangan melafalkan pinyin datar seperti membaca bahasa Indonesia; hayati ayunan intonasi seperti menyanyi.",
    },
}


# ── 4. Vocabulary Flashcard Management Functions ────────────────────────────

def format_vocabulary_flashcard_markdown(
    word: str,
    language: str,
    translation: str,
    pronunciation: str = "",
    part_of_speech: str = "",
    level: str = "A1",
    example: str = "",
    example_translation: str = "",
    notes: str = "",
) -> str:
    """Format entri kosakata menjadi blok Markdown Flashcard rapi dan terstruktur."""
    lines = [
        f"# 📇 Flashcard Kosakata: {word}",
        "",
        f"> 🌐 **Bahasa**: {language.capitalize()}",
        f"> 🗣️ **Pelafalan / Fonetik**: `{pronunciation or word}`",
        f"> 📚 **Tingkat Kemahiran**: {level.upper()}",
    ]
    if part_of_speech:
        lines.append(f"> 🏷️ **Kelas Kata**: {part_of_speech}")
    lines.append(f"> 💡 **Arti / Makna**: **{translation}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    if notes:
        lines.append(f"### 📌 Nuansa & Catatan Penggunaan\n{notes}\n")

    if example:
        lines.append("### 💬 Contoh Kalimat Percakapan")
        lines.append(f"• **{language.capitalize()}**: *{example}*")
        if example_translation:
            lines.append(f"• **Artinya**: {example_translation}")
        lines.append("")

    lines.append("---")
    lines.append("### 🧠 Latihan Cepat XiaoZhi")
    lines.append(f"❓ *Coba buat 1 kalimat spontan menggunakan kata **'{word}'** lalu ucapkan ke XiaoZhi!*")

    return "\n".join(lines).strip()


def save_vocabulary_flashcard(
    store: Any,
    owner_id: int,
    word: str,
    language: str,
    translation: str,
    pronunciation: str = "",
    part_of_speech: str = "",
    level: str = "A1",
    example: str = "",
    example_translation: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Simpan kosakata baru ke Knowledge Base pengguna (tabel materials).
    Mencegah duplikasi kata yang sama pada bahasa yang sama.
    """
    if not store or not owner_id or not word.strip():
        return {"success": False, "message": "Data kosakata atau sesi akun pengguna tidak valid."}

    clean_word = word.strip()
    clean_lang = language.strip().capitalize() or "Inggris"
    clean_trans = translation.strip()
    category = f"Buku Kosakata - {clean_lang}"
    title = f"{clean_word} ({clean_trans[:40]})"

    try:
        # Cek apakah kosakata ini sudah pernah disimpan
        existing = store.search_materials(owner_id, clean_word, limit=5)
        for item in existing:
            if item.get("category") == category and clean_word.lower() in item.get("title", "").lower():
                return {
                    "success": True,
                    "already_existed": True,
                    "material_id": item["id"],
                    "message": f"Kosakata '{clean_word}' sudah tersimpan sebelumnya di buku kosakata {clean_lang} Anda.",
                    "word": clean_word,
                    "language": clean_lang,
                }

        content_md = format_vocabulary_flashcard_markdown(
            word=clean_word,
            language=clean_lang,
            translation=clean_trans,
            pronunciation=pronunciation,
            part_of_speech=part_of_speech,
            level=level,
            example=example,
            example_translation=example_translation,
            notes=notes,
        )

        keywords = f"vocab, {clean_lang.lower()}, {clean_word.lower()}, {clean_trans.lower()}, flashcard, {level.lower()}"
        mat_id = store.add_material(
            owner_id=owner_id,
            title=title[:120],
            category=category[:80],
            content=content_md,
            keywords=keywords[:250],
            source_type="language_flashcard",
        )

        try:
            from xiaozhi.services.mcp_service import signal_mcp_reload
            signal_mcp_reload()
        except Exception:
            pass

        return {
            "success": True,
            "already_existed": False,
            "material_id": mat_id,
            "message": f"Kosakata '{clean_word}' ({clean_trans}) berhasil disimpan ke Buku Kosakata {clean_lang} Anda.",
            "word": clean_word,
            "language": clean_lang,
            "pronunciation": pronunciation or clean_word,
            "level": level,
            "instruksi_ai": (
                f"Puji pengguna karena telah mempelajari kata baru '{clean_word}'. "
                f"Sebutkan cara pelafalannya secara singkat di speaker, lalu tanyakan: 'Apakah kamu mau langsung mencoba membuat kalimat dengan kata {clean_word}?'"
            ),
        }
    except Exception as e:
        logger.exception("Error saving vocabulary flashcard")
        return {"success": False, "message": f"Gagal menyimpan kosakata: {str(e)[:80]}"}


def get_stored_vocabulary_list(store: Any, owner_id: int, language: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """Ambil daftar kosakata yang pernah disimpan oleh pengguna dari tabel materials."""
    if not store or not owner_id:
        return []
    try:
        all_materials = store.list_materials(owner_id)
        vocab_items = []
        target_cat = f"Buku Kosakata - {language.strip().capitalize()}" if language.strip() else "Buku Kosakata"

        for mat in all_materials:
            cat = str(mat.get("category", ""))
            if target_cat in cat or mat.get("source_type") == "language_flashcard":
                vocab_items.append(mat)
                if len(vocab_items) >= limit:
                    break
        return vocab_items
    except Exception as e:
        logger.debug("Error listing vocabulary: %s", e)
        return []


# ── 5. Interactive Language Quiz & Drill Engine ─────────────────────────────

def quiz_language_vocabulary(
    store: Any,
    owner_id: int,
    language: str = "Inggris",
    question_number: int = 1,
    user_answer: str = "",
) -> Dict[str, Any]:
    """
    Kuis drill kosakata interaktif:
    - Menguji hafalan kata dari daftar yang pernah disimpan di Knowledge Base pengguna.
    - Jika pengguna belum menyimpan kata, menggunakan Bank Kosakata Esensial terkurasi.
    - Menilai jawaban pengguna dengan pendekatan pedagogis ramah.
    """
    clean_lang = language.strip().lower() or "inggris"
    stored_cards = get_stored_vocabulary_list(store, owner_id, language=clean_lang, limit=10)

    quiz_items = []
    # 1. Bangun bank soal dari catatan pribadi jika ada
    if stored_cards:
        for card in stored_cards:
            raw_content = card.get("content", "")
            title = card.get("title", "")
            word = title.split("(")[0].strip() if "(" in title else title
            trans_match = re.search(r"💡 \*\*Arti / Makna\*\*:\s*\*\*(.*?)\*\*", raw_content)
            ex_match = re.search(r"• \*\*Artinya\*\*:\s*(.*?)(?=\n|\Z)", raw_content)
            trans = trans_match.group(1).strip() if trans_match else "Arti kosakata"
            if word:
                quiz_items.append({
                    "word": word,
                    "translation": trans,
                    "example_hint": ex_match.group(1).strip() if ex_match else "",
                    "source": "Catatan Pribadi",
                })

    # 2. Jika belum cukup soal, padukan dengan bank kurasi
    starter = STARTER_VOCABULARY_BANK.get(clean_lang, STARTER_VOCABULARY_BANK["inggris"])
    for s in starter:
        if not any(q["word"].lower() == s["word"].lower() for q in quiz_items):
            quiz_items.append({
                "word": s["word"],
                "translation": s["translation"],
                "example_hint": s["example"],
                "source": "Materi Kurasi Rekomendasi",
            })

    if not quiz_items:
        return {
            "success": False,
            "message": f"Belum ada bank kosakata untuk bahasa '{language}'. Silakan minta XiaoZhi mengajarkan kosakata baru terlebih dahulu.",
        }

    total_q = min(5, len(quiz_items))
    idx = max(1, min(question_number, total_q)) - 1
    current = quiz_items[idx]

    # Evaluasi jawaban jika user merespons
    if user_answer.strip():
        correct_word = current["word"]
        correct_trans = current["translation"]
        next_num = idx + 2
        has_next = next_num <= total_q

        return {
            "success": True,
            "mode": "evaluasi_jawaban",
            "soal_ke": idx + 1,
            "bahasa": clean_lang.capitalize(),
            "kata_target": correct_word,
            "arti_resmi": correct_trans,
            "jawaban_user": user_answer.strip(),
            "ada_soal_berikutnya": has_next,
            "nomor_soal_berikutnya": next_num if has_next else None,
            "instruksi_ai": (
                f"PANDUAN MENILAI JAWABAN (GURU BAHASA RAMAH):\n"
                f"1. Jawaban pengguna: '{user_answer}'. Kunci jawaban: '{correct_trans}' (atau bahasa target '{correct_word}').\n"
                f"2. Berikan apresiasi hangat. Jika benar/mendekati, puji secara antusias ('Hebat, tepat sekali!'). "
                f"Jika kurang tepat, berikan pembetulan lembut dan sebutkan artinya yang benar.\n"
                + (f"3. Lalu langsung tanyakan soal nomor {next_num}: 'Sekarang soal nomor {next_num}: Apa arti atau bahasa {clean_lang.capitalize()} dari \"{quiz_items[idx+1]['word']}\"?'"
                   if has_next else "3. Berikan ucapan selamat karena telah menuntaskan seluruh latihan kuis hari ini dengan sangat baik!")
            ),
        }

    # Sajikan soal kuis baru
    return {
        "success": True,
        "mode": "tanya_soal",
        "soal_ke": idx + 1,
        "total_soal": total_q,
        "bahasa": clean_lang.capitalize(),
        "kata_target": current["word"],
        "kunci_jawaban_rahasia": current["translation"],
        "instruksi_ai": (
            f"PANDUAN MENGAJUKAN SOAL KUIS:\n"
            f"Sapa pengguna sebagai guru bahasa yang suportif dan tanyakan soal nomor {idx+1}:\n"
            f"'Pertanyaan nomor {idx+1} dari {total_q}: Coba sebutkan apa arti dari kata **\"{current['word']}\"** dalam bahasa Indonesia?'\n"
            f"(JANGAN MEMBACAKAN KUNCI JAWABAN TERLEBIH DAHULU, tunggu jawaban dari pengguna)."
        ),
    }


# ── 6. Situational Conversation Roleplay Engine ─────────────────────────────

def start_conversation_roleplay(
    scenario: str = "kafe",
    target_language: str = "Inggris",
    level: str = "Pemula",
    turn_number: int = 1,
    user_speech: str = "",
) -> Dict[str, Any]:
    """
    Simulasi percakapan nyata (Roleplay) dengan pembetulan tata bahasa aktif (Feedback-First).
    Mendukung skenario: kafe, bandara, hotel, wawancara, tanya_arah.
    """
    clean_scen = scenario.strip().lower()
    clean_lang = target_language.strip().lower()

    # Normalisasi nama skenario
    matched_key = "kafe"
    for k in ROLEPLAY_SCENARIOS:
        if k in clean_scen:
            matched_key = k
            break

    scen_data = ROLEPLAY_SCENARIOS[matched_key]
    ai_role = scen_data["roles"]["ai"]
    user_role = scen_data["roles"]["user"]

    opening = scen_data["opening_dialogue"].get(clean_lang, scen_data["opening_dialogue"]["inggris"])
    opening_trans = scen_data["opening_translation"].get(clean_lang, scen_data["opening_translation"]["inggris"])
    suggestions = scen_data["response_suggestions"].get(clean_lang, scen_data["response_suggestions"]["inggris"])

    # Giliran pertama (Pembukaan simulasi)
    if turn_number <= 1 and not user_speech.strip():
        return {
            "success": True,
            "mode": "mulai_roleplay",
            "skenario": scen_data["title"],
            "peran_xiaozhi": ai_role,
            "peran_pengguna": user_role,
            "bahasa": target_language.capitalize(),
            "level": level.capitalize(),
            "giliran_ke": 1,
            "kalimat_pembuka_target": opening,
            "arti_bahasa_indonesia": opening_trans,
            "pilihan_jawaban_pengguna": suggestions[:2],
            "instruksi_ai": (
                f"PANDUAN MEMULAI ROLEPLAY:\n"
                f"1. Jelaskan singkat dalam 1 kalimat bahwa kita masuk ke simulasi '{scen_data['title']}'. Anda berperan sebagai {ai_role}.\n"
                f"2. Ucapkan kalimat pembuka dalam bahasa {target_language}: '{opening}'\n"
                f"3. Jika level {level} adalah Pemula, sebutkan artinya: '{opening_trans}'.\n"
                f"4. Berikan panduan santai: 'Kamu bisa membalas dengan contoh: \"{suggestions[0]}\" atau kalimatmu sendiri! Silakan balas.'"
            ),
        }

    # Giliran lanjutan (Mengevaluasi ucapan pengguna + melanjutkan percakapan)
    is_final_turn = turn_number >= 4
    return {
        "success": True,
        "mode": "lanjutkan_roleplay",
        "skenario": scen_data["title"],
        "peran_xiaozhi": ai_role,
        "peran_pengguna": user_role,
        "bahasa": target_language.capitalize(),
        "level": level.capitalize(),
        "giliran_ke": turn_number,
        "ucapan_user": user_speech,
        "apakah_sesi_terakhir": is_final_turn,
        "instruksi_ai": (
            f"PANDUAN GURU BAHASA (FEEDBACK-FIRST PEDAGOGY):\n"
            f"1. KOREKSI RAMAH: Dengarkan kalimat user: '{user_speech}'. Jika ada kesalahan grammar/tenses/pilihan kata, "
            f"berikan apresiasi dulu lalu beri perbaikan singkat: 'Bagus! Sedikit koreksi agar lebih natural: ...'. Jika sudah sempurna, puji kelancarannya!\n"
            f"2. BALAS DALAM KARAKTER: Berperanlah sebagai {ai_role} dan responlah secara natural dalam bahasa {target_language} (maksimal 2 kalimat).\n"
            + (
                f"3. Berikan terjemahan bahasa Indonesia singkat di akhir agar pengguna paham.\n"
                f"4. Ajukan pertanyaan balik agar pengguna membalas lagi giliran berikutnya."
                if not is_final_turn else
                f"3. Sampaikan bahwa simulasi skenario '{scen_data['title']}' telah selesai dengan sukses! Berikan rapor nilai singkat (misal: 'Kelancaran: 9/10, Kosakata: Bagus!') dan motivasi positif."
            )
        ),
    }


# ── 7. Grammar Nuance & Cultural Doctor Engine ──────────────────────────────

def explain_grammar_and_nuance(topic: str, target_language: str = "Inggris") -> Dict[str, Any]:
    """Menjelaskan tata bahasa, rumus, perbedaan tenses, dan nuansa budaya penutur asli."""
    clean_topic = topic.strip().lower()

    # Cari topik dari kamus penjelasan cepat
    matched_entry = None
    for k, v in GRAMMAR_TOPIC_EXPLANATIONS.items():
        if any(word in clean_topic for word in k.split("_")):
            matched_entry = v
            break

    if matched_entry:
        return {
            "success": True,
            "topik": matched_entry["title"],
            "bahasa": matched_entry["language"],
            "konsep_inti": matched_entry["core_concept"],
            "contoh_dan_nuansa": matched_entry["examples"],
            "kesalahan_umum": matched_entry["common_pitfall"],
            "instruksi_ai": (
                f"Jelaskan topik tata bahasa '{matched_entry['title']}' kepada pengguna dengan bahasa Indonesia yang santai, "
                f"jelas, dan tidak kaku layaknya guru les privat terbaik. Soroti contoh kesalahan umum penutur Indonesia di atas."
            ),
        }

    # Topik kustom dinamis
    return {
        "success": True,
        "topik": topic.strip(),
        "bahasa": target_language.capitalize(),
        "instruksi_ai": (
            f"Jelaskan aturan tata bahasa / rumus / nuansa mengenai '{topic}' dalam bahasa {target_language} secara komprehensif:\n"
            f"1. Jelaskan konsep dasar dan kapan harus digunakan.\n"
            f"2. Berikan 2 contoh kalimat yang BENAR beserta artinya dalam bahasa Indonesia.\n"
            f"3. Berikan 1 contoh kesalahan umum (common mistake) yang sering dibuat orang Indonesia dan jelaskan mengapa itu keliru.\n"
            f"4. Akhiri dengan mengajak pengguna mencoba membuat 1 kalimat contoh sendiri."
        ),
    }
