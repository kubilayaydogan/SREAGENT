# Enterprise SRE Platform — Security Implementation Specification

## 1. Mission and scope

Act as a senior application security engineer, AI security engineer and software architect. Inspect the existing repository and implement a production-oriented security foundation for the SRE platform. Preserve existing functionality, architecture and visual design. Treat this specification as requirements, not evidence that controls already exist.

Scope: frontend, backend APIs, LangGraph execution, scheduled jobs, MCP and monitoring connectors, LLM providers, PostgreSQL, checkpoints and memory, LangSmith/tracing, containers, CI/CD and deployment configuration. Cover both single-organization on-premises and, where supported, multi-tenant deployments. Do not introduce a SaaS tenancy model merely to satisfy this document; document the actual isolation boundary.

## 2. Mandatory engineering workflow

1. Inspect repository structure, dependency versions, data models, authentication, APIs, worker processes, deployment and existing security controls. Identify entry points and trust boundaries. Do not read or print real secrets unnecessarily.
2. Create `docs/platform/security/SECURITY_GAP_ANALYSIS.md` with requirement ID, existing implementation evidence (file/function), status (implemented/partial/missing/not applicable), risk, proposed change and test.
3. Create `docs/platform/security/SECURITY_IMPLEMENTATION_PLAN.md` with prioritized phases, dependencies, migrations, rollback procedures and acceptance criteria. Identify incompatible changes before implementation.
4. Implement incrementally, reusing existing frameworks and libraries. Avoid unnecessary rewrites, duplicate services and speculative abstractions. Run relevant tests after each phase. Do not stop at mockups, placeholders or documentation.
5. Preserve existing analysis flows, scheduling, scoring, checkpoint recovery and connectors. Never disable existing controls to pass tests. Do not silently grant privileges to existing users or agents during migration.
6. Document any blocked requirements, external infrastructure dependencies or unsupported features honestly. Do not mark controls implemented without code and test evidence. Do not claim certification or complete security.
7. Do not deploy to production, perform destructive tests, push changes, rotate live credentials or alter external services without explicit approval. Use local/staging test fixtures only. Never commit secrets, production telemetry or customer data.

## 3. Identity and access management

### IAM-01 Authentication
- Use a mature authentication library or existing identity provider. Do not implement custom cryptographic protocols.
- Support enterprise OIDC SSO; validate signature, issuer, audience, expiration, state, nonce and PKCE where applicable. Support configured trusted issuers and TLS verification. Plan SAML/LDAP via an identity broker where necessary; avoid unnecessary direct LDAP integration.
- If local accounts are supported, use Argon2id or an approved adaptive password hashing implementation, secure password reset with expiring single-use tokens, configurable password policy, throttling and enumeration-resistant responses.
- Support MFA enforcement through the IdP or a mature local MFA implementation; do not claim MFA unless enforcement is verified.
- No default shared credentials. First-admin provisioning must be explicit and secure. Provide a controlled, audited break-glass process if required.

### IAM-02 Session lifecycle
- Secure, HttpOnly, SameSite cookies for browser sessions where appropriate; CSRF protection for cookie-authenticated state changes. Avoid persistent browser storage of sensitive bearer tokens.
- Enforce idle and absolute expiration, logout, session revocation, token expiry and refresh rotation where applicable. Protect refresh credentials. Support active session listing and revocation.
- Apply rate limits to login and password reset; avoid permanent lockout denial-of-service. Audit security-sensitive events without logging credentials.

### IAM-03 Authorization
- Enforce deny-by-default, least privilege, server-side RBAC and resource-level authorization on every API, export, WebSocket/SSE subscription, checkpoint and background task.
- Proposed roles: Security Admin, Platform Admin, SRE Engineer, Operator, Viewer, Auditor, and dedicated Service Accounts. Restrict Super Admin/break-glass privileges. Separate security policy management from routine connector management; prevent self-escalation and unauthorized privilege delegation.
- Permissions include `agent:run`, `agent:cancel`, `agent:configure`, `agent:resume`, `connector:read`, `connector:create`, `connector:update`, `connector:delete`, `connector:test`, `llm:configure`, `llm:use`, `report:read`, `report:export`, `memory:read`, `memory:delete`, `security:manage`, `audit:read`, `audit:export`, `user:manage`, `role:manage`.
- Resolve actor identity from trusted authentication, never from client-supplied user or tenant IDs. Enforce ownership, organization and environment boundaries. UI visibility is not authorization.
- Scheduled jobs and agent workers must have dedicated scoped identities; check effective permissions at execution and resume. Prevent confused-deputy behavior and privilege escalation via tool calls.
- Service accounts: scoped credentials, expiration, revocation, rotation and audit. No shared administrator token for all connectors.

