"""T1.3 SQLite 记忆层骨架：建库建表、WAL，以及短连接 + 写锁约定。

约定见 Plan §7「DB 访问约定」：所有 DB 访问经本模块统一封装——每次操作开**短连接**、
用完即关；所有**写**操作经**进程内全局锁**串行化；初始化时设一次
`PRAGMA journal_mode=WAL`；读操作不加锁。表结构严格按 Plan §3。
"""
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings

DB_PATH = settings.data_dir / "asktonote.db"

# 写操作进程内全局锁（Plan §7）：保证任意两个写不会相撞。
_write_lock = threading.Lock()

# 首次使用 DB 时自动建库（只跑一次）。
_init_lock = threading.Lock()
_initialized = False

_DDL = """
CREATE TABLE IF NOT EXISTS concepts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    score        INTEGER NOT NULL DEFAULT 10,
    pending      INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    background        TEXT NOT NULL DEFAULT '',
    background_locked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    file_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER REFERENCES notes(id),
    text        TEXT NOT NULL,
    concept     TEXT,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER REFERENCES notes(id),
    snippet     TEXT NOT NULL,
    concept     TEXT,
    depth       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def init_db() -> None:
    """建库建表、开启 WAL，并初始化 `user_profile` 单行（id=1、空背景）。幂等。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO user_profile (id, background, background_locked) "
            "VALUES (1, '', 0)"
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_initialized() -> None:
    """首次使用 DB 时自动建库（幂等）。"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if not _initialized:
            init_db()
            _initialized = True


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """开一个短连接；正常结束提交，无论成败都关闭（Plan §7）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """读操作：短连接、不加锁（Plan §7）。"""
    _ensure_initialized()
    with _connect() as conn:
        yield conn


@contextmanager
def write_connection() -> Iterator[sqlite3.Connection]:
    """写操作：短连接 + 全局锁串行化（Plan §7）。"""
    _ensure_initialized()
    with _write_lock:
        with _connect() as conn:
            yield conn


# ---------------------------------------------------------------------------
# T1.7 记忆读 / 主题与问题写 helper（供 /api/answer 落盘与 Prompt 组装，Plan §7）
# ---------------------------------------------------------------------------


def level_from_score(score: int) -> str:
    """由分数推导等级（Plan §3）：<20 unfamiliar | 20-49 blurry | 50-79 familiar | ≥80 mastered。"""
    if score < 20:
        return "unfamiliar"
    if score < 50:
        return "blurry"
    if score < 80:
        return "familiar"
    return "mastered"


def _now() -> str:
    """UTC ISO 时间戳（字典序可比，便于 `updated_at` 倒序）。"""
    return datetime.now(timezone.utc).isoformat()


def get_background() -> str:
    """读用户背景（user_profile 单行 id=1）。"""
    with connection() as conn:
        row = conn.execute("SELECT background FROM user_profile WHERE id = 1").fetchone()
    return row["background"] if row else ""


def list_concepts() -> list[dict]:
    """全部概念 `[{name, score, level}]`（`level` 后端推导；不含内部字段 `pending`）。"""
    with connection() as conn:
        rows = conn.execute("SELECT name, score FROM concepts ORDER BY name").fetchall()
    return [
        {"name": r["name"], "score": r["score"], "level": level_from_score(r["score"])}
        for r in rows
    ]


def concepts_by_mastery() -> tuple[list[dict], list[dict]]:
    """按 `score` 切分为 Prompt 两类（Plan §6 渲染约定）。

    返回 `(已掌握, 尚不熟悉)`，元素为 `{name, level}`：`score≥50` → 已掌握；`<50` → 尚不熟悉。
    """
    mastered: list[dict] = []
    learning: list[dict] = []
    for c in list_concepts():
        item = {"name": c["name"], "level": c["level"]}
        (mastered if c["score"] >= 50 else learning).append(item)
    return mastered, learning


def list_notes() -> list[dict]:
    """全部主题，按 `updated_at` 倒序（最新在前）；元素含 id/title/slug/file_path/时间戳。"""
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, title, slug, file_path, created_at, updated_at "
            "FROM notes ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_note_by_slug(slug: str) -> dict | None:
    """按 slug 取主题记录；不存在返回 None。"""
    with connection() as conn:
        row = conn.execute(
            "SELECT id, title, slug, file_path, created_at, updated_at "
            "FROM notes WHERE slug = ?",
            (slug,),
        ).fetchone()
    return dict(row) if row else None


def insert_note(title: str, slug: str, file_path: str) -> int:
    """新建主题记录（`created_at`/`updated_at` 均写当前时间），返回新行 id。"""
    now = _now()
    with write_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, slug, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, slug, file_path, now, now),
        )
        return int(cur.lastrowid)


def touch_note(slug: str) -> None:
    """刷新主题记录的 `updated_at`（Plan §7：追加与新建均刷新）。"""
    with write_connection() as conn:
        conn.execute("UPDATE notes SET updated_at = ? WHERE slug = ?", (_now(), slug))


def insert_question(note_id: int, text: str, concept: str | None, source: str) -> int:
    """写入一条问题索引（Plan §3 `questions`），返回新行 id。"""
    with write_connection() as conn:
        cur = conn.execute(
            "INSERT INTO questions (note_id, text, concept, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (note_id, text, concept, source, _now()),
        )
        return int(cur.lastrowid)
