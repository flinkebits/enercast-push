"""SQLite-Cache für abgeschlossene 15-Minuten-Modbus-Intervalle."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PERIOD_MINUTES = 15
KEEP_INTERVALS = 16
REPO_ROOT = Path(__file__).resolve().parent

STATUS_PENDING = "pending"
STATUS_UPLOADED = "uploaded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_instant(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_interval_start(text: str) -> datetime:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("interval_start ohne Zeitzone")
    return parsed.astimezone(timezone.utc).replace(second=0, microsecond=0)


def floor_interval(moment: datetime, period_minutes: int = PERIOD_MINUTES) -> datetime:
    if period_minutes < 1 or 60 % period_minutes != 0:
        raise ValueError("period_minutes muss 60 teilen")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc).replace(second=0, microsecond=0)
    floored_minute = (moment.minute // period_minutes) * period_minutes
    return moment.replace(minute=floored_minute)


def default_db_path() -> Path:
    override = os.environ.get("ENERCAST_INTERVAL_DB", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / "data" / "modbus_intervals.db"


def ensure_db_parent(path: Path) -> None:
    parent = path.expanduser().resolve().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Verzeichnis für DB nicht anlegbar: {parent} ({exc}). "
            "Bei Pfaden unter /var/lib zuerst anlegen oder ENERCAST_INTERVAL_DB setzen."
        ) from exc


class IntervalStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = (db_path or default_db_path()).expanduser().resolve()
        ensure_db_parent(self.db_path)
        self._conn = sqlite3.connect(self.db_path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intervals (
                interval_start TEXT PRIMARY KEY,
                power_mean_kw REAL NOT NULL,
                power_sent_kw REAL NOT NULL,
                curtailment_w REAL NOT NULL,
                power_samples INTEGER NOT NULL,
                limit_samples INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _prune_old_intervals(self) -> None:
        """Behält nur die KEEP_INTERVALS jüngsten Fenster (nach interval_start)."""
        count = self._conn.execute("SELECT COUNT(*) FROM intervals").fetchone()[0]
        if count <= KEEP_INTERVALS:
            return
        drop = count - KEEP_INTERVALS
        self._conn.execute(
            """
            DELETE FROM intervals
            WHERE interval_start IN (
                SELECT interval_start FROM intervals
                ORDER BY interval_start ASC
                LIMIT ?
            )
            """,
            (drop,),
        )
        self._conn.commit()

    def get(self, interval_start: datetime) -> sqlite3.Row | None:
        key = format_instant(interval_start)
        row = self._conn.execute(
            "SELECT * FROM intervals WHERE interval_start = ?",
            (key,),
        ).fetchone()
        return row

    def save(
        self,
        interval_start: datetime,
        power_mean_kw: float,
        power_sent_kw: float,
        curtailment_w: float,
        power_samples: int,
        limit_samples: int,
        status: str,
        last_error: str | None = None,
    ) -> bool:
        existing = self.get(interval_start)
        if existing is not None and existing["status"] == STATUS_UPLOADED:
            return False
        stamp = format_instant(utc_now())
        key = format_instant(interval_start)
        self._conn.execute(
            """
            INSERT INTO intervals (
                interval_start, power_mean_kw, power_sent_kw, curtailment_w,
                power_samples, limit_samples, status, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interval_start) DO UPDATE SET
                power_mean_kw = excluded.power_mean_kw,
                power_sent_kw = excluded.power_sent_kw,
                curtailment_w = excluded.curtailment_w,
                power_samples = excluded.power_samples,
                limit_samples = excluded.limit_samples,
                status = excluded.status,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                key,
                power_mean_kw,
                power_sent_kw,
                curtailment_w,
                power_samples,
                limit_samples,
                status,
                last_error,
                stamp,
            ),
        )
        self._conn.commit()
        self._prune_old_intervals()
        return True

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM intervals GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def list_all(
        self,
        status: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[sqlite3.Row]:
        order = "DESC" if newest_first else "ASC"
        params: list[object] = []
        where = ""
        if status is not None:
            where = "WHERE status = ?"
            params.append(status)
        sql = f"SELECT * FROM intervals {where} ORDER BY interval_start {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self._conn.execute(sql, params))

    def list_upload_candidates(self, include_failed: bool = True) -> list[sqlite3.Row]:
        statuses = [STATUS_PENDING]
        if include_failed:
            statuses.append(STATUS_FAILED)
        placeholders = ",".join("?" for _ in statuses)
        return list(
            self._conn.execute(
                f"""
                SELECT * FROM intervals
                WHERE status IN ({placeholders})
                ORDER BY interval_start ASC
                """,
                statuses,
            )
        )

    def mark_uploaded(self, interval_start: datetime) -> None:
        key = format_instant(interval_start)
        self._conn.execute(
            """
            UPDATE intervals
            SET status = ?, last_error = NULL, updated_at = ?
            WHERE interval_start = ?
            """,
            (STATUS_UPLOADED, format_instant(utc_now()), key),
        )
        self._conn.commit()
        self._prune_old_intervals()

    def mark_failed(self, interval_start: datetime, message: str) -> None:
        key = format_instant(interval_start)
        self._conn.execute(
            """
            UPDATE intervals
            SET status = ?, last_error = ?, updated_at = ?
            WHERE interval_start = ?
            """,
            (STATUS_FAILED, message[:2000], format_instant(utc_now()), key),
        )
        self._conn.commit()
        self._prune_old_intervals()

    def mark_skipped(self, interval_start: datetime, message: str) -> None:
        key = format_instant(interval_start)
        self._conn.execute(
            """
            UPDATE intervals
            SET status = ?, last_error = ?, updated_at = ?
            WHERE interval_start = ?
            """,
            (STATUS_SKIPPED, message[:2000], format_instant(utc_now()), key),
        )
        self._conn.commit()
        self._prune_old_intervals()
