import sqlite3
from pathlib import Path


class SharedRateLimitError(RuntimeError):
    """Raised when the shared rate-limit store cannot make a safe decision."""


class SQLiteFixedWindowRateLimiter:
    """Coordinates fixed-window request counts across local worker processes."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limit_requests (
                        client_key TEXT NOT NULL,
                        requested_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS ix_rate_limit_client_time
                    ON rate_limit_requests(client_key, requested_at)
                    """
                )
            self.path.chmod(0o600)
        except (OSError, sqlite3.Error) as exc:
            raise SharedRateLimitError("Unable to initialize shared rate-limit storage") from exc

    def allow(self, client_key: str, now: float, limit: int, *, window: float = 60.0) -> bool:
        if limit <= 0 or window <= 0:
            raise ValueError("Rate-limit values must be greater than zero")
        cutoff = now - window
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM rate_limit_requests WHERE requested_at <= ?",
                    (cutoff,),
                )
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM rate_limit_requests
                        WHERE client_key = ? AND requested_at > ?
                        """,
                        (client_key, cutoff),
                    ).fetchone()[0]
                )
                if count >= limit:
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO rate_limit_requests(client_key, requested_at) VALUES (?, ?)",
                    (client_key, now),
                )
                connection.commit()
                return True
        except sqlite3.Error as exc:
            raise SharedRateLimitError("Shared rate-limit transaction failed") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
