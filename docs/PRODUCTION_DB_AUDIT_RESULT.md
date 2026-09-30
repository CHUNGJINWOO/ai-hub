# Production PostgreSQL Audit Result

## 1. Audit metadata

- Audit date: 2026-09-30 (UTC)
- Audit execution window: approximately 10:34-10:40 UTC
- Database: `aihub`
- Database user: `aihub`
- PostgreSQL version: `17.11`
- Audit scope: PostgreSQL catalog metadata and aggregate counts; public
  application tables were checked separately from Keycloak objects.
- Execution mode: a transaction with `SET TRANSACTION READ ONLY`
- Completion: `ROLLBACK` was observed at the end of the full audit and the
  separate foreign-key verification.
- Repository audit specification: [`docs/sql/production_db_audit.sql`](sql/production_db_audit.sql)

The full audit and the focused foreign-key query were executed by the
operator from the Production server terminal. This result document records
those supplied execution results; the current Agent session did not have
Docker socket access and did not rerun the audit.

## 2. Core table row counts

| Table | Rows |
| --- | ---: |
| `public.projects` | 3 |
| `public.memories` | 5 |
| `public.documents` | 55 |
| `public.document_chunks` | 1,041 |

## 3. Extensions

| Extension | Version |
| --- | --- |
| `plpgsql` | `1.0` |
| `vector` | `0.8.6` |

The `vector` extension is the installed pgvector version reported by the
audit.

## 4. Foreign keys

The focused query was executed in a separate read-only transaction with
`BEGIN`, `SET TRANSACTION READ ONLY`, the FK catalog query, and `ROLLBACK`.
It returned three public-schema foreign keys for the requested core source
tables:

| Source | Target | Constraint | ON UPDATE | ON DELETE |
| --- | --- | --- | --- | --- |
| `public.document_chunks.document_id` | `public.documents.id` | `document_chunks_document_id_fkey` | `NO ACTION` | `CASCADE` |
| `public.documents.project_id` | `public.projects.id` | `documents_project_id_fkey` | `NO ACTION` | `SET NULL` |
| `public.memories.project_id` | `public.projects.id` | `memories_project_id_fkey` | `NO ACTION` | `SET NULL` |

The focused FK query completed without SQL errors and returned `(3 rows)`.

## 5. Referential integrity and NULL checks

The full audit reported:

| Check | Result |
| --- | ---: |
| `memories.project_id` NULL count | 0 |
| Orphan `memories.project_id` references | 0 |
| `documents.project_id` NULL count | 0 |
| Orphan `documents.project_id` references | 0 |
| Orphan `document_chunks.document_id` references | 0 |

These results show no orphan references for the three audited public
relationships in the observed data.

## 6. Indexes and schema inventory

The full audit produced the public core index results successfully. The
available execution summary confirms that the expected public core index
output was present, but does not preserve every index definition in this
document.

The audit also checked schema and table metadata, including the distinction
between the public application schema and the separate Keycloak schema.
Keycloak tables are infrastructure tables and are not treated as AI-Hub
application tables.

## 7. Migration metadata

The migration metadata candidate query returned `0 rows`. This is evidence
that no candidate migration metadata rows were found by the audit query. The
available compact output does not distinguish, for every candidate name,
between an absent table and an existing empty table.

No migration was executed and no schema or data change was performed.

## 8. Repository comparison

The current repository documentation records the following comparison:

- `projects` and `memories` are represented by runtime bootstrap DDL in
  [`api/app/core/db.py`](../api/app/core/db.py).
- `documents` and `document_chunks` are used by the application and exist in
  Production, but the repository does not contain complete `CREATE TABLE` DDL
  for them.
- The observed public foreign keys and pgvector version are consistent with
  the documented Production schema in
  [`docs/DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md).
- The repository does not yet provide a complete, reproducible migration
  baseline for the entire observed Production schema.
- No Workspace, identity, or authorization migration was applied during this
  audit.

## 9. Findings

### Confirmed

- Production is PostgreSQL 17.11.
- The audit transaction was read-only and ended with `ROLLBACK`.
- The four public core tables contain the reported rows.
- pgvector `0.8.6` is installed.
- The three requested public core foreign keys exist with the reported
  `ON UPDATE` and `ON DELETE` actions.
- The audited project/document/chunk reference checks reported no orphans.
- The migration metadata candidate query returned no rows.

### Readiness concern

Before introducing Workspace, identity, or authorization migrations, the
repository should establish complete, versioned definitions for the existing
Production schema, especially `documents` and `document_chunks`. The observed
data is internally consistent for the audited references, but the available
repository DDL is not sufficient by itself to recreate all existing tables,
constraints, and indexes.

## 10. Limitations

The compact audit output supplied for this document did not preserve:

- the complete schema/table inventory;
- every column type, nullability, and default;
- every constraint and index definition;
- the complete Keycloak table inventory;
- the exact existence-versus-empty state of each migration metadata
  candidate;
- Workspace/identity-specific catalog objects beyond the stated schema
  separation.

Those items are therefore not inferred or filled in here. A future migration
baseline must use the complete catalog output, not this compact summary alone.
