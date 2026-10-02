# ROS2 / Robotics Reference Skill

**Status:** Reference artifact for an External Agent
**Runtime:** Not implemented in AI-Hub
**Scope:** Project-agnostic ROS2 and robotics analysis

This document defines a portable reference Skill that an External Agent can
use when answering ROS2 or robotics questions with evidence from AI-Hub. It is
not a Skill registry, executor, workflow engine, or AI-Hub core entity.

## Purpose

Use this Skill to investigate ROS2 systems and robotics projects by retrieving
project-scoped knowledge from AI-Hub, reasoning over the retrieved evidence,
and returning a result that separates facts, inferences, and unknowns.

The Skill is reusable across ROS2 projects. LIMO may be used as a validation
project or example, but the procedure is not LIMO-specific and does not assume
particular packages, nodes, topics, or robot hardware.

## When to use

Use the Skill for questions such as:

- explaining a ROS2 node, topic, service, action, launch file, or parameter;
- tracing configuration or data flow across source code and documents;
- comparing documented behavior with an implementation;
- investigating a build, launch, navigation, or integration issue when the
  relevant evidence is indexed in AI-Hub;
- summarizing project-specific ROS2 conventions with source references.

Do not use this Skill when the requested answer requires live robot control,
an unverified external system, or evidence that is not available in the
authorized AI-Hub project context. The External Agent must state that
limitation rather than inventing missing details.

## Inputs

The External Agent supplies:

1. the user's ROS2 or robotics question;
2. the intended `project_id` or a project-selection step;
3. relevant constraints, such as ROS2 distribution, hardware, package, or
   repository revision, when known;
4. the requested output form, such as explanation, diagnosis, comparison, or
   implementation guidance.

The External Agent must not treat `project_id` filtering as authorization.
Authorization must already be established at the AI-Hub request boundary.
A projectless read is not implicit global access.

## AI-Hub context required

The Skill uses AI-Hub as a Knowledge Infrastructure boundary:

- **Knowledge:** projects, documents, source code, memories, and indexed
  document chunks;
- **Search:** semantic/hybrid retrieval through the existing capabilities;
- **Context Assembly:** canonical context items and source references;
- **MCP access:** the currently available Knowledge tools;
- **Project scope and authorization:** access is evaluated before retrieval;
  `project_id` is then forwarded as a data-filtering scope.

For a focused ROS2 question, prefer `get_context` to request canonical,
project-scoped context. Use `search_context` when the result shape of unified
memory/document search is specifically useful. `list_projects` can support
project selection, and `get_document` can retrieve document metadata after a
relevant document has been identified. `health_check` only checks MCP
service availability; it does not establish project authorization.

The Skill does not reproduce AI-Hub search logic, ranking, project filtering,
or Context Assembly semantics.

## Procedure

```text
ROS2 question
    ↓
Identify the required project and context
    ↓
Request authorized AI-Hub context with get_context/search_context
    ↓
Inspect retrieved evidence and provenance
    ↓
Perform ROS2/robotics analysis in the External Agent
    ↓
Produce a result with facts, inferences, and unknowns separated
    ↓
Validate claims and report limitations
```

### Step 1: Establish scope

Clarify the project, ROS2 distribution, package or subsystem, and the
question's desired outcome. If the project is not known, use `list_projects`
only within the caller's authorized access and ask the user or host to select
the intended project.

### Step 2: Retrieve context

Request the smallest useful context with the question and explicit
`project_id`. Preserve the returned item and source references. Refine the
query with `search_context` if the first context set is insufficient.

### Step 3: Inspect evidence

Check which source supports each relevant claim. Distinguish source code,
configuration, generated output, documentation, and memory. Use
`get_document` only when document metadata is needed; it does not replace
context retrieval or provide an unscoped authorization bypass.

### Step 4: Analyze

Reason about the ROS2 system in the External Agent. Examples include node
relationships, topic flow, parameter effects, launch composition, lifecycle,
navigation configuration, and likely causes of a reported behavior. Do not
present an inference as a retrieved fact.

### Step 5: Validate and report

Cross-check important claims against all relevant retrieved sources. If the
evidence conflicts, report the conflict. If evidence is missing, identify the
missing artifact or test needed to resolve it.

## Minimal MCP usage example

The following is a conceptual call sequence using the current MCP tool
contract. It is not an additional tool or API, and the External Agent remains
responsible for supplying the authorized request context.

```text
User:
"Analyze the LIMO Nav2 configuration and explain how the controller
parameters affect navigation."

External Agent / ROS2 Skill:
1. Confirm the authorized project scope for the LIMO project.
2. Call the existing AI-Hub MCP capability:

   get_context(
       query="Nav2 controller parameters navigation behavior",
       limit=10,
       project_id=<authorized_project_id>
   )

3. Inspect returned canonical `items` and `sources`.
4. If more targeted retrieval is needed, call:

   search_context(
       query="Nav2 controller parameters",
       limit=10,
       project_id=<authorized_project_id>
   )

5. Compare the retrieved configuration and documentation evidence.
6. Return the analysis with:
   - Facts supported by cited sources
   - Inferences derived from those facts
   - Unknowns or conflicts
   - Suggested validation steps
```

The example uses only existing tools. `get_context` and `search_context`
perform retrieval; neither generates an answer or runs ROS2 commands.

## Output format

The External Agent should return:

```text
Question
Project scope

Answer

Facts
- [claim] (source reference)

Inference
- [reasoning derived from the facts]

Unknown / conflicting evidence
- [missing or inconsistent information]

Validation
- [test, command, source, or runtime observation still needed]
```

Source references should preserve the identifiers and metadata returned by
AI-Hub. The Skill must not fabricate file paths, line numbers, package names,
runtime observations, or benchmark results.

## Validation and provenance requirements

Before finalizing an answer, the External Agent must verify that:

- the response is within the authorized project scope;
- each material factual claim has a retrieved source or is explicitly marked
  as an inference or unknown;
- the context query and project scope are recorded with the result;
- conflicting or stale evidence is called out;
- no private or secret data is copied into the response unnecessarily;
- proposed commands or changes are clearly proposals, not claims that they
  were executed;
- any user-requested write, migration, production action, or external side
  effect is handled by the host's approval policy rather than by this Skill.

AI-Hub provides Knowledge, retrieval, canonical context, source provenance,
and access boundaries. The External Agent preserves the provenance in its
answer and owns the reasoning and validation narrative.

## Insufficient information and failure behavior

If MCP is unavailable, authorization is denied, the project is missing, or
retrieval returns insufficient evidence:

1. stop the affected analysis path;
2. report the concrete limitation;
3. do not substitute an unscoped query, fabricated context, or assumed
   project;
4. request the missing project, source, authorization, or validation step;
5. retry only when the host or user supplies an authorized, meaningful
   correction.

An empty retrieval result is evidence that no matching context was returned;
it is not permission to infer global access or to claim that the project has
no such behavior.

## Ownership boundary

### AI-Hub owns

- project, document, memory, and indexed knowledge storage;
- semantic/hybrid search and project-scoped retrieval;
- canonical Context Assembly and source/provenance references;
- MCP and REST access boundaries;
- authorization evaluation at the request boundary.

### External Agent owns

- Skill selection and execution;
- ROS2/robotics reasoning and synthesis;
- workflow sequencing, approvals, and execution of proposed commands;
- temporary session state;
- final answer structure, uncertainty reporting, and user communication.

This Skill is consumed by an External Agent through AI-Hub MCP. It does not
make the Skill a runtime component of AI-Hub and does not add session memory,
automatic memory promotion, a registry, or an executor.
