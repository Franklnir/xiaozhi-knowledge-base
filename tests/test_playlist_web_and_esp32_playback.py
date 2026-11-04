import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from xiaozhi.main import app


class FakeStore:
    def __init__(self):
        self.playlists = {
            1: [
                {
                    "id": 10,
                    "owner_id": 1,
                    "track_number": 1,
                    "title": "Lagu Hebat",
                    "artist": "Penyanyi",
                    "youtube_url": "https://www.youtube.com/watch?v=vid123",
                    "video_id": "vid123",
                    "duration": "03:30",
                    "play_count": 5,
                    "is_active": True,
                }
            ]
        }
        self.queued = []
        self.acked = []

    def get_user_playlist(self, owner_id):
        return list(self.playlists.get(owner_id, []))

    def get_playlist_track(self, owner_id, track_id):
        for t in self.playlists.get(owner_id, []):
            if t["id"] == track_id:
                return dict(t)
        return None

    def get_top_played_playlist(self, owner_id, limit=5):
        return self.get_user_playlist(owner_id)[:limit]

    def increment_playlist_play_count(self, owner_id, track_id):
        track = self.get_playlist_track(owner_id, track_id)
        if track:
            track["play_count"] += 1
            return track
        return None

    def get_user_mac_address(self, owner_id):
        return "AA:BB:CC:DD:EE:FF" if owner_id == 1 else None

    def get_user_access_plus(self, owner_id):
        return {"playlist_quota_enabled": False, "max_playlist_tracks": 15}

    def expire_audio_commands(self, minutes=30):
        return 0

    def get_pending_audio_commands(self, owner_id):
        return list(self.queued)

    def ack_audio_command(self, owner_id, cmd_id):
        self.acked.append(cmd_id)

    def queue_audio_command(self, owner_id, title="", stream_url="", video_url="", duration="", video_id=""):
        cmd = {"id": len(self.queued) + 1, "owner_id": owner_id, "title": title, "stream_url": stream_url, "video_id": video_id}
        self.queued.append(cmd)
        return cmd


def test_api_play_playlist_track_with_device():
    fake_store = FakeStore()
    user = {"id": 1, "username": "tester"}

    with patch("xiaozhi.routers.playlist.get_store", return_value=fake_store), \
         patch("xiaozhi.routers.playlist.require_user", return_value=user):
        client = TestClient(app)
        resp = client.post("/api/v1/playlist/10/play")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["has_registered_device"] is True
        assert data["device_mac"] == "AA:BB:CC:DD:EE:FF"
        assert "vid123" in data["stream_url"]
        assert "vid123" == data["video_id"]
        assert len(fake_store.queued) == 1


def test_api_play_playlist_track_without_device():
    fake_store = FakeStore()
    # User 2 has no registered MAC
    fake_store.playlists[2] = [
        {
            "id": 20,
            "owner_id": 2,
            "track_number": 1,
            "title": "Lagu User 2",
            "artist": "Artis 2",
            "youtube_url": "https://www.youtube.com/watch?v=vid999",
            "video_id": "vid999",
            "duration": "02:15",
            "play_count": 0,
            "is_active": True,
        }
    ]
    user2 = {"id": 2, "username": "user2"}

    with patch("xiaozhi.routers.playlist.get_store", return_value=fake_store), \
         patch("xiaozhi.routers.playlist.require_user", return_value=user2):
        client = TestClient(app)
        resp = client.post("/api/v1/playlist/20/play")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["has_registered_device"] is False
        assert data["device_mac"] is None
        assert "Tes Web" in data["message"]
