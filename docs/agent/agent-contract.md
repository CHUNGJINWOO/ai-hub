# External Agent Contract / Harness Reference

**Status:** Reference contract for an External Agent
**Runtime:** Agent Runtime and Harness Runtime not implemented in AI-Hub
**Scope:** Project-agnostic task execution and verification

This document defines the minimum contract for an External Agent Runtime or
Harness that uses AI-Hub Knowledge. It coordinates a task but does not make
the Harness an AI-Hub core component. ECC and similar external harnesses are
reference patterns only; they are not AI-Hub dependencies.

## Ownership boundary

```text
External Agent / Harness
    ├── task planning and execution
    ├── Skill selection and execution
    ├── Workflow sequencing
    ├── Policy and approval decisions
    ├── Hook invocation and review
    └── final judgment and response
             │
             ▼
AI-Hub
    ├── Knowledge
    ├── Search
    ├── Context Assembly
    ├── MCP / REST access
    └── authorization boundary
```

AI-Hub supplies project-scoped Knowledge, retrieval, canonical context,
provenance references, MCP access, and request authorization. It does not
execute an Agent Runtime, Harness, Skill, Workflow, Policy Engine, or Hook
Runtime, and it does not perform Agent reasoning or generate the final answer.

## Relationship to M1 components

- **Skill:** provides reusable domain procedures and reasoning guidance. The
  Harness selects and invokes a Skill; AI-Hub provides the context it needs.
- **Workflow:** defines the ordered task steps. The Harness executes the
  Workflow and manages its temporary state.
- **Policy:** defines scope, permission, approval, production, and data rules.
  The Harness applies host policy; AI-Hub evaluates its supplied
  authorization context at the request boundary.
- **Hook:** checks or gates transitions before and after steps. The Harness
  invokes Hooks and handles stop, review, or retry decisions.

These artifacts remain separate from an Agent Harness. The Harness coordinates
them; it does not absorb their contracts into AI-Hub.

## Minimum Agent Contract

For each task, the Harness must maintain enough state to identify:

- the task and requested outcome;
- the project scope and authorization basis;
- the selected Skill and Workflow, when applicable;
- retrieved context and provenance;
- plan, risk, approval, and execution status;
- build/test and diff/review results;
- final facts, inferences, unknowns, and proposed next steps.

The Harness must preserve the distinction between retrieved evidence and its
own reasoning. It must not claim that an action or validation occurred unless
it actually occurred.

## Execution lifecycle

```text
Task
  →
Repository Inspection
  →
Context Retrieval
  →
Plan
  →
Risk / Scope Check
  →
Implementation
  →
Build / Test
  →
Diff / Review
  →
Documentation / Memory
```

### 1. Task

Capture the user's requested outcome, constraints, acceptance conditions, and
whether the task is read-only or may cause a side effect. Identify the
expected project before accessing project-scoped Knowledge.

### 2. Repository Inspection

Inspect the relevant repository, current implementation, configuration,
tests, documentation, and working-tree state. Confirm the repository boundary
and preserve unrelated user changes. Do not use an external project or
production system as an implicit part of the task.

### 3. Context Retrieval

Request the smallest authorized context needed for the task. Prefer
`get_context` for canonical items and sources; use `search_context` for
focused or follow-up retrieval. Use `list_projects`, `get_document`, and
`health_check` only according to their current MCP meanings.

The current Knowledge MCP tools are exactly:

- `health_check`
- `search_context`
- `list_projects`
- `get_document`
- `get_context`

The Harness must not duplicate AI-Hub search, ranking, project filtering, or
Context Assembly.

### 4. Plan

Define the smallest implementation approach, affected files, expected
behavior, validation commands, and rollback or stop conditions. Separate
facts observed during inspection from assumptions and planned actions.

### 5. Risk / Scope Check

Before implementation, verify:

- the project scope and authorization context permit the requested operation;
- `project_id` filtering is not being mistaken for authorization;
- projectless reads have explicit global-read authorization;
- writes, production access, migrations, external side effects, and secret
  access have the required host approval;
