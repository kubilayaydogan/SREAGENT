# TASK: Complete Redesign of Chaos Controller — Advanced SRE Failure Simulation Platform

## 1. Objective

You are a Senior SRE, Chaos Engineering Architect, Backend Engineer, and Frontend Engineer.

Completely redesign the existing Chaos Controller module in my SRE laboratory project.

**IMPORTANT: Before writing code, analyze the entire existing repository, including all applications, database connections, Docker Compose configuration, Dynatrace integrations, and current Chaos Controller implementation.**

The existing chaos recipes are ineffective. They do not generate meaningful resource pressure, database contention, or cascading failures.

I want to transform Chaos Controller into a professional, configurable, observable Chaos Engineering platform.

The primary objective is:

**An injected failure in Application A must be capable of affecting the shared PostgreSQL database and consequently degrading Application B, C, or D.**

The resulting incidents must be sufficiently observable for my AI-powered SRE Agent to investigate using Dynatrace.

Do not merely implement artificial error responses or sleep statements. Create genuine, measurable resource contention and dependency failures in an isolated laboratory environment.

---

## 2. Existing Architecture Discovery

First, inspect the repository and identify:

- All existing application services.
- Application names and responsibilities.
- Application-to-application dependencies.
- PostgreSQL schemas, tables, connection pools, and shared resources.
- Docker networks and containers.
- Existing Chaos Controller APIs.
- Existing Chaos Controller frontend.
- Dynatrace OneAgent instrumentation.
- Dynatrace MCP integration.
- Existing observability and monitoring infrastructure.
- SRE Agent integration.
- Existing configuration and safety controls.

Produce an architecture analysis before modifying the system.

Do not assume application names, database table names, container names, or API endpoints. Discover them from the actual code.

Preserve all functionality unrelated to the requested redesign.

---

## 3. Complete Chaos Controller Frontend Redesign

Replace the existing Chaos Controller UI with a modern Chaos Engineering dashboard.

The dashboard must contain five main sections.

### A. Application Selection

Display all discovered applications as selectable cards.

Each application card should show:

- Application name
- Running status
- Database dependency
- Current CPU utilization
- Current RAM utilization
- Database connection pool usage
- Request rate
- Error rate
- P95 response latency
- Active chaos experiments

Allow the user to select one or multiple applications as **Chaos Initiators**.

Chaos Initiator means the application responsible for generating the workload, injecting faults, or causing resource contention.

### B. Target Selection

Allow the user to choose which applications or infrastructure components should experience degradation.

Support:

- Single application
- Multiple applications
- All applications
- Shared PostgreSQL database
- Randomly selected applications
- Automatically selected dependency targets

The interface must distinguish between:

**Initiator:** The application generating the failure.

**Direct Target:** The application or resource directly exposed to the failure.

**Affected Application:** Another application impacted indirectly through shared dependencies.

For example:

Initiator: Application A

Direct Target: Shared PostgreSQL

Expected affected applications: Application B and Application C

The UI must never imply that the controller can guarantee an indirect failure. Expected and actually observed impacts must be reported separately.

### C. Chaos Intensity Configuration

Allow configurable workload intensity.

The user must be able to choose:

- Light
- Medium
- High
- Critical
- Custom

Custom parameters must include:

- Concurrent workers
- Request rate per second
- Number of database connections
- Transaction duration
- Query execution frequency
- Target workload duration
- Ramp-up duration
- Maximum execution duration
- Resource consumption budget
- Error injection probability
- Network latency injection
- Network packet loss percentage
- Maximum number of concurrently affected services

Use sliders, numeric inputs, and clear descriptions.

Intensity presets must be calibrated against actual laboratory resources and baseline performance.

Do not use arbitrary fixed workloads and claim they represent high or critical intensity.

### D. Random Chaos Mode

Implement an optional random chaos execution mode.

The system should be capable of randomly selecting:

