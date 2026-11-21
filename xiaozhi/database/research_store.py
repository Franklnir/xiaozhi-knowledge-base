"""Persistence for numbered research topics, sessions and citations."""

import json
import logging
from contextlib import contextmanager

from xiaozhi.core.research_evidence import analyze_research_evidence, continuation_evidence
from xiaozhi.core.utils import utc_now
from xiaozhi.services.research_audit import is_source_access


logger = logging.getLogger(__name__)


RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_counters (
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL,
    last_number INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, slot)
);
CREATE TABLE IF NOT EXISTS research_topics (
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    last_session INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, slot, number),
    UNIQUE (owner_id, slot, title_key)
);
CREATE TABLE IF NOT EXISTS research_runs (
    owner_id BIGINT NOT NULL,
    slot INTEGER NOT NULL,
    topic_number INTEGER NOT NULL,
    session_number INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (owner_id, slot, topic_number, session_number),
    UNIQUE (owner_id, slot, request_id),
    FOREIGN KEY (owner_id, slot, topic_number)
        REFERENCES research_topics(owner_id, slot, number) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS research_sources (
    owner_id BIGINT NOT NULL,
    slot INTEGER NOT NULL,
    topic_number INTEGER NOT NULL,
    number INTEGER NOT NULL,
    source_key TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (owner_id, slot, topic_number, number),
    UNIQUE (owner_id, slot, topic_number, source_key),
    FOREIGN KEY (owner_id, slot, topic_number)
        REFERENCES research_topics(owner_id, slot, number) ON DELETE CASCADE
);
"""

RESEARCH_RETRY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS research_one_retry_per_session
    ON research_runs(owner_id, slot, topic_number, retry_of) WHERE retry_of IS NOT NULL
"""


class ResearchStore:
    def __init__(self, store, owner_id: int, slot: int):
        self.store = store
        self.scope = (int(owner_id), int(slot))

    @staticmethod
    def supported(store) -> bool:
        return callable(getattr(type(store), "_get_research_conn", None))

    @contextmanager
    def _queries(self):
        with self.store._get_research_conn(self.scope[0]) as conn:
            postgres = hasattr(self.store, "pool")

            def execute(query, args=(), *, values=()):
                return conn.execute(query.replace("?", "%s") if postgres else query, (*values, *self.scope, *args))

            yield execute

    def list_topics(self, page=1, title=""):
        term = " ".join(title.casefold().split())
        pattern = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        with self._queries() as query:
            count = query(
                """SELECT COUNT(*) AS total FROM research_topics
                   WHERE owner_id=? AND slot=? AND archived=0 AND title_key LIKE ? ESCAPE '!'""",
                (pattern,),
            ).fetchone()["total"]
            rows = query(
                """SELECT * FROM research_topics WHERE owner_id=? AND slot=? AND title_key LIKE ? ESCAPE '!'
                   ORDER BY number DESC LIMIT 25 OFFSET ?""",
                (pattern, (max(1, page) - 1) * 25),
            ).fetchall()
        return count, [dict(row) for row in rows]

    def create_or_find(self, title):
        title = " ".join((title or "").split())[:160]
        if not title:
            raise ValueError("Isi judul penelitian terlebih dahulu.")
        with self._queries() as query:
            query("INSERT INTO research_counters(owner_id, slot) VALUES (?, ?) ON CONFLICT DO NOTHING")
            query("UPDATE research_counters SET last_number=last_number WHERE owner_id=? AND slot=?")
            topic = query(
                "SELECT * FROM research_topics WHERE owner_id=? AND slot=? AND title_key=?",
                (title.casefold(),),
            ).fetchone()
            if topic:
                if topic["archived"]:
                    raise ValueError("Topik sudah diarsipkan. Pulihkan topik tersebut untuk melanjutkan.")
                return dict(topic)
            number = query(
                "UPDATE research_counters SET last_number=last_number+1 WHERE owner_id=? AND slot=? RETURNING last_number"
            ).fetchone()["last_number"]
            query(
                "INSERT INTO research_topics(owner_id, slot, number, title, title_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (number, title, title.casefold(), utc_now()),
            )
            return {"number": number, "title": title}

    def detail(self, number=None, page=1):
        with self._queries() as query:
            if number is None:
                topic = query(
                    """SELECT t.* FROM research_topics t JOIN research_runs r
                       ON (r.owner_id, r.slot, r.topic_number) = (t.owner_id, t.slot, t.number)
                       WHERE t.owner_id=? AND t.slot=? AND t.archived=0
                       ORDER BY r.started_at DESC, r.request_id DESC LIMIT 1"""
                ).fetchone()
            else:
                topic = query(
                    "SELECT * FROM research_topics WHERE owner_id=? AND slot=? AND number=?", (number,)
                ).fetchone()
            if not topic:
                return None
            topic = dict(topic)
            runs = query(
                """SELECT r.*, c.session_number AS retry_session FROM research_runs r
                   LEFT JOIN research_runs c
                     ON (c.owner_id, c.slot, c.topic_number, c.retry_of) =
                        (r.owner_id, r.slot, r.topic_number, r.session_number)
                   WHERE r.owner_id=? AND r.slot=? AND r.topic_number=?
                   ORDER BY r.session_number DESC LIMIT 20 OFFSET ?""",
                (topic["number"], (max(page, 1) - 1) * 20),
            ).fetchall()
            sources = query(
                """SELECT number, evidence FROM research_sources WHERE owner_id=? AND slot=? AND topic_number=?
                   ORDER BY number""", (topic["number"],)
            ).fetchall()
            feedback_field = "CAST(result AS jsonb)->'feedback'" if hasattr(self.store, "pool") else "json_extract(result, '$.feedback')"
            if hasattr(self.store, "pool"):
                feedback_rating = f"({feedback_field})->>'rating'"
                feedback_note = f"({feedback_field})->>'note'"
                feedback_applied = f"({feedback_field})->>'applied_session'"
                feedback_updated = f"({feedback_field})->>'updated_at'"
            else:
                feedback_rating = "json_extract(result, '$.feedback.rating')"
                feedback_note = "json_extract(result, '$.feedback.note')"
                feedback_applied = "json_extract(result, '$.feedback.applied_session')"
                feedback_updated = "json_extract(result, '$.feedback.updated_at')"
            pending_feedback = query(
                f"""SELECT session_number, {feedback_field} AS feedback FROM research_runs
                    WHERE owner_id=? AND slot=? AND topic_number=?
                      AND {feedback_rating} = 'needs_work' AND TRIM({feedback_note}) <> ''
                      AND {feedback_applied} IS NULL
                    ORDER BY {feedback_updated} DESC, session_number DESC LIMIT 1""",
                (topic["number"],),
            ).fetchone()
        topic["runs"] = [{**dict(run), "result": json.loads(run["result"])} for run in runs]
        topic["sources"] = [{"number": source["number"], **json.loads(source["evidence"])} for source in sources]
        if pending_feedback:
            feedback = pending_feedback["feedback"]
            topic["pending_feedback"] = {
                "session": pending_feedback["session_number"],
                **(json.loads(feedback) if isinstance(feedback, str) else feedback),
            }
        return topic

    def get_run(self, number, session):
        with self._queries() as query:
            row = query(
                """SELECT r.*, c.session_number AS retry_session FROM research_runs r
                   LEFT JOIN research_runs c
                     ON (c.owner_id, c.slot, c.topic_number, c.retry_of) =
                        (r.owner_id, r.slot, r.topic_number, r.session_number)
                   WHERE r.owner_id=? AND r.slot=? AND r.topic_number=? AND r.session_number=?""",
                (number, session),
            ).fetchone()
        return {**dict(row), "result": json.loads(row["result"])} if row else None

    def find_run(self, request_id):
        with self._queries() as query:
            row = query(
                """SELECT topic_number, session_number FROM research_runs
                   WHERE owner_id=? AND slot=? AND request_id=?""", (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_run_history(self, number, page, *, period=None):
        page_size = 8
        where = "WHERE owner_id=? AND slot=? AND topic_number=?"
        args = (number,)
        if period:
            where += " AND started_at>=? AND started_at<?"
            args += tuple(period)
        with self._queries() as query:
            count = query(f"SELECT COUNT(*) AS total FROM research_runs {where}", args).fetchone()["total"]
            rows = query(
                f"""SELECT session_number, request_id, question, status, started_at, finished_at, retry_of
                    FROM research_runs {where} ORDER BY session_number DESC LIMIT ? OFFSET ?""",
                (*args, page_size, (page - 1) * page_size),
            ).fetchall()
        return count, [dict(row) for row in rows], page * page_size < count

    def previous_completed_run(self, number, session):
        with self._queries() as query:
            row = query(
                """SELECT * FROM research_runs WHERE owner_id=? AND slot=? AND topic_number=?
                   AND session_number<? AND status<>'running' ORDER BY session_number DESC LIMIT 1""",
                (number, session),
            ).fetchone()
        return {**dict(row), "result": json.loads(row["result"])} if row else None

    def start(self, number, request_id, question, *, retry_of=None):
        with self._queries() as query:
            if retry_of is not None:
                locked = query(
                    """UPDATE research_topics SET last_session=last_session
                       WHERE owner_id=? AND slot=? AND number=? AND archived=0 RETURNING number""",
                    (number,),
                ).fetchone()
                if not locked:
                    raise ValueError("Topik tidak ditemukan atau sedang diarsipkan.")
                existing = query(
                    """SELECT session_number FROM research_runs
                       WHERE owner_id=? AND slot=? AND topic_number=? AND retry_of=?""",
                    (number, retry_of),
                ).fetchone()
                if existing:
                    return existing["session_number"], False

            row = query(
                """UPDATE research_topics SET last_session=last_session+1
                   WHERE owner_id=? AND slot=? AND number=? AND archived=0 RETURNING last_session""",
                (number,),
            ).fetchone()
            if not row:
                raise ValueError("Topik tidak ditemukan atau sedang diarsipkan.")
            session = row["last_session"]
            query(
                """INSERT INTO research_runs(owner_id, slot, topic_number, session_number, request_id, question, status,
                                             started_at, retry_of)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
                (number, session, request_id, question, utc_now(), retry_of),
            )
        return session, True

    def finish(self, number, session, evidence, attempts, succeeded, *, retry_results=None, continuation=None):
        with self._queries() as query:
            topic = query(
                """UPDATE research_topics SET last_session=last_session
                   WHERE owner_id=? AND slot=? AND number=? RETURNING number""", (number,)
            ).fetchone()
            if not topic:
                raise ValueError("Topik sudah dihapus saat permintaan berjalan.")
            active = query(
                """SELECT question, request_id FROM research_runs WHERE owner_id=? AND slot=? AND topic_number=?
                   AND session_number=? AND status='running'""", (number, session),
            ).fetchone()
            if not active:
                raise ValueError("Sesi penelitian sudah selesai atau tidak ditemukan.")
            existing = query(
                "SELECT number, source_key, evidence FROM research_sources WHERE owner_id=? AND slot=? AND topic_number=?",
                (number,),
            ).fetchall()
            by_key = {source["source_key"]: source for source in existing}
            next_number = max((source["number"] for source in existing), default=0) + 1
            citations = []
            for key, evidence_item in evidence.items():
                previous = by_key.get(key)
                changed = False
                if previous:
                    citation = previous["number"]
                    old = json.loads(previous["evidence"])
                    if old.get("metadata"):
                        evidence_item = {**evidence_item,
                                         "metadata": {**old["metadata"], **evidence_item.get("metadata", {})}}
                    if old.get("excerpt") and (
                        not evidence_item.get("excerpt")
                        or old.get("pages") and evidence_item.get("coverage") == "Abstrak"
                    ):
                        evidence_item = {**evidence_item, **{
                            field: old[field] for field in ("excerpt", "finding", "coverage", "pages", "timestamps")
                            if field in old
                        }}
                    else:
                        changed = bool(evidence_item.get("excerpt") and evidence_item["excerpt"] != old.get("excerpt"))
                else:
                    citation = next_number
                    next_number += 1
                query(
                    """INSERT INTO research_sources(owner_id, slot, topic_number, number, source_key, evidence)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (owner_id, slot, topic_number, source_key)
                       DO UPDATE SET evidence=excluded.evidence""",
                    (number, citation, key, json.dumps(evidence_item, ensure_ascii=False)),
                )
                citations.append({**evidence_item, "number": citation, "new": previous is None, "changed": changed})
            result = {"citations": citations, "attempts": attempts}
            if citations:
                result["evidence_analysis"] = analyze_research_evidence(citations, active["question"])
            if continuation:
                matching_sources = continuation_evidence(continuation, citations)
                result["continuation"] = {**continuation, "matching_sources": matching_sources}
                if continuation["origin"] == "feedback" and matching_sources:
                    self._mark_feedback_applied(query, number, session, continuation)
            if retry_results is not None:
                result["retry_results"] = retry_results
            state = ("partial" if any(a["status"] != "success" for a in attempts) else "success") if succeeded else "failed"
            query(
                """UPDATE research_runs SET status=?, finished_at=?, result=?
                   WHERE owner_id=? AND slot=? AND topic_number=? AND session_number=?""",
                (number, session), values=(state, utc_now(), json.dumps(result, ensure_ascii=False)),
            )
        access_count = sum(is_source_access(entry) for entry in attempts)
        logger.info("research_session_finished request_id=%s session=%d status=%s citations=%d accesses=%d",
                    active["request_id"], session, state, len(citations), access_count)
        return result

    @staticmethod
    def _mark_feedback_applied(query, number, session, continuation):
        source_session = continuation["from_session"]
        row = query(
            """UPDATE research_runs SET result=result WHERE owner_id=? AND slot=? AND topic_number=?
               AND session_number=? RETURNING result""", (number, source_session),
        ).fetchone()
        if not row:
            return
        result = json.loads(row["result"])
        feedback = result.get("feedback", {})
        if feedback.get("updated_at") != continuation["feedback_updated_at"] or feedback.get("applied_session"):
            return
        feedback["applied_session"] = session
        query("UPDATE research_runs SET result=? WHERE owner_id=? AND slot=? AND topic_number=? AND session_number=?",
              (number, source_session), values=(json.dumps(result, ensure_ascii=False),))

    def save_feedback(self, number, session, rating, note, *, request_id=""):
        if rating not in {"helpful", "needs_work"}:
            raise ValueError("Pilih apakah hasil penelitian membantu.")
        with self._queries() as query:
            row = query(
                """UPDATE research_runs SET result=result WHERE owner_id=? AND slot=? AND topic_number=?
                   AND session_number=? AND status<>'running' RETURNING result""",
                (number, session),
            ).fetchone()
            if not row:
                raise ValueError("Sesi tidak ditemukan atau belum selesai.")
            result = json.loads(row["result"])
            result["feedback"] = {
                "rating": rating, "note": note.strip()[:600], "updated_at": utc_now(), "request_id": request_id,
            }
            query(
                "UPDATE research_runs SET result=? WHERE owner_id=? AND slot=? AND topic_number=? AND session_number=?",
                (number, session), values=(json.dumps(result, ensure_ascii=False),),
            )

    def manage(self, number, action, title=""):
        with self._queries() as query:
            if action == "delete":
                return bool(query(
                    "DELETE FROM research_topics WHERE owner_id=? AND slot=? AND number=?", (number,)
                ).rowcount)
            if action == "rename":
                title = " ".join(title.split())[:160]
                if not title:
                    raise ValueError("Judul tidak boleh kosong.")
                query("UPDATE research_counters SET last_number=last_number WHERE owner_id=? AND slot=?")
                conflict = query(
                    "SELECT 1 FROM research_topics WHERE owner_id=? AND slot=? AND title_key=? AND number<>?",
                    (title.casefold(), number),
                ).fetchone()
                if conflict:
                    raise ValueError("Judul itu sudah digunakan.")
                return bool(query(
                    "UPDATE research_topics SET title=?, title_key=? WHERE owner_id=? AND slot=? AND number=?",
                    (number,), values=(title, title.casefold()),
                ).rowcount)
            if action not in {"archive", "restore"}:
                raise ValueError("Tindakan penelitian tidak dikenali.")
            return bool(query(
                "UPDATE research_topics SET archived=? WHERE owner_id=? AND slot=? AND number=?",
                (number,), values=(int(action == "archive"),),
            ).rowcount)
