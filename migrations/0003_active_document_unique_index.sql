-- Migration 0003: Active Document Partial Unique Index
-- Enforces database-level constraint that at most one active document may exist
-- per (project_id, relative_path) for project-scoped documents,
-- and per (relative_path) for global documents (project_id IS NULL).
-- Archived documents (status = 'archived') remain unrestricted to preserve historical audit trails.

-- Step 1: Pre-check for existing duplicate active documents.
-- Fails safely without modifying or deleting any existing rows.
DO $$
DECLARE
    dup_proj_count integer;
    dup_global_count integer;
BEGIN
    SELECT count(*) INTO dup_proj_count
    FROM (
        SELECT project_id, relative_path
        FROM public.documents
        WHERE status = 'active'
          AND project_id IS NOT NULL
          AND relative_path IS NOT NULL
        GROUP BY project_id, relative_path
        HAVING count(*) > 1
    ) sub;

    IF dup_proj_count > 0 THEN
        RAISE EXCEPTION 'Pre-check failed: % duplicate active document paths detected in project-scoped documents. Manual reconciliation required before applying unique index.', dup_proj_count;
    END IF;

    SELECT count(*) INTO dup_global_count
    FROM (
        SELECT relative_path
        FROM public.documents
        WHERE status = 'active'
          AND project_id IS NULL
          AND relative_path IS NOT NULL
        GROUP BY relative_path
        HAVING count(*) > 1
    ) sub;

    IF dup_global_count > 0 THEN
        RAISE EXCEPTION 'Pre-check failed: % duplicate active document paths detected in global documents. Manual reconciliation required before applying unique index.', dup_global_count;
    END IF;
END $$;

-- Step 2: Create partial unique indexes for single active document constraint
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_project_path
ON public.documents (project_id, relative_path)
WHERE status = 'active'
  AND project_id IS NOT NULL
  AND relative_path IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_global_path
ON public.documents (relative_path)
WHERE status = 'active'
  AND project_id IS NULL
  AND relative_path IS NOT NULL;
