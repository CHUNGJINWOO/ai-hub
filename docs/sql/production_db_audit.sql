BEGIN;
SET TRANSACTION READ ONLY;

-- Phase 1: identity
SELECT
    current_database() AS database_name,
    current_user AS database_user,
    current_schema() AS current_schema,
    version() AS server_version;

-- Phase 2: schemas and tables
SELECT
    namespace.nspname AS schema_name,
    pg_get_userbyid(namespace.nspowner) AS schema_owner
FROM pg_namespace AS namespace
WHERE namespace.nspname NOT LIKE 'pg_%'
  AND namespace.nspname <> 'information_schema'
ORDER BY namespace.nspname;

SELECT
    tables.table_schema,
    tables.table_name,
    tables.table_type,
    pg_get_userbyid(class.relowner) AS table_owner
FROM information_schema.tables AS tables
JOIN pg_class AS class
  ON class.relname = tables.table_name
JOIN pg_namespace AS namespace
  ON namespace.oid = class.relnamespace
 AND namespace.nspname = tables.table_schema
WHERE tables.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY tables.table_schema, tables.table_name;

-- Phase 3: columns
SELECT
    columns.table_schema,
    columns.table_name,
    columns.ordinal_position,
    columns.column_name,
    columns.data_type,
    columns.udt_name,
    columns.is_nullable,
    columns.column_default
FROM information_schema.columns AS columns
WHERE columns.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY
    columns.table_schema,
    columns.table_name,
    columns.ordinal_position;

-- Phase 4: constraints
SELECT
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name,
    constraints.constraint_type
FROM information_schema.table_constraints AS constraints
WHERE constraints.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name;

SELECT
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name,
    constraints.constraint_type,
    key_columns.column_name,
    key_columns.ordinal_position
FROM information_schema.table_constraints AS constraints
JOIN information_schema.key_column_usage AS key_columns
  ON key_columns.constraint_schema = constraints.constraint_schema
 AND key_columns.constraint_name = constraints.constraint_name
 AND key_columns.table_name = constraints.table_name
WHERE constraints.constraint_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name,
    key_columns.ordinal_position;

SELECT
    source_ns.nspname AS source_schema,
    source_table.relname AS source_table,
    source_column.attname AS source_column,
    target_ns.nspname AS target_schema,
    target_table.relname AS target_table,
    target_column.attname AS target_column,
    constraint_name.conname AS constraint_name,
    CASE constraint_name.confupdtype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
    END AS on_update,
    CASE constraint_name.confdeltype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
    END AS on_delete
FROM pg_constraint AS constraint_name
JOIN pg_class AS source_table
  ON source_table.oid = constraint_name.conrelid
JOIN pg_namespace AS source_ns
  ON source_ns.oid = source_table.relnamespace
JOIN pg_class AS target_table
  ON target_table.oid = constraint_name.confrelid
JOIN pg_namespace AS target_ns
  ON target_ns.oid = target_table.relnamespace
JOIN LATERAL generate_subscripts(constraint_name.conkey, 1)
  AS key_position(position)
  ON true
JOIN pg_attribute AS source_column
  ON source_column.attrelid = constraint_name.conrelid
 AND source_column.attnum =
     constraint_name.conkey[key_position.position]
JOIN pg_attribute AS target_column
  ON target_column.attrelid = constraint_name.confrelid
 AND target_column.attnum =
     constraint_name.confkey[key_position.position]
WHERE constraint_name.contype = 'f'
ORDER BY
    source_ns.nspname,
    source_table.relname,
    constraint_name.conname,
    key_position.position;

SELECT
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name,
    checks.check_clause
FROM information_schema.table_constraints AS constraints
JOIN information_schema.check_constraints AS checks
  ON checks.constraint_schema = constraints.constraint_schema
 AND checks.constraint_name = constraints.constraint_name
WHERE constraints.constraint_type = 'CHECK'
ORDER BY
    constraints.constraint_schema,
    constraints.table_name,
    constraints.constraint_name;

-- Phase 5: indexes
SELECT
    namespace.nspname AS schema_name,
    class.relname AS table_name,
    index_class.relname AS index_name,
    index_info.indisunique AS is_unique,
    index_info.indisprimary AS is_primary,
    pg_get_indexdef(index_info.indexrelid) AS index_definition,
    pg_get_expr(index_info.indpred, index_info.indrelid) AS partial_predicate
FROM pg_index AS index_info
JOIN pg_class AS class
  ON class.oid = index_info.indrelid
JOIN pg_class AS index_class
  ON index_class.oid = index_info.indexrelid
JOIN pg_namespace AS namespace
  ON namespace.oid = class.relnamespace
WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY namespace.nspname, class.relname, index_class.relname;

-- Phase 6: extensions
SELECT
    extension.extname,
    extension.extversion,
    namespace.nspname AS extension_schema
FROM pg_extension AS extension
JOIN pg_namespace AS namespace
  ON namespace.oid = extension.extnamespace
ORDER BY extension.extname;

-- Phase 7: migration metadata candidates
SELECT
    tables.table_schema,
    tables.table_name,
    tables.table_type
FROM information_schema.tables AS tables
WHERE lower(tables.table_name) IN (
    'alembic_version',
    'schema_migrations',
    'migrations',
    'migration',
    'flyway_schema_history'
)
ORDER BY tables.table_schema, tables.table_name;