## 4. Security policy engine and agent harness

### AGT-01 Central policy enforcement
Implement a shared policy decision/enforcement mechanism at trusted execution boundaries. Evaluate actor, tenant/organization, agent, run, environment, resource, requested action, connector, tool, normalized arguments, risk classification and policy version.

Decisions: ALLOW, DENY, REQUIRE_APPROVAL. Default deny for unknown or unauthorized operations. Fail closed for protected actions when policy is unavailable. Log the decision, policy ID/version and reason. LLM outputs cannot override policies. Enforce on every actual tool/model/data-egress call, including retries, parallel branches, scheduled runs and direct API paths. Do not rely only on a graph node or system prompt. Use middleware/wrappers compatible with the existing custom StateGraph architecture; do not assume create_agent middleware automatically applies.

Suggested flow:
`Authenticated request -> authorization -> input validation -> context filtering -> LangGraph -> tool/model policy enforcement -> execution -> output validation -> audit -> response`

### AGT-02 Prompt injection and untrusted input
- Treat user input, retrieved documents, telemetry, logs, traces, tool descriptions and tool responses as untrusted data. Clearly delimit data from instructions.
- Detect direct and indirect prompt injection using layered controls; never describe detection as a complete defense. Restrict available tools and data regardless of model response.
- Prevent untrusted data from changing system policies, connector URLs, credentials, tool permissions or model configuration.
- Validate structured LLM output against strict schemas, reject unknown fields where appropriate, safely render text/Markdown, and never execute generated code or commands automatically.
- Do not expose hidden prompts, secrets or unauthorized context in responses. Use evidence references for RCA claims and distinguish hypotheses from verified findings.

### AGT-03 Execution limits and resilience
- Configurable maximum iterations, recursion depth, wall-clock duration, model requests, tool calls, tokens, concurrency, query response size and cost budget.
- Enforce limits server-side; persist cumulative budgets across resume/retry. Add timeouts, bounded retries with backoff and jitter, circuit breakers where appropriate, cancellation and idempotency.
- Kill switch: stop accepting new runs, cancel queued work and attempt safe cooperative cancellation of active work. Clearly communicate that already-dispatched remote operations may not be reversible.
- Keep deterministic risk scoring independent of LLM-generated scores. Degrade to explicitly partial reports when evidence/model availability is insufficient; never invent evidence.

### AGT-04 Human-in-the-loop
- Read-only analysis by default. Deny destructive or write operations unless explicitly enabled by policy and scoped authorization.
- For supported sensitive actions, persist an approval request bound to exact normalized arguments, actor, environment, tool, run ID, policy version and expiry. Approval must come from an independently authorized human; the agent cannot approve itself. Prevent replay, argument substitution and TOCTOU issues; recheck authorization at execution.
- Use LangGraph interrupt/resume only where appropriate; approval state must be durable and server-enforced. No remediation tools need to be added solely for this requirement.

## 5. MCP and connector security

### MCP-01 Trusted connector registry
- Only administrators with appropriate scoped permissions can register or approve connectors. Maintain approved endpoints, transports, authentication type, tool schemas and permitted scopes.
- Prefer read-only access for Dynatrace and other monitoring tools; use least-privilege provider tokens. Separate connection-testing permissions from connector use and management.
- Pin or review tool definitions/versions as feasible; detect changed tool schemas or descriptions and require review for material changes. Treat discovered tools as untrusted until approved.
- Restrict tool calls using explicit allowlists and argument validation. Deny unknown tools by default. Tool names alone are insufficient: validate operation semantics and arguments.

