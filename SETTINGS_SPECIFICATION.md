# SRE Platform — Settings Specification

## 1. Objective
Implement a production-oriented Settings module that allows administrators to configure the platform, agent execution, connectors, LLM providers, persistence and observability without editing source code. Preserve existing behavior by default. Inspect the existing repository, frontend, backend, database migrations, agent graph, scheduler and configuration loading before implementation. Reuse established conventions; do not introduce a second configuration system or rewrite unrelated modules.

## 2. UX and navigation
Add Settings to the existing main navigation. Use a secondary sidebar, responsive layout, existing design tokens, restrained colors, accessible labels and keyboard navigation. Avoid gradients, excessive cards and decorative AI-themed elements. Each section has independent save/discard actions, validation, loading/error/empty states, dirty-state warning and concise help text. Display effective configuration, source, connection status and whether changes require restart or apply to the next run. Never display raw secrets. Do not present fake functionality or mock connection success.

## 3. General and Appearance
- Platform display name, timezone (IANA), language where supported, date/time format.
- Light/dark/system theme and existing display preferences; user-specific preferences remain separate from global settings.
- Do not translate technical terms such as MCP, checkpoint, trace, row lock or SLO into misleading labels.

## 4. Agent Configuration
- Execution modes: manual, scheduled, hybrid; support incident-triggered mode only if a real trigger exists.
- Agent enabled/disabled, analysis window, baseline window, execution timeout, maximum concurrent runs, maximum extra-evidence iterations, graph recursion limit, query/tool budget, retry count and bounded exponential backoff.
- Automatic investigation toggle, trigger threshold, cooldown and deduplication.
- Separate deterministic workflow settings from LLM reasoning settings. Preserve plan → collect → analyze → optional extra_evidence → explain → finalize when present.
- Support cancellation and clear run status. Capture an immutable configuration revision per run; changes apply to subsequent runs, not active graph executions. Disabling scheduling prevents new scheduled runs and does not silently terminate active ones.
- Use existing graph and runtime; no arbitrary graph editor or executable user-supplied Python.

## 5. Scheduling
- Enable/disable schedule, interval or validated cron, timezone, next run preview, last run status, missed-run policy and overlap policy.
- Prevent duplicate scheduling across workers through the existing scheduler or distributed locking. Persist schedules and restore after restart. Provide run-now action with authorization and concurrency checks.

## 6. Memory and Persistence
- Expose supported checkpoint backends (e.g. InMemorySaver for development, SQLite for local CLI, PostgresSaver for durable multi-worker deployments) and supported long-term Store backends independently.
- Display backend status, retention policy, thread/run identifiers and isolation scope. Checkpointing is not long-term memory.
- Validate backend compatibility. In-memory data is lost on restart. Backend switches require explicit restart/migration guidance and must not silently discard existing state. Do not permit unsafe live switching.
- Provide scoped deletion with authorization, confirmation and audit logging; never delete checkpoints of active runs.

## 7. Risk Scoring
- Configure existing deterministic scoring weights, signal thresholds, severity bands and missing-data policy. Validate ranges and weight totals according to the actual scoring implementation (normalized weights sum to 1 or 100).
- Keep risk score bounded to 0–100; LLM explains evidence and results but does not replace deterministic scoring.
- Separate confidence/data quality from risk; unavailable evidence must not be interpreted as zero risk. Version scoring configuration, preserve historical scores and provide preview using fixed test fixtures. Never silently recalculate historical reports.

## 8. MCP Servers
Support multiple named connections, with add/edit/disable/delete/test/discover actions.
- Fields: name, enabled, provider/category, transport (Streamable HTTP and other transports only where supported), endpoint, auth type, secret reference, timeout, retry, allowed tools and read-only policy.
- Examples: Dynatrace, Prometheus, Grafana, Elastic, Datadog, New Relic or custom MCP servers. Examples are not claims of built-in compatibility; verify actual servers and tools.
- Test transport/authentication separately from tool discovery; show sanitized errors, last successful check and discovered capabilities. Do not execute destructive tools during a connection test.
- Namespaced tool identities to avoid collisions. Allow administrators to select approved tools. Default to least privilege/read-only. Treat tool responses as untrusted data, not instructions.
- Do not execute arbitrary stdio commands supplied through the UI. If stdio is necessary, use trusted administrator-managed allowlisted deployments.
- Protect outbound connections against SSRF with deployment-specific allowlists, DNS/IP and redirect validation, TLS verification, bounded response sizes and controlled internal-network exceptions.
- Normalize connector capabilities through adapters; do not assume every MCP server supports DQL or the same telemetry schema. Record source and evidence IDs.

## 9. LLM Providers
Support provider profiles with name, provider type, model ID, base URL where applicable, credential reference, timeout, maximum output tokens, temperature only where supported, concurrency and optional budget limits.
- OpenAI, Anthropic, Google Gemini, Azure OpenAI, Ollama and OpenAI-compatible endpoints through verified adapters where supported.
- Choose default model and optional per-task models (planning, investigation, explanation) only for graph nodes that actually use an LLM. Optional explicit fallback chain; no silent provider switching or unapproved data transfer.
- Connection test, model capability validation (tool calling, structured output), optional model listing where API supports it. Distinguish configured from verified.
- Use provider factories/adapters rather than hardcoding one vendor in graph nodes. Apply redaction and data egress policy before sending telemetry to a model. Never send credentials in prompts.
- Show token usage/cost estimates where available; enforce configured budgets with actual runtime checks.

