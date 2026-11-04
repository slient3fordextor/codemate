"""Durable task budgets: reserve before calls and atomically checkpoint their results."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.adapters.models.base import ModelProviderError
from app.agent.graph import ExecutionGraph, LoopState, NodeType
from app.agent.persistence import SQLiteAgentStore
from app.agent.task_state import TaskRecord
from app.schemas.chat import ChatMessage


class TaskBudget:
    def __init__(
        self,
        store: SQLiteAgentStore,
        record: TaskRecord,
        *,
        max_rounds: int = 30,
        max_tokens: int = 120000,
        max_replans: int = 3,
    ) -> None:
        if max_rounds <= 0 or max_tokens <= 0 or max_replans < 0:
            raise ValueError("Invalid task budget")
        self.store = store
        self.task_id = record.task_id
        self.run_id = uuid4().hex
        self._graph: ExecutionGraph | None = None
        self._messages: list[ChatMessage] = []
        legacy = bool(
            record.graph_id
            and any(node.type is NodeType.MODEL for node in store.load_graph(record.graph_id).nodes)
        )
        with store._transaction() as db:
            db.execute(
                """INSERT OR IGNORE INTO task_budgets
                (task_id,max_rounds,max_tokens,max_replans,tokens) VALUES(?,?,?,?,?)""",
                (record.task_id, max_rounds, max_tokens, max_replans, max_tokens if legacy else 0),
            )

    def snapshot(self) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM task_budgets WHERE task_id=?", (self.task_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Missing task budget")
        values = dict(row)
        values.pop("messages")
        return values

    def start(
        self, mode: str, model: dict[str, Any], request_id: str | None = None
    ) -> list[ChatMessage]:
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT * FROM task_budgets WHERE task_id=?", (self.task_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Missing task budget")
            if row["runs"] and row["replans"] >= row["max_replans"]:
                raise ModelProviderError("TASK_REPLAN_LIMIT", "Task resume/replan budget exhausted")
            if row["rounds"] >= row["max_rounds"] or row["tokens"] >= row["max_tokens"]:
                raise ModelProviderError(
                    "TASK_BUDGET_EXHAUSTED", "Task cumulative budget exhausted"
                )
            db.execute(
                "UPDATE task_budgets SET replans=replans+?,runs=runs+1 WHERE task_id=?",
                (int(row["runs"] > 0), self.task_id),
            )
            now = datetime.now(UTC).isoformat()
            db.execute(
                "UPDATE task_runs SET status='interrupted',finished_at=? "
                "WHERE task_id=? AND status='running'",
                (now, self.task_id),
            )
            db.execute(
                "UPDATE task_approvals SET status='expired' WHERE task_id=? "
                "AND status IN ('pending','approved')",
                (self.task_id,),
            )
            db.execute(
                "INSERT INTO task_runs(id,task_id,mode,model,request_id,status,started_at) "
                "VALUES(?,?,?,?,?,'running',?)",
                (self.run_id, self.task_id, mode, json.dumps(model), request_id, now),
            )
            return [ChatMessage.model_validate(value) for value in json.loads(row["messages"])]

    def checkpoint(self, graph: ExecutionGraph, messages: list[ChatMessage]) -> None:
        self._graph, self._messages = graph, list(messages)
        with self.store._transaction() as db:
            self._checkpoint(db)

    def _checkpoint(self, db: Any) -> None:
        if self._graph is None:
            return
        self.store.append_graph_in_transaction(db, self._graph)
        db.execute(
            "INSERT INTO task_run_graphs VALUES(?,?) ON CONFLICT(run_id) "
            "DO UPDATE SET graph_id=excluded.graph_id",
            (self.run_id, self._graph.graph_id),
        )
        now = datetime.now(UTC).isoformat()
        db.execute(
            "UPDATE tasks SET graph_id=?,updated_at=? WHERE task_id=?",
            (self._graph.graph_id, now, self.task_id),
        )
        db.execute(
            "UPDATE task_budgets SET messages=? WHERE task_id=?",
            (json.dumps([message.model_dump() for message in self._messages]), self.task_id),
        )
        row = db.execute("SELECT * FROM task_budgets WHERE task_id=?", (self.task_id,)).fetchone()
        state = LoopState(
            iterations=row["rounds"], used_tokens=row["tokens"], replans=row["replans"]
        )
        db.execute(
            "INSERT INTO loop_states VALUES(?,?,?,?) ON CONFLICT(graph_id,loop_id) "
            "DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
            (
                self._graph.graph_id,
                self.task_id,
                json.dumps({**state.__dict__, "processed_validation_ids": []}),
                now,
            ),
        )

    def reserve(self, input_tokens: int, output_tokens: int) -> tuple[str, int]:
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT * FROM task_budgets WHERE task_id=?", (self.task_id,)
            ).fetchone()
            allowance = min(output_tokens, row["max_tokens"] - row["tokens"] - input_tokens)
            if row["rounds"] >= row["max_rounds"] or allowance <= 0:
                raise ModelProviderError(
                    "TASK_BUDGET_EXHAUSTED", "Task cumulative budget exhausted"
                )
            identity = uuid4().hex
            reservation = input_tokens + allowance
            db.execute(
                "UPDATE task_budgets SET rounds=rounds+1,tokens=tokens+? WHERE task_id=?",
                (reservation, self.task_id),
            )
            db.execute(
                "UPDATE task_runs SET rounds=rounds+1,tokens=tokens+? WHERE id=?",
                (reservation, self.run_id),
            )
            db.execute(
                "INSERT INTO model_reservations VALUES(?,?,?,?,NULL)",
                (identity, self.task_id, self.run_id, reservation),
            )
            self._checkpoint(db)
            return identity, allowance

    def settle(self, identity: str, actual: int) -> None:
        if actual < 0:
            raise ModelProviderError("INVALID_USAGE", "Model usage cannot be negative")
        with self.store._transaction() as db:
            row = db.execute(
                "SELECT * FROM model_reservations WHERE id=? AND task_id=? AND run_id=?",
                (identity, self.task_id, self.run_id),
            ).fetchone()
            if row is None or row["actual"] is not None:
                raise ValueError("Model reservation was already settled or is invalid")
            difference = actual - row["reserved"]
            db.execute("UPDATE model_reservations SET actual=? WHERE id=?", (actual, identity))
            db.execute(
                "UPDATE task_budgets SET tokens=tokens+? WHERE task_id=?",
                (difference, self.task_id),
            )
            db.execute("UPDATE task_runs SET tokens=tokens+? WHERE id=?", (difference, self.run_id))
            self._checkpoint(db)
        snapshot = self.snapshot()
        if snapshot["tokens"] > snapshot["max_tokens"]:
            raise ModelProviderError(
                "TASK_BUDGET_EXHAUSTED", "Reported model usage exceeded task budget"
            )

    def finish(self, status: str) -> None:
        with self.store._transaction() as db:
            db.execute(
                "UPDATE task_runs SET status=?,finished_at=? WHERE id=?",
                (status, datetime.now(UTC).isoformat(), self.run_id),
            )
            db.execute(
                "UPDATE task_approvals SET status='expired' WHERE run_id=? "
                "AND status IN ('pending','approved')",
                (self.run_id,),
            )