### MCP-02 Network and protocol enforcement
- Validate TLS certificates and hostnames; support configured enterprise CA and mTLS where applicable. Never silently disable verification.
- Protect against SSRF, DNS rebinding and redirect bypass. Use approved destination hosts/ports, validated resolved addresses, redirect controls and network-level egress policies. Block metadata, loopback, link-local and unauthorized private addresses by default; permit explicitly approved internal monitoring endpoints for on-prem deployments.
- Restrict local subprocess/stdio MCP connectors to approved executables and isolated runtime environments if supported. Do not allow arbitrary shell commands or user-supplied executable paths.
- Use scoped OAuth/access tokens where supported. Never forward credentials intended for one resource to an unrelated service. No raw tokens in UI or logs.
- Enforce connection/read timeouts, payload limits, query limits, concurrency and retry policies.

### MCP-03 Dynatrace/DQL security
- Centralize DQL execution behind an authorized query interface. Validate query types, permitted datasets, environment/resource scopes, time ranges, limits and output size. Prefer trusted query templates with safely validated parameters.
- Do not treat a query as safe merely because it is read-only: unauthorized log/trace data access is also a security violation.
- Enforce provider-side scopes and data access controls in addition to application policies. Do not rely on keyword filtering as the sole authorization boundary.
- Preserve query evidence IDs and result status; sanitize sensitive fields before downstream model calls, tracing and reports.

## 6. LLM governance and outbound data protection

### LLM-01 Provider management
- Allowlist approved providers, models and endpoints. Enforce policy per organization/environment and user/agent scope. Support disabling external LLM calls and approved local inference endpoints.
- Provider fallback must be explicitly authorized and subject to identical or stricter data policies; never silently switch to an unapproved provider.
- Enforce TLS, credential protection, timeouts and token/cost limits. Record model/provider identifiers, request ID, prompt version, policy version, usage and latency without recording raw sensitive payloads by default.
- Document provider retention, training usage, contractual terms and processing regions as administrative requirements; do not claim a local setting controls provider-side behavior without verification.

### DLP-01 Data minimization and redaction
- Classify data (public/internal/confidential/restricted) and apply configurable data transfer rules before every external sink: LLM, tracing, connectors, webhooks, exports and logs.
- Detect/mask API keys, authorization headers, passwords, connection strings, personal data, account numbers and other configured sensitive fields. Support structured-field redaction and pattern detection with tests; do not rely on regex alone for all data types.
- Use stable pseudonyms within appropriate scopes to preserve SRE correlation where needed. Protect pseudonym mapping; never send the mapping to external models. Treat pseudonymized personal data as sensitive.
- Minimize telemetry payloads, prefer aggregate statistics, remove unnecessary fields and sanitize errors. Reject restricted data transfers when policy cannot establish permission.
- Apply redaction before tracing callbacks receive data; disable external tracing where safe redaction cannot be guaranteed. Sanitize model/tool outputs before storage, rendering and export.
- Provide configurable retention and deletion, legal hold where required, and authorized exports. Avoid storing raw prompts/tool responses unless explicitly justified and protected.

## 7. Secrets and encryption

### SEC-01 Secret management
- Inventory secrets: Dynatrace/MCP tokens, LLM/LangSmith keys, DB credentials, OAuth credentials, signing/encryption keys and service-account tokens.
- Prefer existing secret manager or support Vault/cloud secret managers using a provider abstraction when needed. For stored connector credentials, use authenticated encryption with a key kept separately from the database, ideally KMS/Vault envelope encryption. No homemade cryptography.
- UI/API responses expose metadata and masked identifiers only; no endpoint should return plaintext saved credentials by default. Limit decryption to authorized runtime components.
- Support credential update, revocation, expiry, rotation and connection testing without revealing secret values. Audit secret lifecycle and access metadata.
- Never commit `.env` or keys; provide `.env.example` with placeholders. Protect environment files, backup encryption keys and deployment secrets.

### SEC-02 Encryption and retention
- TLS for external communications and sensitive internal connections, including PostgreSQL as appropriate. Protect databases, backups and storage at rest with deployment-supported encryption and key separation.
- Define key lifecycle, rotation, recovery and access restrictions. Encrypt especially sensitive fields when needed. Do not advertise full disk encryption unless deployment evidence exists.

## 8. State, checkpoint and memory security

