import pytest
from xiaozhi.services.semantic_memory_service import (
    analyze_user_persona_from_chats,
    chunk_conversations_for_persona,
    _PERSONA_ANALYSIS_CACHE
)

def test_chunking_operational_vs_personal():
    chats = [
        {"user_message": "nyalakan lampu ruang tamu", "tool_name": "execute_relay_command", "xiaozhi_answer": "Lampu ruang tamu dinyalakan."},
        {"user_message": "matikan saklar 2", "tool_name": "execute_relay_command", "xiaozhi_answer": "Saklar 2 dimatikan."},
        {"user_message": "saya suka koding python dan mikrokontroler esp32", "tool_name": "save_chat_history", "xiaozhi_answer": "Keren sekali proyek IoT Anda!"},
    ]
    chunks = chunk_conversations_for_persona(chats)
    assert len(chunks) == 3
    # Check operational classification
    assert chunks[0]["is_operational"] is True
    assert chunks[1]["is_operational"] is True
    assert chunks[2]["is_operational"] is False
    assert chunks[2]["is_self_disclosure"] is True

def test_no_fake_fallbacks_when_no_complaints():
    # Only IoT usage and coding enthusiasm
    chats = [
        {"user_message": "nyalakan lampu ruang kerja", "tool_name": "execute_relay_command"},
        {"user_message": "saya hobi ngulik mikrokontroler esp32 dan python", "tool_name": "save_chat_history"},
        {"user_message": "putar lagu lofi kesukaan", "tool_name": "play_playlist_track"},
    ]
    analysis = analyze_user_persona_from_chats(chats, stored_personas=[], force_refresh=True)
    # Must NOT invent fake skripsi or finance problems
    assert isinstance(analysis["challenges"], list)
    assert len(analysis["challenges"]) == 0
    
    # Must recognize IoT & Coding as dominant hobby
    assert len(analysis["hobbies"]) > 0
    top_hobby = analysis["hobbies"][0]["name"]
    assert "Koding & Teknologi IoT" in top_hobby

def test_real_malfunction_detection():
    # User genuinely reports hardware failure
    chats = [
        {"user_message": "mati lampu nih dan server down", "tool_name": "save_chat_history"},
        {"user_message": "perangkat rusak dan konslet di saklar", "tool_name": "save_chat_history"},
    ]
    analysis = analyze_user_persona_from_chats(chats, stored_personas=[], force_refresh=True)
    assert len(analysis["challenges"]) > 0
    top_challenge = analysis["challenges"][0]["name"]
    assert "Kendala Teknis & Perangkat" in top_challenge

def test_persona_caching_and_force_refresh():
    cache_key = "test_cache_owner_1"
    chats = [{"user_message": "saya suka main game valorant", "tool_name": "save_chat_history"}]
    
    res1 = analyze_user_persona_from_chats(chats, cache_key=cache_key, force_refresh=True)
    assert cache_key in _PERSONA_ANALYSIS_CACHE
    
    # Modify chats in call, but without force_refresh it should return cached res1
    chats_new = [{"user_message": "saya suka koding", "tool_name": "save_chat_history"}]
    res2 = analyze_user_persona_from_chats(chats_new, cache_key=cache_key, force_refresh=False)
    assert res2["hobbies"][0]["name"] == res1["hobbies"][0]["name"]
    
    # Now force refresh
    res3 = analyze_user_persona_from_chats(chats_new, cache_key=cache_key, force_refresh=True)
    assert "Koding" in res3["hobbies"][0]["name"]
