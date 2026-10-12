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
    """源码精确命中 → brief：原词**就地升级为 `==高亮==`**（Spec FR-15）+ 解释侧锚点 + 简释，**不做跳转**。"""
    content = "# Docker 入门\n\n## 什么是容器\n\n容器是一种轻量化的虚拟化技术。\n"
    out = notes.apply_annotation(content, "轻量化", "（即共享宿主内核）")
    m = re.search(
        r'==轻量化==<a id="anno-([0-9a-f]{8})"></a>（即共享宿主内核）的虚拟化技术。', out
    )
    assert m is not None
    assert "容器是一种==轻量化==" in out
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[轻量化](#anno-" not in out
    assert "容器是一种" in out


def test_insert_at_line_end_when_anchor_crosses_inline_markup() -> None:
    """anchor 跨行内标记（源码搜不到）→ 兜底命中 → **行末**插解释，且**降级单向**（不重建高亮/无来源锚点/回跳）。"""
    content = "# Docker\n\n## 容器与镜像\n\n**容器**和**镜像**是核心。\n"
    out = notes.apply_annotation(content, "容器和镜像", "（本节要弄清的一对概念）")
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（本节要弄清的一对概念）', out)
    assert m is not None
    assert "**容器**和**镜像**是核心。" in out
    assert "==" not in out
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[容器和镜像](#anno-" not in out


def test_fallback_in_markup_inserts_at_anchor_line_end() -> None:
    """原词在**跨主题** `[[slug#概念]]` 内（不可重建）→ 降级单向：解释插到**原词所在行**行末（非整个列表末尾）。"""
    content = (
        "# T\n\n## Q\n\n"
        "1. 后端做三个 [[api#RESTful API]] + 一张表。\n"
        "2. 然后把它俩连起来。\n"
        "3. 再决定往哪边深入。\n"
    )
    out = notes.apply_annotation(content, "RESTful API", "（一种接口风格）")
    assert '1. 后端做三个 [[api#RESTful API]] + 一张表。<a id="anno-' in out
    assert out.endswith("（一种接口风格）\n2. 然后把它俩连起来。\n3. 再决定往哪边深入。\n")
    assert "[[api#RESTful API]]" in out  # 跨主题内链语法未被截断
    assert "==" not in out
    assert "-src" not in out and "[↩]" not in out


def test_fallback_with_newline_anchor_inserts_at_paragraph_end() -> None:
    """选区跨行（anchor 含换行）→ 逐行都不命中 → 退回整段脱标记匹配，插**段末**。"""
    content = "# T\n\n## Q\n\n**容器**和\n**镜像**是核心。\n\n后一段。\n"
    out = notes.apply_annotation(content, "容器和\n镜像", "（一对概念）")
    assert out.endswith("（一对概念）\n\n后一段。\n")
    assert "==" not in out


def test_source_hit_inside_markup_degrades_to_one_way() -> None:
    """原词源码可命中、但已落在 `**...**` 标记内 → **降级单向**（不重建高亮、只插解释侧锚点、不回跳）。"""
    content = "# T\n\n## Q\n\n**容器**是核心。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（轻量虚拟化）', out)
    assert m is not None
    assert out.count("**") == 2  # 原有粗体标记未被破坏
    assert "==" not in out
    assert "-src" not in out
    assert "[↩]" not in out
    assert "[容器](#anno-" not in out


def test_source_hit_inside_wikilink_upgrades_to_highlight() -> None:
    """**同主题** `[[原词]]`（无 slug）→ 就地重建为 `==原词==`（Spec FR-15），不再是内链、也不降级。"""
    content = "# T\n\n## Q\n\n参见 [[容器]] 概念。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    assert "[[容器]]" not in out
    assert '参见 ==容器==<a id="anno-' in out
    assert "（轻量虚拟化） 概念。" in out
    assert "-src" not in out and "[↩]" not in out


def test_source_hit_inside_cross_topic_wikilink_degrades_to_one_way() -> None:
    """**跨主题** `[[slug#概念]]` → 不重建（保留跨主题信息）× 降级单向（只插解释侧锚点、插原词所在行末）。"""
    content = "# T\n\n## Q\n\n参见 [[docker#容器]] 概念。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    assert "[[docker#容器]]" in out
    assert "==" not in out
    assert "-src" not in out and "[↩]" not in out
    assert "[容器](#anno-" not in out
    assert '<a id="anno-' in out


def test_brief_upgrades_inline_code_to_highlight() -> None:
    """模式① 首次提及的名词是行内代码 → 标注扩写后就地升级为 `==高亮==`（Spec FR-15）。"""
    content = "# T\n\n## Q\n\n`容器`是一种轻量化技术。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    assert "`容器`" not in out
    assert '==容器==<a id="anno-' in out
    assert "（轻量虚拟化）是一种轻量化技术。" in out


def test_deep_upgrades_inline_code_and_keeps_jump() -> None:
    """深入了解：行内代码原词 → `[==原词==](#anno-{n})`（可点跳文末）+ 词前来源锚点 + 文末 `####` 章节 + `[↩]`。"""
    content = "# T\n\n## Q\n\n`容器`是核心。\n"
    out = notes.apply_annotation(
        content,
        "容器",
        "（比喻：集装箱）",
        depth="deep",
        new_section={"title": "容器要义", "content": "详解。"},
    )
    m = re.search(
        r'<a id="anno-([0-9a-f]{8})-src"></a>\[==容器==\]\(#anno-([0-9a-f]{8})\)（比喻：集装箱）',
        out,
    )
    assert m is not None
    n = m.group(1)
    assert "`容器`" not in out
    assert out.endswith(
        f'#### 容器要义 <a id="anno-{n}"></a>\n\n详解。\n\n[↩](#anno-{n}-src)\n'
    )


def test_already_highlighted_not_double_wrapped() -> None:
    """已是 `==原词==` → 不重复包（避免出现 `====原词====`）。"""
    content = "# T\n\n## Q\n\n参见 ==容器== 概念。\n"
    out = notes.apply_annotation(content, "容器", "（轻量虚拟化）")
    assert "====" not in out
    assert '参见 ==容器==<a id="anno-' in out


def test_insert_picks_first_occurrence() -> None:
    """anchor 多次出现 → 取**首次**（只插一处）。"""
    content = "# T\n\n## Q\n\nalpha beta gamma。\n\nlater alpha beta again。\n"
    out = notes.apply_annotation(content, "alpha beta", "（首次）")
    assert out.count("（首次）") == 1
    m = re.search(r'==alpha beta==<a id="anno-([0-9a-f]{8})"></a>（首次）', out)
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


def test_insert_deep_appends_section_at_file_end() -> None:
    """depth=deep → 原地**只留括号简释**（无锚点/回跳）；`####` 新章节（末尾带「↩」）追加到**文件最末尾**。"""
    content = "# Docker\n\n## 容器\n\n容器是核心概念。\n\n## 镜像\n\n镜像是只读模板。\n"
    out = notes.apply_annotation(
        content,
        "核心概念",
        "（比喻：集装箱）",
        depth="deep",
        new_section={"title": "镜像深入", "content": "## 细节\n镜像是只读模板。"},
    )
    m = re.search(
        r'<a id="anno-([0-9a-f]{8})-src"></a>\[==核心概念==\]\(#anno-([0-9a-f]{8})\)（比喻：集装箱）', out
    )
    assert m is not None
    n = m.group(1)
    # 原地简释：不带解释侧锚点、不带「↩」回跳
    assert f'<a id="anno-{n}"></a>（比喻：集装箱）' not in out
    assert "（比喻：集装箱）[↩]" not in out
    # `####` 章节追加到文件末尾，锚点在标题行内、末尾带「↩」回跳
    assert out.endswith(
        f'#### 镜像深入 <a id="anno-{n}"></a>\n\n### 细节\n镜像是只读模板。\n\n[↩](#anno-{n}-src)\n'
    )
    assert "\n## 细节" not in out  # content 行首 `##` 已压为 `###`


def test_insert_redirects_when_hit_only_in_heading() -> None:
    """anchor 仅命中标题行 → 简释仍插到该标题下**首个正文段首行行末**，但**标题行内的原词照常升级为 `==高亮==`**（FR-15）。"""
    content = "# Docker\n\n## `容器`与镜像\n\n这是核心概念。\n"
    out = notes.apply_annotation(content, "容器", "（此处展开）")
    assert "## ==容器==与镜像" in out  # 标题行内原词就地升级（不再是行内代码）
    assert "`容器`" not in out
    m = re.search(r'<a id="anno-([0-9a-f]{8})"></a>（此处展开）', out)
    assert m is not None
    assert "这是核心概念。<a id=" in out  # 简释仍在标题下正文段行末
    assert "[容器](#anno-" not in out  # brief 不包链接
    assert "-src" not in out
    assert "[↩]" not in out


def test_deep_heading_hit_upgrades_and_keeps_bidirectional_jump() -> None:
    """仅命中标题行的**深入了解**：标题行内原词包成 `[==原词==](#anno-n)` + 词前来源锚点；简释落正文段行末（无锚点）；文末 `####` 章节带「↩」（FR-9c/FR-15，T3.14）。"""
    content = "# Docker\n\n## `容器`与镜像\n\n这是核心概念。\n"
    out = notes.apply_annotation(
        content,
        "容器",
        "（比喻：集装箱）",
        depth="deep",
        new_section={"title": "容器要义", "content": "详解。"},
    )
    m = re.search(
        r'## <a id="anno-([0-9a-f]{8})-src"></a>\[==容器==\]\(#anno-\1\)与镜像', out
    )
    assert m is not None
    n = m.group(1)
    assert "`容器`" not in out  # 行内代码已升级为高亮
    assert "这是核心概念。（比喻：集装箱）" in out  # 简释落在正文段行末
    assert f'<a id="anno-{n}"></a>（比喻：集装箱）' not in out  # deep 原地不带解释侧锚点
    assert out.endswith(
        f'#### 容器要义 <a id="anno-{n}"></a>\n\n详解。\n\n[↩](#anno-{n}-src)\n'
    )


def test_insert_deep_requires_new_section() -> None:
    """depth=deep 缺 new_section → 报错。"""
    with pytest.raises(ValueError):
        notes.apply_annotation("# T\n\n正文。\n", "正文", "（x）", depth="deep")


def test_insert_annotation_writes_file_under_lock(notes_dir) -> None:
    """文件级插入函数复用写锁，落盘并返回 `(全文, 解释侧 anchor_id)`。"""
    notes.create_note("Docker", "docker")
    notes.append_qa_block("docker", "什么是容器", "容器是一种轻量化技术。")
    out, anchor_id = notes.insert_annotation("docker", "轻量化", "（共享内核）")
    assert anchor_id.startswith("anno-")
    assert f'==轻量化==<a id="{anchor_id}"></a>（共享内核）' in out
    assert "[↩]" not in out and "-src" not in out
    assert notes.read_note("docker") == out


def test_apply_annotation_uses_given_anno_id() -> None:
    """显式传入 `anno_id` → brief 锚点用该 id（供调用方拿回同一 id 定位解释）。"""
    out = notes.apply_annotation(
        "# T\n\n## Q\n\n容器是核心。\n", "核心", "（x）", anno_id="deadbeef"
    )
    assert '==核心==<a id="anno-deadbeef"></a>（x）' in out
    assert "-src" not in out and "[↩]" not in out


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
