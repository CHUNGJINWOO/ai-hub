-- Fresh database baseline for the four public AI-Hub application tables.
-- Existing Production databases must use fingerprint-verified baseline
-- registration and must not replay this DDL.

CREATE EXTENSION IF NOT EXISTS vector
    WITH SCHEMA public;

CREATE SEQUENCE public.projects_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1
    NO CYCLE;

CREATE SEQUENCE public.memories_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1
    NO CYCLE;

CREATE SEQUENCE public.documents_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1
    NO CYCLE;

CREATE SEQUENCE public.document_chunks_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1
    NO CYCLE;

CREATE TABLE public.projects (
    id bigint NOT NULL DEFAULT nextval('public.projects_id_seq'::regclass),
    name text NOT NULL,
    slug text NOT NULL,
    description text,
    status text NOT NULL DEFAULT 'active'::text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT projects_pkey PRIMARY KEY (id),
    CONSTRAINT projects_slug_key UNIQUE (slug),
    CONSTRAINT projects_status_check
        CHECK (status = ANY (ARRAY['active'::text, 'archived'::text, 'completed'::text]))
);

CREATE TABLE public.memories (
    id bigint NOT NULL DEFAULT nextval('public.memories_id_seq'::regclass),
    content text NOT NULL,
    memory_type text NOT NULL DEFAULT 'fact'::text,
    category text,
    importance integer NOT NULL DEFAULT 3,
    source text,
    embedding vector(384),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    project_id bigint,
    CONSTRAINT memories_pkey PRIMARY KEY (id),
    CONSTRAINT memories_importance_check
        CHECK (importance >= 1 AND importance <= 5),
    CONSTRAINT memories_project_id_fkey
        FOREIGN KEY (project_id)
        REFERENCES public.projects(id)
        ON UPDATE NO ACTION
        ON DELETE SET NULL
);

CREATE TABLE public.documents (
    id bigint NOT NULL DEFAULT nextval('public.documents_id_seq'::regclass),
    project_id bigint,
    title text NOT NULL,
    filename text,
    mime_type text,
    source text,
    description text,
    status text NOT NULL DEFAULT 'active'::text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    file_path text,
    file_size bigint,
    sha256 text,
    document_type text,
    relative_path text,
    file_hash text,
    CONSTRAINT documents_pkey PRIMARY KEY (id),
    CONSTRAINT documents_project_id_fkey
        FOREIGN KEY (project_id)
        REFERENCES public.projects(id)
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT documents_status_check
        CHECK (status = ANY (ARRAY['active'::text, 'archived'::text]))
);

CREATE TABLE public.document_chunks (
    id bigint NOT NULL DEFAULT nextval('public.document_chunks_id_seq'::regclass),
    document_id bigint NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    page_number integer,
    embedding vector(384),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_pkey PRIMARY KEY (id),
    CONSTRAINT document_chunks_document_id_chunk_index_key
        UNIQUE (document_id, chunk_index),
    CONSTRAINT document_chunks_document_id_fkey
        FOREIGN KEY (document_id)
        REFERENCES public.documents(id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE
);

ALTER SEQUENCE public.projects_id_seq
    OWNED BY public.projects.id;

ALTER SEQUENCE public.memories_id_seq
    OWNED BY public.memories.id;

ALTER SEQUENCE public.documents_id_seq
    OWNED BY public.documents.id;

ALTER SEQUENCE public.document_chunks_id_seq
    OWNED BY public.document_chunks.id;

CREATE INDEX idx_projects_slug
    ON public.projects USING btree (slug);

CREATE INDEX idx_memories_project_id
    ON public.memories USING btree (project_id);

CREATE INDEX idx_documents_project_id
    ON public.documents USING btree (project_id);

CREATE INDEX idx_documents_project_relative_path
    ON public.documents USING btree (project_id, relative_path);

CREATE INDEX idx_documents_sha256
    ON public.documents USING btree (sha256);

CREATE INDEX idx_documents_status
    ON public.documents USING btree (status);

CREATE INDEX idx_document_chunks_document_id
    ON public.document_chunks USING btree (document_id);