- Chaos initiating applications
- Chaos scenarios
- Direct targets
- Workload intensity
- Experiment duration
- Number of participating applications

Provide additional configuration:

- Random seed for reproducibility
- Allowed target list
- Excluded applications
- Maximum experiment intensity
- Number of scenarios per execution
- Sequential or parallel execution
- Cooldown between scenarios

Random mode must obey exactly the same safety constraints as manually selected scenarios.

### E. Live Experiment Monitoring

Create a real-time dashboard displaying:

- Current experiment
- Initiating application
- Direct targets
- Expected downstream impact
- Observed affected applications
- Execution state
- Elapsed time
- CPU utilization
- RAM utilization
- PostgreSQL active connections
- PostgreSQL lock waits
- PostgreSQL query latency
- PostgreSQL transaction throughput
- PostgreSQL deadlocks
- PostgreSQL I/O activity
- Application error rate
- Application P95 and P99 latency
- Dynatrace monitoring links

Use real-time metric streaming or periodic polling.

Never display fake monitoring values.

---

## 4. Implement 20 Genuine Chaos Scenarios

Implement the following scenarios as distinct, configurable chaos recipes.

Each recipe must declare its prerequisites, initiator, direct target, expected dependency impact, configurable intensity, measurable success criteria, abort conditions, and cleanup procedure.

### PostgreSQL Resource Contention

**Scenario 01 — Connection Pool Exhaustion**

Application A generates enough concurrent database requests to saturate its configured connection pool.

Expected impact:
- Connection acquisition latency increases.
- Application A experiences request delays.
- Other applications may degrade if PostgreSQL connection capacity becomes constrained.

**Scenario 02 — PostgreSQL Lock Contention**

Application A executes concurrent transactions against dedicated shared laboratory tables.

Expected impact:
- Competing transactions block.
- Lock wait duration increases.
- Application B experiences increased latency if it accesses the same rows or tables.

**Scenario 03 — Long-Running Transactions**

Application A holds configurable transactions open for a bounded period.

Expected impact:
- Database transactions remain active.
- Lock contention may increase.
- Other applications accessing shared resources can experience delays.

**Scenario 04 — CPU-Intensive Database Queries**

Generate bounded, computationally expensive SQL operations using dedicated test data.

Expected impact:
- PostgreSQL CPU consumption increases.
- Query latency rises.
- Other applications sharing the database may slow down.

**Scenario 05 — High-Frequency Database Writes**

Application A generates configurable INSERT and UPDATE workloads against isolated test tables.

Expected impact:
- Write throughput increases.
- WAL generation increases.
- Database I/O pressure increases.
- Other applications may experience slower database operations.

### PostgreSQL Performance and Storage

**Scenario 06 — Heavy Read Workload**

Application A generates concurrent read requests against sufficiently large test datasets.

Expected impact:
- Read throughput and database CPU usage increase.
- Cache effectiveness may change.
- Query latency increases under saturation.

**Scenario 07 — Temporary Data Pressure**

Generate bounded temporary query workloads that exercise PostgreSQL temporary-file usage.

Expected impact:
- Temporary I/O increases.
- Query execution duration may increase.
- Shared database performance may degrade.

**Scenario 08 — Transaction Contention**

Multiple applications execute competing transactional operations on controlled shared records.

Expected impact:
- Lock waits and transaction latency increase.
- Application response time deteriorates.
- Retry behavior becomes observable.

**Scenario 09 — Deadlock Simulation**

Create controlled competing transaction sequences on dedicated test records to produce PostgreSQL deadlock detection events.

Expected impact:
- Deadlock count increases.
- PostgreSQL aborts one transaction.
- Application retry or error handling becomes observable.

**Scenario 10 — Database Connection Storm**

Selected applications simultaneously generate a rapid but bounded increase in database connection attempts.

Expected impact:
- Connection setup overhead increases.
- Database connection usage rises.
- Connection errors or acquisition delays may appear.