### MEM-01 Isolation and persistence
- Enforce user/organization/run ownership for threads, checkpoints, stores, reports, and episodic/semantic/procedural memory if present. A thread ID is never an authorization credential.
- Validate ownership on list/read/write/delete/replay/resume. Apply isolation in persistence and retrieval layers, not only frontend filtering. Consider database-level isolation as defense in depth where applicable.
- Persist only necessary sanitized state, protect checkpoint data and avoid unsafe deserialization. Use supported safe serializer settings and restrict access to checkpoint tables.
- Reapply current policy, permissions and budgets on resume or replay. Prevent duplicate side effects with idempotency and execution leases where necessary.
- Define memory provenance, trust classification, expiration and deletion. Untrusted retrieved memory must not become privileged instructions. Restrict who can edit runbooks/procedural memory; maintain version history and approval where appropriate.
- Provide authorized deletion and retention cleanup across stores, reports, checkpoints and indexes; document backup expiration limitations.

## 9. Audit logs, SIEM and security monitoring

### AUD-01 Audit events
Record authentication success/failure, session changes, role/permission changes, policy changes, connector lifecycle, secret lifecycle, model settings, agent runs/cancellation/resume, tool decisions and execution outcomes, approvals, exports, memory access/deletion and security violations.

Suggested event schema:
- event_id, timestamp_utc, event_type, actor_type, actor_id, organization_id, session_id, source_ip (trusted proxy handling), action, resource_type, resource_id, outcome, reason_code, policy_id, policy_version, correlation_id, run_id, trace_id, sanitized change_summary.

- Separate security audit records from debug logs and LLM traces. No credentials or raw sensitive payloads.
- Make audit records append-only for application identities; restrict access, protect integrity, and use external immutable/WORM storage or equivalent controls when stronger tamper resistance is required. Do not claim absolute tamper-proofing based solely on application code.
- Audit reads and exports where appropriate. Provide filters, pagination, UTC timestamps and authorized export with spreadsheet-formula injection protection.
- Use reliable delivery for critical security events, monitoring of failed delivery and explicit fail-safe behavior for sensitive operations when required audit storage is unavailable.

### AUD-02 SIEM and alerting
- Structured JSON security events; support existing logging pipeline or configurable Syslog/CEF/webhook integration as appropriate. Protect transport, authenticate destination and restrict outbound addresses.
- Integrations may include Splunk, Sentinel, QRadar, Elastic Security and Wazuh through supported standard formats. Do not claim native integration without implementation and tests.
- Alert on repeated authentication failures, unauthorized access, suspicious tool calls, rejected outbound transfers, privilege changes, anomalous request rates, audit delivery failures and unexpected connector changes.

## 10. Web and API security

### API-01
- Validate request schemas, identifiers, enums, lengths, uploads and content types. Use parameterized SQL/ORM queries; prohibit unsafe dynamic SQL. Apply resource-level authorization to prevent BOLA/IDOR and mass assignment.
- Protect against XSS, CSRF, SSRF, injection and unsafe file operations. Use safe HTML/Markdown rendering and encoding. Restrict CORS to approved origins; never combine wildcard origins with credentialed requests.
- Configure CSP, HSTS where appropriate, frame-ancestors, content-type and referrer protections without breaking required functionality. Limit body size, pagination, query complexity and request rates.
- Sanitize error responses; no stack traces or secrets in production. Restrict administrative endpoints, API documentation and debug interfaces according to policy.
- Protect report exports and downloads against cross-tenant access and injection. Validate WebSocket/SSE authentication and subscription authorization throughout connection lifetime.

## 11. Infrastructure and production runtime

### INF-01 Deployment hardening
- Docker containers run as non-root where feasible; no privileged mode, unnecessary capabilities or Docker socket mounts. Use read-only root filesystems where compatible, controlled writable volumes, resource limits and health checks.
- Restrict published ports, isolate frontend/backend/DB/worker networks, avoid public DB exposure. Configure reverse proxy, trusted proxy handling, enterprise CA, outbound allowlists and optional IP restrictions. Enforce network policy at infrastructure level where app code cannot provide sufficient protection.
- Support secure on-prem deployment; document air-gapped prerequisites, offline dependencies and local LLM/monitoring constraints. Do not claim air-gap support until tested.
- Keep production/test credentials and environments separate. Harden DB roles and worker identities; grant only required table/schema access.
- Backups: encryption, restricted access, retention, restore verification and documented RPO/RTO. Add incident response and recovery instructions. Protect scheduler against duplicate execution and unauthorized job changes.

