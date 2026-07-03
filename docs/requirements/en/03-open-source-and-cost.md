# Open Source And Cost

## 1. Open-Source Positioning

CodeMate is an open-source Agent project. MVP does not include pricing, paid plans, or an official public demo.

The project prioritizes local execution. Users choose either a local model or a third-party model API.

| Item | Strategy |
| --- | --- |
| Repository | Publish core code, docs, and example config |
| License | To review: MIT, Apache-2.0, or AGPL-3.0 |
| Usage | Local run only; user configures local model or third-party API key |
| Community | Use Issues, Discussions, and PRs for feedback and contribution |
| Plugin ecosystem | Future support for tools, model adapters, and workflows |

## 2. No Public Demo

MVP does not provide an official public demo.

Reasons:

- Avoid paying model API costs for anonymous users.
- Avoid hosting or processing user project code on official servers.
- Keep the trust boundary clear: user code stays local by default.
- Reduce infrastructure, abuse prevention, and compliance burden.

## 3. Code Hosting Boundary

CodeMate does not host user project code in MVP.

This means:

- Users do not upload repositories to CodeMate servers.
- CodeMate does not store user source code in official databases.
- Project file reading and indexing happen in the user's local environment.
- Third-party model calls only happen after the user configures a model provider.
- Users should be warned before selected code or file snippets are sent to a third-party model API.

## 4. Cost Ownership

Open source does not mean the Agent is free to run in every mode. CodeMate itself does not charge software fees, but users may pay for model APIs or local hardware resources.

| Cost Item | Required? | Notes |
| --- | --- | --- |
| Third-party model API | Depends on config | OpenAI, Anthropic, and compatible APIs charge by usage |
| Local model | Depends on config | No API fee, but consumes local CPU/GPU/memory |
| Vector index | Possible | Local storage is cheap; cloud vector DBs cost money |
| Code execution sandbox | Possible | Local execution consumes machine resources |
| Network access | Possible | Third-party APIs require external network; local models can run offline |

## 5. Supported Run Modes

### 5.1 Local Model Mode

Users run an open-source model locally. CodeMate does not generate API cost, but quality and speed depend on the user's hardware and selected model.

### 5.2 Third-Party API Mode

Users configure their own OpenAI, Anthropic, or compatible model API key. Model usage is charged to the user's own provider account.

## 6. Product Policy

- Do not provide official public demo in MVP.
- Do not pay user model API costs.
- Do not host user project code.
- Do not hide model usage from users.
- Show clear warnings for third-party model calls that include code context.
