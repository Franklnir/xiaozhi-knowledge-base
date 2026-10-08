"""
Unit tests for Expression, Emotion, and Vocal Tone Tools for XiaoZhi AI.
"""
import pytest
from xiaozhi.config import ALL_MCP_TOOLS_CATALOG, ALL_MCP_TOOL_NAMES, EMOTE_ALIASES
from xiaozhi.core.utils import extract_emote_value, text_with_emote
from xiaozhi.services.expression_service import (
    SUPPORTED_EMOTIONS,
    SUPPORTED_PERSONA_MOODS,
    build_expression_payload,
    format_persona_mood_guidelines,
    resolve_emotion_key,
    resolve_intensity_level,
    resolve_persona_mood_key,
)


def test_required_emotions_present():
    """Memastikan semua emosi yang diminta user tersedia di katalog."""
    required = ["marah", "senyum", "bahagia", "bingung", "senang", "teriak", "ketawa"]
    for emo in required:
        assert emo in SUPPORTED_EMOTIONS, f"Emosi {emo} wajib ada di SUPPORTED_EMOTIONS"
        data = SUPPORTED_EMOTIONS[emo]
        assert "emoji" in data
        assert "vocal_cues" in data
        assert "tone_guidance" in data
        assert "sample_dialogue" in data


def test_resolve_emotion_aliases():
    """Menguji kecerdasan pencocokan alias kata emosi bahasa Indonesia dan Inggris."""
    assert resolve_emotion_key("marah") == "marah"
    assert resolve_emotion_key("ngamuk") == "marah"
    assert resolve_emotion_key("geram") == "marah"
    assert resolve_emotion_key("kesal") == "marah"
    assert resolve_emotion_key("angry") == "marah"

    assert resolve_emotion_key("senyum") == "senyum"
    assert resolve_emotion_key("tersenyum") == "senyum"
    assert resolve_emotion_key("ramah") == "senyum"
    assert resolve_emotion_key("smile") == "senyum"

    assert resolve_emotion_key("ketawa") == "ketawa"
    assert resolve_emotion_key("tertawa") == "ketawa"
    assert resolve_emotion_key("ngakak") == "ketawa"
    assert resolve_emotion_key("wkwk") == "ketawa"
    assert resolve_emotion_key("laugh") == "ketawa"

    assert resolve_emotion_key("teriak") == "teriak"
    assert resolve_emotion_key("shout") == "teriak"
    assert resolve_emotion_key("scream") == "teriak"
    assert resolve_emotion_key("berseru") == "teriak"

    assert resolve_emotion_key("bingung") == "bingung"
    assert resolve_emotion_key("confused") == "bingung"
    assert resolve_emotion_key("heran") == "bingung"

    assert resolve_emotion_key("bahagia") == "bahagia"
    assert resolve_emotion_key("happy") == "bahagia"
    assert resolve_emotion_key("sukacita") == "bahagia"

    assert resolve_emotion_key("senang") == "senang"
    assert resolve_emotion_key("excited") == "senang"


def test_intensity_level_resolution():
    """Menguji penentuan tingkat intensitas emosi."""
    assert resolve_intensity_level("ringan") == "ringan"
    assert resolve_intensity_level("sedikit") == "ringan"
    assert resolve_intensity_level("sedang") == "sedang"
    assert resolve_intensity_level("tinggi") == "tinggi"
    assert resolve_intensity_level("kencang") == "tinggi"
    assert resolve_intensity_level("ekstrem") == "ekstrem"
    assert resolve_intensity_level("parah banget") == "ekstrem"


def test_build_expression_payload_ketawa():
    """Menguji output payload saat ekspresi ketawa dipanggil."""
    res = build_expression_payload(emotion="ketawa", intensity="tinggi", reason="Lelucon lucu")
    assert res["success"] is True
    assert res["emotion"] == "ketawa"
    assert res["emoji"] == "😆"
    assert res["intensity"] == "tinggi"
    assert any(cue in ["HAHAHAHA!", "WKWKWKWK!", "BWAHAHAHA!"] for cue in [res["vocal_cue"]])
    assert "Tertawalah" in res["instruksi_xiaozhi"]
    assert "metadata" in res
    assert res["metadata"]["face"] == "ketawa"
    assert res["metadata"]["emoji"] == "😆"


def test_build_expression_payload_teriak():
    """Menguji output payload saat ekspresi teriak dipanggil."""
    res = build_expression_payload(emotion="teriak", intensity="ekstrem", reason="Kaget dan kegirangan")
    assert res["success"] is True
    assert res["emotion"] == "teriak"
    assert res["emoji"] == "📢"
    assert "HURUF KAPITAL" in res["instruksi_xiaozhi"]
    assert "WAAAAAAAGHHH" in res["vocal_cue"] or "TOLOOOONGGG" in res["vocal_cue"] or "ALLAHU AKBAR" in res["vocal_cue"] or "TIDAAAK" in res["vocal_cue"]
    assert "[shout]" in res["tts_tag"] or "[screaming]" in res["tts_tag"]


