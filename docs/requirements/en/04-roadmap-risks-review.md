# Roadmap, Risks, And Review Questions

## 1. Milestones

### Phase 1: MVP Validation, 8 Weeks

- Web workspace basic UI.
- Conversational coding.
- Current file and project file tree context.
- Python, JavaScript, and TypeScript code generation.
- Basic logs, rate limiting, and error handling.
- FastAPI backend skeleton if framework selection is confirmed.

### Phase 2: Productization, 8 Weeks

- Code change preview and apply flow.
- Basic retrieval or vector index.
- VSCode extension alpha.
- User feedback loop.
- Usage and cost visibility.

### Phase 3: Team Workflow, 8-12 Weeks

- Team workspace.
- Rule checks.
- PR assistance.
- Execution sandbox beta.
- Multi-model adapters.

### Phase 4: Advanced Capabilities, Ongoing

- Private deployment package.
- Advanced audit.
- Permission and compliance improvements.
- Long-term memory and personalization.

## 2. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Model output quality is unstable | User trust drops | Add context constraints, feedback loop, regeneration |
| Third-party API cost is hard to control | User cost becomes unpredictable | Cost hints, usage stats, user-owned keys, local model option |
| Project context is inaccurate | Generated code is not usable | Prioritize current file, gradually improve retrieval |
| User code security risk | Code leakage or compliance issues | Local-first design, log masking, sensitive data detection |
| Competition pressure | User acquisition is harder | Focus on lightweight workflow and local project context |

## 3. Review Questions

1. Should MVP only include Web workspace, with VSCode extension moved to Phase 2?
2. Should the first supported languages be limited to Python, JavaScript, and TypeScript?
3. Should project context read the whole local repository or only user-selected files?
4. Should code execution sandbox be included in MVP?
5. Which local models and third-party model APIs should be adapted first?
6. Should we generate the FastAPI skeleton now, or complete a technical selection review first?
7. Which open-source license should be selected: MIT, Apache-2.0, or AGPL-3.0?

## 4. Reference Competitors

- Claude Code
- GitHub Copilot
- Cursor
- Codeium
- Tabnine

## 5. Glossary

| Term | Definition |
| --- | --- |
| Token | Basic text unit processed by a model |
| Context window | Maximum text range a model can process in one request |
| RAG | Retrieval-augmented generation |
| Sandbox | Isolated environment for restricted code execution |
| First token latency | Time from request submission to first model output |
