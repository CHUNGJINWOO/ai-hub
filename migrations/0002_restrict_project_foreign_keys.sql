-- Migration 0002: Harden foreign keys from ON DELETE SET NULL to ON DELETE RESTRICT
-- Prevents cascade/silent promotion of owned documents and memories to global resources.

ALTER TABLE public.memories
    DROP CONSTRAINT IF EXISTS memories_project_id_fkey;

ALTER TABLE public.memories
    ADD CONSTRAINT memories_project_id_fkey
    FOREIGN KEY (project_id)
    REFERENCES public.projects(id)
    ON UPDATE NO ACTION
    ON DELETE RESTRICT;

ALTER TABLE public.documents
    DROP CONSTRAINT IF EXISTS documents_project_id_fkey;

ALTER TABLE public.documents
    ADD CONSTRAINT documents_project_id_fkey
    FOREIGN KEY (project_id)
    REFERENCES public.projects(id)
    ON UPDATE NO ACTION
    ON DELETE RESTRICT;

