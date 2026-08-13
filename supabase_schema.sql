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

-- ============================================================================
-- PART 2 — CSV INGESTION, TASK TRACKING, PROFILE HEALTH, ERROR LOGS
-- Safe to re-run: every statement is idempotent.
-- ============================================================================

-- 5. CSV Ingestion Tables (proposal §20, §21, §22, §45)

CREATE TABLE IF NOT EXISTS csv_files (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    encoding VARCHAR(50),
    delimiter VARCHAR(10),
    row_count INTEGER NOT NULL DEFAULT 0,
    column_count INTEGER NOT NULL DEFAULT 0,
    valid_row_count INTEGER NOT NULL DEFAULT 0,
    invalid_row_count INTEGER NOT NULL DEFAULT 0,
    has_master_prompt BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(50) NOT NULL DEFAULT 'ANALYZED',
    raw_rows JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS csv_columns (
    id SERIAL PRIMARY KEY,
    csv_file_id INTEGER NOT NULL REFERENCES csv_files(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    column_index INTEGER NOT NULL DEFAULT 0,
    original_name VARCHAR(255) NOT NULL,
    detected_meaning VARCHAR(100) NOT NULL DEFAULT 'custom_metadata',
    mapped_meaning VARCHAR(100),
    confidence INTEGER NOT NULL DEFAULT 0,
    data_type VARCHAR(50) DEFAULT 'text',
    non_empty_count INTEGER NOT NULL DEFAULT 0,
    example_value TEXT,
    is_manual_override BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Error Logs (proposal §45, §48, §58)

CREATE TABLE IF NOT EXISTS error_logs (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    scene_id INTEGER,
    task_id INTEGER,
    api_profile_id INTEGER,
    error_category VARCHAR(50) NOT NULL DEFAULT 'PROVIDER_ERROR',
    error_message TEXT NOT NULL,
    is_retryable BOOLEAN NOT NULL DEFAULT FALSE,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. Column additions to existing tables (idempotent)

-- 7.0 Reconcile base columns.
-- `CREATE TABLE IF NOT EXISTS` above does nothing when the table already
-- exists, so a database created by an older revision keeps its old shape and
-- the API fails with "column ... does not exist". These statements bring any
-- pre-existing table up to the current base definition.
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS provider VARCHAR(50) NOT NULL DEFAULT 'google';
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS connection_status VARCHAR(50) DEFAULT 'active';
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS last_tested TIMESTAMPTZ;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS test_result TEXT;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE projects ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS mode VARCHAR(50) NOT NULL DEFAULT 'IMAGE_VOICE';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS settings_json JSONB;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE scenes ADD COLUMN IF NOT EXISTS external_id VARCHAR(100);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS master_prompt TEXT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS negative_prompt TEXT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS style VARCHAR(100);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS tone VARCHAR(100);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS voice_name VARCHAR(100);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS voice VARCHAR(100);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS language VARCHAR(20);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS speaking_speed FLOAT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS duration FLOAT DEFAULT 5.0;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS media_type VARCHAR(50) DEFAULT 'image';
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS filename VARCHAR(255);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS custom_metadata JSONB DEFAULT '{}'::jsonb;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS enhanced_visual_prompt TEXT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS enhanced_voiceover_script TEXT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS video_status VARCHAR(50) NOT NULL DEFAULT 'PENDING';
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS merge_status VARCHAR(50);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS merged_path VARCHAR(500);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS processing_tasks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS pending_tasks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE assets ADD COLUMN IF NOT EXISTS mime_type VARCHAR(100);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS size BIGINT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS provider VARCHAR(50) DEFAULT 'google';
ALTER TABLE assets ADD COLUMN IF NOT EXISTS model VARCHAR(100);

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_generation_mode VARCHAR(50) DEFAULT 'IMAGE_VOICE';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_aspect_ratio VARCHAR(20) DEFAULT '16:9';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_voice VARCHAR(100);
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_language VARCHAR(20) DEFAULT 'en-US';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_concurrency INTEGER DEFAULT 1;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_retry_count INTEGER DEFAULT 3;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE activity_logs ADD COLUMN IF NOT EXISTS scene_id INTEGER;
ALTER TABLE activity_logs ADD COLUMN IF NOT EXISTS scene_number VARCHAR(50);

-- 7.1 api_profiles: health / quota / usage tracking (proposal §10, §13)
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS request_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS unavailable_until TIMESTAMPTZ;
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS key_hint VARCHAR(20);
ALTER TABLE api_profiles ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;

-- 7.2 generation_tasks: full audit trail per asset (proposal §32, §58)
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS api_profile_id INTEGER REFERENCES api_profiles(id) ON DELETE SET NULL;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS prompt TEXT;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS storage_path VARCHAR(500);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS error_category VARCHAR(50);
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS scene_number VARCHAR(50);
ALTER TABLE generation_tasks ALTER COLUMN attempt_count SET DEFAULT 0;
ALTER TABLE generation_tasks ALTER COLUMN job_id DROP NOT NULL;

-- One row per (scene, asset type) so re-runs update instead of duplicating (proposal §32 idempotency)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'generation_tasks_scene_type_unique'
    ) THEN
        ALTER TABLE generation_tasks
            ADD CONSTRAINT generation_tasks_scene_type_unique UNIQUE (scene_id, task_type);
    END IF;
END $$;

-- 7.3 assets: verification metadata backing the blue completion check (proposal §34, §42)
ALTER TABLE assets ADD COLUMN IF NOT EXISTS task_id INTEGER REFERENCES generation_tasks(id) ON DELETE SET NULL;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS scene_number VARCHAR(50);
ALTER TABLE assets ADD COLUMN IF NOT EXISTS prompt TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'assets_scene_type_unique'
    ) THEN
        ALTER TABLE assets ADD CONSTRAINT assets_scene_type_unique UNIQUE (scene_id, asset_type);
    END IF;
END $$;

-- 7.4 scenes / projects: fields the pipeline reads back
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS video_prompt TEXT;
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS video_path VARCHAR(500);
ALTER TABLE scenes ADD COLUMN IF NOT EXISTS csv_file_id INTEGER REFERENCES csv_files(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS skipped_scenes INTEGER NOT NULL DEFAULT 0;

-- 7.5 user_settings: generation defaults surfaced in the Settings page (proposal §9)
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_speech_speed FLOAT DEFAULT 1.0;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_negative_prompt TEXT;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS merge_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS video_generation_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- 8. Indexes for the hot query paths
CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id);
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(project_id, overall_status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON generation_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_job ON generation_tasks(job_id);
CREATE INDEX IF NOT EXISTS idx_tasks_scene ON generation_tasks(scene_id);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
CREATE INDEX IF NOT EXISTS idx_assets_scene ON assets(scene_id);
CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_logs(project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_error_logs_project ON error_logs(project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_csv_files_project ON csv_files(project_id);
CREATE INDEX IF NOT EXISTS idx_csv_columns_file ON csv_columns(csv_file_id);

-- 9. RLS for the new tables
ALTER TABLE csv_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE csv_columns ENABLE ROW LEVEL SECURITY;
ALTER TABLE error_logs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their csv files" ON csv_files';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their csv columns" ON csv_columns';
    EXECUTE 'DROP POLICY IF EXISTS "Users can manage their error logs" ON error_logs';
END $$;

CREATE POLICY "Users can manage their csv files" ON csv_files FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their csv columns" ON csv_columns FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users can manage their error logs" ON error_logs FOR ALL USING (auth.uid() = user_id);

-- 10. Realtime publication so the frontend receives live task/scene updates (proposal §37)
DO $$ BEGIN
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE scenes;
    EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE generation_tasks;
    EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE generation_jobs;
    EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE assets;
    EXCEPTION WHEN duplicate_object THEN NULL; END;
    BEGIN
        ALTER PUBLICATION supabase_realtime ADD TABLE activity_logs;
    EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;
