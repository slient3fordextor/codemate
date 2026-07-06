# CodeMate

Local AI coding agent backend built with FastAPI.

## Requirements

- Python 3.12+

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Start

```bash
make dev
```

The API starts at `http://127.0.0.1:8000` by default.

## Health Check

```bash
curl -i http://127.0.0.1:8000/health
```

Expected result: HTTP `200` with a JSON body whose `status` is `ok`.

The versioned health endpoint is also available:

```bash
curl -i http://127.0.0.1:8000/api/v1/health
```

## Tests

```bash
make test
```
