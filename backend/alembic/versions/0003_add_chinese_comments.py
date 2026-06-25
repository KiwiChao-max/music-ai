"""add Chinese comments to audio_tasks (table, columns, enum, trigger)

All statements are pure `COMMENT ON ...` so the migration is idempotent and
cheap to re-run. `COMMENT ON` is itself idempotent in PostgreSQL: the comment
is just replaced on every call.

Revision ID: 0003_add_chinese_comments
Revises: 0002_progress_fields
Create Date: 2026-06-25

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_add_chinese_comments"
down_revision: Union[str, None] = "0002_progress_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Table -----------------------------------------------------------------
_TABLE_COMMENT = "音频处理任务表，记录每一次上传的处理进度与结果。"

# -- Columns ---------------------------------------------------------------
_COLUMN_COMMENTS: dict[str, str] = {
    "id": "主键，自增 ID。",
    "filename": "用户上传的原始音频文件名（仅 basename，路径分隔符已剥离）。",
    "status": "任务状态：UPLOADED 已上传 / PROCESSING 处理中 / FINISHED 已完成 / FAILED 失败。",
    "created_at": "记录创建时间，数据库侧默认 NOW()。",
    "updated_at": "记录最近一次修改时间，由 trg_set_updated_at 触发器自动维护。",
    "progress": "处理进度百分比，0-100。worker 在每个步骤 commit 一次。",
    "current_step": "当前步骤的简短描述，例如 \"Separating drums...\"，用于前端展示。",
    "duration": "音频时长（秒），由 upload 接口用 stdlib wave 探出；非 WAV 留空。",
    "output_dir": "worker 写入产物（人声 / 鼓 / MIDI 等）的目录路径。",
    "error_message": "任务失败时的错误信息；成功时清空。",
    "finished_at": "任务进入终态（FINISHED 或 FAILED）的时间戳。",
}

# -- Enum ------------------------------------------------------------------
_ENUM_COMMENT = "audio_tasks.status 字段使用的枚举：UPLOADED / PROCESSING / FINISHED / FAILED。"

# -- Trigger ---------------------------------------------------------------
_TRIGGER_FN_COMMENT = "通用 updated_at 自动维护函数：UPDATE 时把 NEW.updated_at 置为 NOW()。"
_TRIGGER_COMMENT = (
    "audio_tasks 表的 BEFORE UPDATE 触发器，调用 trg_set_updated_at() 自动维护 updated_at。"
)


def upgrade() -> None:
    op.execute(f"COMMENT ON TABLE audio_tasks IS '{_TABLE_COMMENT}'")
    for col, comment in _COLUMN_COMMENTS.items():
        op.execute(f"COMMENT ON COLUMN audio_tasks.{col} IS '{comment}'")
    op.execute(f"COMMENT ON TYPE audio_task_status IS '{_ENUM_COMMENT}'")
    op.execute(f"COMMENT ON FUNCTION trg_set_updated_at() IS '{_TRIGGER_FN_COMMENT}'")
    op.execute(
        "COMMENT ON TRIGGER set_audio_tasks_updated_at ON audio_tasks "
        f"IS '{_TRIGGER_COMMENT}'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON TRIGGER set_audio_tasks_updated_at ON audio_tasks IS NULL")
    op.execute("COMMENT ON FUNCTION trg_set_updated_at() IS NULL")
    op.execute("COMMENT ON TYPE audio_task_status IS NULL")
    for col in _COLUMN_COMMENTS:
        op.execute(f"COMMENT ON COLUMN audio_tasks.{col} IS NULL")
    op.execute("COMMENT ON TABLE audio_tasks IS NULL")
