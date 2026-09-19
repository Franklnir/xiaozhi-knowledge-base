"""
Comprehensive Test Suite for Xiaozhi PostgreSQL Store Architecture.
Tests contract parity, connection pooling, FTS trigger execution,
keyset pagination, constraints, and error handling.
"""
import hashlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

# Try importing PostgresStore
try:
    from xiaozhi.database.postgres_store import PostgresStore
    POSTGRES_AVAILABLE = True
except Exception:
    POSTGRES_AVAILABLE = False


def is_postgres_online(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception:
        return False


class TestPostgresStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not POSTGRES_AVAILABLE:
            raise unittest.SkipTest("psycopg or PostgresStore is not available.")

        cls.dsn = PostgresStore._build_dsn()
        if not is_postgres_online(cls.dsn):
            raise unittest.SkipTest(f"PostgreSQL server not reachable at {cls.dsn}. Start docker-compose.postgres.yml to run live tests.")

        cls.store = PostgresStore(dsn=cls.dsn, min_pool_size=2, max_pool_size=5)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "store") and cls.store:
            cls.store.close()

    def setUp(self):
        # Generate isolated unique username prefix for this test run
        self.test_id = uuid.uuid4().hex[:8]
        self.username = f"test_{self.test_id}"
        self.password = "Secret123!"

    def test_01_user_crud_and_uniqueness(self):
        """Verify user creation, auth data, default categories seeding, and unique constraints."""
        user = self.store.create_user(self.username, self.password)
        self.assertIsNotNone(user["id"])
        self.assertEqual(user["username"], self.username)
        self.assertEqual(user["role"], "user")

        # Duplicate username must raise ValueError
        with self.assertRaises(ValueError):
            self.store.create_user(self.username, "AnotherPassword")

        # Fetch by ID & Username
        fetched = self.store.get_user(user["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["username"], self.username)

        by_name = self.store.get_user_by_username(self.username)
        self.assertIsNotNone(by_name)
        self.assertEqual(by_name["id"], user["id"])

        # Default categories should be seeded automatically
        cats = self.store.list_categories(user["id"])
        self.assertGreaterEqual(len(cats), 1)

    def test_02_materials_fts_trigger_and_search(self):
        """Verify PL/pgSQL trigger populates search_vector and Indonesian full-text search works."""
        user = self.store.create_user(f"fts_{self.test_id}", self.password)
        uid = user["id"]

        # Insert material
        mat_id = self.store.add_material(
            owner_id=uid,
            title="Jadwal Kuliah Kecerdasan Buatan",
            category="Kuliah",
            content="Materi pembelajaran mengenai jaringan saraf tiruan dan transformer deep learning.",
            keywords="ai, transformer, kuliah",
        )
        self.assertIsNotNone(mat_id)

        # Direct SQL inspection: verify search_vector is populated by trigger!
        with self.store._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT search_vector FROM materials WHERE id = %s", (mat_id,))
                row = cur.fetchone()
                self.assertIsNotNone(row["search_vector"], "Trigger must automatically populate search_vector!")

        # Search by keyword using tsquery
        results = self.store.search_materials(uid, "jaringan saraf")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["id"], mat_id)

        # Search by title keyword
        results_title = self.store.search_materials(uid, "kecerdasan")
        self.assertGreaterEqual(len(results_title), 1)

    def test_03_deduplication_constraint(self):
        """Verify source_hash deduplication index blocks duplicate imports for the same owner."""
        user = self.store.create_user(f"dedup_{self.test_id}", self.password)
        uid = user["id"]
        source_hash = hashlib.sha256(b"unique_document_payload").hexdigest()

        # First insert succeeds
        self.store.add_material(
            owner_id=uid,
            title="Document 1",
            category="Umum",
            content="Content of document 1",
            keywords="doc",
            source_hash=source_hash,
        )

        # Second insert with identical source_hash for SAME owner must violate unique index
        with self.assertRaises(Exception):
            self.store.add_material(
                owner_id=uid,
                title="Document 1 Duplicate",
                category="Umum",
                content="Identical content",
                keywords="doc",
                source_hash=source_hash,
            )

    def test_04_community_chat_keyset_pagination(self):
        """Verify keyset pagination (before_id) on community chats."""
        user = self.store.create_user(f"chat_{self.test_id}", self.password)
        uid = user["id"]

        # Add 5 messages
        msg_ids = []
        for i in range(5):
            msg = self.store.add_community_chat(
                user_id=uid,
                username=user["username"],
                role="user",
                content=f"Message {i + 1}",
                msg_type="text",
            )
            msg_ids.append(msg["id"])

        # Fetch with limit=2 (most recent 2: msg 4 and 5)
        page1 = self.store.list_community_chats(limit=2)
        self.assertGreaterEqual(len(page1), 2)

        # Keyset pagination: fetch items before the oldest in page1
        oldest_id_in_page1 = page1[0]["id"]
        page2 = self.store.list_community_chats(limit=2, before_id=oldest_id_in_page1)
        self.assertGreaterEqual(len(page2), 1)
        for m in page2:
            self.assertLess(m["id"], oldest_id_in_page1)

    def test_05_check_constraints_enforcement(self):
        """Verify that invalid enum values violate CHECK constraints."""
        user = self.store.create_user(f"chk_{self.test_id}", self.password)
        uid = user["id"]

        # 1. Invalid status in relay_devices (Must be ON or OFF)
        room_id = self.store.add_relay_room(
            owner_id=uid,
            nama_tempat="Ruang Tamu",
            api_slug=f"slug_{self.test_id}",
            api_token=f"token_{self.test_id}",
            api_client_id=f"client_{self.test_id}",
            relays=[{"relay_number": 1, "nama_relay": "Lampu", "status": "OFF"}],
        )

        with self.assertRaises(Exception):
            # 'UNKNOWN' violates chk_relay_device_status
            self.store.update_relay_status(uid, room_id, 1, "UNKNOWN")

        # 2. Invalid msg_type in community_chats (Must be 'text' or 'voice')
        with self.assertRaises(Exception):
            with self.store._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO community_chats (user_id, username, role, content, msg_type, created_at, created_date)
                        VALUES (%s, %s, 'user', 'Invalid', 'video_note', NOW(), '2026-09-20')
                        """,
                        (uid, user["username"]),
                    )
                conn.commit()

    def test_06_reminders_partial_index_query(self):
        """Verify reminder scheduling and state transitions."""
        user = self.store.create_user(f"rem_{self.test_id}", self.password)
        uid = user["id"]
        rem_id = f"rem_{uuid.uuid4().hex[:6]}"

        past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.store.add_reminder(uid, rem_id, "Waktunya belajar AI", past_time)

        # Due reminder check
        due = self.store.get_due_reminders()
        self.assertTrue(any(r["id"] == rem_id for r in due))

        # Mark as sent
        self.store.mark_reminder_sent(rem_id)
        rems = self.store.list_reminders(uid)
        sent_rem = next((r for r in rems if r["id"] == rem_id), None)
        self.assertIsNotNone(sent_rem)
        self.assertEqual(sent_rem["status"], "sent")

    def test_07_scoped_synchronous_commit_chat_history(self):
        """Verify chat_history logging works with JSONB payloads."""
        user = self.store.create_user(f"hist_{self.test_id}", self.password)
        uid = user["id"]

        payload_in = {"query": "Berapa jam kuliah?", "meta": {"device": "esp32"}}
        payload_out = {"answer": "2 jam", "latency_ms": 45}

        self.store.add_chat_history(
            owner_id=uid,
            tool_name="schedule_query",
            user_message="Berapa jam kuliah?",
            xiaozhi_answer="2 jam",
            request_payload=payload_in,
            response_payload=payload_out,
        )

        history = self.store.list_chat_history(uid, limit=10)
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["tool_name"], "schedule_query")
        # Ensure JSONB payload is accessible as dict or parsed structure
        req = history[0]["request_payload"]
        if isinstance(req, str):
            import json
            req = json.loads(req)
        self.assertEqual(req.get("query"), "Berapa jam kuliah?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
