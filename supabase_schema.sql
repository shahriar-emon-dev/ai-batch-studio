-- Supabase SQL Schema for AI Batch Studio
-- Run this in your Supabase SQL Editor

-- 1. Create custom enums
CREATE TYPE project_status AS ENUM ('PENDING', 'PROCESSING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED');
CREATE TYPE scene_status AS ENUM ('PENDING', 'PROCESSING', 'VISUAL_GENERATING', 'VISUAL_COMPLETED', 'VOICE_GENERATING', 'VOICE_COMPLETED', 'MERGING', 'COMPLETED', 'FAILED', 'SKIPPED');
CREATE TYPE generation_mode AS ENUM ('IMAGE_VOICE', 'VIDEO_VOICE', 'IMAGE_ONLY');
CREATE TYPE attempt_type AS ENUM ('VISUAL', 'VOICE', 'MERGE', 'ENHANCEMENT');
CREATE TYPE attempt_status AS ENUM ('STARTED', 'SUCCEEDED', 'FAILED');
CREATE TYPE log_level AS ENUM ('INFO', 'WARNING', 'ERROR', 'SUCCESS');

-- 2. Create tables
CREATE TABLE api_profiles (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL DEFAULT 'google',
    profile_name VARCHAR(255) NOT NULL,
    encrypted_credentials TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_tested TIMESTAMPTZ,
    test_result TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_settings (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    default_generation_mode generation_mode DEFAULT 'IMAGE_VOICE',
    default_aspect_ratio VARCHAR(20) DEFAULT '16:9',
    default_voice VARCHAR(100),
    default_language VARCHAR(20) DEFAULT 'en-US',
    default_concurrency INTEGER DEFAULT 1,
    default_retry_count INTEGER DEFAULT 3,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL DEFAULT 'Untitled Project',
    description TEXT,
    mode generation_mode NOT NULL DEFAULT 'IMAGE_VOICE',
    status project_status NOT NULL DEFAULT 'PENDING',
    total_scenes INTEGER NOT NULL DEFAULT 0,
    completed_scenes INTEGER NOT NULL DEFAULT 0,
    failed_scenes INTEGER NOT NULL DEFAULT 0,
    skipped_scenes INTEGER NOT NULL DEFAULT 0,
    settings_json JSONB,
    api_profile_id INTEGER REFERENCES api_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE scenes (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scene_number VARCHAR(50) NOT NULL,
    visual_prompt TEXT NOT NULL,
    voiceover_script TEXT DEFAULT '',
    aspect_ratio VARCHAR(20) DEFAULT '16:9',
    filename VARCHAR(255),
    style VARCHAR(100),
    negative_prompt TEXT,
    voice VARCHAR(100),
    language VARCHAR(20),
    speaking_speed FLOAT,
    enhanced_visual_prompt TEXT,
    enhanced_voiceover_script TEXT,
    visual_status scene_status NOT NULL DEFAULT 'PENDING',
    voice_status scene_status NOT NULL DEFAULT 'PENDING',
    merge_status scene_status,
    overall_status scene_status NOT NULL DEFAULT 'PENDING',
    visual_path VARCHAR(500),
    audio_path VARCHAR(500),
    merged_path VARCHAR(500),
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE generation_attempts (
    id SERIAL PRIMARY KEY,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    attempt_type attempt_type NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    status attempt_status NOT NULL DEFAULT 'STARTED',
    error_message TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE activity_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scene_id INTEGER,
    scene_number VARCHAR(50),
    level log_level NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Enable Row Level Security (RLS)
ALTER TABLE api_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_logs ENABLE ROW LEVEL SECURITY;

-- 4. Create RLS Policies
-- Users can only read and write their own data.

-- api_profiles
CREATE POLICY "Users can manage their own API profiles" ON api_profiles
    FOR ALL USING (auth.uid() = user_id);

-- user_settings
CREATE POLICY "Users can manage their own settings" ON user_settings
    FOR ALL USING (auth.uid() = user_id);

-- projects
CREATE POLICY "Users can manage their own projects" ON projects
    FOR ALL USING (auth.uid() = user_id);

-- scenes
CREATE POLICY "Users can manage scenes for their projects" ON scenes
    FOR ALL USING (auth.uid() = user_id);

-- generation_attempts
CREATE POLICY "Users can manage generation attempts for their scenes" ON generation_attempts
    FOR ALL USING (auth.uid() = user_id);

-- activity_logs
CREATE POLICY "Users can view and manage their activity logs" ON activity_logs
    FOR ALL USING (auth.uid() = user_id);
