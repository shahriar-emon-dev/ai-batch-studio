-- Supabase SQL Schema for AI Content Studio (AI Batch Studio)
-- Run this in your Supabase SQL Editor

-- 1. Create custom enums if they do not exist
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'project_status') THEN
        CREATE TYPE project_status AS ENUM ('PENDING', 'PROCESSING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'scene_status') THEN
        CREATE TYPE scene_status AS ENUM ('PENDING', 'PROCESSING', 'VISUAL_GENERATING', 'VISUAL_COMPLETED', 'VOICE_GENERATING', 'VOICE_COMPLETED', 'MERGING', 'COMPLETED', 'FAILED', 'SKIPPED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'generation_mode') THEN
        CREATE TYPE generation_mode AS ENUM ('IMAGE_VOICE', 'VIDEO_VOICE', 'IMAGE_ONLY');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'attempt_type') THEN
        CREATE TYPE attempt_type AS ENUM ('VISUAL', 'VOICE', 'MERGE', 'ENHANCEMENT');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'attempt_status') THEN
        CREATE TYPE attempt_status AS ENUM ('STARTED', 'SUCCEEDED', 'FAILED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'log_level') THEN
        CREATE TYPE log_level AS ENUM ('INFO', 'WARNING', 'ERROR', 'SUCCESS');
    END IF;
END $$;

-- 2. Create Core Tables

-- 2.1 API Profiles
CREATE TABLE IF NOT EXISTS api_profiles (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL DEFAULT 'google',
    profile_name VARCHAR(255) NOT NULL,
    encrypted_credentials TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    connection_status VARCHAR(50) DEFAULT 'active',
    last_tested TIMESTAMPTZ,
    test_result TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.2 User Settings
CREATE TABLE IF NOT EXISTS user_settings (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    default_generation_mode VARCHAR(50) DEFAULT 'IMAGE_VOICE',
    default_aspect_ratio VARCHAR(20) DEFAULT '16:9',
    default_voice VARCHAR(100),
    default_language VARCHAR(20) DEFAULT 'en-US',
    default_concurrency INTEGER DEFAULT 1,
    default_retry_count INTEGER DEFAULT 3,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.3 Projects
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL DEFAULT 'Untitled Project',
    description TEXT,
    mode VARCHAR(50) NOT NULL DEFAULT 'IMAGE_VOICE',
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
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

-- 2.4 Scenes
CREATE TABLE IF NOT EXISTS scenes (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scene_number VARCHAR(50) NOT NULL DEFAULT '1',
    external_id VARCHAR(100),
    visual_prompt TEXT NOT NULL,
    voiceover_script TEXT DEFAULT '',
    master_prompt TEXT,
    aspect_ratio VARCHAR(20) DEFAULT '16:9',
    duration FLOAT DEFAULT 5.0,
    media_type VARCHAR(50) DEFAULT 'image',
    filename VARCHAR(255),
    voice_name VARCHAR(100),
    style VARCHAR(100),
    tone VARCHAR(100),
    negative_prompt TEXT,
    voice VARCHAR(100),
    language VARCHAR(20),
    speaking_speed FLOAT,
    custom_metadata JSONB DEFAULT '{}'::jsonb,
    enhanced_visual_prompt TEXT,
    enhanced_voiceover_script TEXT,
    visual_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    voice_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    video_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    merge_status VARCHAR(50),
    overall_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    visual_path VARCHAR(500),
    audio_path VARCHAR(500),
    merged_path VARCHAR(500),
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.5 Generation Jobs
CREATE TABLE IF NOT EXISTS generation_jobs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    total_tasks INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    processing_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    pending_tasks INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.6 Generation Tasks
CREATE TABLE IF NOT EXISTS generation_tasks (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    task_type VARCHAR(50) NOT NULL DEFAULT 'visual',
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.7 Assets
CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    scene_id INTEGER REFERENCES scenes(id) ON DELETE SET NULL,
    asset_type VARCHAR(50) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100),
    size BIGINT,
    provider VARCHAR(50) DEFAULT 'google',
    model VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.8 Activity Logs
CREATE TABLE IF NOT EXISTS activity_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scene_id INTEGER,
    scene_number VARCHAR(50),
    level VARCHAR(50) NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Enable Row Level Security (RLS)
ALTER TABLE api_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_logs ENABLE ROW LEVEL SECURITY;

-- 4. Create RLS Policies (Drop existing first to allow clean rerun)
DO $$ BEGIN
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their own API profiles" ON api_profiles';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their own settings" ON user_settings';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their own projects" ON projects';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage scenes for their projects" ON scenes';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their generation jobs" ON generation_jobs';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their generation tasks" ON generation_tasks';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their assets" ON assets';
    EXECUTE 'DROP POLICY IF EXISTS "Users can view and manage their activity logs" ON activity_logs';
END $$;

CREATE POLICY "Users can manage their own API profiles" ON api_profiles FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their own settings" ON user_settings FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their own projects" ON projects FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage scenes for their projects" ON scenes FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their generation jobs" ON generation_jobs FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their generation tasks" ON generation_tasks FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their assets" ON assets FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can view and manage their activity logs" ON activity_logs FOR ALL USING (auth.uid() = user_id);
