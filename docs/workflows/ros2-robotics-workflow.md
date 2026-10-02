# ROS2 / Robotics Reference Workflow

**Status:** Reference workflow for an External Agent
**Runtime:** Not implemented in AI-Hub
**Scope:** Project-agnostic ROS2 and robotics work

This document defines the order and conditions an External Agent can use with
the [ROS2 / Robotics Reference Skill](../skills/ros2-robotics-skill.md) and
AI-Hub Knowledge MCP. It is a portable workflow artifact, not a Workflow
Engine, Registry, database entity, or Agent Runner.

## Responsibility and execution boundary

The External Agent executes this workflow. It owns:

- workflow sequencing and temporary execution state;
- ROS2 / Robotics Skill execution;
- reasoning and interpretation of retrieved evidence;
- approval and execution of proposed commands or external side effects;
- final response, uncertainty reporting, and user communication.

AI-Hub is not the Workflow Runtime. It provides:

- Knowledge and project-scoped data;
- Search and retrieval;
- canonical Context Assembly;
- MCP access;
- the request authorization boundary.

AI-Hub does not select or execute the Skill, perform the Agent's reasoning,
run ROS2 commands, or generate the final answer. The workflow must not
reimplement AI-Hub search, project filtering, or Context Assembly.

## Workflow

```text
User Request
    ↓
Project confirmation
    ↓
Authorization confirmation
    ↓
Knowledge retrieval
    ↓
search_context / get_context
    ↓
ROS2 / Robotics Skill execution
    ↓
Evidence and provenance verification
    ↓
Result composition
    ↓
Uncertainty and validation reporting
```

### 1. Inspect the request

The External Agent identifies:

- the ROS2 or robotics question;
- the intended project and `project_id`;
- ROS2 distribution, package, subsystem, hardware, or repository revision
  constraints when known;
- whether the requested work is read-only analysis or could cause an external
  side effect.

If the project is unknown, the Agent may use the current `list_projects` MCP
tool within its authorized scope and ask the user or host to select the
project. LIMO is an example project only; this workflow is not LIMO-specific.

### 2. Confirm authorization

The Agent confirms that the request has an authorization context for the
intended project before requesting project data. `project_id` filtering is a
data-filtering scope, not authorization.

A projectless read is not implicit global access. If authorization is denied,
the Agent must stop the affected path and report the denial explicitly. It
must not convert authorization denial into an empty search result or infer
that no matching knowledge exists.

The current authorization boundary evaluates access before retrieval. The
workflow does not invent token, role, identity-provider, or project-mapping
behavior that is not supplied by the host.

### 3. Retrieve required knowledge

The Agent requests the smallest useful project-scoped context:

- Prefer `get_context` when canonical items and source references are needed
  for analysis.
- Use `search_context` for targeted unified memory/document retrieval or to
  refine an insufficient context result.
- Use `get_document` only when metadata for an identified document is needed.
- Use `health_check` only to check MCP service availability; it is not an
  authorization check.

The current MCP tools are exactly:

- `health_check`
- `search_context`
- `list_projects`
- `get_document`
- `get_context`

Both retrieval tools accept a query, bounded limit, and optional `project_id`.
For project-scoped ROS2 work, the Agent should pass the authorized
`project_id` explicitly.

### 4. Execute the ROS2 / Robotics Skill

The External Agent applies the ROS2 / Robotics Skill to the retrieved
context. It may reason about nodes, topics, services, actions, parameters,
launch composition, lifecycle, navigation configuration, or likely causes of
a reported behavior.

Retrieved evidence and Agent reasoning must remain separate:

- **Evidence:** claims directly supported by returned items and sources.
- **Inference:** conclusions derived by the Agent from that evidence.
- **Unknown:** information not present, conflicting, stale, or not verified.

The Agent must not claim that a command, build, launch, robot test, or external
observation was performed unless it actually was.

### 5. Verify evidence and provenance

Before composing the result, the Agent checks:

- all material claims remain within the authorized project scope;
- each factual claim has a relevant returned source or is marked as inference;
- item/source references and the query/project scope are preserved;
- conflicting or stale evidence is reported;
- no unnecessary secret or private data is repeated.

AI-Hub provides retrieval and provenance references. The External Agent owns
the interpretation and the provenance-aware explanation.

### 6. Compose and report the result

The External Agent returns a result in a structure such as:

```text
Question
Project scope

Answer

Evidence / facts
- [claim] (retrieved source reference)

Reasoning / inference
- [conclusion derived from the evidence]

Unknowns / conflicts
- [missing, stale, or inconsistent information]

Validation still needed
- [test, command, source, or runtime observation]
```

If the context is insufficient, the Agent must state that limitation and
perform additional authorized retrieval or request a validation step. It must
not fill gaps with assumptions.

## Failure and stop conditions

Stop the affected workflow path when:

- the project cannot be identified;
- authorization is denied or unavailable;
- MCP is unavailable;
- retrieval returns insufficient or conflicting evidence that cannot be
  resolved;
- the requested operation requires an approval or external capability not
  available to the host.

For each stop, report the concrete condition, preserve the distinction between
denial and an empty result, and identify the next information or approval
needed. Do not retry with a projectless or unscoped request as a workaround.

## Read-only analysis and side effects

This reference workflow primarily describes read-only analysis. A proposed
code change, migration, production action, live robot command, or other
external side effect remains under the External Agent host's approval and
execution policy. AI-Hub authorization is not a substitute for those
workflow approvals.

This document does not implement Policy or Hook runtime. Approval gates,
failure hooks, audit events, and durable session state remain External Agent
responsibilities and future contract areas.

## Boundary summary

| Concern | AI-Hub | External Agent |
|---|---|---|
| Knowledge storage and project data | Owns | Consumes |
| Search and Context Assembly | Owns | Requests and interprets |
| MCP access | Provides | Calls |
| Request authorization boundary | Evaluates supplied context | Supplies/uses authorized scope |
| Project filtering | Applies after authorization | Passes intended `project_id` |
| Skill execution | Does not execute | Owns |
| Workflow execution | Does not execute | Owns |
| Reasoning and final response | Does not generate | Owns |
| Temporary session state | Does not own | Owns |

The workflow is reusable for ROS2 projects beyond LIMO and does not make
ROS2, LIMO, a Skill runtime, or a Workflow runtime part of AI-Hub core.