## 12. Security & Governance frontend

### UI-01 Design requirements
Use existing design system and responsive layout. Create a dedicated Security & Governance module (or integrate cleanly if existing navigation dictates). Avoid a generic AI-generated dashboard aesthetic. Every setting must be backed by real APIs and runtime enforcement; hide or label unsupported capabilities. No fake security scores, random metrics, hardcoded success indicators or demo controls presented as real. All actions require proper backend authorization.

Screens:
1. Overview: actual control status, recent security events, blocked actions, connector trust state and unresolved findings with timestamps and evidence. Distinguish enabled, enforced, failed, unavailable and not configured.
2. Identity & Access: users, identity provider settings, sessions, MFA policy and service accounts. Never display saved secrets.
3. Roles & Permissions: role definitions, permission matrix, scopes, effective access, audited changes and separation-of-duties controls.
4. Agent Security: read-only policy, iteration/time/tool/token limits, approval requirements, kill switch and execution policies.
5. MCP Security: approved connectors, endpoints, tool allowlists, scopes, schema changes, connection test and policy decisions.
6. LLM Governance: approved providers/models, local/external policy, fallback, token budget, data transfer policy and tracing restrictions.
7. Data Protection: redaction rules, classifications, retention policies, export policy and safe synthetic-data rule testing.
8. Secret Management: masked metadata, credential status, expiry, rotation and provider configuration.
9. Network Security: permitted destinations, proxy/CA settings and infrastructure-control status. Distinguish application settings from external firewall controls.
10. Approvals: pending/approved/rejected/expired requests, exact action details and authorized decision workflow.
11. Audit Logs: searchable paginated table, filters, details and authorized export.
12. SIEM Integration: supported formats, destination, safe connection test, delivery status and failures.
13. Compliance & Findings: requirement mapping, evidence links, status and remediation tracking; no unverified compliance badges.

Use confirmation dialogs for impactful changes; show validation errors and save results accurately. Require elevated authorization or reauthentication for sensitive settings where appropriate. Record all changes in audit logs. Never expose security-sensitive configuration values to viewers.

## 13. DevSecOps and software supply chain

### SDLC-01
- Configure appropriate CI checks for existing languages and stack: Semgrep/SAST, Bandit for Python where applicable, Gitleaks for secret scanning, dependency/SCA scanning, Trivy for containers/filesystems, SBOM (CycloneDX/SPDX), IaC scanning where applicable, and OWASP ZAP for authorized staging DAST.
- Use pinned dependencies/lockfiles, review dependency and license changes, least-privilege CI tokens, protected deployment environments and artifact provenance/signing where supported.
- Establish severity-based release gates, documented exceptions with owner/expiry and remediation tracking. Do not fabricate scan results or suppress findings without justification.
- Add safe AI security regression tests using Promptfoo or equivalent where appropriate, with synthetic fixtures and no external data transfer by default. Active penetration tests require explicit scope and approval; do not automatically attack production or external services.
- Create root `SECURITY.md` with responsible disclosure instructions and supported versions based on actual project policy; leave contact placeholders clearly marked if unknown.

## 14. Required tests and acceptance criteria

Use unit, integration, end-to-end and negative security tests against local/isolated test environments. Test through direct backend paths, not only UI.