def test_build_expression_payload_marah():
    """Menguji output payload saat ekspresi marah dipanggil."""
    res = build_expression_payload(emotion="marah", intensity="sedang", reason="User mengejek")
    assert res["success"] is True
    assert res["emotion"] == "marah"
    assert res["emoji"] == "😠"
    assert "tegas" in res["tone_guidance"].lower() or "ketus" in res["tone_guidance"].lower()
    assert res["vocal_cue"] in ["Hih!", "Astaga!", "Nyebelin banget!", "Argh!"]


def test_build_expression_custom_interjection():
    """Menguji custom interjection / kata seru kustom."""
    res = build_expression_payload(
        emotion="ketawa",
        intensity="sedang",
        custom_interjection="Wkwkwk kocak banget!",
        custom_message="Sumpah itu lucu parah!",
    )
    assert res["vocal_cue"] == "Wkwkwk kocak banget!"
    assert "Wkwkwk kocak banget!" in res["sample_response"]
    assert "Sumpah itu lucu parah!" in res["sample_response"]


def test_supported_persona_moods():
    """Menguji katalog mood persona."""
    moods = ["ceria_humoris", "hangat_penyayang", "santai_cuek", "galak_tsundere", "bijak_tenang", "antusias_eksploratif", "manja_akrab", "tegas_profesional"]
    for m in moods:
        assert m in SUPPORTED_PERSONA_MOODS
    
    guidelines = format_persona_mood_guidelines("ceria_humoris", expressiveness="tinggi")
    assert guidelines["mood_key"] == "ceria_humoris"
    assert "Ceria" in guidelines["title"]
    assert "[Mood & Persona Aktif" in guidelines["prompt_context"]


def test_config_tools_catalog_and_names():
    """Memastikan tool-tool baru terdaftar di ALL_MCP_TOOLS_CATALOG dan ALL_MCP_TOOL_NAMES."""
    new_tools = ["express_emotion", "set_persona_mood", "get_available_expressions"]
    for t in new_tools:
        assert t in ALL_MCP_TOOL_NAMES, f"Tool {t} harus ada di ALL_MCP_TOOL_NAMES"
        matched = [item for item in ALL_MCP_TOOLS_CATALOG if item["name"] == t]
        assert len(matched) == 1
        assert matched[0]["category"] == "persona"


def test_config_emote_aliases_integration():
    """Memastikan mapping kata emosi bahasa Indonesia di EMOTE_ALIASES berfungsi dengan utils."""
    indonesian_emotes = ["marah", "senyum", "bahagia", "bingung", "senang", "teriak", "ketawa", "sedih", "kaget", "bisik"]
    for emo in indonesian_emotes:
        assert emo in EMOTE_ALIASES, f"Emote '{emo}' harus ada di EMOTE_ALIASES"
    
    # Test extract_emote_value dari utils
    payload = {"emotion": "ketawa", "content": "Halo!"}
    assert extract_emote_value(payload) == "😆"

    payload_marah = {"expression": "marah", "content": "Apa?!"}
    assert extract_emote_value(payload_marah) == "😠"

    payload_teriak = {"face": "teriak", "content": "WAAAH!"}
    assert extract_emote_value(payload_teriak) == "📢"

    # Test text_with_emote
    formatted = text_with_emote("Halo semuanya", "😆")
    assert formatted == "😆 Halo semuanya"


def test_tools_py_registration(tmp_path):
    """Menguji pendaftaran dan eksekusi fungsi tool di register_tools xiaozhi/mcp/tools.py."""
    class DummyServer:
        def __init__(self):
            self.tools = {}

        def tool(self, *args, **kwargs):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

    server = DummyServer()
    from xiaozhi.mcp.tools import register_tools
    from xiaozhi.database.sqlite_store import SQLiteStore

    db_file = tmp_path / "test_mcp_tools.db"
    store = SQLiteStore(str(db_file))

    register_tools(server, store, lambda *a, **k: None)
    assert "express_emotion" in server.tools
    assert "set_persona_mood" in server.tools
    assert "get_available_expressions" in server.tools

    # Test eksekusi express_emotion
    res_emo = server.tools["express_emotion"](emotion="ketawa", intensity="tinggi", reason="Candaan lucu")
    assert res_emo["success"] is True
    assert res_emo["emoji"] == "😆"
    assert res_emo["emotion"] == "ketawa"

    # Test eksekusi teriak
    res_shout = server.tools["express_emotion"](emotion="teriak", intensity="ekstrem")
    assert res_shout["success"] is True
    assert res_shout["emoji"] == "📢"

    # Test eksekusi set_persona_mood
    res_mood = server.tools["set_persona_mood"](mood="ceria_humoris", expressiveness="tinggi")
    assert res_mood["success"] is True
    assert "Ceria" in res_mood["title"]

    # Test eksekusi get_available_expressions
    res_avail = server.tools["get_available_expressions"]()
    assert res_avail["success"] is True
    assert res_avail["total_ekspresi"] >= 15
    assert res_avail["total_mood_persona"] >= 8
