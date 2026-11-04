import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.agent.persistence import SQLiteAgentStore
from app.core.config import ModelProviderConfig
from app.core.errors import AppError
from app.schemas.model_config import ModelConfigUpdateRequest


class ModelProfiles:
    def __init__(self, store: SQLiteAgentStore) -> None:
        self.store = store

    def get(self, identity: str) -> tuple[ModelProviderConfig, dict[str, Any]]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM model_profiles WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError("PROFILE_NOT_FOUND", "Model profile not found", 404)
        config = ModelProviderConfig.model_validate_json(row["config"])
        return config, {
            "id": row["id"],
            "label": row["label"],
            "version": row["version"],
            "updated_at": row["updated_at"],
            "api_key_set": bool(config.api_key),
            "model": config.model_dump(exclude={"api_key"}),
        }

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            identities = db.execute("SELECT id FROM model_profiles ORDER BY label,id").fetchall()
        return [self.get(row[0])[1] for row in identities]

    def save(
        self, label: str, request: ModelConfigUpdateRequest, identity: str | None = None
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.store._transaction() as db:
            row = (
                db.execute("SELECT * FROM model_profiles WHERE id=?", (identity,)).fetchone()
                if identity
                else None
            )
            if identity and row is None:
                raise AppError("PROFILE_NOT_FOUND", "Model profile not found", 404)
            old = ModelProviderConfig.model_validate_json(row["config"]) if row else None
            values = request.model_dump(exclude={"api_key_mode"})
            if request.api_key_mode == "preserve":
                values["api_key"] = old.api_key if old else None
            elif request.api_key_mode == "clear":
                values["api_key"] = None
            config = ModelProviderConfig.model_validate(values)
            identity = identity or uuid4().hex
            try:
                db.execute(
                    """INSERT INTO model_profiles VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET label=excluded.label,config=excluded.config,
                    version=model_profiles.version+1,updated_at=excluded.updated_at""",
                    (identity, label, config.model_dump_json(), 1, now),
                )
            except sqlite3.IntegrityError as exc:
                raise AppError("PROFILE_LABEL_EXISTS", "Profile name already exists", 409) from exc
        return self.get(identity)[1]

    def delete(self, identity: str) -> None:
        self.get(identity)
        with self.store._transaction() as db:
            referenced = db.execute(
                "SELECT 1 FROM task_specs JOIN tasks USING(task_id) "
                "WHERE profile_id=? AND state!='discarded' LIMIT 1",
                (identity,),
            ).fetchone()
            if referenced:
                raise AppError(
                    "PROFILE_IN_USE", "Profile is still referenced by a retained task", 409
                )
            db.execute("DELETE FROM model_profiles WHERE id=?", (identity,))


def public_model(config: ModelProviderConfig) -> dict[str, Any]:
    return {
        **config.model_dump(exclude={"api_key"}),
        "supports_tools": None,
        "tool_capability": "provider-dependent",
    }
