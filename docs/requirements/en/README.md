# CodeMate Requirements

Version: v1.2  
Last updated: 2026-07-03  
Status: Draft for review

CodeMate is an open-source local Agent for coding assistance. It does not provide a public demo, does not host user project code, and does not charge for software usage. Users run it locally and choose either a local model or a third-party model API.

## Document Index

| File | Purpose |
| --- | --- |
| [01-product-requirements.md](01-product-requirements.md) | Product positioning, MVP scope, functional requirements, UX, non-functional requirements |
| [02-technical-selection.md](02-technical-selection.md) | Architecture, backend framework comparison, FastAPI skeleton scope, data model, context strategy |
| [03-open-source-and-cost.md](03-open-source-and-cost.md) | Open-source strategy, model options, cost ownership, privacy boundary |
| [04-roadmap-risks-review.md](04-roadmap-risks-review.md) | Milestones, risks, review questions, glossary |

## Current Decisions

- MVP runs locally by default.
- No official public demo.
- No hosting of user project code.
- No pricing or paid plans in MVP.
- Model options: local model or user-provided third-party API key.
- Backend recommendation: FastAPI.

## Recommended Next Step

Confirm the backend selection, then generate the FastAPI skeleton described in [02-technical-selection.md](02-technical-selection.md).
