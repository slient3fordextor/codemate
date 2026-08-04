import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.graph import ExecutionGraph, GraphEventType, LoopState, NodeState
from app.agent.graph.models import GraphEvent
from app.agent.task_state import TaskRecord, TaskState


class PersistenceError(RuntimeError):
    """Raised when durable Agent state is corrupt or incompatible."""


class SQLiteAgentStore:
    """Transactional, append-only storage for graph events and loop checkpoints."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        try:
            self.path.chmod(0o600)
        except OSError as exc:
            raise PersistenceError(f"Unable to secure Agent state database: {self.path}") from exc

    def append_graph(self, graph: ExecutionGraph) -> None:
        with self._transaction() as connection:
            for event in graph.events:
                row = self._event_row(event)
                existing = connection.execute(
                    """
                    SELECT event_type, at, node_id, previous_state, state, detail_json
                    FROM graph_events WHERE graph_id = ? AND sequence = ?
                    """,
                    (event.graph_id, event.sequence),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row[2:]:
                        raise PersistenceError(
                            f"Graph event conflict at {event.graph_id}:{event.sequence}"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO graph_events (
                        graph_id, sequence, event_type, at, node_id,
                        previous_state, state, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

    def load_graph(self, graph_id: str) -> ExecutionGraph:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_type, at, node_id, previous_state, state, detail_json
                FROM graph_events WHERE graph_id = ? ORDER BY sequence
                """,
                (graph_id,),
            ).fetchall()
        if not rows:
            raise PersistenceError(f"Unknown persisted graph: {graph_id}")
        return ExecutionGraph.from_events(tuple(self._row_event(graph_id, row) for row in rows))

    def save_loop_state(self, graph_id: str, loop_id: str, state: LoopState) -> None:
        payload = json.dumps(
            {
                "iterations": state.iterations,
                "used_tokens": state.used_tokens,
                "replans": state.replans,
                "pending_replan_from": state.pending_replan_from,
                "processed_validation_ids": list(state.processed_validation_ids),
                "last_replan_approval_id": state.last_replan_approval_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO loop_states (graph_id, loop_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(graph_id, loop_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (graph_id, loop_id, payload, datetime.now().astimezone().isoformat()),
            )

    def load_loop_state(self, graph_id: str, loop_id: str) -> LoopState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM loop_states WHERE graph_id = ? AND loop_id = ?",
                (graph_id, loop_id),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            return LoopState(
                iterations=int(payload["iterations"]),
                used_tokens=int(payload["used_tokens"]),
                replans=int(payload["replans"]),
                pending_replan_from=payload.get("pending_replan_from"),
                processed_validation_ids=tuple(payload["processed_validation_ids"]),
                last_replan_approval_id=payload.get("last_replan_approval_id"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"Invalid persisted loop state: {graph_id}/{loop_id}") from exc

    def graph_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT graph_id FROM graph_events ORDER BY graph_id"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def save_task(self, task: TaskRecord) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, objective, repository_root, worktree_path, base_commit,
                    base_branch, state, graph_id, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    objective = excluded.objective,
                    repository_root = excluded.repository_root,
                    worktree_path = excluded.worktree_path,
                    base_commit = excluded.base_commit,
                    base_branch = excluded.base_branch,
                    state = excluded.state,
                    graph_id = excluded.graph_id,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.objective,
                    str(task.repository_root),
                    str(task.worktree_path),
                    task.base_commit,
                    task.base_branch,
                    task.state.value,
                    task.graph_id,
                    task.error,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def load_task(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT task_id, objective, repository_root, worktree_path, base_commit,
                       base_branch, state, graph_id, error, created_at, updated_at
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError(f"Unknown persisted task: {task_id}")
        try:
            return TaskRecord(
                task_id=row[0],
                objective=row[1],
                repository_root=Path(row[2]),
                worktree_path=Path(row[3]),
                base_commit=row[4],
                base_branch=row[5],
                state=TaskState(row[6]),
                graph_id=row[7],
                error=row[8],
                created_at=datetime.fromisoformat(row[9]),
                updated_at=datetime.fromisoformat(row[10]),
            )
        except (TypeError, ValueError) as exc:
            raise PersistenceError(f"Invalid persisted task: {task_id}") from exc

    def task_ids(self, *, include_terminal: bool = True) -> tuple[str, ...]:
        query = "SELECT task_id FROM tasks"
        parameters: tuple[str, ...] = ()
        if not include_terminal:
            query += " WHERE state IN (?, ?, ?, ?, ?)"
            parameters = (
                TaskState.CREATED.value,
                TaskState.PREPARING.value,
                TaskState.ACTIVE.value,
                TaskState.WAITING_APPROVAL.value,
                TaskState.RETAINED.value,
            )
        query += " ORDER BY updated_at DESC, task_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _initialize(self) -> None:
        with self._transaction() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > self.SCHEMA_VERSION:
                raise PersistenceError(
                    f"Agent state schema {current} is newer than supported "
                    f"schema {self.SCHEMA_VERSION}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_events (
                    graph_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    at TEXT NOT NULL,
                    node_id TEXT,
                    previous_state TEXT,
                    state TEXT,
                    detail_json TEXT NOT NULL,
                    PRIMARY KEY (graph_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS loop_states (
                    graph_id TEXT NOT NULL,
                    loop_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, loop_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    repository_root TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    base_branch TEXT,
                    state TEXT NOT NULL,
                    graph_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path, timeout=10.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
        except sqlite3.Error as exc:
            raise PersistenceError(f"Unable to open Agent state database: {self.path}") from exc
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _event_row(self, event: GraphEvent) -> tuple[Any, ...]:
        return (
            event.graph_id,
            event.sequence,
            event.type.value,
            event.at.isoformat(),
            event.node_id,
            event.previous_state.value if event.previous_state is not None else None,
            event.state.value if event.state is not None else None,
            json.dumps(
                event.detail,
                default=self._json_default,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _row_event(self, graph_id: str, row: sqlite3.Row) -> GraphEvent:
        try:
            detail = json.loads(row[6])
            if row[1] == GraphEventType.NODE_ADDED.value and isinstance(
                detail.get("created_at"), str
            ):
                detail["created_at"] = datetime.fromisoformat(detail["created_at"])
            return GraphEvent(
                sequence=int(row[0]),
                graph_id=graph_id,
                type=GraphEventType(row[1]),
                at=datetime.fromisoformat(row[2]),
                node_id=row[3],
                previous_state=NodeState(row[4]) if row[4] is not None else None,
                state=NodeState(row[5]) if row[5] is not None else None,
                detail=detail,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"Invalid persisted graph event: {graph_id}:{row[0]}") from exc

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Path):
            return value.as_posix()
        raise TypeError(f"Unsupported event value: {type(value).__name__}")


def default_agent_state_path(workspace: Path) -> Path:
    state_root = os.environ.get("CODEMATE_STATE_DIR")
    if state_root:
        return Path(state_root).expanduser().resolve() / "agent-state.sqlite3"
    return workspace.expanduser().resolve() / ".codemate" / "agent-state.sqlite3"
