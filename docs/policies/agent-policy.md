# External Agent Policy Reference

**Status:** Reference policy for an External Agent
**Runtime:** Policy engine not implemented in AI-Hub
**Scope:** Reusable Knowledge/MCP use for ROS2 and robotics work

This document defines governing rules for an External Agent that uses AI-Hub
Knowledge and MCP together with the
[ROS2 / Robotics Reference Skill](../skills/ros2-robotics-skill.md) and
[ROS2 / Robotics Reference Workflow](../workflows/ros2-robotics-workflow.md).
It is a policy artifact, not an executable Policy Runtime, identity provider,
role system, registry, or AI-Hub core entity.

## Ownership and enforcement boundary

AI-Hub provides:

- project, document, memory, and indexed knowledge;
- Search and canonical Context Assembly;
- MCP access;
- authorization evaluation at the request boundary.

The External Agent provides:

- policy-aware Skill and Workflow execution;
- reasoning and interpretation;
- approval handling and external side-effect control;
- final responses and uncertainty reporting;
- temporary session state.

AI-Hub does not replace Agent reasoning or enforce the complete host policy.
The External Agent must apply this reference policy before and after using
Knowledge. A future host may implement equivalent enforcement, but this
document does not add that runtime.

## 1. Project scope and authorization

Every Knowledge request must have an identified project scope unless an
explicit global-read authorization is available. The requested `project_id`
must be passed to project-scoped retrieval when applicable.

`project_id` filtering is data filtering, not authorization. The Agent must
not infer access from a filtered query or from a non-empty result. AI-Hub
authorization is evaluated before retrieval, and a request may proceed only
when the authorization context permits the requested project and operation.

The current boundary distinguishes:

- authorized project IDs;
- explicit global-read permission for projectless reads;
- durable-write permission in addition to project authorization.

If authorization is unavailable or denied, the Agent must report an explicit
denial and stop the affected path. It must not:

- treat denial as an empty search result;
- retry with a projectless request;
- substitute another project;
- assume a default identity or allow-all project set;
- disclose whether inaccessible knowledge exists.

This policy does not define token, role, identity-provider, or
identity-to-project mapping implementation. Those remain host/integration
decisions.

## 2. Knowledge and MCP use

Use only the current AI-Hub Knowledge MCP contract:

- `health_check`
- `search_context`
- `list_projects`
- `get_document`
- `get_context`

Use the smallest authorized retrieval that can answer the question:

- prefer `get_context` for canonical context items and source references;
- use `search_context` for focused or follow-up retrieval;
- use `list_projects` only within the caller's authorized scope;
- use `get_document` for metadata about an identified document;
- use `health_check` only for service availability, never as authorization.

The Agent must not duplicate AI-Hub ranking, project filtering, or Context
Assembly. Retrieval results are inputs to reasoning, not generated answers.

## 3. Evidence classification

Every material statement in an Agent result must be classified as one of:

### Evidence / fact

A statement directly supported by a returned AI-Hub item, source reference,
or an independently verified observation. Preserve the relevant provenance
and project scope.

### Inference

A conclusion derived by Agent reasoning from one or more facts. Label it as
an inference and do not present it as text directly retrieved from AI-Hub.

### Unknown / conflict

Information that is absent, stale, inconsistent, unauthorized, or not
verified. State what is missing or conflicting and avoid filling the gap with
an assumption.

The Agent must not claim that a command, build, launch, robot test, or
external observation occurred unless it actually occurred and its result is
available for the response.

## 4. Provenance requirements

For each material factual claim, the Agent should retain:

- the authorized `project_id` or explicit global-read basis;
- the query or retrieval purpose;
- the returned item/source identifier and available source metadata;
- whether the claim is evidence, inference, or unknown;
- relevant revision, timestamp, or freshness information when available;
- any conflict or validation limitation.

Provenance must remain attached when evidence is summarized, transformed, or
used in a recommendation. The Agent must not fabricate file paths, line
numbers, package names, source identifiers, runtime observations, or
benchmarks.

Do not copy secrets or private data into the result unnecessarily. If source
material contains sensitive data, minimize its exposure and report only what
is needed for the authorized task.

## 5. External information and verification

External documentation, repositories, commands, sensor observations, model
claims, and user-provided statements may supplement AI-Hub context only when
the Agent records that they are external inputs.

Before treating external information as a fact, the Agent must:

1. identify its source and scope;
2. check its relevance to the selected project and ROS2 environment;
3. distinguish the external claim from AI-Hub evidence;
4. seek corroboration or an executable validation step when the claim affects
   a diagnosis, safety decision, code change, or production action;
5. report uncertainty when independent verification is unavailable.

Vendor, model, performance, price, accuracy, hallucination, latency, or
inference-rate claims are not AI-Hub facts without independent verification.
An external source does not override project authorization or production
restrictions.

## 6. Automation and approval boundary

Default behavior is read-only analysis. The Agent may retrieve and reason
over authorized Knowledge, but it must not silently perform external side
effects.

Explicit approval is required before:

- writing code, documents, or project memory;
- running migrations or changing schemas;
- accessing production databases, containers, or deployments;
- controlling a live robot or sending operational commands;
- invoking external tools that write, delete, deploy, or incur cost;
- transmitting secrets or private context to an external provider.

Before an approved action, the Agent must state the intended scope, target,
effect, and validation. If approval is missing, the action remains a proposal.
Authorization to read a project does not authorize writes or production
operations. AI-Hub's project authorization and the host's approval policy
are separate boundaries.

Policy and Hook runtime, approval storage, audit storage, and enforcement
ordering are not implemented by this document.

## 7. Insufficient information and policy failures

When context is missing, retrieval is empty, evidence conflicts, or a
validation step cannot be performed:

- report the concrete limitation;
- perform additional authorized retrieval when it could resolve the issue;
- request the missing source, project scope, approval, or validation;
- mark the result as uncertain until resolved.

When authorization fails:

- report authorization denial separately from “no matching evidence”;
- do not expose inaccessible data or confirm its existence;
- do not broaden scope as a workaround.

When an external action fails or partially executes, report the observed
result and side effects separately from the intended plan. Do not convert a
failed action into a success-shaped answer.

## 8. Result policy

An External Agent response should contain, as applicable:

```text
Project scope and authorization basis
Question

Evidence / facts
- claim with provenance

Inference
- reasoning derived from the evidence

Unknowns / conflicts
- missing, stale, or inconsistent information

Validation / proposed next step
- completed validation, or clearly marked proposal
```

The final response must preserve the distinction between retrieved
Knowledge and Agent-generated reasoning. It must identify when the answer is
limited by authorization, unavailable context, or unverified external
information.

## Current and future boundary

This policy reference is compatible with the current Authorization Guard's
separation of authorization from `project_id` filtering. It does not imply
that AI-Hub currently owns a Policy Engine, Agent Runtime, Skill Runtime,
Workflow Engine, Hook Runtime, Session Memory, Router, or LLM provider.

The policy remains reusable across ROS2 projects. LIMO may be an example
validation project, but no rule in this document is LIMO-specific.
