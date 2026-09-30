"""Read-only PostgreSQL 17 catalog queries used by the Phase B adapter."""

from __future__ import annotations


CATALOG_QUERIES: dict[str, str] = {
    "identity": """
        SELECT current_database() AS database_name,
               current_schema() AS schema_name
    """,
    "extensions": """
        SELECT n.nspname AS schema,
               e.extname AS name,
               e.extversion AS version
          FROM pg_extension AS e
          JOIN pg_namespace AS n ON n.oid = e.extnamespace
         WHERE e.extname IN ('vector')
         ORDER BY n.nspname, e.extname
    """,
    "tables": """
        SELECT n.nspname AS schema,
               c.relname AS name,
               c.relkind AS relation_kind
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
         ORDER BY n.nspname, c.relname
    """,
    "columns": """
        SELECT n.nspname AS schema,
               c.relname AS table,
               a.attname AS name,
               a.attnum AS ordinal_position,
               format_type(a.atttypid, a.atttypmod) AS type,
               (a.attnotnull = false) AS nullable,
               pg_get_expr(d.adbin, d.adrelid) AS default
          FROM pg_attribute AS a
          JOIN pg_class AS c ON c.oid = a.attrelid
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
          LEFT JOIN pg_attrdef AS d
            ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND a.attnum > 0
           AND NOT a.attisdropped
         ORDER BY n.nspname, c.relname, a.attnum
    """,
    "sequences": """
        SELECT n.nspname AS schema,
               c.relname AS name,
               format_type(s.seqtypid, NULL) AS data_type,
               s.seqstart AS start,
               s.seqmin AS minimum,
               s.seqmax AS maximum,
               s.seqincrement AS increment,
               s.seqcycle AS cycle,
               s.seqcache AS cache,
               owner_ns.nspname AS owner_schema,
               owner_table.relname AS owner_table,
               owner_attr.attname AS owner_column
          FROM pg_sequence AS s
          JOIN pg_class AS c ON c.oid = s.seqrelid
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
          LEFT JOIN pg_depend AS dep
            ON dep.objid = c.oid
           AND dep.deptype = 'a'
           AND dep.classid = 'pg_class'::regclass
          LEFT JOIN pg_class AS owner_table ON owner_table.oid = dep.refobjid
          LEFT JOIN pg_namespace AS owner_ns ON owner_ns.oid = owner_table.relnamespace
          LEFT JOIN pg_attribute AS owner_attr
            ON owner_attr.attrelid = dep.refobjid
           AND owner_attr.attnum = dep.refobjsubid
         WHERE n.nspname = 'public'
         ORDER BY n.nspname, c.relname
    """,
    "constraints": """
        SELECT n.nspname AS schema,
               c.relname AS table,
               con.conname AS name,
               CASE con.contype
                   WHEN 'p' THEN 'PRIMARY KEY'
                   WHEN 'u' THEN 'UNIQUE'
                   WHEN 'c' THEN 'CHECK'
                   WHEN 'f' THEN 'FOREIGN KEY'
               END AS constraint_type,
               (
                   SELECT array_agg(att.attname ORDER BY key.ordinality)
                     FROM unnest(con.conkey) WITH ORDINALITY AS key(attnum, ordinality)
                     JOIN pg_attribute AS att
                       ON att.attrelid = con.conrelid
                      AND att.attnum = key.attnum
               ) AS columns,
               ref_ns.nspname AS referenced_schema,
               ref_table.relname AS referenced_table,
               (
                   SELECT array_agg(att.attname ORDER BY key.ordinality)
                     FROM unnest(con.confkey) WITH ORDINALITY AS key(attnum, ordinality)
                     JOIN pg_attribute AS att
                       ON att.attrelid = con.confrelid
                      AND att.attnum = key.attnum
               ) AS referenced_columns,
               CASE con.confupdtype
                   WHEN 'a' THEN 'NO ACTION'
                   WHEN 'r' THEN 'RESTRICT'
                   WHEN 'c' THEN 'CASCADE'
                   WHEN 'n' THEN 'SET NULL'
                   WHEN 'd' THEN 'SET DEFAULT'
               END AS on_update,
               CASE con.confdeltype
                   WHEN 'a' THEN 'NO ACTION'
                   WHEN 'r' THEN 'RESTRICT'
                   WHEN 'c' THEN 'CASCADE'
                   WHEN 'n' THEN 'SET NULL'
                   WHEN 'd' THEN 'SET DEFAULT'
               END AS on_delete,
               CASE WHEN con.contype = 'c'
                    THEN pg_get_constraintdef(con.oid, true)
               END AS check_expression
          FROM pg_constraint AS con
          JOIN pg_class AS c ON c.oid = con.conrelid
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
          LEFT JOIN pg_class AS ref_table ON ref_table.oid = con.confrelid
          LEFT JOIN pg_namespace AS ref_ns ON ref_ns.oid = ref_table.relnamespace
         WHERE n.nspname = 'public'
           AND con.contype IN ('p', 'u', 'c', 'f')
         ORDER BY n.nspname, c.relname, con.contype, con.conname
    """,
    "indexes": """
        SELECT n.nspname AS schema,
               table_class.relname AS table,
               index_class.relname AS name,
               am.amname AS access_method,
               index_info.indisunique AS unique,
               index_info.indisprimary AS primary,
               index_info.indisexclusion AS exclusion,
               pg_get_indexdef(index_class.oid) AS definition,
               pg_get_expr(index_info.indpred, index_info.indrelid) AS predicate,
               pg_get_expr(index_info.indexprs, index_info.indrelid) AS expressions
          FROM pg_index AS index_info
          JOIN pg_class AS index_class ON index_class.oid = index_info.indexrelid
          JOIN pg_class AS table_class ON table_class.oid = index_info.indrelid
          JOIN pg_namespace AS n ON n.oid = table_class.relnamespace
          JOIN pg_am AS am ON am.oid = index_class.relam
         WHERE n.nspname = 'public'
         ORDER BY n.nspname, table_class.relname, index_class.relname
    """,
}

READ_ONLY_BEGIN = "BEGIN; SET TRANSACTION READ ONLY;"
READ_ONLY_ROLLBACK = "ROLLBACK;"