### Application and Infrastructure Failures

**Scenario 11 — Application CPU Saturation**

Generate bounded CPU-intensive tasks inside selected laboratory application containers.

Expected impact:
- Application request latency increases.
- Health-check performance may degrade.
- Database operation scheduling may be delayed.

**Scenario 12 — Application Memory Pressure**

Generate bounded memory pressure inside selected application containers.

Expected impact:
- Memory utilization increases.
- Garbage collection or allocation latency may increase.
- Application responsiveness may deteriorate.

**Scenario 13 — Database Network Latency**

Inject bounded network latency specifically between selected application containers and PostgreSQL.

Expected impact:
- Database round-trip latency increases.
- Application database calls slow down.
- Connection pool saturation may follow.

**Scenario 14 — Intermittent Database Packet Loss**

Apply controlled packet loss on the laboratory network path between selected applications and PostgreSQL.

Expected impact:
- Database operations become unreliable.
- Timeouts and retries increase.
- End-user API latency deteriorates.

**Scenario 15 — Application-to-Application Dependency Latency**

Selected applications introduce configurable delays in calls to dependent laboratory services.

Expected impact:
- Upstream request latency increases.
- Timeouts and retries may occur.
- Dependency-related incidents become visible.

### Cascading Failure Scenarios

**Scenario 16 — Retry Storm**

Application A experiences a controlled dependency failure and executes configurable retries against a laboratory dependency.

Expected impact:
- Request amplification occurs.
- Dependency utilization increases.
- Other applications sharing the dependency may deteriorate.

**Scenario 17 — Cascading Database Saturation**

Application A generates sustained database workload.

Application B simultaneously executes realistic transactional operations against the same PostgreSQL instance.

Application C executes periodic read queries.

Expected impact:
- Database resource contention increases.
- Application B transactional latency rises.
- Application C read latency rises.
- Multiple applications exhibit correlated degradation.

**Scenario 18 — Multi-Application Concurrent Load**

Two or more selected applications generate concurrent workloads against PostgreSQL.

Expected impact:
- Shared database CPU, I/O, or connection utilization increases.
- Database latency increases.
- Multiple applications experience performance degradation.

**Scenario 19 — Slow Database + Retry Amplification**

Introduce controlled database latency while applications execute bounded retry policies.

Expected impact:
- Database connection occupancy increases.
- Request volume may be amplified.
- Application timeouts and errors increase.

**Scenario 20 — Randomized Cascading Failure**

A configurable scenario combining multiple compatible failure types.

Example:

Application A generates heavy database writes.

Application B generates intensive read queries.

Application C experiences bounded database network latency.

Expected impact:
- PostgreSQL becomes a potential common bottleneck.
- Multiple services experience correlated performance degradation.
- SRE Agent must identify initiating failures and shared dependency effects.

---

## 5. Genuine Workload Generation

A critical requirement is that scenarios must generate **real, measurable pressure**, not merely return simulated error responses.

For each scenario:

1. Measure baseline performance.
2. Validate prerequisites.
3. Gradually increase workload.
4. Observe real resource utilization.
5. Stop increasing load when the requested target is reached or a safety threshold is hit.
6. Maintain the target for the configured duration where feasible.
7. Stop the workload.
8. Restore the environment.
9. Verify resource recovery.

Implement adaptive workload control.

For example:

Target PostgreSQL CPU utilization: 65%

The controller should adjust workload concurrency based on observed metrics, instead of assuming that a fixed number of requests will create 65% utilization.

If a target cannot be reached safely, report the actual observed effect and explain the limitation.

Do not modify production data, real business records, or existing application tables destructively.

Use dedicated chaos schemas, synthetic records, and bounded datasets.

---

## 6. Cross-Application Database Impact

This is the most important architectural requirement.

The chaos scenarios must support genuine dependency propagation.

Example architecture:

Application A → PostgreSQL ← Application B

Application C → PostgreSQL ← Application D