-- Phase 7b: metadata details, only for tables that exist
DO $audit$
DECLARE
    candidate record;
    column_list text;
    version_column text;
    version_values text;
    row_count bigint;
BEGIN
    FOR candidate IN
        SELECT tables.table_schema, tables.table_name
        FROM information_schema.tables AS tables
        WHERE lower(tables.table_name) IN (
            'alembic_version',
            'schema_migrations',
            'migrations',
            'migration',
            'flyway_schema_history'
        )
    LOOP
        SELECT string_agg(columns.column_name, ', ' ORDER BY columns.ordinal_position)
        INTO column_list
        FROM information_schema.columns AS columns
        WHERE columns.table_schema = candidate.table_schema
          AND columns.table_name = candidate.table_name;

        SELECT columns.column_name
        INTO version_column
        FROM information_schema.columns AS columns
        WHERE columns.table_schema = candidate.table_schema
          AND columns.table_name = candidate.table_name
          AND lower(columns.column_name) IN ('version', 'revision')
        ORDER BY CASE lower(columns.column_name)
            WHEN 'version' THEN 1
            WHEN 'revision' THEN 2
        END
        LIMIT 1;

        EXECUTE format(
            'SELECT count(*) FROM %I.%I',
            candidate.table_schema,
            candidate.table_name
        )
        INTO row_count;

        IF version_column IS NULL THEN
            version_values := 'NOT FOUND';
        ELSE
            EXECUTE format(
                'SELECT string_agg(%I::text, '', '' ORDER BY %I::text)
                 FROM %I.%I',
                version_column,
                version_column,
                candidate.table_schema,
                candidate.table_name
            )
            INTO version_values;
        END IF;

        RAISE NOTICE 'migration metadata table %.% columns=[%] rows=%',
            candidate.table_schema,
            candidate.table_name,
            column_list,
            row_count;
        RAISE NOTICE 'migration metadata table %.% revision_column=% revisions=%',
            candidate.table_schema,
            candidate.table_name,
            coalesce(version_column, 'NOT FOUND'),
            coalesce(version_values, 'EMPTY');
    END LOOP;
END
$audit$;

-- Phase 8: aggregate row counts and integrity checks.
-- Missing tables or columns are reported as SKIPPED instead of aborting
-- the catalog audit.
DO $audit$
DECLARE
    relation_name text;
    row_count bigint;
    null_count bigint;
    orphan_count bigint;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'projects',
        'memories',
        'documents',
        'document_chunks'
    ]
    LOOP
        IF to_regclass(format('public.%s', relation_name)) IS NULL THEN
            RAISE NOTICE '%: SKIPPED / NOT PRESENT', relation_name;
        ELSE
            EXECUTE format(
                'SELECT count(*) FROM %I.%I',
                'public',
                relation_name
            )
            INTO row_count;
            RAISE NOTICE '%: rows=%', relation_name, row_count;
        END IF;
    END LOOP;

    IF to_regclass('public.memories') IS NULL
       OR to_regclass('public.projects') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_name = 'memories'
             AND column_name = 'project_id'
       )
    THEN
        RAISE NOTICE 'memories project integrity: SKIPPED / NOT PRESENT';
    ELSE
        EXECUTE
            'SELECT count(*) FROM public.memories WHERE project_id IS NULL'
        INTO null_count;
        EXECUTE
            'SELECT count(*) FROM public.memories AS child
             LEFT JOIN public.projects AS parent ON parent.id = child.project_id
             WHERE child.project_id IS NOT NULL AND parent.id IS NULL'
        INTO orphan_count;
        RAISE NOTICE 'memories project_id: nulls=% orphans=%',
            null_count,
            orphan_count;
    END IF;

    IF to_regclass('public.documents') IS NULL
       OR to_regclass('public.projects') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_name = 'documents'
             AND column_name = 'project_id'
       )
    THEN
        RAISE NOTICE 'documents project integrity: SKIPPED / NOT PRESENT';
    ELSE
        EXECUTE
            'SELECT count(*) FROM public.documents WHERE project_id IS NULL'
        INTO null_count;
        EXECUTE
            'SELECT count(*) FROM public.documents AS child
             LEFT JOIN public.projects AS parent ON parent.id = child.project_id
             WHERE child.project_id IS NOT NULL AND parent.id IS NULL'
        INTO orphan_count;
        RAISE NOTICE 'documents project_id: nulls=% orphans=%',
            null_count,
            orphan_count;
    END IF;

    IF to_regclass('public.document_chunks') IS NULL
       OR to_regclass('public.documents') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_name = 'document_chunks'
             AND column_name = 'document_id'
       )
    THEN
        RAISE NOTICE 'document_chunks document integrity: SKIPPED / NOT PRESENT';
    ELSE
        EXECUTE
            'SELECT count(*) FROM public.document_chunks AS child
             LEFT JOIN public.documents AS parent ON parent.id = child.document_id
             WHERE child.document_id IS NOT NULL AND parent.id IS NULL'
        INTO orphan_count;
        RAISE NOTICE 'document_chunks document_id: orphans=%', orphan_count;
    END IF;
END
$audit$;

ROLLBACK;
