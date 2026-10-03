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


def test_esp32_c3_audio_profile_resolution():
    from xiaozhi.routers.youtube import resolve_chip_audio_profile, resolve_adaptive_bitrate

    # ESP32-C3 must resolve to 16kHz to prevent single-core CPU overload / watchdog reset
    assert resolve_chip_audio_profile("esp32c3") == 16000
    assert resolve_chip_audio_profile("esp32-c3") == 16000
    assert resolve_chip_audio_profile("c3") == 16000

    # ESP32-S3 resolves to 24kHz
    assert resolve_chip_audio_profile("esp32s3") == 24000
    assert resolve_chip_audio_profile("esp32-s3") == 24000
    assert resolve_chip_audio_profile("s3") == 24000

    # Bitrate for ESP32-C3: ladder is 6k -> 8k -> 10k -> 14k, max 14k, default 10k
    assert resolve_adaptive_bitrate("auto", rssi=-60, chip="esp32c3") == "14k"
    assert resolve_adaptive_bitrate("auto", rssi=-70, chip="esp32c3") == "10k"
    assert resolve_adaptive_bitrate("auto", rssi=-80, chip="esp32c3") == "8k"
    assert resolve_adaptive_bitrate("auto", rssi=-90, chip="esp32c3") == "6k"
    assert resolve_adaptive_bitrate("auto", rssi=None, chip="esp32c3") == "10k"
    assert resolve_adaptive_bitrate("12k", chip="esp32c3") == "10k"
    assert resolve_adaptive_bitrate("16k", chip="esp32c3") == "14k"
    assert resolve_adaptive_bitrate("32k", chip="esp32c3") == "14k"
    assert resolve_adaptive_bitrate("auto", rssi=-60, chip="esp32s3") == "30k"

