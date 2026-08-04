# CodeMate

[English](README.md) | [简体中文](README.zh-CN.md)

Local-first AI coding agent with a CLI and FastAPI chat backend.

## Requirements

- Python 3.12+

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -c requirements.lock -e ".[dev]"
```

Optional local configuration:

```bash
cp .env.example .env
```

## Agent CLI

The CLI supports `readonly`, `confirm`, and `agent` policies. Writable tasks run
in detached Git worktrees. Text edits are proposed as diffs, validation commands
run without network in Bubblewrap, task state is checkpointed in SQLite, and no
task change reaches the source checkout until an explicit `deliver` command.

Start an interactive session:

```bash
codemate
```

Run one non-interactive task:

```bash
codemate run "review the current changes" --mode readonly --workspace .
```

Run a controlled writable task on Linux:

```bash
codemate run "fix the failing parser" --mode agent --workspace . --task-id parser-fix
codemate deliver parser-fix --workspace .
```

Use `--mode confirm` in an interactive session to approve each patch application
and command. Use `codemate discard TASK_ID --workspace . --force` to explicitly
remove a retained task with unreviewed changes. Writable modes require Git and
Bubblewrap; if either boundary is unavailable, execution fails closed.

Use `--json` for machine-readable output. The loop runs up to 10 rounds by
default; rounds 11-15 require new tool evidence, and each round after 15 needs
interactive confirmation. Non-interactive runs stop instead of bypassing that
confirmation.

## Web Chat

```bash
make dev
```

The API starts at `http://127.0.0.1:8000` by default.

The local Web chat page is available at:

```bash
http://127.0.0.1:8000/
```

To use a different bind address or port:

```bash
make dev HOST=0.0.0.0 PORT=8080
```

The API is loopback-only by default. Remote binding also requires
`ALLOW_REMOTE_API=true` and a strong `REMOTE_API_TOKEN`; remote API clients must
send it as a Bearer token. The bundled Web page is intended for local use.

## Health Check

```bash
curl -i http://127.0.0.1:8000/health
```

Expected result: HTTP `200` with a JSON body whose `status` is `ok`.

The versioned health endpoint is also available:

```bash
curl -i http://127.0.0.1:8000/api/v1/health
```

An explicit deep probe performs a minimal model request and may consume provider
quota:

```bash
curl -i 'http://127.0.0.1:8000/api/v1/health?probe_model=true'
```

## Tests

```bash
make test
```

`make` automatically uses `.venv/bin/python` when that environment exists.

Run the same checks used by CI:

```bash
.venv/bin/ruff check .
.venv/bin/mypy app
.venv/bin/pytest -q
```
