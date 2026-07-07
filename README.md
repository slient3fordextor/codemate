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

Optional local configuration:

```bash
cp .env.example .env
```

## Start

```bash
make dev
```

The API starts at `http://127.0.0.1:8000` by default.

The CLI chat page is available at:

```bash
http://127.0.0.1:8000/
```

To use a different bind address or port:

```bash
make dev HOST=0.0.0.0 PORT=8080
```

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

If your dependencies are installed only in the project virtual environment:

```bash
make test PYTHON=.venv/bin/python
```
