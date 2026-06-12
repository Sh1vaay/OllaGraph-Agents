import os
import aiosqlite
from typing import Any

DB_PATH = "db/chat_history.db"

class DatabaseManager:
    """Manages local SQLite database operations for persistent session histories."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        # Ensure the target directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    async def initialize(self) -> None:
        """Initializes tables and configures optimal SQLite concurrency settings."""
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode and a busy timeout to resolve concurrency contention
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            
            # Create Sessions Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    session_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Create Messages Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
                );
            """)
            await db.commit()

    async def create_session(self, session_id: str, session_name: str) -> None:
        """Inserts a new chat session history record if it doesn't already exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute("""
                INSERT OR IGNORE INTO chat_sessions (session_id, session_name)
                VALUES (?, ?);
            """, (session_id, session_name))
            await db.commit()

    async def save_message(self, session_id: str, sender: str, role: str, content: str) -> None:
        """Saves a message log entry under the specified session ID."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute("""
                INSERT INTO chat_messages (session_id, sender, role, content)
                VALUES (?, ?, ?, ?);
            """, (session_id, sender, role, content))
            await db.commit()

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieves all messages for a specific session sorted chronologically."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT sender, role, content, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC;
            """, (session_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Lists all persistent sessions sorted by their creation date."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT session_id, session_name, created_at
                FROM chat_sessions
                ORDER BY created_at DESC;
            """) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
