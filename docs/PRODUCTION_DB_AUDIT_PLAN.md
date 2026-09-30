# Production PostgreSQL Audit Plan

## 1. Purpose

This plan defines a safe, read-only audit required before selecting a
migration baseline. It is intended to:

- establish Production schema ownership and catalog facts;
- collect the schema and data-integrity evidence needed for a baseline;
- identify blockers before Workspace and authorization migrations.

The plan does not connect to Production, apply SQL, create migration metadata,
or change repository schema ownership.

## 2. Safety rules

An operator running the audit must:

- use a dedicated `READ ONLY` transaction;
- run catalog queries and aggregate counts only;
- never execute schema or data changes;
- never run migrations during the audit;
- never print passwords, tokens, connection secrets, or row content;
- use counts and metadata rather than selecting application data;
- roll back the audit transaction when all queries finish.

The SQL specification is [production_db_audit.sql](sql/production_db_audit.sql).
This repository does not execute that file automatically.

## 3. Audit phases

### Phase 1: Database identity

Collect:

- `current_database()`
- `current_user`
- `current_schema()`
- PostgreSQL `version()`

### Phase 2: Schema and table inventory

Use `pg_namespace`, `pg_class`, and
`information_schema.tables` to record:

- schema name and owner;
- table name, owner, and relation kind;
- application tables and possible workspace, identity, and migration tables.

The audit must explicitly check:

`projects`, `memories`, `documents`, `document_chunks`, `workspaces`,
`users`, `memberships`, `workspace_members`, `project_members`, `principals`,
`identities`, `service_accounts`, `tenants`, `alembic_version`,
`schema_migrations`, `migrations`, `migration`, and
`flyway_schema_history`.

### Phase 3: Column inventory

For every core and authorization-related table, collect:

- schema and table;
- column name and ordinal position;
- PostgreSQL data type and UDT name;
- nullability;
- default expression.

Pay special attention to vector, bigint, timestamp with time zone, JSON/JSONB,
enum, identity, and sequence-backed defaults.

### Phase 4: Constraints

Record primary key, unique, foreign key, check, and exclusion constraints.
For foreign keys, record:

- constraint name;
- source table and column;
- referenced table and column;
- delete action;
- update action.

Explicitly verify or report the absence of:

- `memories.project_id -> projects.id`;
- `documents.project_id -> projects.id`;
- `document_chunks.document_id -> documents.id`.

### Phase 5: Indexes

Record every index on the core tables, including:

- name and table;
- uniqueness and primary status;
- indexed columns and definition;
- partial predicate, if present.

Explicitly inspect `idx_projects_slug`, `idx_memories_project_id`,
project/document indexes, `(document_id, chunk_index)`, and vector indexes.

### Phase 6: Extensions

Record every installed extension and version, with particular attention to
`vector`/pgvector and any other application dependency.

### Phase 7: Migration metadata

For each candidate migration table, record existence, columns, row count, and
current revision/version. Candidate names are:

- `alembic_version`
- `schema_migrations`
- `migrations`
- `migration`
- `flyway_schema_history`

An empty table and an absent table are different findings.

### Phase 8: Data integrity

Collect counts only for:

- `projects`;
- `memories`;
- `documents`;
- `document_chunks`;
- any discovered Workspace/identity tables.

Where the relevant tables and columns exist, collect:

- `memories.project_id IS NULL`;
- orphan `memories.project_id`;
- `documents.project_id IS NULL`;
- orphan `documents.project_id`;
- orphan `document_chunks.document_id`.

If Workspace relationships already exist, also count null or orphan project
workspace references and membership/project relationship references. Do not
return application row content.

## 4. Repository versus Production comparison

Compare the audit output with:

- `api/app/core/db.py`;
- `docs/DATABASE_SCHEMA.md`;
- `docs/MIGRATION_ARCHITECTURE.md`;
- application query contracts for documents and chunks.

The comparison matrix must cover:

| Object | Repository evidence | Production evidence | Classification |
| --- | --- | --- | --- |
| `projects` | Partial runtime DDL | Catalog audit | Equal, different, missing, or incomplete |
| `memories` | Partial runtime DDL | Catalog audit | Equal, different, missing, or incomplete |
| `documents` | Query contract, no full DDL | Catalog audit | Equal, different, missing, or incomplete |
| `document_chunks` | Query contract, no full DDL | Catalog audit | Equal, different, missing, or incomplete |
| Extensions | Compose image/dependencies | Extension catalog | Equal, different, missing, or incomplete |
| Indexes | Partial runtime DDL | Index catalog | Equal, different, missing, or incomplete |
| Constraints | Partial runtime DDL | Constraint catalog | Equal, different, missing, or incomplete |
| Migration metadata | No runner or applied table | Candidate table audit | Present, absent, or incomplete |
| Workspace/identity objects | No schema implementation | Table/column/FK audit | Present, absent, or incomplete |

Application query usage is not proof of a complete table definition.

## 5. Baseline decision criteria

Do not select a baseline before the catalog and integrity audit is complete.
Evaluate these alternatives separately:

1. **Existing Production baseline**: declare the observed Production schema as
   the starting revision and apply later revisions without recreating existing
   data.
2. **Fresh-install baseline**: provide complete reproducible definitions for a
   new database, including all tables, constraints, indexes, and extensions.
3. **Dual baseline**: document separate procedures when an existing Production
   baseline cannot also safely initialize a new database.

The decision must account for complete `documents` and `document_chunks` DDL,
actual constraints and indexes, extensions, migration metadata, schema drift,
existing data, NULL/orphan counts, and Workspace backfill prerequisites.

Until those facts are available, the baseline remains undecided.

## 6. Workspace migration blockers

Workspace migration design remains blocked until the following are known:

- complete Production definitions for `documents` and `document_chunks`;
- actual foreign keys, indexes, and constraints;
- existing migration metadata and revision state;
- project, memory, document, and chunk counts;
- NULL and orphan integrity counts;
- existing Workspace, identity, membership, and project relationships;
- a policy for resources whose `project_id` is null;
- whether chunks inherit project scope through documents.

This plan does not implement Workspace, User, Membership, authorization, or
backfill behavior.

## 7. Repository access findings

The current repository defines PostgreSQL as a Docker-internal service at
`postgres:5432`; it does not publish the database port on the host. The API
and MCP health endpoints do not expose catalog metadata. The backup service
uses `docker compose exec postgres` for `pg_dump`, but no separate read-only
audit command, SSH host alias, Docker context, catalog export, or schema-only
dump is defined in the repository.

The audit operator must obtain an approved read-only Production access path
before running the SQL specification. This plan does not create a tunnel,
change permissions, or infer an external endpoint.
