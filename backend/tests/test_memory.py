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


# ---------------------------------------------------------------------------
# T4.1 掌握度规则（Spec §7 / Plan §7）
#   被标注：分数不变（不在库 → 10）且置 pending=1（多次标注只置一次）
#   已理解：仅 pending=1 → pending=0、score=min(100, score+10)（幂等）
#   mastered = score ≥ 80（等级由分数推导）
# ---------------------------------------------------------------------------


def _concept_row(name: str) -> tuple[int, int] | None:
    """读某概念的 (score, pending)；不存在返回 None。"""
    with memory.connection() as conn:
        row = conn.execute(
            "SELECT score, pending FROM concepts WHERE name = ?", (name,)
        ).fetchone()
    return (row["score"], row["pending"]) if row else None


def test_annotation_new_concept_scores_ten_and_pending(db_path) -> None:
    """被标注的新概念：不在库 → score=10、pending=1；等级推导为 unfamiliar。"""
    memory.mark_concept_annotated("镜像")
    assert _concept_row("镜像") == (10, 1)
    assert memory.list_concepts() == [{"name": "镜像", "score": 10, "level": "unfamiliar"}]


def test_annotation_existing_concept_keeps_score(db_path) -> None:
    """已在库的概念被标注：分数不变，仅置 pending=1。"""
    with memory.write_connection() as conn:
        conn.execute(
            "INSERT INTO concepts (name, score, updated_at) VALUES ('容器', 60, 't')"
        )
    memory.mark_concept_annotated("容器")
    assert _concept_row("容器") == (60, 1)


def test_annotation_twice_keeps_score_and_pending(db_path) -> None:
    """同一概念多次标注：分数不变、pending 恒为 1（只置一次，未确认前不叠加）。"""
    memory.mark_concept_annotated("镜像")
    memory.mark_concept_annotated("镜像")
    assert _concept_row("镜像") == (10, 1)
    with memory.connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM concepts").fetchone()["c"]
    assert count == 1  # 不因重复标注产生重复行


def test_annotation_existing_pending_concept_keeps_score(db_path) -> None:
    """已在库且已 pending 的概念再次标注：分数仍不变。"""
    memory.mark_concept_annotated("容器")  # 新建 10/pending
    memory.mark_concept_annotated("容器")  # 再次标注
    assert _concept_row("容器") == (10, 1)


def test_confirm_clears_pending_and_adds_ten(db_path) -> None:
    """「已理解」：pending 清 0、score +10，返回新分数。"""
    memory.mark_concept_annotated("镜像")
    assert memory.confirm_concept("镜像") == 20
    assert _concept_row("镜像") == (20, 0)


def test_confirm_is_idempotent(db_path) -> None:
    """重复「已理解」不再加分（pending=0 时原样返回、不改分）。"""
    memory.mark_concept_annotated("镜像")
    assert memory.confirm_concept("镜像") == 20
    assert memory.confirm_concept("镜像") == 20
    assert _concept_row("镜像") == (20, 0)


def test_confirm_caps_at_hundred(db_path) -> None:
    """分数上限 100：95 → 100，不越界。"""
    memory.mark_concept_annotated("镜像")
    with memory.write_connection() as conn:
        conn.execute(
            "UPDATE concepts SET score = 95, pending = 1 WHERE name = '镜像'"
        )
    assert memory.confirm_concept("镜像") == 100
    assert _concept_row("镜像") == (100, 0)


def test_confirm_reaches_mastered_boundary(db_path) -> None:
    """确认到 80 分即达 mastered（等级由分数推导）。"""
    with memory.write_connection() as conn:
        conn.execute(
            "INSERT INTO concepts (name, score, pending, updated_at) "
            "VALUES ('容器', 70, 1, 't')"
        )
    assert memory.confirm_concept("容器") == 80
    assert memory.list_concepts() == [{"name": "容器", "score": 80, "level": "mastered"}]


def test_confirm_at_cap_keeps_hundred(db_path) -> None:
    """已达 100 分再确认：仍为 100（min 封顶），且 pending 清 0。"""
    with memory.write_connection() as conn:
        conn.execute(
            "INSERT INTO concepts (name, score, pending, updated_at) "
            "VALUES ('容器', 100, 1, 't')"
        )
    assert memory.confirm_concept("容器") == 100
    assert _concept_row("容器") == (100, 0)


def test_confirm_missing_concept_returns_none(db_path) -> None:
    """概念不在库：返回 None，且不凭空建行。"""
    assert memory.confirm_concept("不存在") is None
    assert _concept_row("不存在") is None


def test_level_boundaries_from_score(db_path) -> None:
    """等级由分数推导的边界（Plan §3）：<20 unfamiliar | 20-49 blurry | 50-79 familiar | ≥80 mastered。"""
    assert memory.level_from_score(19) == "unfamiliar"
    assert memory.level_from_score(20) == "blurry"
    assert memory.level_from_score(49) == "blurry"
    assert memory.level_from_score(50) == "familiar"
    assert memory.level_from_score(79) == "familiar"
    assert memory.level_from_score(80) == "mastered"
