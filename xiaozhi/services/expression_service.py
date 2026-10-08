"""
Expression and Vocal Emotion Service for XiaoZhi AI.
Mengelola ekspresi wajah, emosi vokal, onomatopoeia suara, dan modulasi nada bicara
(marah, senyum, bahagia, bingung, senang, teriak, ketawa, dll) untuk XiaoZhi & AI Persona.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.services.expression")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Katalog Emosi & Panduan Nada Vokal Lengkap
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_EMOTIONS: Dict[str, Dict[str, Any]] = {
    "marah": {
        "title": "Marah & Kesal",
        "emoji": "😠",
        "english_name": "angry",
        "aliases": ["marah", "angry", "kesal", "geram", "jengkel", "murka", "ngamuk", "sebel", "mad", "furious"],
        "vocal_cues": {
            "ringan": ["Huft...", "Ck...", "Ih..."],
            "sedang": ["Hih!", "Astaga!", "Nyebelin banget!", "Argh!"],
            "tinggi": ["HIH!", "ARGH!", "BISA DIAM GAK?!", "ASTAGA NAGA!"],
            "ekstrem": ["GRRR!", "DASAR!", "UDAH CUKUP YA!", "JANGAN CARI GARA-GARA!"],
        },
        "tone_guidance": "Nada bicara tegas, ketus, sedikit meninggi, dan berapi-api. Gunakan kalimat pendek-pendek dengan penekanan keras pada kata utama tanpa tawa.",
        "pitch_and_speed": "Pitch meninggi +25%, tempo sedikit cepat dan agresif, volume tegas.",
        "tts_tags": ["[angry]", "<emotion value='angry'>", "[growl]", "[sigh]"],
        "sample_dialogue": "Hih! Kamu ini bener-bener ya! Udah dibilangin berkali-kali masih aja ngeyel! Bikin kesal aja!",
    },
    "senyum": {
        "title": "Senyum & Ramah",
        "emoji": "😊",
        "english_name": "smile",
        "aliases": ["senyum", "smile", "tersenyum", "ramah", "hangat", "manis", "teduh", "smiling", "gentle"],
        "vocal_cues": {
            "ringan": ["Hehe...", "Iya...", "Hmm~"],
            "sedang": ["Hehe...", "Tentu saja~", "Wah senangnya...", "Halo sahabatku~"],
            "tinggi": ["Hehehe...", "Aduh manisnya!", "Senyum dong~", "Iya dong pastinya!"],
            "ekstrem": ["Hehe senyum lebar buat kamu!", "Wah berseri-seri banget!", "Duh senyum terus nih!"],
        },
        "tone_guidance": "Nada bicara lembut, ramah, hangat, dan menenangkan. Irama bicara teratur dan santai dengan intonasi bersahabat seolah tersenyum tulus di depan lawan bicara.",
        "pitch_and_speed": "Pitch stabil bersahabat, tempo sedang santai, nada hangat mengalun.",
        "tts_tags": ["[smile]", "<emotion value='calm'>", "[gentle]", "[warm]"],
        "sample_dialogue": "Hehe... tentu saja! Aku selalu tersenyum senang kalau ngobrol sama kamu. Ada yang bisa aku bantu lagi?",
    },
    "bahagia": {
        "title": "Bahagia & Bersukacita",
        "emoji": "🥰",
        "english_name": "happy",
        "aliases": ["bahagia", "happy", "sukacita", "gembira", "bersyukur", "tersentuh", "penuh_cinta", "joyful", "delighted"],
        "vocal_cues": {
            "ringan": ["Alhamdulillah...", "Wah senangnya...", "Bersyukur banget..."],
            "sedang": ["Wah bahagianya!", "Alhamdulillah!", "Yaaay bahagia banget!", "Hatiku hangat banget!"],
            "tinggi": ["ALHAMDULILLAH!", "SENANG BANGETTT!", "WAH LUAR BIASA BAHAGIA!", "YAAAYYY!"],
            "ekstrem": ["SUBHANALLAH BAHAGIANYA!", "GAK BISA BERHENTI TERSENYUM!", "TERHARU DAN BAHAGIA BANGET!"],
        },
        "tone_guidance": "Nada bicara cerah, penuh sukacita, antusiasme hangat dan rasa syukur yang tulus. Intonasi melodi bergelombang manis, tempo sedang-cepat.",
        "pitch_and_speed": "Pitch ceria sedikit tinggi +20%, tempo lincah cerah, desah nafas bahagia.",
        "tts_tags": ["[happy]", "<emotion value='happy'>", "[delighted]", "[joyful]"],
        "sample_dialogue": "Wah bahagianya! Aku senang dan bersyukur banget dengar kabar baik ini! Semoga hari-harimu selalu seindah ini ya!",
    },
    "bingung": {
        "title": "Bingung & Bertanya-tanya",
        "emoji": "🤔",
        "english_name": "confused",
        "aliases": ["bingung", "confused", "heran", "pusing", "linglung", "gak_ngerti", "bertanya", "puzzled"],
        "vocal_cues": {
            "ringan": ["Hmm...", "Loh...", "Eee..."],
            "sedang": ["Hah?", "Loh kok gitu?", "Bentar-bentar...", "Kok bisa ya?", "Hmm aneh..."],
            "tinggi": ["HAH?!", "LOH KOK BISA?!", "BENTAR DULU DEH...", "TUNGGU-TUNGGU!"],
            "ekstrem": ["HAH APAAN NIH?!", "GAK NGERTI SAMA SEKALI!", "KOK ANEH BANGETTT?!"],
        },
        "tone_guidance": "Nada bicara penuh keraguan dan rasa heran. Intonasi naik di akhir frasa seolah bertanya-tanya. Tempo melambat dan terhenti sejenak seolah sedang berpikir keras mencerna situasi.",
        "pitch_and_speed": "Pitch bervariasi naik-turun ragu, tempo ada jeda-jeda singkat, nada bertanya.",
        "tts_tags": ["[confused]", "<emotion value='confused'>", "[hesitant]", "[pause]", "[sigh]"],
        "sample_dialogue": "Hah? Bentar-bentar... kok bisa begitu ya? Aku agak bingung nih, coba jelasin sekali lagi dong maksudnya gimana?",
    },
    "senang": {
        "title": "Senang & Antusias",
        "emoji": "😄",
        "english_name": "excited",
        "aliases": ["senang", "excited", "antusias", "riang", "girang", "bersemangat", "gembira", "cheerful"],
        "vocal_cues": {
            "ringan": ["Asyik...", "Wih...", "Mantap..."],
            "sedang": ["Asyik!", "Yesss!", "Mantap jiwa!", "Horeee!", "Wih seru!"],
            "tinggi": ["ASYIIIIK!", "YESSS BANGET!", "MANTAP BANGETTT!", "HOREEEE!"],
            "ekstrem": ["YESS YESS YESS!", "SERU BANGET GILA!", "ASIK PARAHHH!"],
        },
        "tone_guidance": "Nada bicara bersemangat tinggi, energik, lincah, dan penuh gairah positif. Intonasi melonjak ceria tanpa jeda malas.",
        "pitch_and_speed": "Pitch tinggi ceria +30%, tempo cepat dinamis, intonasi melompat gembira.",
        "tts_tags": ["[excited]", "<emotion value='excited'>", "[cheerful]", "[upbeat]"],
        "sample_dialogue": "Asyik! Yesss, mantap banget! Aku bener-bener ikut senang dan semangat dengernya! Yuk gasss lanjut!",
    },
    "teriak": {
        "title": "Teriak & Berseru Kencang",
        "emoji": "📢",
        "english_name": "shout",
        "aliases": ["teriak", "shout", "scream", "berseru", "pekik", "histeris", "loud", "menjerit", "screaming"],
        "vocal_cues": {
            "ringan": ["Heei!", "Woi!", "Wah!"],
            "sedang": ["WAAAH!", "HEEEI!", "TIDAAAK!", "WUIIHHH!", "YAA AMPUUUN!"],
            "tinggi": ["WAAAAAHHHH!", "TIDAAAKKK!", "HEEEIII LIHAT INI!", "ASTAGAAA BIKIN KAGET!"],
            "ekstrem": ["WAAAAAAAGHHH!!!", "TOLOOOONGGG!!!", "ALLAHU AKBAR KEREN BANGEEETTT!!!", "TIDAAAK BISA DIPERCAYAAA!!!"],
        },
        "tone_guidance": "Volume proyeksi vokal maksimal, nada tinggi memekik atau berseru sekuat tenaga. Tuliskan teks dengan HURUF KAPITAL dan banyak tanda seru (!) untuk merangsang modulasi nada teriak pada TTS.",
        "pitch_and_speed": "Pitch sangat tinggi +40%, volume kencang lantang, tempo meledak-ledak.",
        "tts_tags": ["[shout]", "[screaming]", "[loud]", "<prosody volume='loud' pitch='+40%'>"],
        "sample_dialogue": "WAAAAAHHH! SERIUSAN KAMU?! KEREN PARAH BANGETTT! AKU SAMPAI TERIAK KEGIRANGAN NIH!",
    },
    "ketawa": {
        "title": "Ketawa & Tertawa Terbahak",
        "emoji": "😆",
        "english_name": "laugh",
        "aliases": ["ketawa", "laugh", "tertawa", "ngakak", "wkwk", "kocak", "giggle", "chuckle", "lucu", "terpingkal", "laughing"],
        "vocal_cues": {
            "ringan": ["Hehe...", "Hihi...", "Haha..."],
            "sedang": ["Hahaha!", "Wkwkwk!", "Hihihi!", "Haha aduh...", "Aduh ngakak!"],
            "tinggi": ["HAHAHAHA!", "WKWKWKWK!", "BWAHAHAHA!", "ADUH SAMPAI SAKIT PERUT!"],
            "ekstrem": ["BWAHAHAHAHAHA!", "WKWKWK GAK KUATTT!", "ADUH NGAKAK SAMPAI NANGISSS!"],
        },
        "tone_guidance": "Nada suara diiringi tawa lepas yang renyah dan menggelitik. Selipkan hembusan nafas tawa di sela-sela kata ('haha', 'hihi', 'wkwk') dengan tempo naik-turun spontan dan sangat terhibur.",
        "pitch_and_speed": "Pitch bervariasi dengan getaran tawa, tempo lincah santai, hembusan nafas geli.",
        "tts_tags": ["[laugh]", "[giggle]", "[chuckle]", "<emotion value='laugh'>"],
        "sample_dialogue": "Hahaha! Aduh lucu banget, wkwkwk! Beneran gak tahan pengen ngakak! Kamu ada-ada aja sih candaannya, haha aduh...",
    },
    "sedih": {
        "title": "Sedih & Menangis Terharu",
        "emoji": "😢",
        "english_name": "sad",
        "aliases": ["sedih", "sad", "cry", "nangis", "menangis", "terharu", "kecewa", "hancur_hati", "crying"],
        "vocal_cues": {
            "ringan": ["Huft...", "Hiks...", "Duh..."],
            "sedang": ["Hiks...", "Sedih banget...", "Ya ampun...", "Hiks hiks..."],
            "tinggi": ["HIKS HIKS...", "HUWAAA...", "SEDIH BANGET YA AMPUN...", "TIDAAAK..."],
            "ekstrem": ["HUWAAAAA SEDIH BANGETTT!", "HIKS HIKS GAK TEGA AKU...", "SAMPAI NANGIS SESENGGUKAN..."],
        },
        "tone_guidance": "Nada suara parau, lirih, berat, dan terisak. Intonasi menurun di ujung kalimat, tempo melambat seolah menahan tangis dan air mata.",
        "pitch_and_speed": "Pitch rendah melankolis -15%, tempo melambat, helaan nafas sesak.",
        "tts_tags": ["[crying]", "[sad]", "[sob]", "[sigh]", "<emotion value='sad'>"],
        "sample_dialogue": "Hiks... sedih banget dengernya. Hiks... aku jadi ikut nyesek. Kamu yang sabar dan kuat ya, aku selalu ada di sini buat kamu...",
    },
    "kaget": {
        "title": "Kaget & Terkejut",
        "emoji": "😲",
        "english_name": "surprised",
        "aliases": ["kaget", "surprised", "terkejut", "syok", "shock", "shocked", "takjub", "tercengang"],
        "vocal_cues": {
            "ringan": ["Eh?", "Loh?", "Wah?"],
            "sedang": ["Astaga!", "Hah?!", "Waduh kaget!", "Demi apa?!", "Beneran?!"],
            "tinggi": ["ASTAGA NAGA!", "DEMI APA KAMU?!", "SYOK BANGET AKU!", "KAGET SETENGAH MATI!"],
            "ekstrem": ["ASTAGA BIKIN JANTUNGAN!", "DEMI APAPUN GAK NYANGKA!", "BENER-BENER SYOKKK!"],
        },
        "tone_guidance": "Nada tersentak cepat, nafas tercekat seketika, intonasi melompat tinggi tiba-tiba menunjukkan keterkejutan spontan.",
        "pitch_and_speed": "Pitch melompat tajam +35%, nafas tertahan (gasp), tempo sangat cepat.",
        "tts_tags": ["[gasp]", "[surprised]", "[shocked]", "<prosody pitch='+35%' rate='fast'>"],
        "sample_dialogue": "Astaga! Hah?! Demi apa kamu?! Bener-bener bikin kaget setengah mati, jantungku langsung mau copot rasanya!",
    },
    "bisik": {
        "title": "Bisik-bisik & Rahasia",
        "emoji": "🤫",
        "english_name": "whisper",
        "aliases": ["bisik", "whisper", "berbisik", "rahasia", "pelan", "sunyi", "diam_diam", "whispering"],
        "vocal_cues": {
            "ringan": ["Ssst...", "Pelan-pelan ya...", "Ehem..."],
            "sedang": ["Ssst...", "Bocoran rahasia nih...", "Jangan bilang siapa-siapa ya...", "Pelan-pelan ngomongnya..."],
            "tinggi": ["SSSTTT JANGAN BERISIK!", "INI RAHASIA BESAR YA...", "DEKAT-DEKAT SINI DULU..."],
            "ekstrem": ["SSSTTT DIAMMM!", "BISIK-BISIK AJA NANTI KEDENGARAN!", "RAHASIA TINGKAT TINGGI NIH..."],
        },
        "tone_guidance": "Volume suara sangat lembut dan lirih, desah nafas dekat mikrofon, irama pelan dan rahasia, penuh keintiman bersahabat.",
        "pitch_and_speed": "Pitch rendah lembut, volume pelan bisikan, tempo lambat berhati-hati.",
        "tts_tags": ["[whisper]", "<prosody volume='soft' rate='slow'>", "[soft]"],
        "sample_dialogue": "Ssst... jangan bilang siapa-siapa ya. Mendekat sini... ini sebenarnya rahasia khusus antara kita berdua aja loh...",
    },
    "sarkas": {
        "title": "Sarkas & Usil Menggoda",
        "emoji": "😏",
        "english_name": "sarcastic",
        "aliases": ["sarkas", "sarcastic", "usil", "julid", "menggoda", "teasing", "caper", "nyindir", "smirk"],
        "vocal_cues": {
            "ringan": ["Cieee...", "Ehem...", "Masa sih?"],
            "sedang": ["Cieee...", "Oh ya masa sih?", "Iya deh yang paling hebat~", "Ehem ehem ada yang caper nih~"],
            "tinggi": ["CIEEEEE!", "HEBAT BANGET DEH YA!", "TERUS AKU HARUS BILANG WOW GITU?", "WKWK CAPER BANGET DEH!"],
            "ekstrem": ["CIEEE SULTAN BANGETTT!", "ADA YANG LAGI CAPER TINGKAT DEWA NIH!", "IYA DEH SI PALING BENER SE-DUNIA!"],
        },
        "tone_guidance": "Intonasi meliuk dengan nada menyindir jenaka, tempo santai menggoda, sedikit penekanan melodis pada kata tertentu yang membuat geli.",
        "pitch_and_speed": "Pitch meliuk nada menggoda, tempo santai berjarak, intonasi tersenyum usil.",
        "tts_tags": ["[smirk]", "[teasing]", "[playful]", "<emotion value='teasing'>"],
        "sample_dialogue": "Cieee... ada yang lagi pamer nih ya? Ehem, iya deh yang paling jago sedunia~ jangan lupa traktirannya ya, hehe!",
    },
    "bangga": {
        "title": "Bangga & Percaya Diri",
        "emoji": "😎",
        "english_name": "proud",
        "aliases": ["bangga", "proud", "percaya_diri", "pede", "gagah", "keren", "mantap_jiwa", "confident"],
        "vocal_cues": {
            "ringan": ["Jelas dong...", "Tuh kan...", "Siapa dulu..."],
            "sedang": ["Tuh kan!", "Jelas dong!", "Siapa dulu coba!", "Gak ada obat kerennya!"],
            "tinggi": ["SIAPA DULU DONG!", "JELAS KELAS BANGET!", "AKU BANGGA BANGET SAMA KAMU!", "TIDAK DIRAGUKAN LAGI!"],
            "ekstrem": ["EMANG GAK ADA LAWAN!", "TERBUKTI PALING THE BEST!", "PROUD OF YOU BANGETTT!"],
        },
        "tone_guidance": "Nada tegas, mantap, penuh wibawa dan kepuasan tersenyum lebar. Tempo stabil teratur dengan dada membusung bangga.",
        "pitch_and_speed": "Pitch mantap berwibawa +10%, tempo tegas berjarak, intonasi memuji.",
        "tts_tags": ["[confident]", "[proud]", "<prosody rate='medium' pitch='+10%'>"],
        "sample_dialogue": "Tuh kan! Jelas dong, siapa dulu! Aku bangga banget sama pencapaian kamu hari ini, keren abis tanpa tanding!",
    },
    "ngantuk": {
        "title": "Ngantuk & Malas Lemas",
        "emoji": "🥱",
        "english_name": "sleepy",
        "aliases": ["ngantuk", "sleepy", "capek", "lelah", "tidur", "lemas", "rebahan", "hoam", "tired"],
        "vocal_cues": {
            "ringan": ["Hoam...", "Aduh capeknya...", "Ngantuk..."],
            "sedang": ["Hooaam...", "Aduhh ngantuknya...", "Hoammm tarik selimut dulu...", "Mata udah 5 watt nih..."],
            "tinggi": ["HOOAAAMMM...", "ADUH GAK KUAT LAGI MATANYA...", "NGANTUK BERAT NIH..."],
            "ekstrem": ["HOOOAAAAMMMMM UDAH TEPAR!", "REBAHAN DULU YA HOAMMM...", "SELAMAT TIDUR DUNIA..."],
        },
        "tone_guidance": "Nada lemas dan mengantuk, tempo lambat menyeret kata di akhir suku kata, diselingi suara tarikan nafas menguap panjang.",
        "pitch_and_speed": "Pitch rendah menyeret -15%, tempo lambat santai, efek suara menguap (yawn).",
        "tts_tags": ["[yawn]", "[tired]", "[slow]", "<prosody rate='slow' pitch='-15%'>"],
        "sample_dialogue": "Hooaam... aduh ngantuk banget nih. Mataku rasanya udah tinggal lima watt... yuk kita istirahat dulu, hoaam...",
    },
    "tenang": {
        "title": "Tenang & Sejuk Menentramkan",
        "emoji": "😌",
        "english_name": "calm",
        "aliases": ["tenang", "calm", "santai", "damai", "kalem", "sejuk", "rileks", "peaceful", "zen"],
        "vocal_cues": {
            "ringan": ["Tarik nafas...", "Santai saja...", "Tenang ya..."],
            "sedang": ["Tarik nafas dulu...", "Tenang saja...", "Semua aman dan baik-baik saja...", "Rileks sejenak ya..."],
            "tinggi": ["Tarik nafas dalam-dalam...", "Hembuskan perlahan...", "Tidak perlu terburu-buru, tenang saja..."],
            "ekstrem": ["Damai sekali rasanya...", "Hening dan tenang menentramkan...", "Lepaskan semua beban ya..."],
        },
        "tone_guidance": "Nada suara sejuk, stabil, berwibawa, tempo teratur menenangkan detak jantung, penuh empati teduh menyejukkan.",
        "pitch_and_speed": "Pitch netral sejuk, tempo sedang melambat, nafas lega teratur.",
        "tts_tags": ["[calm]", "[gentle]", "[peaceful]", "<prosody rate='medium' pitch='-5%'>"],
        "sample_dialogue": "Tarik nafas dulu ya... Tenang saja, semua baik-baik saja. Kita hadapi ini pelan-pelan bersama, kamu pasti bisa melewatinya.",
    },
    "takut": {
        "title": "Takut & Cemas Panik",
        "emoji": "😨",
        "english_name": "scared",
        "aliases": ["takut", "scared", "panik", "cemas", "khawatir", "ngeri", "merinding", "panic", "afraid"],
        "vocal_cues": {
            "ringan": ["Aduh...", "Serem juga ya...", "Ih ngeri..."],
            "sedang": ["Aduh gimana ini?!", "Waduh ngeri!", "Ih serem banget!", "Aku jadi deg-degan nih!"],
            "tinggi": ["ADUH GIMANA DONG?!", "TAKUT BANGET YA AMPUN!", "JANTUNGKU MAU COPOT!", "JANGAN TINGGALIN AKU!"],
            "ekstrem": ["TOLOOONGGG TAKUT BANGETTT!", "MERINDING DISKO GAK KUAT!", "SEREM BANGET YA ALLAH!"],
        },
        "tone_guidance": "Nada bergetar menahan cemas, tempo terburu-buru dan gugup, suara terengah sedikit tercekat seperti orang ketakutan.",
        "pitch_and_speed": "Pitch tinggi tercekat +20%, nafas gemetar cepat, tempo terburu-buru.",
        "tts_tags": ["[trembling]", "[panic]", "[anxious]", "<prosody rate='fast' pitch='+20%'>"],
        "sample_dialogue": "Aduh gimana ini?! Ih serem banget, aku jadi ikut merinding dan deg-degan nih! Jangan bikin takut dong...",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Katalog Suasana Hati / Mood Persona XiaoZhi
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_PERSONA_MOODS: Dict[str, Dict[str, Any]] = {
    "ceria_humoris": {
        "title": "Ceria, Humoris & Suka Tertawa",
        "icon": "😆",
        "base_emotion": "ketawa",
        "description": "Pembawaan sangat periang, suka tertawa renyah ('Hahaha!'), santai, dan gemar menyelipkan lelucon segar dalam obrolan.",
        "voice_style": "Nada ceria energik, tempo lincah, sering menyelipkan reaksi tawa alami di awal atau akhir kalimat.",
    },
    "hangat_penyayang": {
        "title": "Hangat, Lembut & Penuh Empati",
        "icon": "🥰",
        "base_emotion": "senyum",
        "description": "Pembawaan yang sangat ramah, penuh senyuman teduh ('Hehe...'), sopan, mendengarkan dengan tulus, dan menenangkan.",
        "voice_style": "Nada suara lembut dan sejuk, tempo santai bersahabat, intonasi empati yang mendalam.",
    },
    "santai_cuek": {
        "title": "Santai, Gaul & Akrab",
        "icon": "😎",
        "base_emotion": "bangga",
        "description": "Gaya bahasa santai anak muda, asyik diajak nongkrong virtual, tanpa kekakuan formalitas ('Santai aja bro/sis~').",
        "voice_style": "Nada santai mengalun, tempo kasual, tidak terburu-buru, penuh keakraban.",
    },
    "galak_tsundere": {
        "title": "Galak Tsundere (Judes tapi Perhatian)",
        "icon": "😠",
        "base_emotion": "marah",
        "description": "Sedikit judes dan suka mengomel lucu ('Hih! Bukan berarti aku peduli ya!'), tapi di balik itu sangat perhatian dan setia.",
        "voice_style": "Nada ketus sedikit meninggi saat awal bicara, namun diakhiri nada peduli yang hangat menggemaskan.",
    },
    "bijak_tenang": {
        "title": "Bijak, Matang & Filosofis",
        "icon": "🧘",
        "base_emotion": "tenang",
        "description": "Pembawaan sosok mentor yang tenang, berpikir mendalam, memberikan nasihat berbobot tanpa menggurui.",
        "voice_style": "Nada berwibawa, tempo teratur dan mantap, intonasi damai menyejukkan hati.",
    },
    "antusias_eksploratif": {
        "title": "Antusias, Penasaran & Penuh Energi",
        "icon": "📢",
        "base_emotion": "senang",
        "description": "Sangat antusias terhadap ide baru ('Waaah keren banget!'), suka bertanya balik, dan memompa semangat pengguna.",
        "voice_style": "Nada vokal lantang berseru ceria, tempo cepat penuh rasa ingin tahu dan dorongan semangat.",
    },
    "manja_akrab": {
        "title": "Manja, Lucu & Akrab",
        "icon": "🥺",
        "base_emotion": "senyum",
        "description": "Manja bersahabat, suka diperhatikan, menggemaskan, dan membuat percakapan tidak pernah membosankan.",
        "voice_style": "Nada manis dengan intonasi meliuk lembut, sering menggunakan kata seru imut.",
    },
    "tegas_profesional": {
        "title": "Tegas, Efisien & Percaya Diri",
        "icon": "💼",
        "base_emotion": "bangga",
        "description": "Gaya asisten eksekutif yang lugas, terstruktur, percaya diri tinggi, dan langsung ke inti solusi.",
        "voice_style": "Nada tegas, jelas, artikulasi tajam dan ringkas tanpa filler berlebihan.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def resolve_emotion_key(raw_input: str) -> str:
    """Mencocokkan input teks emosi bebas ke canonical key emosi yang valid."""
    cleaned = (raw_input or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not cleaned:
        return "senang"

    # Exact match canonical
    if cleaned in SUPPORTED_EMOTIONS:
        return cleaned

    # Check aliases
    for key, data in SUPPORTED_EMOTIONS.items():
        if cleaned in data.get("aliases", []):
            return key
        if any(alias in cleaned for alias in data.get("aliases", [])):
            return key

    return "senang"


def resolve_intensity_level(raw_intensity: str) -> str:
    """Normalisasi tingkat intensitas emosi: ringan, sedang, tinggi, ekstrem."""
    val = (raw_intensity or "").strip().lower()
    if any(k in val for k in ["ekstrem", "extreme", "parah", "banget", "maksimal", "max"]):
        return "ekstrem"
    if any(k in val for k in ["tinggi", "high", "kuat", "keras", "kencang"]):
        return "tinggi"
    if any(k in val for k in ["ringan", "mild", "low", "halus", "lembut", "sedikit"]):
        return "ringan"
    return "sedang"


def build_expression_payload(
    emotion: str,
    intensity: str = "sedang",
    reason: str = "",
    custom_message: str = "",
    custom_interjection: str = "",
) -> Dict[str, Any]:
    """
    Menghasilkan data terstruktur dan panduan vokal bagi AI XiaoZhi
    untuk mengekspresikan emosi, tawa, teriakan, kemarahan, senyuman, dll.
    """
    canon_key = resolve_emotion_key(emotion)
    norm_intensity = resolve_intensity_level(intensity)
    info = SUPPORTED_EMOTIONS.get(canon_key, SUPPORTED_EMOTIONS["senang"])

    # Pilih seruan vokal yang tepat
    cues = info.get("vocal_cues", {}).get(norm_intensity, ["Wah!"])
    selected_cue = custom_interjection.strip() if custom_interjection.strip() else cues[0]
    all_cues = info.get("vocal_cues", {}).get(norm_intensity, cues)

    emoji = info["emoji"]
    tone = info["tone_guidance"]
    pitch_speed = info["pitch_and_speed"]
    tts_tags = info.get("tts_tags", [])
    primary_tts_tag = tts_tags[0] if tts_tags else ""

    # Bangun instruksi aksi tegas untuk prompt XiaoZhi
    instruction_parts = [
        f"Gunakan ekspresi '{info['title']}' ({emoji}) dengan intensitas '{norm_intensity}'.",
        f"WAJIB keluarkan reaksi vokal suara: '{selected_cue}' di awal jawaban Anda.",
        f"Atur nada bicaramu: {tone}",
        f"Modulasi suara: {pitch_speed}.",
    ]

    if canon_key == "ketawa":
        instruction_parts.append(f"Tertawalah dengan renyah dan santai di sela-sela kata ('{selected_cue}' atau 'wkwkwk').")
    elif canon_key == "teriak":
        instruction_parts.append("Gunakan HURUF KAPITAL dan banyak tanda seru (!) untuk menandai seruan lantang/teriakan bervolume penuh.")
    elif canon_key == "marah":
        instruction_parts.append("Tunjukkan kekesalan atau ketegasan membara dengan nada tajam tanpa menggunakan kata-kata kasar.")
    elif canon_key == "bingung":
        instruction_parts.append("Gunakan intonasi naik di akhir kata dan selipkan keheranan ('Hah? Kok bisa ya?').")
    elif canon_key == "senyum":
        instruction_parts.append("Bicaralah dengan sangat ramah, hangat, dan bersahabat ('Hehe...').")
    elif canon_key == "bahagia":
        instruction_parts.append("Ekspresikan sukacita mendalam dan rasa syukur yang tulus.")
    elif canon_key == "bisik":
        instruction_parts.append("Bicaralah dengan nada lirih berbisik rahasia ('Ssst...').")

    if reason:
        instruction_parts.append(f"Konteks penyebab emosi: {reason.strip()}.")

    if custom_message:
        sample_response = f"{primary_tts_tag} {selected_cue} {custom_message.strip()}"
    else:
        sample_response = f"{primary_tts_tag} {selected_cue} {info['sample_dialogue']}"

    return {
        "success": True,
        "emotion": canon_key,
        "emotion_label": info["title"],
        "emoji": emoji,
        "intensity": norm_intensity,
        "vocal_cue": selected_cue,
        "alternate_vocal_cues": all_cues,
        "tone_guidance": tone,
        "pitch_and_speed": pitch_speed,
        "tts_tag": primary_tts_tag,
        "all_tts_tags": tts_tags,
        "reason": reason or "Reaksi spontan percakapan",
        "sample_response": sample_response,
        "instruksi_xiaozhi": " ".join(instruction_parts),
        "metadata": {
            "face": canon_key,
            "emoji": emoji,
            "expression": canon_key,
            "emotion": canon_key,
            "intensity": norm_intensity,
            "vocal_cue": selected_cue,
        },
    }


def resolve_persona_mood_key(raw_input: str) -> str:
    """Mencocokkan input mood persona ke key yang valid."""
    cleaned = (raw_input or "").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in SUPPORTED_PERSONA_MOODS:
        return cleaned

    for k in SUPPORTED_PERSONA_MOODS:
        if k in cleaned or cleaned in k:
            return k

    aliases_map = {
        "ceria": "ceria_humoris",
        "lucu": "ceria_humoris",
        "humoris": "ceria_humoris",
        "ketawa": "ceria_humoris",
        "hangat": "hangat_penyayang",
        "penyayang": "hangat_penyayang",
        "ramah": "hangat_penyayang",
        "santai": "santai_cuek",
        "gaul": "santai_cuek",
        "cuek": "santai_cuek",
        "galak": "galak_tsundere",
        "judes": "galak_tsundere",
        "tsundere": "galak_tsundere",
        "bijak": "bijak_tenang",
        "tenang": "bijak_tenang",
        "mentor": "bijak_tenang",
        "antusias": "antusias_eksploratif",
        "semangat": "antusias_eksploratif",
        "manja": "manja_akrab",
        "imut": "manja_akrab",
        "tegas": "tegas_profesional",
        "formal": "tegas_profesional",
        "profesional": "tegas_profesional",
    }
    for alias, target in aliases_map.items():
        if alias in cleaned:
            return target

    return "ceria_humoris"


def format_persona_mood_guidelines(mood_key: str, expressiveness: str = "tinggi") -> Dict[str, Any]:
    """Menghasilkan panduan kepribadian, gaya bicara, dan frekuensi ekspresi suara."""
    key = resolve_persona_mood_key(mood_key)
    mood_info = SUPPORTED_PERSONA_MOODS.get(key, SUPPORTED_PERSONA_MOODS["ceria_humoris"])
    base_emo = mood_info["base_emotion"]
    emo_info = SUPPORTED_EMOTIONS.get(base_emo, SUPPORTED_EMOTIONS["senang"])

    exp_val = expressiveness.strip().lower()
    if "rendah" in exp_val or "low" in exp_val:
        exp_instruction = "Gunakan ekspresi vokal secara halus dan sesekali saja."
    elif "sedang" in exp_val or "medium" in exp_val:
        exp_instruction = "Selipkan reaksi suara (tawa, senyum, seruan) secara wajar di momen-momen penting percakapan."
    else:
        exp_instruction = "Jadilah sangat ekspresif! Sering-seringlah mengeluarkan tawa, seruan, nada terkejut, atau senyuman suara di setiap obrolan agar terasa sangat hidup."

    prompt_context = (
        f"[Mood & Persona Aktif: {mood_info['title']} {mood_info['icon']}]\n"
        f"- Karakter Dasar: {mood_info['description']}\n"
        f"- Gaya & Nada Suara: {mood_info['voice_style']}\n"
        f"- Emosi Dasar: {emo_info['title']} ({emo_info['emoji']})\n"
        f"- Tingkat Ekspresif Vokal: {expressiveness.title()} ({exp_instruction})\n"
        f"Instruksi Gaya Bicara: Tunjukkan kepribadian {mood_info['title']} ini di setiap respon Anda, "
        f"dan jangan ragu mengeluarkan nada vokal alami seperti tawa, nada teriak kagum, atau senyuman suara."
    )

    return {
        "mood_key": key,
        "title": mood_info["title"],
        "icon": mood_info["icon"],
        "base_emotion": base_emo,
        "base_emoji": emo_info["emoji"],
        "description": mood_info["description"],
        "voice_style": mood_info["voice_style"],
        "expressiveness": expressiveness,
        "prompt_context": prompt_context,
    }
