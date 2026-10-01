import pytest
from xiaozhi.routers.youtube import sanitize_youtube_query

def test_sanitize_youtube_query_clipped_asr():
    # As seen in VPS log: 'kan lagu On My Way Alan Walker di Youtube.'
    raw = "kan lagu On My Way Alan Walker di Youtube."
    clean = sanitize_youtube_query(raw)
    assert clean == "On My Way Alan Walker"

def test_sanitize_youtube_query_full_indonesian():
    raw = "putarkan lagu Alan Walker On My Way di youtube"
    clean = sanitize_youtube_query(raw)
    assert clean == "Alan Walker On My Way"

def test_sanitize_youtube_query_with_punctuation():
    raw = "tolong setel musik Lathi di youtube music!"
    clean = sanitize_youtube_query(raw)
    assert clean == "Lathi"

def test_sanitize_youtube_query_with_prefix():
    raw = "halo xiaozhi tolong putarkan lagu judulnya Separuh Aku dari youtube"
    clean = sanitize_youtube_query(raw)
    assert clean == "Separuh Aku"

def test_sanitize_youtube_query_english():
    raw = "play song Faded by Alan Walker on youtube."
    clean = sanitize_youtube_query(raw)
    assert clean == "Faded by Alan Walker"

def test_sanitize_youtube_query_plain_song():
    raw = "bohemian rhapsody"
    clean = sanitize_youtube_query(raw)
    assert clean == "bohemian rhapsody"

def test_sanitize_youtube_query_empty():
    assert sanitize_youtube_query("") == ""
    assert sanitize_youtube_query("   ") == ""
