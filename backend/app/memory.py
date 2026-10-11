"""T1.3 SQLite 记忆层骨架：建库建表、WAL，以及短连接 + 写锁约定。

约定见 Plan §7「DB 访问约定」：所有 DB 访问经本模块统一封装——每次操作开**短连接**、
用完即关；所有**写**操作经**进程内全局锁**串行化；初始化时设一次
`PRAGMA journal_mode=WAL`；读操作不加锁。表结构严格按 Plan §3。
"""
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

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
