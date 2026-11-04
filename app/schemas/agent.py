from pydantic import BaseModel, Field

from app.agent.tools import PermissionMode


class TaskCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=16000)
    mode: PermissionMode = PermissionMode.CONFIRM
    profile_id: str | None = Field(default=None, max_length=64)
    max_rounds: int = Field(default=30, ge=1, le=100)
    max_tokens: int = Field(default=120000, ge=1000, le=1000000)
    max_replans: int = Field(default=3, ge=0, le=10)


class TaskRun(BaseModel):
    approve_interrupted_command: bool = False


class ApprovalDecision(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    approved: bool
    patch_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class TaskAction(BaseModel):
    approved: bool = False
    patch_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class TaskValidation(TaskAction):
    template_id: str = Field(max_length=64)