- the task does not expand into an unrequested runtime, API, migration, or
  external system change.

Authorization denial is an explicit failure. The Harness must not convert it
into an empty search result, retry without scope, substitute another project,
or assume an allow-all identity.

### 6. Implementation

Apply only the approved plan and scope. Preserve existing behavior and
unrelated changes. The Harness owns execution; AI-Hub remains the
Knowledge/Context/MCP infrastructure and is not modified to become an Agent
Runtime merely to complete a task.

### 7. Build / Test

Run the smallest validation that proves the requested behavior, then broaden
only when required. Record command, result, environment, and limitations.
Distinguish a baseline failure from a regression introduced by the task.

### 8. Diff / Review

Inspect the complete diff and verify:

- only intended files changed;
- project scope and authorization remained valid;
- evidence, provenance, and uncertainty are represented correctly;
- no secret, generated data, or unrelated change was included;
- implementation, tests, and documentation agree.

### 9. Documentation / Memory

Document the resulting behavior, validation evidence, limitations, and
follow-up decisions. The Harness may prepare a handoff to an External Agent
session or an explicitly approved Knowledge Memory write, but AI-Hub does not
provide Session Memory Runtime or automatic promotion.

## Failure and uncertainty handling

Stop or request review when:

- the project or repository boundary is unclear;
- authorization is missing, denied, or inconsistent with the requested
  `project_id`;
- context is unavailable, empty, stale, conflicting, or insufficient;
- provenance for a material claim is missing;
- a plan exceeds approved scope;
- approval is missing for a write or external side effect;
- build, test, command, or external action fails or partially executes.

The Harness must report the concrete failure, preserve observed side effects,
and distinguish:

- **Evidence / fact:** directly supported by retrieved or executed evidence;
- **Inference:** reasoning derived from that evidence;
- **Unknown / conflict:** missing, stale, inconsistent, unauthorized, or
  unverified information.

It may perform additional authorized retrieval or request a validation step.
It must not fill an evidence gap with a confident assumption or produce a
success-shaped result after a failed action.

## Memory handoff contract

Memory handoff is an External Agent responsibility and is not automatic.
When a task produces durable, project-relevant information, the Harness may
prepare a proposed handoff with:

### Decision

- decision statement;
- alternatives considered;
- rationale and affected project scope;
- evidence and provenance;
- status, owner, and validation state.

### Troubleshooting

- observed symptom and environment;
- investigated evidence;
- confirmed cause, if any;
- rejected hypotheses and uncertainty;
- remediation and verification result.

### Project State

- state description and scope;
- relevant revision or timestamp;
- dependencies and known limitations;
- next validation or transition condition.

Session notes, temporary execution state, private conversation history, and
unapproved handoffs remain External Agent Session Memory. Promotion to
AI-Hub Knowledge Memory requires explicit approval, project scope, and
provenance; it is never implied by the Harness lifecycle.

## Future Knowledge connection

The current contract uses AI-Hub Search and Context Assembly. In a future
design, an Ontology or Knowledge Graph could organize entities,
relationships, decisions, and project state before context retrieval:

```text
Ontology / Knowledge Graph
            ↓
      Context Assembly
            ↓
   External Agent / Harness
            ↓
   Skill / Workflow / Policy / Hook
```

This is a future architecture direction, not an implemented graph, ontology,
router, M3 domain intelligence, M4 model layer, or new MCP/API contract.
Context Assembly remains the boundary that supplies normalized evidence to the
Harness.

## Non-goals

This document does not implement:

- AI-Hub Agent Runtime or Harness Runtime;
- Skill Runtime or Registry;
- Workflow Engine, Policy Engine, or Hook Runtime;
- new MCP tools, REST APIs, authentication adapters, or identity providers;
- Session Memory Runtime or automatic Memory promotion;
- Router, M3 Domain Intelligence, or M4 Model & Routing.

The contract is reusable across projects and does not make ROS2 or LIMO an
AI-Hub platform boundary.
