import difflib
import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.agent.graph import EvaluationEvidence, EvidenceKind
from app.agent.sandbox import SandboxPolicy, SandboxPolicyError


class EditToolError(ValueError):
    """Raised when a proposed or approved edit violates the write contract."""


@dataclass(frozen=True)
class PatchProposal:
    id: str
    relative_path: Path
    before_digest: str
    after_digest: str
    content: bytes
    diff: str
    created_at: datetime


@dataclass(frozen=True)
class AppliedEdit:
    proposal_id: str
    relative_path: Path
    approval_id: str
    applied_at: datetime
    evidence: EvaluationEvidence


@dataclass(frozen=True)
class _UndoRecord:
    proposal: PatchProposal
    previous_content: bytes | None


class WorkspaceEditor:
    """Creates reviewable replacements and applies them with optimistic concurrency."""

    _missing_digest = "missing"
    _blocked_names = frozenset(
        {
            ".env",
            ".git-credentials",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "credentials.json",
            "id_ed25519",
            "id_rsa",
        }
    )
    _blocked_suffixes = frozenset({".key", ".p12", ".pfx"})

    def __init__(self, policy: SandboxPolicy, *, max_file_bytes: int = 1_000_000) -> None:
        if max_file_bytes <= 0:
            raise ValueError("Edit size limit must be greater than zero")
        self._policy = policy
        self._max_file_bytes = max_file_bytes
        self._proposals: dict[str, PatchProposal] = {}
        self._history: list[_UndoRecord] = []

    def propose_write(self, relative_path: str, content: str) -> PatchProposal:
        path = self._resolve(relative_path)
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise EditToolError("Proposed file exceeds the write size limit")
        if path.exists() and not path.is_file():
            raise EditToolError("Edit target must be a regular file")
        if not path.parent.is_dir():
            raise EditToolError("Edit target parent directory does not exist")
        previous = path.read_bytes() if path.exists() else None
        if previous is not None and b"\x00" in previous:
            raise EditToolError("Binary files cannot be replaced by the text editor")
        proposal = PatchProposal(
            id=f"patch-{uuid4().hex}",
            relative_path=path.relative_to(self._policy.workspace_root),
            before_digest=self._digest(previous),
            after_digest=self._digest(encoded),
            content=encoded,
            diff=self._diff(relative_path, previous, encoded),
            created_at=datetime.now(UTC),
        )
        self._proposals[proposal.id] = proposal
        return proposal

    def apply(self, proposal_id: str, *, approval_id: str) -> AppliedEdit:
        if not approval_id:
            raise EditToolError("An approval id is required to apply a patch")
        try:
            proposal = self._proposals.pop(proposal_id)
        except KeyError as exc:
            raise EditToolError("Unknown or already consumed patch proposal") from exc
        path = self._resolve(proposal.relative_path.as_posix())
        previous = path.read_bytes() if path.exists() else None
        if self._digest(previous) != proposal.before_digest:
            raise EditToolError("Edit target changed after the patch was proposed")
        self._atomic_replace(path, proposal.content, previous)
        self._history.append(_UndoRecord(proposal, previous))
        evidence = EvaluationEvidence(
            id=f"diff-{proposal.id}",
            kind=EvidenceKind.DIFF,
            source_id=proposal.id,
            attempt=1,
            valid=True,
        )
        return AppliedEdit(
            proposal_id=proposal.id,
            relative_path=proposal.relative_path,
            approval_id=approval_id,
            applied_at=datetime.now(UTC),
            evidence=evidence,
        )

    def undo_last(self, *, approval_id: str) -> Path:
        if not approval_id:
            raise EditToolError("An approval id is required to undo an edit")
        if not self._history:
            raise EditToolError("There is no task edit to undo")
        record = self._history.pop()
        path = self._resolve(record.proposal.relative_path.as_posix())
        current = path.read_bytes() if path.exists() else None
        if self._digest(current) != record.proposal.after_digest:
            self._history.append(record)
            raise EditToolError("Edit target changed after this task wrote it")
        if record.previous_content is None:
            path.unlink()
            self._fsync_directory(path.parent)
        else:
            self._atomic_replace(path, record.previous_content, current)
        return record.proposal.relative_path

    def proposal(self, proposal_id: str) -> PatchProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise EditToolError("Unknown patch proposal") from exc

    def _resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute():
            raise EditToolError("Edit paths must be workspace-relative")
        if any(
            part in self._blocked_names
            or part.startswith(".env.")
            or Path(part).suffix.casefold() in self._blocked_suffixes
            for part in raw.parts
        ):
            raise EditToolError("Edit path is blocked by the workspace policy")
        try:
            return self._policy.resolve_workspace_path(raw)
        except SandboxPolicyError as exc:
            raise EditToolError(str(exc)) from exc

    def _atomic_replace(self, path: Path, content: bytes, previous: bytes | None) -> None:
        mode = (path.stat().st_mode & 0o777) if previous is not None else 0o644
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.chmod(mode)
            os.replace(temporary_path, path)
            self._fsync_directory(path.parent)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise EditToolError(f"Unable to apply patch to {path.name}") from exc

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _digest(cls, content: bytes | None) -> str:
        if content is None:
            return cls._missing_digest
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _diff(relative_path: str, previous: bytes | None, content: bytes) -> str:
        before = [] if previous is None else previous.decode("utf-8").splitlines(keepends=True)
        after = content.decode("utf-8").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{relative_path}" if previous is not None else "/dev/null",
                tofile=f"b/{relative_path}",
            )
        )