When Application A generates significant database workload, Applications B, C, and D should continue executing realistic baseline traffic.

This allows actual downstream degradation to become measurable.

Implement background traffic generators for each application.

Each generator must support:

- Baseline RPS
- Concurrent users
- Read/write ratio
- Transaction types
- Request timeout
- Retry policy
- Think time
- Maximum workload limits

The platform must compare each application's baseline performance with its performance during the experiment.

A cross-application impact must be reported only when supported by observed metrics.

---

## 7. Experiment Execution Engine

Build an experiment orchestration engine with these states:

CREATED → VALIDATING → BASELINING → RAMPING_UP → RUNNING → RECOVERING → COMPLETED

Additional terminal states:

FAILED, ABORTED, TIMED_OUT

Implement:

- Experiment queue
- Execution concurrency limits
- Experiment isolation
- Scenario parameter validation
- Experiment lifecycle persistence
- Progress reporting
- Execution logs
- Stop button
- Emergency stop
- Automatic timeout
- Recovery procedures
- Post-experiment verification

Experiments must continue to be managed correctly even when the frontend disconnects.

The backend must remain the source of truth for experiment state.

---

## 8. Dynatrace Observability Integration

The purpose of the chaos environment is to test my AI-powered SRE Agent.

Integrate experiments with the existing Dynatrace instrumentation and MCP architecture.

Every experiment must have a unique experiment ID.

Attach correlation metadata where supported:

- chaos.experiment.id
- chaos.scenario
- chaos.initiator
- chaos.target
- chaos.intensity
- chaos.phase

Use these fields in application traces, structured logs, and experiment events where appropriate.

Do not assume arbitrary custom attributes are automatically searchable in Dynatrace. Inspect the existing instrumentation and ingestion pipeline, and configure supported attribute mapping as necessary.

Make the following relationships observable:

Initiating Application → PostgreSQL → Affected Applications

Collect evidence for:

- Increased database latency
- Increased connection acquisition duration
- Lock wait events
- Deadlocks
- Slow SQL queries
- Database CPU pressure
- Application error rates
- Request latency degradation
- Retry amplification
- Resource recovery

Use supported existing telemetry sources.

Do not fabricate Dynatrace metrics or rely on undocumented endpoints.

Generate a concise experiment report explaining:

- What was injected
- Which application initiated it
- Which shared dependency was affected
- Which applications actually degraded
- Evidence supporting the observed impact
- Whether the experiment reached its target
- Recovery status

The SRE Agent must be able to investigate incidents using real telemetry without being directly given the expected root cause as its answer.

---

## 9. Frontend UX Requirements

The interface must have a polished, professional SRE dashboard design.

Required views:

**Overview**
- Application health overview
- PostgreSQL health
- Active experiments
- Recent experiments

**Scenario Library**
- 20 scenario cards
- Scenario descriptions
- Expected effects
- Required infrastructure
- Search and filtering

**Experiment Builder**
- Select initiator applications
- Select scenarios
- Select direct targets
- Select desired impact intensity
- Configure workload parameters
- Select random or manual execution
- Preview expected dependency effects

**Live Experiment**
- Execution timeline
- Real-time metrics
- Application dependency view
- Experiment progress
- Stop and emergency-stop controls

**Experiment History**
- Previous experiments
- Parameters used
- Observed metrics
- Actual affected applications
- Outcome
- Recovery verification
- Re-run previous configuration

Avoid placeholder buttons, mock metrics, and nonfunctional controls.

---

## 10. Safety and Resource Isolation

All experiments must run exclusively within the authorized laboratory environment.

Required safety mechanisms:

- Explicit allowlist of application containers
- Explicit allowlist of databases
- Never connect to production databases
- Container-specific resource limits
- Maximum concurrent experiments
- Maximum workload duration
- Maximum database connection usage
- Maximum synthetic dataset size
- Disk free-space safety thresholds
- Automatic workload termination
- Emergency stop
- Automatic cleanup
- Recovery verification
- Auditable experiment history

