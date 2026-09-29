# AI Agent Workflow

This workflow is for AI agents making changes in the AI-Hub repository. Follow
the stages in order and keep the scope within the user's request. For
documentation-only work, investigate and edit documentation without changing
application code.

## 1. Repository entry

- Confirm the current working directory is the intended repository root.
- Confirm the Git remote and current branch identify the intended project.
- Stop before editing if the workspace is not the requested repository.
- Treat external project repositories, deployed services, and indexed data as
  out of scope unless the user explicitly includes them.

## 2. State check

Before making changes, inspect the current branch, concise worktree status, and
recent commit history. Identify existing user changes and preserve them.
Distinguish the repository's local state from deployed production or external
service state.

## 3. Structure investigation

Inspect the relevant repository layout to locate the implementation, tests,
configuration, and documentation involved in the request. Use focused searches
and reads rather than assuming that a file or feature exists.

## 4. Documentation investigation

Read the relevant README, status/architecture documentation, database schema,
development log, troubleshooting notes, and deployment guidance as applicable.
Use documentation to establish intended behavior and historical context, not
as a substitute for verifying implementation.

## 5. Code investigation

Trace the relevant implementation and its callers, shared helpers, tests, and
configuration. For changes crossing API, database, or MCP boundaries, inspect
all affected surfaces and their existing contracts.

If the request is documentation-only, do not modify or extend code to make the
documentation appear implemented. Confirm claims about current behavior from
available source and the project status material.

## 6. Design

Define the smallest change that fully addresses the request and preserves
existing behavior. Separate:

- **Implemented now:** behavior verifiable in the current repository.
- **Historical:** dated changes or past test/deployment results.
- **Planned or deferred:** future design that is not implemented.

Do not imply that a historical external verification proves present-day
availability. If materially different approaches or unresolved behavior choices
affect the implementation, ask the user before proceeding.

## 7. Small change

Make a focused, surgical change using existing project conventions. Avoid
unrelated cleanup, duplicate logic, unnecessary dependencies, or edits outside
the repository and requested scope. Preserve unrelated worktree changes.

## 8. Tests

Run the smallest existing test or validation command that meaningfully covers
the change. Add or adjust focused tests for changed behavior when appropriate.
For documentation-only changes, run documentation-oriented checks such as
`git diff --check`; do not alter application code merely to create a test.

## 9. Verification

Verify the requirement itself, not only that a command exits successfully.
Check relevant build/type/lint results and important unchanged behavior when
applicable. For API/MCP changes, verify the corresponding interface and output
shape where the available test environment permits.

Report checks that could not be run, and distinguish local test results from
production or external verification.

## 10. Documentation

Update only documentation directly related to the change. Record current
behavior as current, historical evidence with its date/context, and future
ideas as planned or deferred. Keep examples free of real credentials and
environment-specific secrets.

## 11. Diff check

Review the final status and complete diff. Confirm that:

- Only intended files changed.
- No unrelated user changes were overwritten or staged.
- Documentation agrees with the implementation and the actual test results.
- No secrets, generated artifacts, or unintended behavior changes are present.
- `git diff --check` passes.

## 12. Commit and push

Commit and push are separate, conditional final actions—not automatic workflow
steps. Do them only when the user explicitly requests them. Before either
action, confirm the intended branch, inspect exactly what will be included, and
ensure no unrelated changes or secrets are included. Never amend or rewrite
existing history unless specifically authorized.
