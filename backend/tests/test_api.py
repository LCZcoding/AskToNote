"""T1.7 `/api/answer` SSE + 落盘单测（Plan §4.1 / §7）。

用 `TestClient` + `dependency_overrides` 注入 FakeLLM（不真实计费）；
DB（`memory.DB_PATH`）与笔记目录（`notes.NOTES_DIR`）用 `tmp_path` 隔离。
覆盖：SSE 事件序列（delta*→done）、新建/追加落盘、`notes`/`questions` 增行、
错误以 `error` 收尾、正文清洗三条规则、Prompt 组装读记忆且排除当前主题。
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.memory as memory
import app.notes as notes
from app import api
from app.api import get_llm
from main import app


class FakeLLM:
    """可配置分块/异常、并记录 prompt 的假客户端。"""

    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self.chunks: list[str] = list(chunks or [])
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        raise AssertionError("模式①作答不应走非流式 chat")

    def chat_stream(self, system: str, user: str):
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return iter(self.chunks)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 DB/笔记目录，并覆盖 LLM 依赖；yield (client, holder)。"""
    monkeypatch.setattr(memory, "DB_PATH", tmp_path / "asktonote.db")
    monkeypatch.setattr(memory, "_initialized", False)
    monkeypatch.setattr(notes, "NOTES_DIR", tmp_path / "notes")

    holder = {"llm": FakeLLM(["正文"])}
    app.dependency_overrides[get_llm] = lambda: holder["llm"]
    client = TestClient(app)
    try:
        yield client, holder
    finally:
        app.dependency_overrides.clear()


def _parse_sse(text: str) -> list[dict]:
    """把 SSE 响应体切分为事件列表（按 `\\n\\n` 分块、去 `data: ` 前缀再 JSON 解析）。"""
    events: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: ")
        events.append(json.loads(block[len("data: ") :]))
    return events


def _count(table: str) -> int:
    with memory.connection() as conn:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


# ---------------------------------------------------------------------------
# 正文清洗（Plan §7 步骤4，纯函数）
# ---------------------------------------------------------------------------


def test_clean_body_strips_overall_code_fence() -> None:
    """整体被 ``` 围栏包裹 → 去围栏。"""
    assert api._clean_body("```markdown\n正文。\n```", "Q") == "正文。"


def test_clean_body_removes_duplicate_leading_question_heading() -> None:
    """正文开头的重复问题标题行被删除（`#`/`##` 均可）。"""
    assert api._clean_body("# 什么是容器\n\n正文。", "什么是容器") == "正文。"
    assert api._clean_body("## 什么是容器\n正文。", "什么是容器") == "正文。"


def test_clean_body_compresses_headings_to_three_hashes() -> None:
    """行首标题一律压为 `###`；非重复标题保留。"""
    assert api._clean_body("# 小节\n\n正文。", "无关问题") == "### 小节\n\n正文。"


def test_clean_body_keeps_hash_lines_inside_code_fence() -> None:
    """代码围栏内的 `#` 注释（如 bash）不被压为 `###`；围栏外的标题照常压缩。"""
    raw = "### 原理\n\n```bash\n# 启动容器\ndocker run -d nginx\n```\n"
    out = api._clean_body(raw, "无关问题")
    assert "### 原理" in out
    assert "\n# 启动容器\n" in out
    assert "### 启动容器" not in out


# ---------------------------------------------------------------------------
# /api/answer：SSE 事件序列 + 落盘
# ---------------------------------------------------------------------------


def test_answer_new_note_streams_deltas_then_done(env) -> None:
    """新 slug：delta* → done；新建文件（先 `# title`）、notes/questions 各增一行。"""
    client, holder = env
    holder["llm"] = FakeLLM(["# 什么是容器\n", "\n容器是轻量级虚拟化技术。"])
    resp = client.post(
        "/api/answer",
        json={"question": "什么是容器", "slug": "docker", "title": "Docker 入门"},
    )
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert events[0]["text"] == "# 什么是容器\n"

    done = events[-1]
    md = done["note_markdown"]
    assert md.startswith("# Docker 入门")
    assert "## 什么是容器" in md
    assert "容器是轻量级虚拟化技术。" in md
    assert md.count("## 什么是容器") == 1  # 问题标题只出现一次（重复的开头标题已删）
    assert "# 什么是容器" not in md.splitlines()  # 不再有 `#` 级问题标题行

    # 磁盘落盘与 done 一致
    assert notes.read_note("docker") == md

    with memory.connection() as conn:
        n = conn.execute("SELECT title, slug FROM notes").fetchall()
        q = conn.execute("SELECT text, concept, source FROM questions").fetchall()
    assert len(n) == 1 and n[0]["slug"] == "docker" and n[0]["title"] == "Docker 入门"
    assert len(q) == 1 and q[0]["source"] == "initial"
    assert q[0]["concept"] is None


