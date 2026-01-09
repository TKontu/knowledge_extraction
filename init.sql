-- TechFacts Scraper Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

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
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    companies TEXT[] NOT NULL,
    categories TEXT[],
    fact_ids UUID[],
    format TEXT DEFAULT 'md',
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_reports_type ON reports(type);
CREATE INDEX idx_reports_generated_at ON reports(generated_at);

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
