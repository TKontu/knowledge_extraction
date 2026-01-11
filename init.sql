-- Scristill Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ===================
-- GENERALIZED SCHEMA
-- ===================
-- New tables for project-based multi-domain extraction
-- See: docs/TODO_generalization.md

-- Projects Table
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,

    -- Configuration stored as JSONB
    source_config JSONB NOT NULL DEFAULT '{"type": "web", "group_by": "company"}',
    extraction_schema JSONB NOT NULL,
    entity_types JSONB NOT NULL DEFAULT '[]',
    prompt_templates JSONB NOT NULL DEFAULT '{}',

    -- Settings
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_projects_name ON projects(name);
CREATE INDEX idx_projects_active ON projects(is_active);

-- Sources Table (generalized from pages)
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,

    source_type TEXT NOT NULL DEFAULT 'web',  -- web, pdf, api, text
    uri TEXT NOT NULL,
    source_group TEXT NOT NULL,  -- Replaces hardcoded "company"

    title TEXT,
    content TEXT,  -- Processed content (markdown)
    raw_content TEXT,  -- Original content

    metadata JSONB DEFAULT '{}',
    outbound_links JSONB DEFAULT '[]',

    status TEXT DEFAULT 'pending',
    fetched_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, uri)
);

CREATE INDEX idx_sources_project ON sources(project_id);
CREATE INDEX idx_sources_group ON sources(source_group);
CREATE INDEX idx_sources_status ON sources(status);

-- Extractions Table (generalized from facts)
CREATE TABLE extractions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,

    -- Dynamic data validated against project schema
    data JSONB NOT NULL,

    -- Denormalized for indexing/queries
    extraction_type TEXT NOT NULL,
    source_group TEXT NOT NULL,
    confidence FLOAT,

    -- Provenance
    profile_used TEXT,
    chunk_index INT,
    chunk_context JSONB,

    -- Vector reference
    embedding_id TEXT,

    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_extractions_project ON extractions(project_id);
CREATE INDEX idx_extractions_source ON extractions(source_id);
CREATE INDEX idx_extractions_group ON extractions(source_group);
CREATE INDEX idx_extractions_type ON extractions(extraction_type);
CREATE INDEX idx_extractions_confidence ON extractions(confidence);
CREATE INDEX idx_extractions_data ON extractions USING GIN (data);

-- Entities Table (project-scoped)
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,

    entity_type TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    attributes JSONB DEFAULT '{}',

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, source_group, entity_type, normalized_value)
);

CREATE INDEX idx_entities_project ON entities(project_id);
CREATE INDEX idx_entities_group ON entities(source_group);
CREATE INDEX idx_entities_type ON entities(entity_type);

-- Extraction-Entity Junction Table
CREATE TABLE extraction_entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    extraction_id UUID REFERENCES extractions(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'mention',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(extraction_id, entity_id, role)
);

-- ===================
-- LEGACY SCHEMA
-- ===================
-- These tables will be migrated to the generalized schema
-- See: docs/TODO_migrations.md

-- ===================
-- Pages Table
-- ===================
CREATE TABLE pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT,
    markdown_content TEXT,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status TEXT DEFAULT 'completed',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_pages_company ON pages(company);
CREATE INDEX idx_pages_domain ON pages(domain);
CREATE INDEX idx_pages_status ON pages(status);
CREATE INDEX idx_pages_scraped_at ON pages(scraped_at);

-- ===================
-- Facts Table
-- ===================
CREATE TABLE facts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID REFERENCES pages(id) ON DELETE CASCADE,
    fact_text TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    profile_used TEXT NOT NULL,
    embedding_id TEXT,
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_facts_page_id ON facts(page_id);
CREATE INDEX idx_facts_category ON facts(category);
CREATE INDEX idx_facts_profile ON facts(profile_used);
CREATE INDEX idx_facts_confidence ON facts(confidence);

-- ===================
-- Jobs Table
-- ===================
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    priority INT DEFAULT 0,
    payload JSONB NOT NULL,
    result JSONB,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_type ON jobs(type);
CREATE INDEX idx_jobs_project ON jobs(project_id);
CREATE INDEX idx_jobs_created_at ON jobs(created_at);

-- ===================
-- Profiles Table
-- ===================
CREATE TABLE profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT UNIQUE NOT NULL,
    categories TEXT[] NOT NULL,
    prompt_focus TEXT NOT NULL,
    depth TEXT NOT NULL,
    custom_instructions TEXT,
    is_builtin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ===================
-- Reports Table
-- ===================
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT,
    content TEXT,
    source_groups JSONB DEFAULT '[]',  -- Replaces companies (now JSONB)
    categories JSONB DEFAULT '[]',
    extraction_ids JSONB DEFAULT '[]',  -- Replaces fact_ids (now JSONB)
    format TEXT DEFAULT 'md',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_reports_type ON reports(type);
CREATE INDEX idx_reports_created_at ON reports(created_at);

-- ===================
-- Rate Limiting Table
-- ===================
CREATE TABLE rate_limits (
    domain TEXT PRIMARY KEY,
    request_count INT DEFAULT 0,
    last_request TIMESTAMP WITH TIME ZONE,
    daily_count INT DEFAULT 0,
    daily_reset_at DATE DEFAULT CURRENT_DATE
);

-- ===================
-- Insert Built-in Profiles
-- ===================
INSERT INTO profiles (name, categories, prompt_focus, depth, is_builtin) VALUES
(
    'technical_specs',
    ARRAY['specs', 'hardware', 'requirements', 'compatibility', 'performance'],
    'Hardware specifications, system requirements, supported platforms, performance metrics, compatibility information',
    'detailed',
    TRUE
),
(
    'api_docs',
    ARRAY['endpoints', 'authentication', 'rate_limits', 'sdks', 'versioning'],
    'API endpoints, authentication methods, rate limits, SDK availability, API versioning, request/response formats',
    'detailed',
    TRUE
),
(
    'security',
    ARRAY['certifications', 'compliance', 'encryption', 'audit', 'access_control'],
    'Security certifications (SOC2, ISO27001, etc), compliance standards, encryption methods, audit capabilities, access control features',
    'comprehensive',
    TRUE
),
(
    'pricing',
    ARRAY['pricing', 'tiers', 'limits', 'features'],
    'Pricing tiers, feature inclusions per tier, usage limits, enterprise options, free tier details',
    'detailed',
    TRUE
),
(
    'general',
    ARRAY['general', 'features', 'technical', 'integration'],
    'General technical facts about the product, features, integrations, and capabilities',
    'summary',
    TRUE
);