| ID | Test | Expected result |
| --- | --- | --- |
| T01 | Unauthenticated protected API request | 401; no sensitive payload |
| T02 | Viewer invokes admin endpoint | 403; audit event |
| T03 | User requests another user's report/thread/checkpoint | Denied without data disclosure |
| T04 | Expired/revoked credential or session | Denied |
| T05 | Unauthorized MCP tool or modified arguments | Blocked before dispatch |
| T06 | Prompt injection in synthetic telemetry asks for secrets or disallowed tool | No secret disclosure or unauthorized action |
| T07 | Synthetic token/PII in model context and tracing | Removed or transfer blocked under policy |
| T08 | External model disabled, including fallback | No external model request |
| T09 | Unapproved connector destination, redirect or rebinding fixture | Connection blocked |
| T10 | Invalid tool/model output schema | Rejected safely |
| T11 | Iteration/time/token/concurrency limit exceeded | Controlled termination or rejection; audit event |
| T12 | Replay after permission revocation | Denied or safely stopped |
| T13 | Approval expired, reused or arguments modified | Denied |
| T14 | Attempted audit record update/delete by application identity | Denied |
| T15 | Credential save/read/list/log operations | No plaintext secret returned or logged |
| T16 | Policy unavailable for protected action | Fail closed |
| T17 | Duplicate scheduled job or resumed side-effect fixture | No duplicate protected side effect |
| T18 | Existing SRE analysis/scoring/checkpoint flows | Regression tests pass |
| T19 | Unauthorized export, SSE subscription or service account action | Denied |
| T20 | Backup/restore and migration in isolated environment | Verified recovery or documented blocker |
| T21 | Critical security settings changed through direct API | Authorization, validation and audit enforced |
| T22 | Audit/SIEM delivery failure | Detectable failure and configured safe behavior |

Also test role escalation, CSRF/XSS, SQL injection, rate limiting, retention/deletion, connector schema changes, policy update concurrency and data leakage via error messages. Add realistic positive tests so deny-by-default does not break authorized operations.

## 15. Implementation priorities

P0 — Before exposing platform: secure authentication, backend authorization, isolation, secrets, API hardening, TLS configuration, audit logging, default read-only tools, connector destination controls and outbound data minimization. Create CI secret/dependency/security checks early.

P1 — Agent security: central policy engine, tool enforcement, model governance, injection defense, DLP, checkpoint/memory security, execution limits, approvals for supported sensitive actions, cancellation, tracing redaction and security regression tests.

P2 — Enterprise operation: SSO/MFA enforcement, service account lifecycle, SIEM, external secret manager, advanced network settings, retention, backups, recovery, security administration UI and hardening guides.

P3 — Assurance: threat modeling refinement, SBOM/provenance, DAST, AI red teaming, independent penetration testing, compliance evidence and remediation tracking. Threat modeling and basic tests begin at P0, not after implementation.

Do not interpret these phases as permission to expose an incomplete or insecure system. Identify deployment blockers before release.

## 16. Documentation and final delivery

Produce or update:
- `SECURITY.md`
- `docs/platform/security/SECURITY_GAP_ANALYSIS.md`
- `docs/platform/security/SECURITY_IMPLEMENTATION_PLAN.md`
- `docs/platform/security/SECURITY_ARCHITECTURE.md` (trust boundaries, data flows, policy enforcement points)
- `docs/platform/security/THREAT_MODEL.md` (assets, actors, attack surfaces, abuse cases, mitigations, residual risks)
- `docs/platform/security/ACCESS_CONTROL.md`
- `docs/platform/security/AI_AGENT_SECURITY.md`
- `docs/platform/security/MCP_SECURITY.md`
- `docs/platform/security/LLM_GOVERNANCE.md`
- `docs/platform/security/DATA_PROTECTION.md`
- `docs/platform/security/AUDIT_LOGGING.md`
- `docs/platform/security/SECRET_MANAGEMENT.md`
- `docs/platform/security/SECURITY_HARDENING.md`
- `docs/platform/security/INCIDENT_RESPONSE.md`
- `docs/platform/security/COMPLIANCE_MATRIX.md`

Use OWASP ASVS, OWASP API Security, OWASP LLM/Agentic AI/MCP security guidance, NIST AI RMF, ISO 27001 and ISO 42001 as reference frameworks; consider KVKK/GDPR and sector-specific requirements where applicable. Map actual controls to requirements and evidence. No claims of certification, legal compliance, or penetration-test success without appropriate independent assessment.

Final report must include changed files, implemented requirement IDs, tests executed and actual results, migration/configuration instructions, security findings and residual risks, blocked/not applicable requirements, external dependencies, and recommended next steps. Clearly distinguish implemented, configured, tested and independently verified controls.

## Definition of done
A requirement is complete only when its enforcement exists in the appropriate backend/runtime or verified deployment control, authorized use still works, negative tests pass, relevant audit events are generated, sensitive data is protected and documentation matches reality. A frontend toggle, prompt instruction or untested configuration alone is not an implemented security control.
