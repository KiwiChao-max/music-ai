-- audio_tasks table
-- Stores audio processing task metadata and lifecycle status.

CREATE TYPE audio_task_status AS ENUM (
    'UPLOADED',
    'PROCESSING',
    'FINISHED',
    'FAILED'
);

CREATE TABLE audio_tasks (
    id          BIGSERIAL PRIMARY KEY,
    filename    VARCHAR(512) NOT NULL,
    status      audio_task_status NOT NULL DEFAULT 'UPLOADED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audio_tasks_status ON audio_tasks (status);
CREATE INDEX idx_audio_tasks_created_at ON audio_tasks (created_at DESC);

-- Auto-update updated_at on row update
CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_audio_tasks_updated_at
BEFORE UPDATE ON audio_tasks
FOR EACH ROW
EXECUTE FUNCTION trg_set_updated_at();
