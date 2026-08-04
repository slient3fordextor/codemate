import os
import resource
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.agent.graph import EvaluationEvidence, EvidenceKind
from app.agent.sandbox.policy import SandboxPolicy, SandboxPolicyError


@dataclass(frozen=True)
class CommandArtifact:
    id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    output_truncated: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def evidence(self, source_id: str, *, attempt: int = 1) -> EvaluationEvidence:
        return EvaluationEvidence(
            id=self.id,
            kind=EvidenceKind.COMMAND,
            source_id=source_id,
            attempt=attempt,
            valid=self.passed,
        )


class BubblewrapExecutor:
    """Runs approved verification commands in a networkless Linux namespace."""

    _python_modules = frozenset({"compileall", "mypy", "pytest", "ruff", "unittest"})
    _direct_commands = frozenset({"mypy", "pytest", "ruff"})

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        timeout_seconds: float = 120.0,
        max_output_bytes: int = 200_000,
        memory_limit_bytes: int = 2_147_483_648,
    ) -> None:
        if policy.network_enabled:
            raise SandboxPolicyError("P0 command execution does not permit network access")
        if timeout_seconds <= 0 or max_output_bytes <= 0 or memory_limit_bytes <= 0:
            raise ValueError("Sandbox resource limits must be greater than zero")
        executable = shutil.which("bwrap")
        if executable is None:
            raise SandboxPolicyError("Bubblewrap is required for command execution")
        if not policy.workspace_root.is_dir() or not policy.temporary_root.is_dir():
            raise SandboxPolicyError("Sandbox workspace and temporary roots must exist")
        self._policy = policy
        self._bwrap = executable
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._memory_limit_bytes = memory_limit_bytes

    @property
    def process_isolation_enforced(self) -> bool:
        return True

    def execute(self, argv: Sequence[str]) -> CommandArtifact:
        command = self._validated_command(argv)
        sandbox_argv = self._sandbox_argv(command)
        started = monotonic()
        process = subprocess.Popen(
            sandbox_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
            preexec_fn=self._limit_resources,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        combined_size = len(stdout) + len(stderr)
        return CommandArtifact(
            id=f"command-{uuid4().hex}",
            argv=tuple(command),
            exit_code=124 if timed_out else process.returncode,
            stdout=self._decode_bounded(stdout),
            stderr=self._decode_bounded(stderr),
            duration_seconds=monotonic() - started,
            timed_out=timed_out,
            output_truncated=combined_size > self._max_output_bytes,
        )

    def _validated_command(self, argv: Sequence[str]) -> list[str]:
        if not argv or len(argv) > 128:
            raise SandboxPolicyError("A bounded command argv is required")
        if any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
            raise SandboxPolicyError("Command argv contains an invalid value")
        executable_name = Path(argv[0]).name
        if executable_name in {"python", "python3", Path(sys.executable).name}:
            if len(argv) < 3 or argv[1] != "-m" or argv[2] not in self._python_modules:
                raise SandboxPolicyError("Python may only run an approved verification module")
            return [self._sandbox_runtime_executable("python"), *argv[1:]]
        if executable_name in self._direct_commands:
            return [self._sandbox_runtime_executable(executable_name), *argv[1:]]
        raise SandboxPolicyError(f"Command is not in the verification allowlist: {argv[0]}")

    def _sandbox_runtime_executable(self, name: str) -> str:
        if name == "python":
            return str(Path(sys.executable))
        candidate = Path(sys.prefix) / "bin" / name
        if not candidate.is_file():
            raise SandboxPolicyError(f"Approved command is not installed: {name}")
        return str(candidate)

    def _sandbox_argv(self, command: list[str]) -> list[str]:
        argv = [
            self._bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-net",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--clearenv",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "PATH",
            f"{Path(sys.prefix) / 'bin'}:/usr/bin:/bin",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind-try",
            "/bin",
            "/bin",
            "--ro-bind-try",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
        ]
        runtime = Path(sys.prefix).resolve()
        if runtime != Path("/usr") and not runtime.is_relative_to(Path("/usr")):
            for parent in reversed(runtime.parents[:-1]):
                if parent != Path("/"):
                    argv.extend(("--dir", str(parent)))
            argv.extend(("--ro-bind", str(runtime), str(runtime)))
        argv.extend(
            (
                "--bind",
                str(self._policy.workspace_root),
                "/workspace",
                "--bind",
                str(self._policy.temporary_root),
                "/tmp",
                "--chdir",
                "/workspace",
                "--",
                *command,
            )
        )
        return argv

    def _decode_bounded(self, value: bytes) -> str:
        return value[: self._max_output_bytes].decode("utf-8", errors="replace")

    def _limit_resources(self) -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self._memory_limit_bytes, self._memory_limit_bytes),
        )
        cpu_seconds = max(1, int(self._timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
