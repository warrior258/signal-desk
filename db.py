import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import DB_PATH, BACKLOG_MAX_AGE_HOURS

def get_connection():
    """Returns a connection to the SQLite database with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes database schema if not already present."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Documents table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT UNIQUE,
            content_hash TEXT UNIQUE NOT NULL,
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            raw_text TEXT,
            passed_filter INTEGER DEFAULT 0,
            analyzed INTEGER DEFAULT 0
        );
        """)

        # AI Analyses table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER REFERENCES documents(id),
            summary TEXT,
            stage TEXT,
            issuing_body TEXT,
            impact_score INTEGER,
            confidence TEXT,
            horizon TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL
        );
        """)

        # Stock-level Signals table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER REFERENCES analyses(id),
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            reason TEXT,
            relevance TEXT DEFAULT 'primary',
            price_at_signal REAL,
            change_today_pct REAL,
            change_5d_pct REAL,
            volume_ratio REAL,
            connected INTEGER DEFAULT 0,
            final_score REAL,
            alert_level TEXT,
            alerted_at TEXT,
            price_1d REAL,
            price_5d REAL,
            price_20d REAL
        );
        """)

        # Migration: ensure change_5d_pct and relevance exist in existing databases
        for col_def in ["change_5d_pct REAL", "relevance TEXT DEFAULT 'primary'"]:
            try:
                cursor.execute(f"ALTER TABLE signals ADD COLUMN {col_def};")
            except sqlite3.OperationalError:
                pass

        # Macro Indicators definition table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            area TEXT NOT NULL,
            source TEXT,
            frequency TEXT DEFAULT 'daily',
            amber_threshold REAL,
            red_threshold REAL,
            higher_is_worse INTEGER DEFAULT 1
        );
        """)

        # Macro Indicator Values table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS indicator_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_id INTEGER REFERENCES indicators(id),
            value REAL NOT NULL,
            status TEXT NOT NULL,
            observed_for TEXT,
            first_seen_at TEXT NOT NULL
        );
        """)

        conn.commit()

def exists(content_hash: str) -> bool:
    """Checks if a document with this content_hash already exists."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM documents WHERE content_hash = ? LIMIT 1;", (content_hash,))
        return cursor.fetchone() is not None

def save_document(doc: dict) -> int:
    """
    Saves a raw collected document.
    Returns the inserted document ID, or existing ID if already present.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
            INSERT INTO documents (source, title, url, content_hash, published_at, first_seen_at, raw_text, passed_filter, analyzed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0);
            """, (
                doc.get("source", "unknown"),
                doc.get("title", "").strip(),
                doc.get("url", "").strip(),
                doc.get("content_hash"),
                doc.get("published_at"),
                doc.get("first_seen_at", now_iso),
                doc.get("raw_text", ""),
                1 if doc.get("passed_filter") else 0
            ))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Either content_hash or url collided; look the existing row up by both.
            cursor.execute(
                "SELECT id FROM documents WHERE content_hash = ? OR url = ? LIMIT 1;",
                (doc.get("content_hash"), doc.get("url", "").strip())
            )
            row = cursor.fetchone()
            return row["id"] if row else -1

def get_unanalyzed_documents(limit: int = 10,
                            max_age_hours: int = BACKLOG_MAX_AGE_HOURS) -> list[dict]:
    """
    Retrieves recent documents that passed the filter but were never analyzed.

    The age bound matters: without it a quiet news day would spend the LLM budget
    re-reading a backlog of week-old policy items that are no longer tradeable.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, source, title, url, content_hash, published_at, first_seen_at, raw_text
        FROM documents
        WHERE passed_filter = 1 AND analyzed = 0 AND first_seen_at >= ?
        ORDER BY first_seen_at DESC, id DESC
        LIMIT ?;
        """, (cutoff, limit))
        return [dict(row) for row in cursor.fetchall()]

def mark_document_filtered(doc_id: int, passed: bool):
    """Updates the filter status for a document."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE documents SET passed_filter = ? WHERE id = ?;", (1 if passed else 0, doc_id))
        conn.commit()

def mark_document_analyzed(doc_id: int):
    """Marks a document as analyzed."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE documents SET analyzed = 1 WHERE id = ?;", (doc_id,))
        conn.commit()

def save_analysis(document_id: int, analysis_data: dict, raw_json_str: str) -> int:
    """Saves structured analysis returned by the LLM."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO analyses (document_id, summary, stage, issuing_body, impact_score, confidence, horizon, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            document_id,
            analysis_data.get("summary", ""),
            analysis_data.get("document_stage", "other"),
            analysis_data.get("issuing_body", ""),
            analysis_data.get("impact_score", 1),
            analysis_data.get("confidence", "low"),
            analysis_data.get("time_horizon", "weeks"),
            raw_json_str,
            now_iso
        ))
        conn.commit()
        return cursor.lastrowid

def save_signal(analysis_id: int, symbol: str, direction: str, reason: str,
                price: float, change_today: float, volume_ratio: float,
                connected: int, score: float, alert_level: str,
                change_5d: float = 0.0, relevance: str = "primary") -> int:
    """Saves a stock signal derived from analysis."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO signals (analysis_id, symbol, direction, reason, relevance, price_at_signal, change_today_pct, change_5d_pct, volume_ratio, connected, final_score, alert_level, alerted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            analysis_id, symbol, direction, reason, relevance, price, change_today, change_5d, volume_ratio, connected, score, alert_level, now_iso
        ))
        conn.commit()
        return cursor.lastrowid

if __name__ == "__main__":
    init_db()
    print(f"✅ Database initialized successfully at {DB_PATH}")
