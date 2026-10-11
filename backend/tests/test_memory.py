"""T1.3 SQLite 记忆层骨架单测：建库建表、WAL、user_profile 初始化、短连接 + 写锁。

表结构断言严格对齐 Plan §3，访问约定对齐 Plan §7。
用 `tmp_path` 隔离，不触碰真实的 backend/data/asktonote.db。
"""
import sqlite3

import pytest

import app.memory as memory

EXPECTED_TABLES = {"concepts", "user_profile", "notes", "questions", "annotations"}

# (列名, 声明类型)，顺序即建表顺序（对齐 Plan §3）
EXPECTED_COLUMNS = {
    "concepts": [
        ("id", "INTEGER"),
        ("name", "TEXT"),
        ("score", "INTEGER"),
        ("pending", "INTEGER"),
        ("updated_at", "TEXT"),
    ],
    "user_profile": [
        ("id", "INTEGER"),
        ("background", "TEXT"),
        ("background_locked", "INTEGER"),
    ],
    "notes": [
        ("id", "INTEGER"),
        ("title", "TEXT"),
        ("slug", "TEXT"),
        ("file_path", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "questions": [
        ("id", "INTEGER"),
        ("note_id", "INTEGER"),
        ("text", "TEXT"),
        ("concept", "TEXT"),
        ("source", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "annotations": [
        ("id", "INTEGER"),
        ("note_id", "INTEGER"),
        ("snippet", "TEXT"),
        ("concept", "TEXT"),
        ("depth", "TEXT"),
        ("created_at", "TEXT"),
    ],
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """把 DB_PATH 指向临时文件，并重置「已初始化」标志。"""
    path = tmp_path / "asktonote.db"
    monkeypatch.setattr(memory, "DB_PATH", path)
    monkeypatch.setattr(memory, "_initialized", False)
    return path


def _table_names(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _table_info(path, table) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()


def test_init_creates_db_file(db_path) -> None:
    """首次 init_db 应自动创建库文件。"""
    assert not db_path.exists()
    memory.init_db()
    assert db_path.exists()


def test_all_tables_created(db_path) -> None:
    """五张表齐全（对齐 Plan §3）。"""
    memory.init_db()
    assert _table_names(db_path) >= EXPECTED_TABLES


def test_table_columns_match_plan(db_path) -> None:
    """每张表的列名与声明类型、顺序与 Plan §3 一致。"""
    memory.init_db()
    for table, expected in EXPECTED_COLUMNS.items():
        got = [(row[1], row[2]) for row in _table_info(db_path, table)]
        assert got == expected, table


def test_not_null_constraints(db_path) -> None:
    """NOT NULL 约束与 Plan §3 一致（PK/可空列除外）。"""
    memory.init_db()
    notnull = {
        table: {row[1] for row in _table_info(db_path, table) if row[3]}
        for table in EXPECTED_COLUMNS
    }
    assert notnull["concepts"] == {"name", "score", "pending", "updated_at"}
    assert notnull["user_profile"] == {"background", "background_locked"}
    assert notnull["notes"] == {"title", "slug", "file_path", "created_at", "updated_at"}
    assert notnull["questions"] == {"text", "source", "created_at"}
    assert notnull["annotations"] == {"snippet", "depth", "created_at"}


def test_default_values(db_path) -> None:
    """score/pending/background/background_locked 默认值符合 Plan §3。"""
    memory.init_db()
    concepts = {row[1]: row[4] for row in _table_info(db_path, "concepts")}
    profile = {row[1]: row[4] for row in _table_info(db_path, "user_profile")}
    assert concepts["score"] == "10"
    assert concepts["pending"] == "0"
    assert profile["background"] == "''"
    assert profile["background_locked"] == "0"


def test_wal_mode_enabled(db_path) -> None:
    """初始化后日志模式应为 WAL（Plan §7）。"""
    memory.init_db()
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode == "wal"


def test_unique_constraints(db_path) -> None:
    """concepts.name 与 notes.slug 唯一。"""
    memory.init_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO concepts (name, updated_at) VALUES ('x', 't')")
        conn.execute(
            "INSERT INTO notes (title, slug, file_path, created_at, updated_at) "
            "VALUES ('T', 's', 'p', 'a', 'a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO concepts (name, updated_at) VALUES ('x', 't')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notes (title, slug, file_path, created_at, updated_at) "
                "VALUES ('T2', 's', 'p', 'a', 'a')"
            )
    finally:
        conn.close()


def test_user_profile_initialized_once(db_path) -> None:
    """user_profile 初始化单行 id=1、空背景，且重复 init 幂等。"""
    memory.init_db()
    memory.init_db()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, background, background_locked FROM user_profile"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, "", 0)]


def test_lazy_init_on_first_use(db_path) -> None:
    """首次使用连接即自动建库（无需手动 init）。"""
    assert not db_path.exists()
    with memory.connection() as conn:
        conn.execute("SELECT 1")
    assert db_path.exists()


def test_write_connection_commits_and_read_back(db_path) -> None:
    """写连接应提交、读连接能读回。"""
    with memory.write_connection() as conn:
        conn.execute(
            "INSERT INTO notes (title, slug, file_path, created_at, updated_at) "
            "VALUES ('标题', 't', 'p', 'a', 'a')"
        )
    with memory.connection() as conn:
        row = conn.execute("SELECT title FROM notes WHERE slug = 't'").fetchone()
    assert row["title"] == "标题"
