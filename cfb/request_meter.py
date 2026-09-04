"""Durable gate and audit log for every production CFBD request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable, TypeVar


T = TypeVar("T")


class RequestBudgetExhausted(RuntimeError):
    """Raised before transport when a configured request budget is exhausted."""


class MeteredRequestFailed(RuntimeError):
    """A credential-safe production request failure."""


@dataclass(frozen=True)
class RequestBudgets:
    scheduled: int = 100
    historical: int = 500
    absolute: int = 2500


class RequestMeter:
    """Gate, execute, and durably audit outbound requests.

    A permitted attempt is committed before ``transport`` is invoked. Interrupted
    attempts therefore consume budget, which is deliberately safer than silently
    exceeding the account allowance.
    """

    def __init__(
        self,
        path: str | Path,
        budgets: RequestBudgets | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.budgets = budgets or RequestBudgets()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    purpose TEXT,
                    endpoint TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    cache_decision TEXT,
                    budget_impact INTEGER,
                    outcome TEXT NOT NULL,
                    error_type TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(request_audit)")
            }
            migrations = {
                "purpose": "ALTER TABLE request_audit ADD COLUMN purpose TEXT",
                "cache_decision": (
                    "ALTER TABLE request_audit ADD COLUMN cache_decision TEXT"
                ),
                "budget_impact": (
                    "ALTER TABLE request_audit ADD COLUMN budget_impact INTEGER"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)
            connection.execute(
                "UPDATE request_audit SET purpose = category WHERE purpose IS NULL"
            )
            connection.execute(
                "UPDATE request_audit SET cache_decision = 'miss' "
                "WHERE cache_decision IS NULL"
            )
            connection.execute(
                "UPDATE request_audit SET budget_impact = "
                "CASE WHEN outcome = 'blocked' THEN 0 ELSE 1 END "
                "WHERE budget_impact IS NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS request_audit_month "
                "ON request_audit(month, category, outcome)"
            )

    def execute(
        self,
        *,
        endpoint: str,
        season: int,
        cache_decision: str,
        transport: Callable[[], T],
        purpose: str | None = None,
        category: str | None = None,
    ) -> T:
        if purpose is not None and category is not None and purpose != category:
            raise ValueError("purpose and compatibility category must agree")
        purpose = purpose or category
        if purpose not in {"scheduled", "historical"}:
            raise ValueError("purpose must be 'scheduled' or 'historical'")
        now = self._clock().astimezone(timezone.utc)
        month = now.strftime("%Y-%m")
        if cache_decision not in {"miss", "refresh", "incomplete"}:
            raise ValueError(
                "cache_decision must be 'miss', 'refresh', or 'incomplete'"
            )
        request_id = self._gate_and_record(
            now, month, purpose, endpoint, season, cache_decision
        )
        try:
            result = transport()
        except Exception as error:
            self._record_outcome(request_id, "failed", type(error).__name__)
            raise MeteredRequestFailed(
                f"CFBD {endpoint} request {request_id} failed; inspect the request audit"
            ) from None
        self._record_outcome(request_id, "succeeded", None)
        return result

    def _gate_and_record(
        self,
        now: datetime,
        month: str,
        purpose: str,
        endpoint: str,
        season: int,
        cache_decision: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            total = connection.execute(
                "SELECT COALESCE(SUM(budget_impact), 0) FROM request_audit "
                "WHERE month = ?",
                (month,),
            ).fetchone()[0]
            category_total = connection.execute(
                "SELECT COALESCE(SUM(budget_impact), 0) FROM request_audit "
                "WHERE month = ? AND category = ?",
                (month, purpose),
            ).fetchone()[0]
            category_limit = getattr(self.budgets, purpose)
            blocked = total >= self.budgets.absolute or category_total >= category_limit
            outcome = "blocked" if blocked else "started"
            cursor = connection.execute(
                """
                INSERT INTO request_audit
                    (requested_at, month, category, purpose, endpoint, season,
                     cache_decision, budget_impact, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(), month, purpose, purpose, endpoint,
                    int(season), cache_decision, 0 if blocked else 1, outcome,
                ),
            )
            request_id = int(cursor.lastrowid)
        if blocked:
            raise RequestBudgetExhausted(
                f"CFBD request budget exhausted before {endpoint} transport "
                f"(audit request {request_id})"
            )
        return request_id

    def _record_outcome(
        self, request_id: int, outcome: str, error_type: str | None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE request_audit SET outcome = ?, error_type = ? WHERE id = ?",
                (outcome, error_type, request_id),
            )

    def audit_records(self) -> list[dict[str, object]]:
        """Return credential-free audit records for operations and tests."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, requested_at, month, category, purpose, endpoint, "
                "season, cache_decision, budget_impact, outcome, error_type "
                "FROM request_audit ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]
