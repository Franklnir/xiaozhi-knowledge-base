import asyncio
import json
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from xiaozhi import dependencies
from xiaozhi.config import SESSION_COOKIE, session_serializer
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.main import app
from xiaozhi.mcp.context import mcp_active_owner_ctx
from xiaozhi.mcp.tools import register_tools
from xiaozhi.routers import playlist, youtube
from xiaozhi.services import radio_service


def test_save_and_play_registered_radio(tmp_path, monkeypatch):
    page = "https://open.noice.id/catalog/test-station"
    source = "https://radio.example.test/live/playlist.m3u8"
    second_page = "https://open.noice.id/catalog/second-station"
    config = tmp_path / "radio_stations.json"
    config.write_text(json.dumps([{
        "id": "test-station", "title": "Radio Contoh", "page_url": page, "stream_url": source,
    }, {
        "id": "second-station", "title": "Radio Malam", "page_url": second_page,
        "stream_url": "https://radio.example.test/night/playlist.m3u8",
    }]), encoding="utf-8")
    monkeypatch.setattr(radio_service, "STATIONS_FILE", config)
    store = SQLiteStore(str(tmp_path / "radio.db"))
    owner = store.create_user("radio_owner", "test-password-123")
    other = store.create_user("radio_other", "test-password-123")
    monkeypatch.setattr(dependencies, "_store", store)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps(owner))

    async def fake_audio(video_id, stream_url, **opts):
        assert video_id == "radio_live_test-station"
        assert stream_url == source
        assert opts["radio"] is True and opts["user_id"] == owner["id"]
        yield b"OggS synthetic radio"

    def reject_music_probe(*args, **kwargs):
        raise AssertionError("Radio should not invoke the music duration probe")

    monkeypatch.setattr(youtube, "_stream_opus_audio", fake_audio)
    monkeypatch.setattr(playlist, "check_media_duration_limit", reject_music_probe)

    result = client.post("/api/v1/playlist", json={"youtube_url": page + "?utm_source=app&referrer_id=one"})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["platform"] == "radio"
    track = payload["track"]
    track_id = track["id"]
    assert track["title"] == "Radio Contoh" and track["duration"] == "LIVE"
    assert track["youtube_url"] == page
    assert client.get("/api/v1/playlist").json()["tracks"][0]["cache_status"] == "live"
    status_url = "/api/v1/playlist/radio-status"
    assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "registered"

    assert client.post("/api/v1/playlist", json={"youtube_url": "https://other.example.test/live.m3u8"}).status_code == 400
    noice_link = "https://open.noice.id/catalog/f7c42e96-4ce6-4788-b81b-ffc9c43e3529"
    unregistered = {"youtube_url": noice_link + "?utm_source=app"}
    response = client.post("/api/v1/playlist", json=unregistered)
    assert response.status_code == 400 and "Tautan Noice terdeteksi" in response.json()["detail"]
    response = client.put(f"/api/v1/playlist/{track_id}", json=unregistered)
    assert response.status_code == 400 and "Stasiun belum terdaftar" in response.json()["detail"]
    lookalike = noice_link.replace("open.noice.id", "open.noice.id.evil.test")
    response = client.post("/api/v1/playlist", json={"youtube_url": lookalike})
    assert response.status_code == 400 and "Tautan Noice terdeteksi" not in response.json()["detail"]
    assert client.post(f"/api/v1/playlist/{track_id}/cache").status_code == 400
    assert client.post("/api/v1/playlist/cache-all").json()["queued_count"] == 0
    store.add_playlist_track(owner["id"], title="Lagu YouTube", youtube_url="https://youtu.be/aaaaaaaaaaa", video_id="aaaaaaaaaaa")
    store.add_playlist_track(owner["id"], title="Lagu TikTok", youtube_url="https://vt.tiktok.com/ZStest/", video_id="tt_ZStest")
    second = client.post("/api/v1/playlist", json={"youtube_url": second_page}).json()["track"]
    assert second["track_number"] != 2
    rendered = client.get("/playlist")
    assert rendered.status_code == 200
    assert "Playlist YouTube &amp; TikTok" in rendered.text
    assert "Playlist Stream Radio" in rendered.text
    music_table = rendered.text.split('id="playlistTableBody"', 1)[1].split("</tbody>", 1)[0]
    radio_table = rendered.text.split('id="radioPlaylistTableBody"', 1)[1].split("</tbody>", 1)[0]
    assert "Lagu YouTube" in music_table and "Lagu TikTok" in music_table
    assert "Radio Contoh" not in music_table
    assert "Radio Contoh" in radio_table and 'data-stream-state="registered"' in radio_table
    assert "Radio Malam" in radio_table and 'title="Putar radio nomor 2"' in radio_table
    assert "playRadioTrack" not in rendered.text and "previewYouTubeModal" not in radio_table

    client.cookies.set(SESSION_COOKIE, session_serializer.dumps(other))
    assert client.post(f"/api/v1/playlist/{track_id}/play").status_code == 404
    assert client.get("/api/v1/playlist").json()["tracks"] == []
    assert client.get(status_url).json()["tracks"] == {}
    direct = "/api/audio/play_direct"
    assert client.get(direct, params={"q": track["video_id"], "owner_id": owner["id"]}).status_code == 403
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps(owner))
    assert radio_service.find_radio_station(source).video_id == track["video_id"]
    edited = client.put(f"/api/v1/playlist/{track_id}", json={"youtube_url": source, "title": "Radio Pagi"})
    assert edited.json()["track"]["title"] == "Radio Pagi"

    response = client.post(f"/api/v1/playlist/{track_id}/play")
    assert response.status_code == 403, response.text
    assert client.get(direct, params={"q": track["video_id"]}).status_code == 403
    assert client.get(direct, params={"q": "Radio Pagi"}).status_code == 403
    assert client.post("/api/audio/queue", json={"video_id": track["video_id"]}).status_code == 403
    assert not store.get_pending_audio_commands(owner["id"])

    tools = {}

    class ToolRegistry:
        def tool(self, *args, **kwargs):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    register_tools(ToolRegistry(), store, lambda *_: None)
    ctx = mcp_active_owner_ctx.set(owner["id"])
    try:
        listings = tools["list_user_playlist"]
        radios = listings("radio")
        assert radios["total"] == radios["total_radio"] == 2
        assert "Ada 2 stasiun" in radios["message"]
        assert [(item["radio_number"], item["title"]) for item in radios["playlist"]] == [(1, "Radio Pagi"), (2, "Radio Malam")]
        assert listings("music")["total"] == 2 and listings()["total"] == 4
        clarify = tools["play_playlist_song"]("putar radio")
        assert clarify["needs_clarification"] and [item["number"] for item in clarify["choices"]] == [1, 2]
        assert not tools["play_playlist_song"]("putar radio nomor 3")["success"]
        assert not store.get_pending_audio_commands(owner["id"])

        voice = tools["play_playlist_song"]("putar radio Radio Pagi")
        assert voice["success"] and voice["source"] == "radio" and voice["radio_number"] == 1
        pending = store.get_pending_audio_commands(owner["id"])
        assert len(pending) == 1 and pending[0]["video_id"] == track["video_id"]
        assert not store.get_pending_audio_commands(other["id"])
        stream = urlsplit(pending[0]["stream_url"])
        assert client.get(stream.path).status_code == 403
        assert client.get(stream.path + "?radio_ticket=invalid").status_code == 403
        assert client.get(stream.path + "-wrong?" + stream.query).status_code == 403
        reply = client.get(stream.path + "?" + stream.query)
        assert reply.headers["content-type"] == "audio/ogg"
        assert reply.content == b"OggS synthetic radio"

        by_number = tools["play_youtube_song"]("putar radio nomor 2")
        assert by_number["success"] and by_number["title"] == "Radio Malam" and by_number["radio_number"] == 2
        second_command = store.get_pending_audio_commands(owner["id"])[-1]
        assert second_command["video_id"] == second["video_id"]
        ticket = parse_qs(urlsplit(second_command["stream_url"]).query)["radio_ticket"][0]
        assert radio_service.resolve_radio_playback(store, second["video_id"], ticket)[1]["id"] == second["id"]

        session = youtube.playback_tracker.start_session(
            user_id=owner["id"], username="radio_owner", video_id=track["video_id"], title="Radio Pagi",
        )
        try:
            assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "connecting"
            session.record_chunk(1000)
            assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "streaming"
            session.last_active_at -= 11
            assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "waiting"
            assert tools["stop_youtube_song"]()["stopped"]
            assert session.abort_event.is_set()
        finally:
            youtube.playback_tracker.end_session(session.session_id, reason="aborted")
        assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "registered"

        session = youtube.playback_tracker.start_session(
            user_id=owner["id"], username="radio_owner", video_id=track["video_id"], title="Radio Pagi",
        )
        youtube.playback_tracker.end_session(session.session_id, reason="error")
        assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "interrupted"
    finally:
        mcp_active_owner_ctx.reset(ctx)

    config.write_text("[]", encoding="utf-8")
    assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "unavailable"
    store.set_user_playlist_track_active(owner["id"], track_id, is_active=False)
    assert client.get(status_url).json()["tracks"][str(track_id)]["state"] == "disabled"
    assert client.post(f"/api/v1/playlist/{track_id}/play").status_code == 403
    assert client.get(stream.path + "?" + stream.query).status_code == 403
    assert client.delete(f"/api/v1/playlist/{track_id}").json()["success"]


def test_stop_radio_while_upstream_has_no_audio(monkeypatch):
    async def exercise():
        began_reading = asyncio.Event()

        class StalledEncoder:
            returncode = None
            pid = 123

            @property
            def stdout(self):
                return self

            async def read(self, _size):
                began_reading.set()
                await asyncio.Future()

            def kill(self):
                self.returncode = -1

            async def wait(self):
                return self.returncode

        encoder = StalledEncoder()

        async def start_encoder(*args, **kwargs):
            return encoder

        monkeypatch.setattr(youtube.asyncio, "create_subprocess_exec", start_encoder)
        monkeypatch.setattr(youtube, "_FFMPEG_PATH", "ffmpeg")
        owner_id = 505050
        stream = youtube._stream_opus_audio(
            "radio_live_stalled", "https://radio.example.test/live.m3u8", user_id=owner_id, radio=True,
        )
        reading = asyncio.create_task(anext(stream, None))
        await asyncio.wait_for(began_reading.wait(), timeout=2)
        assert youtube.playback_tracker.stop_user_playback(owner_id)
        assert await asyncio.wait_for(reading, timeout=2) is None
        assert encoder.returncode == -1
        assert youtube.playback_tracker.get_user_session(owner_id) is None

    asyncio.run(exercise())