## 10. Observability
- LangSmith enabled flag, project, endpoint/region where supported, credential reference, environment, sampling rate, metadata and redaction preferences. Enable tracing using the installed SDK's supported runtime configuration; document restart requirements if any.
- OpenTelemetry integration if supported: collector endpoint, protocol, service name, credential reference and sampling configuration.
- Correlate platform run_id, thread_id, graph node, trace and connector calls. Collect execution latency, errors, token usage and tool calls. Distinguish agent observability from monitoring connectors used as analysis data sources.
- Disable external trace export by default for sensitive deployments; redact prompts, telemetry and credentials according to policy. Test connection without transmitting real production evidence.

## 11. Notifications
- Optional webhook, Slack, Teams and SMTP adapters; only expose working integrations as available. Configure destination, credential reference, severity threshold, cooldown, deduplication and notification template.
- Test notification action must be clearly labeled and authorized. Bounded retries and sanitized delivery status. No secrets or sensitive raw evidence in messages by default.

## 12. Security, Data and Audit
- Enforce authorization server-side for all settings reads/writes, connection tests, tool discovery and execution. Use existing identity/RBAC; distinguish administrators and read-only operators where supported. Do not rely on hidden buttons as authorization.
- Secrets via existing secret manager, deployment-managed environment references or encryption at rest using a key stored outside the settings database. Mask secrets in UI, omit from GET responses, logs, traces, errors, exports and audit diffs. Support replace/rotate/delete without returning secret values. Never commit secrets to Git.
- Configure retention separately for analysis reports, evidence, checkpoint data, long-term memory and operational logs; validate limits and protect active runs. Audit retention follows deployment security policy.
- Audit each configuration mutation with actor, time, section, revision, redacted before/after values and result. Audit history is read-only for ordinary settings users.
- Read-only investigation by default; remediation actions require separately defined authorization and human approval. Do not introduce autonomous remediation as part of this change.

## 13. Configuration architecture
Use the current stack and existing database where appropriate. Suggested logical entities (adapt names to repository conventions): settings revisions, connector profiles, provider profiles, scheduler policies, secret references, audit events. Do not store secrets in generic JSON settings.
- Use typed schemas and server-side validation for all sections.
- Precedence: deployment-enforced constraints > persisted administrator settings > environment bootstrap defaults > application defaults. Explicitly document immutable/deployment-only fields and effective source. User appearance preferences are separate.
- Implement atomic updates, revision-based optimistic concurrency, audit records, migration of existing configuration and safe rollback. Rollback creates a new revision; never restore deleted secret values automatically.
- Runtime services consume validated configuration through a common service/provider factory. Refresh safe settings for the next run; flag restart-only settings. Avoid unsafe mutation of global environment variables during concurrent runs.
- Keep graph checkpoint state separate from platform configuration and secret storage. Use secret references in run snapshots.

## 14. Backend API (illustrative; adapt to existing conventions)
- GET /api/settings and GET/PATCH /api/settings/{section}
- GET/POST /api/settings/connectors; PATCH/DELETE /api/settings/connectors/{id}
- POST /api/settings/connectors/{id}/test and /discover
- GET/POST /api/settings/llm-providers; PATCH/DELETE /api/settings/llm-providers/{id}
- POST /api/settings/llm-providers/{id}/test
- POST /api/settings/observability/test
- GET /api/settings/audit
Use consistent error schemas, revision checks, pagination where appropriate, strict request models and sanitized responses. Validate references before deletion; reject deletion of active default profiles until reassigned. Connection tests must be bounded and rate-limited. Draft tests may accept a new secret over TLS without echoing it.

## 15. Implementation phases
1. Inspect repository and document current settings/runtime behavior, dependencies, gaps and files to change.
2. Implement schemas, persistence, migration, secret references, authorization and configuration service; preserve current defaults.
3. Wire settings into graph runtime, scheduler, deterministic scorer and provider/connector factories.
4. Implement Settings navigation and functional frontend sections, starting with General, Agent, Scheduling, MCP, LLM and Observability.
5. Add supported memory, risk, notification, security, retention and audit features. Explicitly mark unsupported integrations; do not simulate them.
6. Add tests, migration/rollback instructions, configuration reference, environment example with placeholders and deployment notes.

## 16. Acceptance criteria
- Existing analysis workflows still run with unchanged default behavior; no regression in existing tests.
- Saved settings survive platform restart. Invalid settings cannot become active. Concurrent edits are detected.
- A scheduled/manual run uses the correct immutable configuration revision; new interval is actually applied and duplicate runs are prevented.
- A configured MCP connection can be tested and discovered with approved tools; disabled connectors cannot be selected for new runs.
- LLM profile selection actually changes the relevant runtime client; fallback and unsupported model capabilities are tested.
- LangSmith enable/disable and redaction work with the installed SDK; credentials never appear in responses, logs or trace metadata.
- Deterministic scoring produces the same result for identical evidence and scoring revision; missing data remains distinguishable.
- RBAC, secret masking, SSRF defenses, retry/timeouts, validation, persistence, audit and restart-only behavior have positive and negative tests.
- Frontend is responsive and accessible; all visible actions work or are explicitly disabled with an explanation.
- Provide final implementation summary, changed files, migration commands, actual test results and remaining limitations. Do not claim unexecuted tests passed.

## 17. Engineering constraints
No unnecessary framework migration, no hardcoded credentials, no fake integrations, no destructive database reset, no breaking API changes without migration, no unrelated UI redesign. If repository capabilities differ from this specification, implement the compatible subset and document the discrepancy. Prefer small, testable changes over speculative abstractions.