def test_answer_appends_to_existing_note_and_refreshes(env) -> None:
    """已存在 slug：追加 Q/A 区块、不新建 notes 行、questions +1、updated_at 刷新。"""
    client, holder = env
    notes.create_note("Docker 入门", "docker")
    notes.append_qa_block("docker", "第一个问题", "第一个答案。")
    note_id = memory.insert_note("Docker 入门", "docker", str(notes.note_path("docker")))
    memory.insert_question(note_id, "第一个问题", None, "initial")

    holder["llm"] = FakeLLM(["补充的正文。"])
    resp = client.post(
        "/api/answer",
        json={"question": "第二个问题", "slug": "docker", "title": "应被忽略的标题"},
    )
    md = _parse_sse(resp.text)[-1]["note_markdown"]
    assert md.count("## ") == 2
    assert "## 第一个问题" in md and "## 第二个问题" in md
    assert "# Docker 入门" in md

    assert _count("notes") == 1
    assert _count("questions") == 2
    with memory.connection() as conn:
        row = conn.execute("SELECT title, created_at, updated_at FROM notes").fetchone()
        stored_id = conn.execute("SELECT note_id FROM questions ORDER BY id").fetchall()
    assert row["title"] == "Docker 入门"  # title 以 notes 表为准，不被请求值覆盖
    assert row["updated_at"] >= row["created_at"]
    assert [r["note_id"] for r in stored_id] == [note_id, note_id]


def test_answer_error_event_on_llm_failure(env) -> None:
    """LLM 抛错 → 以 `error` 事件收尾，且不落盘。"""
    client, holder = env
    holder["llm"] = FakeLLM(error=RuntimeError("boom"))
    resp = client.post(
        "/api/answer",
        json={"question": "Q", "slug": "docker", "title": "Docker 入门"},
    )
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert "boom" in events[-1]["message"]
    assert _count("notes") == 0
    assert not notes.note_path("docker").exists()


# ---------------------------------------------------------------------------
# Prompt 组装：读记忆两类 + 排除当前主题
# ---------------------------------------------------------------------------


def test_answer_prompt_uses_memory_two_categories_and_excludes_current_topic(env) -> None:
    """概念按 score 分两类进 Prompt；【其他主题】只含非当前主题。"""
    client, holder = env
    with memory.write_connection() as conn:
        conn.execute("INSERT INTO concepts (name, score, updated_at) VALUES ('容器', 60, 't')")
        conn.execute("INSERT INTO concepts (name, score, updated_at) VALUES ('镜像', 10, 't')")
    memory.insert_note("Java 基础", "java", "p")

    holder["llm"] = FakeLLM(["回答。"])
    client.post(
        "/api/answer",
        json={"question": "Q", "slug": "docker", "title": "Docker 入门"},
    )
    _, user = holder["llm"].calls[0]
    assert "容器(familiar)" in user
    assert "镜像(unfamiliar)" in user
    # 【其他主题】只含非当前主题（取该行 `//` 前的渲染部分，避开模板示例注释）
    topics_line = next(l for l in user.splitlines() if l.startswith("【其他主题】"))
    rendered = topics_line.split("//")[0]
    assert "java: Java 基础" in rendered
    assert "docker:" not in rendered  # 当前主题被排除（避免内链指向自身）


# ---------------------------------------------------------------------------
# 读接口（Plan §4.1）：GET /api/note/{slug}、GET /api/notes
# ---------------------------------------------------------------------------


def _seed_note(slug: str, title: str) -> None:
    """建文件 + 写 notes 记录（读接口的前置数据）。"""
    notes.create_note(title, slug)
    memory.insert_note(title, slug, str(notes.note_path(slug)))


def test_get_note_returns_slug_title_markdown(env) -> None:
    """读笔记全文：`{slug, title, markdown}`，title 以 notes 表为准、markdown 与磁盘一致。"""
    client, _ = env
    _seed_note("docker", "Docker 入门")
    notes.append_qa_block("docker", "什么是容器", "容器是轻量级虚拟化技术。")

    resp = client.get("/api/note/docker")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "docker"
    assert data["title"] == "Docker 入门"
    assert data["markdown"] == notes.read_note("docker")
    assert "# Docker 入门" in data["markdown"]
    assert "## 什么是容器" in data["markdown"]


def test_get_note_404_when_topic_unknown(env) -> None:
    """未落盘的主题 → 404。"""
    client, _ = env
    assert client.get("/api/note/nope").status_code == 404


def test_list_notes_returns_slug_title_ordered_by_updated_at_desc(env) -> None:
    """主题列表 `[{slug,title}]`，按 `updated_at` 倒序（最新在前）；不暴露其余字段。"""
    client, _ = env
    _seed_note("a", "A")
    _seed_note("b", "B")
    _seed_note("c", "C")
    with memory.write_connection() as conn:
        conn.execute("UPDATE notes SET updated_at = ? WHERE slug = 'b'", ("2026-03-01T00:00:00+00:00",))
        conn.execute("UPDATE notes SET updated_at = ? WHERE slug = 'c'", ("2026-02-01T00:00:00+00:00",))
        conn.execute("UPDATE notes SET updated_at = ? WHERE slug = 'a'", ("2026-01-01T00:00:00+00:00",))

    resp = client.get("/api/notes")
    assert resp.status_code == 200
    assert resp.json() == [
        {"slug": "b", "title": "B"},
        {"slug": "c", "title": "C"},
        {"slug": "a", "title": "A"},
    ]


def test_list_notes_empty_when_no_notes(env) -> None:
    """无主题时返回空列表。"""
    client, _ = env
    assert client.get("/api/notes").json() == []