Do not implement unrestricted host-level stress operations.

Network fault injection must be scoped to the designated laboratory containers and must not disrupt unrelated host traffic.

Database workloads must use the least privileges necessary.

No destructive operations against real application data.

Prefer dedicated chaos database users and schemas.

Use bounded execution, cancellation, and cleanup even if the controller process crashes.

---

## 11. Technical Architecture

Inspect and reuse the existing backend and frontend technology stack wherever appropriate.

Prefer a modular design separating:

1. Chaos Controller API
2. Experiment Orchestrator
3. Scenario Registry
4. Workload Generator
5. Fault Injection Adapters
6. Application Traffic Generator
7. Metrics Collector
8. Impact Analyzer
9. Safety Controller
10. Experiment Persistence
11. Frontend Dashboard

Do not create unnecessary microservices if existing services can support these responsibilities cleanly.

Store experiments, scenario configurations, execution events, and result summaries in the existing project persistence infrastructure where appropriate.

Avoid allowing experiment-history writes to interfere with measurements of the shared database under test. Document and mitigate this risk.

Implement idempotent stop and cleanup operations.

---

## 12. Acceptance Tests

Do not consider the task complete merely because 20 recipe definitions exist.

All 20 scenarios must be independently executable and verifiable.

Create automated tests proving:

- Every recipe has valid configuration.
- Every recipe starts correctly.
- Every recipe can be stopped.
- Every recipe respects resource limits.
- Every recipe cleans up after execution.
- Random mode is reproducible using a seed.
- Experiment status remains consistent.
- Application selection works.
- Direct target selection works.
- Intensity parameters affect actual workload.
- The live dashboard displays real collected metrics.
- Experiment history persists correctly.
- Dynatrace correlation metadata is produced.

Additionally, implement integration tests for cascading impact.

Example acceptance case:

1. Start baseline traffic against Applications A, B, C, and D.
2. Measure baseline PostgreSQL latency and application P95 latency.
3. Start a database pressure scenario from Application A.
4. Verify actual PostgreSQL resource pressure.
5. Verify whether Applications B, C, or D experience measurable latency degradation.
6. Correlate the observed degradation to the experiment interval.
7. Stop the experiment.
8. Verify PostgreSQL and application recovery.
9. Persist the results.

The test must not pass solely because the experiment was started.

It must validate real workload execution and report observed effects, including cases where the intended cascading impact did not occur.

---

## 13. Implementation Workflow

Work in the following order:

Phase 1 — Repository and Architecture Analysis

Phase 2 — Chaos Execution Engine

Phase 3 — Genuine Workload Generators

Phase 4 — 20 Configurable Chaos Recipes

Phase 5 — Shared Database Cascading Impact Mechanism

Phase 6 — Complete Frontend Redesign

Phase 7 — Dynatrace Integration

Phase 8 — Automated Testing

Phase 9 — End-to-End Laboratory Verification

Phase 10 — Technical Documentation

Do not stop after producing an architecture document or implementation plan.

Implement the necessary code changes, tests, configurations, and UI.

If laboratory services or dependencies are unavailable, implement what can be safely verified and clearly distinguish completed functionality from untested functionality.

Do not claim successful testing without execution evidence.

---

## 14. Final Deliverables

When implementation is finished, provide:

- Updated architecture diagram
- List of modified files
- All 20 implemented scenarios
- Scenario configuration schema
- Chaos API documentation
- Frontend screenshots if actually generated
- Database changes
- Required Docker Compose changes
- Dynatrace correlation design
- Automated test results
- Cascading-failure test results
- Performance baseline and experiment comparison
- Known limitations
- Instructions for running the platform

**Final priority: I want an SRE laboratory that produces real, observable, configurable, and recoverable cascading failures across multiple applications through PostgreSQL, rather than superficial chaos simulations.**

 