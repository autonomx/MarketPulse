# Agent Runtime Guidance

This subsystem contains authority-sensitive orchestration. Preserve these invariants when changing it:

- `AgentRunSpec` is issued authority; profiles are ceilings and approval does not add capabilities.
- `TaskRevision` is the canonical task contract for steering and completion evidence.
- PostgreSQL durable state owns crash/recovery semantics; process-local Pi state is not sufficient authority.
- Pi/model completion is a claim. Omnix acceptance is the only path to durable `completed`.
- Validation, review, evidence, and artifacts must be tied to the active task revision and exact workspace state where applicable.
- A new task revision or changed workspace state invalidates task-specific downstream quality evidence.
- Reviewer/subagent capabilities must be subsets of parent authority and reviewers must not mutate the parent implementation.
- TaskGraph describes user work; internal coding-quality stages are executor mechanics, not semantic TaskGraph authority.

Before changing runtime lifecycle code, inspect service, repository, contracts, persistence migrations, recovery tests, broker/guard behavior, and affected API/UI consumers together.