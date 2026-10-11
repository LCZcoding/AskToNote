"""T1.4 笔记文件读写单测：创建、读取、末尾追加 Q/A 区块、写锁覆盖读改写。

用 `tmp_path` 隔离，不触碰真实的 backend/data/notes/。
（T1.5 将在此文件追加「段落定位插入」用例。）
"""
import re

import pytest

import app.notes as notes


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """把 NOTES_DIR 指向临时目录。"""
    d = tmp_path / "notes"
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    return d


def test_create_note_writes_header_and_makes_dir(notes_dir) -> None:
    """create_note 应自动建目录，并先写入 `# {title}`。"""
    assert not notes_dir.exists()
    notes.create_note("标题", "topic")
    assert notes_dir.exists()
    assert notes.note_path("topic").read_text(encoding="utf-8") == "# 标题\n"


def test_read_note(notes_dir) -> None:
    """read_note 返回文件全文。"""
    notes.create_note("标题", "topic")
    assert notes.read_note("topic") == "# 标题\n"


def test_read_note_missing_raises(notes_dir) -> None:
    """文件不存在时抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        notes.read_note("nope")


def test_append_qa_block_once(notes_dir) -> None:
    """首次追加应接在标题后，且返回追加后的全文。"""
    notes.create_note("标题", "topic")
    content = notes.append_qa_block("topic", "Q1", "A1")
    assert content == "# 标题\n\n## Q1\n\nA1\n"
    assert notes.read_note("topic") == content


def test_append_twice_no_overwrite(notes_dir) -> None:
    """连续两次追加应得到 `# 标题` + 两个 `## 问题` 区块，无覆盖。"""
    notes.create_note("标题", "topic")
    notes.append_qa_block("topic", "Q1", "A1")
    content = notes.append_qa_block("topic", "Q2", "A2")
    assert content == "# 标题\n\n## Q1\n\nA1\n\n## Q2\n\nA2\n"
    assert content.count("## ") == 2
    assert "# 标题" in content


def test_append_creates_dir_when_missing(notes_dir) -> None:
    """目录/文件都不存在时，追加也应自动建目录并落盘。"""
    assert not notes_dir.exists()
    content = notes.append_qa_block("fresh", "Q", "A")
    assert notes_dir.exists()
    assert notes.read_note("fresh") == content


# ---------------------------------------------------------------------------
# T1.5 段落定位插入（Plan §4.1：优先词后、兜底段末）
# ---------------------------------------------------------------------------


def test_insert_after_anchor_when_source_hit() -> None:
    """源码精确命中 → **双向跳转**：原词前插来源锚点并包成链接，词后插解释侧锚点与「↩」回跳。"""
    content = "# Docker 入门\n\n## 什么是容器\n\n容器是一种轻量化的虚拟化技术。\n"
    out = notes.apply_annotation(content, "轻量化", "（即共享宿主内核）")
    m = re.search(r'<a id="anno-([0-9a-f]{8})-src"></a>\[轻量化\]\(#anno-([0-9a-f]{8})\)', out)
    assert m is not None
    assert m.group(1) == m.group(2)  # 原词链接指向同一 id
    n = m.group(1)
    assert f'<a id="anno-{n}"></a>（即共享宿主内核）[↩](#anno-{n}-src)' in out
    assert "容器是一种" in out and "的虚拟化技术。" in out


def test_insert_at_paragraph_end_when_anchor_crosses_inline_markup() -> None:
    """anchor 跨行内标记（源码搜不到）→ 兜底命中 → 段末插解释，且**降级为单向**（无来源锚点/回跳/包词）。"""
    content = "# Docker\n\n## 容器与镜像\n\n**容器**和**镜像**是核心。\n"
    out = notes.apply_annotation(content, "容器和镜像", "（本节要弄清的一对概念）")
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（本节要弄清的一对概念）', out)
    assert m is not None
    assert "**容器**和**镜像**是核心。" in out
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[容器和镜像](#anno-" not in out


def test_source_hit_inside_markup_degrades_to_one_way() -> None:
    """原词源码可命中、但已落在 `**...**` 标记内 → **降级单向**（只插解释侧锚点，不包词、不回跳）。"""
    content = "# T\n\n## Q\n\n**容器**是核心。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（轻量虚拟化）', out)
    assert m is not None
    assert out.count("**") == 2  # 原有粗体标记未被破坏
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[容器](#anno-" not in out


def test_source_hit_inside_wikilink_degrades_to_one_way() -> None:
    """原词落在 `[[内链]]` 内 → 降级单向（v1 内链不可点，不得破坏其语法）。"""
    content = "# T\n\n## Q\n\n参见 [[容器]] 概念。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    assert "[[容器]]" in out
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[容器](#anno-" not in out
    assert '<a id="anno-' in out


def test_insert_picks_first_occurrence() -> None:
    """anchor 多次出现 → 取**首次**（只包/插一处）。"""
    content = "# T\n\n## Q\n\nalpha beta gamma。\n\nlater alpha beta again。\n"
    out = notes.apply_annotation(content, "alpha beta", "（首次）")
    assert out.count("（首次）") == 1
    m = re.search(r'\[alpha beta\]\(#anno-([0-9a-f]{8})\)<a id="anno-\1"></a>（首次）', out)
    assert m is not None
    assert "later alpha beta again。" in out


def test_insert_raises_when_anchor_not_found() -> None:
    """anchor 在任何段落都找不到 → 报错（前端提示重选）。"""
    content = "# T\n\n## Q\n\n正文内容。\n"
    with pytest.raises(notes.AnchorNotFoundError):
        notes.apply_annotation(content, "不存在的片段", "（x）")


def test_insert_raises_when_anchor_empty() -> None:
    """空 anchor 一律报错，避免误命中首个段落。"""
    with pytest.raises(notes.AnchorNotFoundError):
        notes.apply_annotation("# T\n\n正文。\n", "   ", "（x）")


def test_insert_deep_adds_four_hash_section_after_paragraph() -> None:
    """depth=deep → 段落之后紧跟一个 `####` 新章节，其 content 行首标题压为 `###`。"""
    content = "# Docker\n\n## 容器\n\n容器是核心概念。\n"
    out = notes.apply_annotation(
        content,
        "核心概念",
        "（比喻：集装箱）",
        depth="deep",
        new_section={"title": "镜像", "content": "## 细节\n镜像是只读模板。"},
    )
    m = re.search(r'<a id="anno-([0-9a-f]{8})-src"></a>\[核心概念\]\(#anno-([0-9a-f]{8})\)', out)
    assert m is not None
    n = m.group(1)
    # 简释末尾的「↩」回跳之后，紧跟 `####` 章节
    assert f'（比喻：集装箱）[↩](#anno-{n}-src)。\n\n#### 镜像\n\n### 细节\n镜像是只读模板。' in out
    # 正文小节不出现 `##`（已压为 `###`）
    assert "\n## 细节" not in out


def test_insert_redirects_when_hit_only_in_heading() -> None:
    """anchor 仅命中标题行 → 改取该标题下**首个正文段**，段末插解释侧锚点（单向）。"""
    content = "# Docker\n\n## 容器与镜像\n\n容器是核心概念。\n"
    out = notes.apply_annotation(content, "容器与镜像", "（此处展开）")
    assert "## 容器与镜像" in out
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（此处展开）', out)
    assert m is not None
    assert "容器是核心概念。<a id=" in out
    assert "[容器与镜像](#anno-" not in out
    assert "-src" not in out


def test_insert_deep_requires_new_section() -> None:
    """depth=deep 缺 new_section → 报错。"""
    with pytest.raises(ValueError):
        notes.apply_annotation("# T\n\n正文。\n", "正文", "（x）", depth="deep")


def test_insert_annotation_writes_file_under_lock(notes_dir) -> None:
    """文件级插入函数复用写锁，落盘并返回更新后的全文。"""
    notes.create_note("Docker", "docker")
    notes.append_qa_block("docker", "什么是容器", "容器是一种轻量化技术。")
    out = notes.insert_annotation("docker", "轻量化", "（共享内核）")
    assert "[轻量化](#anno-" in out
    assert "（共享内核）[↩](#anno-" in out
    assert notes.read_note("docker") == out


# ---------------------------------------------------------------------------
# compress_headings：行首标题压 `###`，但代码围栏内除外（Plan §7）
# ---------------------------------------------------------------------------


def test_compress_headings_compresses_outside_fence_only() -> None:
    """围栏外的行首标题压为 `###`；围栏内（``` 与 ~~~）的 `#` 注释原样保留。"""
    text = "# 标题\n\n```bash\n# 注释\ndocker run x\n```\n\n## 小节\n\n~~~\n# 波浪围栏内的注释\n~~~\n"
    out = notes.compress_headings(text)
    assert "### 标题" in out
    assert "### 小节" in out
    assert "\n# 注释\n" in out  # 反引号围栏内未被压
    assert "\n# 波浪围栏内的注释\n" in out  # 波浪围栏内未被压
    assert "### 注释" not in out
    assert "### 波浪围栏内的注释" not in out
