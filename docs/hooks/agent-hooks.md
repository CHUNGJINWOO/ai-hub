# External Agent Hook Reference

**Status:** Reference hook contract for an External Agent
**Runtime:** Hook Runtime and Executor not implemented in AI-Hub
**Scope:** Reusable ROS2 and robotics workflow lifecycle checks

This document defines lifecycle checks that an External Agent may perform
around the ROS2 / Robotics Workflow and Skill. A Hook is a signal and
validation boundary around a workflow step; it is not a Skill, Workflow,
Policy Engine, or AI-Hub core entity.

## Purpose and distinctions

### Hook

A Hook observes or gates a workflow transition. It checks whether a step may
start, whether its result is sufficiently evidenced, or whether a failure
requires stopping and review. A Hook does not perform the main reasoning or
domain work.

### Skill

A Skill defines reusable ROS2/Robotics procedures and reasoning guidance. The
ROS2 Skill interprets authorized AI-Hub context and produces an analysis. A
Hook can validate that the Skill received permitted context and reported its
evidence, but does not execute the Skill.

### Workflow

A Workflow defines the ordered work sequence: project inspection,
authorization, retrieval, Skill execution, review, and result composition. A
Hook runs at selected lifecycle points in that sequence; it does not replace
the sequence or own its execution.

### Policy

A Policy defines rules and approval boundaries, such as project scope,
read/write restrictions, production limits, and secret handling. A Hook
checks or signals policy-relevant conditions supplied by the External Agent;
it does not implement the Policy Engine or decide identity permissions on its
own.

## Ownership and execution boundary

The External Agent executes this Hook contract and owns:

- Hook invocation and lifecycle state;
- pre-step gating and post-step validation;
- workflow interruption, retry, review, and handoff decisions;
- evidence interpretation and uncertainty reporting.

AI-Hub is not a Hook Runtime or Executor. It provides:

- Knowledge and project-scoped data;
- Search and canonical Context Assembly;
- MCP access;
- authorization evaluation at the request boundary.

AI-Hub does not invoke Hooks, execute the Skill or Workflow, perform Agent
reasoning, or generate the final response. This document adds no Hook
registry, executor, callback API, MCP tool, or REST endpoint.

## Lifecycle model

```text
Workflow
    ↓
Pre-Hook
    ↓
Context Retrieval
    ↓
ROS2 / Robotics Skill
    ↓
Post-Hook
    ↓
Result
```

Failure or policy-relevant uncertainty can interrupt the path at any stage:

```text
Pre-Hook or Post-Hook failure
    ↓
Stop affected step
    ↓
Review evidence, scope, authorization, and approval
    ↓
Retry only with a meaningful authorized correction
```

## Pre-Hook

The External Agent runs a Pre-Hook before a workflow or step starts. It
checks:

1. **Project scope:** the intended project is identified and the requested
   `project_id` is explicit when project-scoped context is needed.
2. **Authorization:** the request has an authorization context permitting the
   requested read or write operation. Projectless read requires explicit
   global-read permission.
3. **Context plan:** the question, retrieval purpose, and expected evidence
   are clear enough to select a bounded context request.
4. **Approval boundary:** any write, production, live robot, migration, or
   external side effect is identified before execution and has the required
   host approval.
5. **Data boundary:** secrets and private context are not being sent beyond
   the authorized integration boundary.

`project_id` filtering is data filtering, not authorization. The Pre-Hook
must not infer authorization from the requested project ID or from a previous
search result.

If authorization is denied or unavailable, the Pre-Hook must stop and report
explicit denial. It must not convert denial into an empty search result,
retry without a project scope, substitute another project, or assume an
allow-all identity.

## Context retrieval boundary

After a successful Pre-Hook, the External Agent uses the current AI-Hub MCP
contract:

- `health_check` checks service availability only;
- `list_projects` supports project selection within authorized scope;
- `get_context` retrieves canonical context items and source references;
- `search_context` performs focused unified retrieval or follow-up search;
- `get_document` retrieves metadata for an identified document.

These are the current five tools. The Hook does not duplicate AI-Hub search,
ranking, project filtering, or Context Assembly. The External Agent passes an
authorized `project_id` to project-scoped retrieval and preserves the
returned item and source references.

An empty retrieval result is not authorization evidence. A denied request is
not an empty result.

## Post-Hook

The External Agent runs a Post-Hook after context retrieval, Skill execution,
or a workflow step. It checks:

1. **Evidence:** material factual claims are supported by retrieved items,
   source references, or explicitly verified observations.
2. **Provenance:** item/source identifiers, project scope, query purpose, and
   relevant revision or freshness information remain attached when available.
3. **Classification:** the result distinguishes:
   - **Evidence / fact:** directly supported information;
   - **Inference:** reasoning derived from evidence;
   - **Unknown / conflict:** missing, stale, inconsistent, unauthorized, or
     unverified information.
4. **Uncertainty:** unresolved gaps, conflicting sources, and validation
   limitations are visible in the result.
5. **Scope:** the result does not disclose inaccessible project data or
   claim that an unperformed command, build, launch, or robot test occurred.
6. **Output boundary:** proposed actions are marked as proposals unless the
   External Agent actually performed and verified them.

The Post-Hook validates the result; it does not turn uncertainty into
confidence or generate missing evidence.

## Failure, interruption, and review

The Hook must stop the affected workflow step and require review when:

- the project cannot be identified or scope changes unexpectedly;
- authorization is missing, denied, or inconsistent with the request;
- the MCP service or retrieval capability is unavailable;
- context is empty or insufficient for a material claim;
- evidence sources conflict or appear stale and cannot be resolved;
- provenance is missing for a material factual statement;
- an inference is presented as retrieved fact;
- a requested side effect lacks approval or exceeds its authorized scope;
- a command, build, launch, live robot action, or external observation fails
  or partially executes.

Review may request additional authorized retrieval, a corrected project scope,
an explicit approval, or an independent validation step. Retry is permitted
only after a meaningful correction. The Hook must not broaden authorization
or silently continue after a failed gate.

When an action partially executes, the External Agent reports observed side
effects separately from intended actions. A failed Hook does not produce a
success-shaped result.

## Minimal Hook record

The following is a conceptual record for the External Agent's temporary
workflow state, not a new AI-Hub schema:

```text
hook_phase: pre | post
workflow_step: project | authorization | retrieval | skill | result
project_id: authorized project or explicit global-read basis
operation: read | write
authorization: allowed | denied | unavailable
evidence_status: sufficient | insufficient | conflicting
provenance_status: present | incomplete
uncertainty_status: explicit | missing
decision: continue | stop | review | retry-after-correction
```

The storage location, event transport, retry semantics, ordering, audit
retention, and persistence owner are open design decisions for a future
runtime. This reference document does not implement them.

## Reusable ROS2 boundary

The Hook contract applies to ROS2 projects generally: nodes, topics,
services, actions, parameters, launch systems, navigation, and related
configuration. LIMO may be used as an example validation project, but no
Hook rule assumes LIMO packages, hardware, or deployment details.

The contract remains read-oriented by default. Live robot control, production
changes, migrations, code writes, and external provider calls remain subject
to the External Agent host's Policy and approval boundaries.

## Explicitly out of scope

This document does not implement:

- Hook Runtime, Executor, Registry, or callback transport;
- new MCP tools, REST APIs, or authorization adapters;
- token, role, or identity-provider integration;
- Policy Engine, Workflow Engine, Skill Runtime, or Agent Runner;
- Memory runtime, automatic promotion, Router, M3, or M4 features.
