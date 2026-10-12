"""T1.4 笔记文件读写：创建、读取、末尾追加 Q/A 区块。
T1.5 段落定位插入：按空行切段 → 源码 grep 优先（词后）→ 脱标记兜底（**原词所在行末**）。

文件布局：`data/notes/{slug}.md`（相对运行目录 backend/，见 Plan §1/§2）。
写入约定：模块级 `threading.Lock` 把「读→改→写」整段包成临界区（Plan §7
「笔记文件写锁」），保证写笔记串行化、不会互相覆盖。
"""
import re
import threading
from pathlib import Path
from uuid import uuid4

from app.config import settings

NOTES_DIR = settings.notes_dir

# 模块级文件写锁：覆盖「读文件 → 改 → 写文件」整段（Plan §7）。
_file_lock = threading.Lock()


def sanitize_slug(raw: str) -> str:
    """把（AI 生成的）主题 slug 清洗为合法形式：`[a-z0-9-]`（Plan §4.0）。

    规则：转小写、非 `[a-z0-9]` 连续字符压成一个 `-`、去掉首尾 `-`。
    清洗后为空则回退为 `"topic"`（调用方还需再做去重，见 Plan §7）。
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    return cleaned or "topic"


def note_path(slug: str) -> Path:
    """返回某主题笔记的磁盘路径。"""
    return NOTES_DIR / f"{slug}.md"


def read_note(slug: str) -> str:
    """读取笔记全文；文件不存在时抛 `FileNotFoundError`。"""
    return note_path(slug).read_text(encoding="utf-8")


def create_note(title: str, slug: str) -> None:
    """新建笔记文件（先写 `# {title}`）；`data/notes/` 不存在时自动创建。"""
    with _file_lock:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        note_path(slug).write_text(f"# {title}\n", encoding="utf-8")


def append_qa_block(slug: str, question: str, body: str) -> str:
    """在笔记末尾追加 Q/A 区块（`## {question}` + 正文），并返回追加后的全文。

    锁覆盖「读 → 改 → 写」整段（Plan §7），避免并发写互相覆盖。
    """
    with _file_lock:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        path = note_path(slug)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_content = existing + f"\n## {question}\n\n{body}\n"
        path.write_text(new_content, encoding="utf-8")
        return new_content


# ---------------------------------------------------------------------------
# T1.5 段落定位插入（Plan §4.1：定位由后端工具函数完成，优先词后、兜底行末）
# ---------------------------------------------------------------------------

# 行首标题符（`#`/`##`/…，含其后空白）；用于「脱标记匹配」与「压标题」。
_HEADING_PREFIX_RE = re.compile(r"(?m)^#{1,6}[ \t]*")

# 代码围栏起始行（``` 或 ~~~，允许最多 3 个前导空格）。
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

# 行内「链接/标记」跨度：命中即不包原词（Spec FR-9c 降级条件）。
_MARKUP_SPAN_RES = (
    re.compile(r"\[\[[^\[\]]*\]\]"),          # [[内链]]
    re.compile(r"\[[^\[\]]*\]\([^)]*\)"),     # [文本](地址)
    re.compile(r"`[^`]*`"),                    # 行内代码
    re.compile(r"\*\*[^*]*\*\*"),              # 粗体
)


class AnchorNotFoundError(Exception):
    """锚点在笔记中定位不到（前端应提示用户重新选择片段）。"""


def _is_heading(paragraph_source: str) -> bool:
    """判断一个段落是否为标题行（其首行以 `#` 开头）。"""
    return paragraph_source.split("\n", 1)[0].strip().startswith("#")


def _strip_inline_markup(text: str) -> str:
    """脱去行内标记，**仅用于定位匹配**（Plan §4.1 步骤 3）。

    去掉：行首标题符 `#`、`**`、`_`、行内反引号、`[]()`。
    """
    stripped = _HEADING_PREFIX_RE.sub("", text)
    stripped = stripped.replace("**", "").replace("_", "").replace("`", "")
    return re.sub(r"[\[\]()]", "", stripped)


def compress_headings(text: str) -> str:
    """把正文中的**行首标题一律压为 `###`**（Plan §7；正文小节最高 `###`）。

    **代码围栏（``` / ~~~）内的行原样保留**——避免把代码里的 `#` 注释（bash/dockerfile/yaml 等）
    误压为标题。
    """
    out: list[str] = []
    fence: str | None = None  # 当前所处围栏的标记字符（None = 不在围栏内）
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            out.append(line)
        elif fence is None:
            out.append(_HEADING_PREFIX_RE.sub("### ", line))
        else:
            out.append(line)
    return "\n".join(out)


def _split_paragraphs(content: str) -> list[tuple[int, int]]:
    """按空行把源码切成段落，返回每段的 `[start, end)` 字符区间（不含分隔空行）。"""
    paragraphs: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    pos = 0
    for line in content.split("\n"):
        line_start = pos
        pos += len(line) + 1  # 记录下一行起点（含本行换行符）
        if line.strip():
            if start is None:
                start = line_start
            end = line_start + len(line)
        elif start is not None:
            paragraphs.append((start, end))
            start = None
    if start is not None:
        paragraphs.append((start, end))
    return paragraphs


def _is_in_markup(ptext: str, idx: int, length: int) -> bool:
    """原词 `[idx, idx+length)` 是否已落在链接/行内标记内（Spec FR-9c 降级条件）。"""
    end = idx + length
    for rx in _MARKUP_SPAN_RES:
        for m in rx.finditer(ptext):
            if m.start() <= idx and end <= m.end():
                return True
    return False


def _upgrade_span(
    content: str, word_start: int, anchor: str, para_start: int, para_end: int
) -> tuple[int, int, str] | None:
    """原词在源码中的「可重建为 `==原词==` 强调高亮」区间（Spec FR-15 / Plan §4.1）。

    返回 `(span_start, span_end, core)`：把 `[span_start, span_end)` 换成 `core` 即得**升级后**的原词；
    `None` 表示**不可升级**（原词落在 `[...](...)`/`**...**`/跨主题 `[[slug#概念]]` 等无法安全嵌套的
    标记内），调用方按既有规则**降级为单向**。四种可重建写法（原词 `X`）：

    - `` `X` ``（行内代码，模式① 首次提及的新概念）→ `==X==`；
    - `==X==`（已是高亮）→ 原样；
    - `[[X]]`（**同主题**内链，无 slug）→ `==X==`；
    - 裸文本 `X` → `==X==`。
    """
    word_end = word_start + len(anchor)
    before1 = content[word_start - 1:word_start]
    before2 = content[word_start - 2:word_start]
    after1 = content[word_end:word_end + 1]
    after2 = content[word_end:word_end + 2]

    if before1 == "`" and after1 == "`" and before2 != "``" and after2 != "``":
        return word_start - 1, word_end + 1, f"=={anchor}=="  # `` `X` `` → `==X==`
    if before2 == "==" and after2 == "==":
        return word_start - 2, word_end + 2, f"=={anchor}=="  # 已是高亮（连 `==` 一并纳入区间）
    if before2 == "[[" and after2 == "]]":
        return word_start - 2, word_end + 2, f"=={anchor}=="  # 同主题内链 → 高亮
    if not _is_in_markup(
        content[para_start:para_end], word_start - para_start, len(anchor)
    ):
        return word_start, word_end, f"=={anchor}=="  # 裸文本 → 高亮
    return None


def _line_spans(content: str, start: int, end: int) -> list[tuple[int, int]]:
    """把 `[start, end)` 段落按行切成 `[行首, 行末)` 区间（行末不含换行符）。"""
    spans: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        nl = content.find("\n", pos, end)
        line_end = end if nl == -1 else nl
        spans.append((pos, line_end))
        if nl == -1:
            break
        pos = nl + 1
    return spans


def _line_end_at(content: str, start: int, end: int, pos: int) -> int:
    """返回段落 `[start, end)` 内包含位置 `pos` 的那一行的**行末**（用于降级命中定位）。"""
    for line_start, line_end in _line_spans(content, start, end):
        if line_start <= pos <= line_end:
            return line_end
    return end


def _locate(
    content: str, paragraphs: list[tuple[int, int]], anchor: str
) -> tuple[int, int, int, str, int, tuple[int, int] | None]:
    """定位插入点，返回 `(插入位置, 插入段 start, 插入段 end, 命中方式, 原词 start, 原词所在段)`。

    优先级（Plan §4.1）：源码命中正文段（**词后**）→ 逐**行**脱标记命中（**该行行末**）
    → 整段脱标记命中（**段末**，选区跨同一段内多行时）→ 仅命中标题行时改取该标题下
    首个正文段（其**首行行末**）；都找不到报错。

    命中方式：`"source"`（源码精确命中，词后）/ `"fallback"`（逐行脱标记，行末）
    / `"paragraph"`（整段脱标记，段末）/ `"heading"`（仅命中标题行，插入点重定向到正文段行末）。

    **原词位置**（后两项）供 Spec FR-15 高亮升级：`"source"`/`"heading"` 给出**源码精确**的
    原词 `start` 与所在段；`"fallback"`/`"paragraph"` 为 `-1`/`None`（无源码精确位置 → 不重建
    原词、降级单向，Spec FR-9c）。注意 `"heading"` 的**插入段**与**原词所在段**不同：简释插到
    标题下正文段行末，而原词在**标题行内**就地升级（T3.14）。
    """
    # 1) 源码 grep 优先：命中正文段 → 词后插入
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        idx = ptext.find(anchor)
        if idx != -1:
            return (
                start + idx + len(anchor),
                start,
                end,
                "source",
                start + idx,
                (start, end),
            )
    # 2) 脱标记兜底：先逐行（→ 行末，列表项/表格行各自成行时紧邻原词），再整段（→ 段末，跨行选区）
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        for line_start, line_end in _line_spans(content, start, end):
            if anchor in _strip_inline_markup(content[line_start:line_end]):
                return line_end, start, end, "fallback", -1, None
        if anchor in _strip_inline_markup(ptext):
            return end, start, end, "paragraph", -1, None
    # 3) 仅命中标题行 → **只**把插入点改取该标题下首个正文段（其**首行行末**）；
    #    原词仍定位在**标题行内**（源码精确）→ 照常参与高亮升级与双向跳转（Spec FR-15/FR-9c）
    for i, (start, end) in enumerate(paragraphs):
        ptext = content[start:end]
        if not _is_heading(ptext):
            continue
        idx = ptext.find(anchor)
        if idx == -1 and anchor not in _strip_inline_markup(ptext):
            continue
        for bstart, bend in paragraphs[i + 1:]:
            if not _is_heading(content[bstart:bend]):
                line_end = _line_spans(content, bstart, bend)[0][1]
                word_start = start + idx if idx != -1 else -1
                word_para = (start, end) if idx != -1 else None
                return line_end, bstart, bend, "heading", word_start, word_para
    raise AnchorNotFoundError(anchor)


def apply_annotation(
    content: str,
    anchor: str,
    insert_text: str,
    depth: str = "brief",
    new_section: dict | None = None,
    anno_id: str | None = None,
) -> str:
    """在笔记全文中定位 `anchor` 并插入解释，返回插入后的全文（纯函数、不加锁）。

    - **名词高亮升级（Spec FR-15）**：源码精确命中原词（正文段 `"source"`，或**仅命中标题行**
      的 `"heading"`——原词在**标题行内**，同属源码精确）且它可安全重建时，把原词**就地**重建为
      `==原词==`——原为行内代码 `` `原词` ``、裸文本或**同主题** `[[原词]]` 时替换其包裹标记；
      已是 `==原词==` 则原样保留；落在 `[...](...)`/`**...**`/跨主题 `[[slug#概念]]` 内时**不重建**
      （走降级）。
    - `depth="brief"`：`==原词==` 后紧跟 `<a id="anno-{n}"></a>` + `insert_text`（括号简释）；
    - `depth="deep"`：原地**只插 `insert_text`**（括号简释，不带锚点），原词包成
      `[==原词==](#anno-{n})`，并把 `<a id="anno-{n}"></a>` + `#### 新章节`（+「↩」）**追加到笔记
      文件最末尾**（`new_section.content` 的行首标题压为 `###`）——原词链接跳到底部章节、底部「↩」跳回原词。
    - **仅命中标题行（`"heading"`）**：**只把简释的插入点**改到标题下首个正文段首行行末；原词仍在
      **标题行内**就地升级/包链接，故按升级前后的**长度差平移**插入点（T3.14）。

    跳转标记（Spec FR-9c）：**只有 `deep`** 才做双向跳转——源码精确命中且可重建时，词前插来源锚点
    `<a id="anno-{n}-src">`。`brief` 的简释就插在原词旁，故**不加来源锚点、不加「↩」**（只留解释侧
    锚点供前端滚动定位高亮），避免在笔记源码里留下点了等于没跳的空链接。`deep` 中原词无法重建时
    **降级单向**（不包原词、文末章节不带「↩」）。`n = uuid4().hex[:8]`；`anno_id`
    可显式指定（供调用方拿回同一 id 定位解释，见 `insert_annotation`）。
    """
    if not anchor or not anchor.strip():
        raise AnchorNotFoundError(anchor)
    paragraphs = _split_paragraphs(content)
    insert_pos, para_start, para_end, kind, word_start, word_para = _locate(
        content, paragraphs, anchor
    )

    n = anno_id or uuid4().hex[:8]
    # 源码精确命中（正文段或标题行内）、且原词可安全重建为 `==原词==` 高亮（Spec FR-15）
    # ——只有这种情况才谈得上高亮升级与包链接。
    upgrade = (
        _upgrade_span(content, word_start, anchor, word_para[0], word_para[1])
        if word_para is not None
        else None
    )
    # **只有 `deep` 才做双向跳转**：深释章节被追加到笔记最末尾、离原词很远，回跳才有意义；
    # `brief` 的简释就插在原词旁，包链接/加「↩」都是空跳（Spec FR-9c）。
    bidirectional = depth == "deep" and upgrade is not None

    # 简释：`deep` 的锚点在文末章节，原地只留简释；`brief` 原地插解释侧锚点（供前端滚动定位高亮）
    inline = insert_text if depth == "deep" else f'<a id="anno-{n}"></a>{insert_text}'

    if upgrade is None:  # 降级单向（原词落在无法安全嵌套的标记内，或仅脱标记兜底命中）
        if kind == "source":
            # 源码命中但落在标记内时，插入点须改取该行行末（插在词后会切断 `[[…]]`/`**…**`）
            pos = _line_end_at(content, para_start, para_end, word_start)
        else:
            pos = insert_pos  # 脱标记兜底命中／标题行命中的定位点
        updated = content[:pos] + inline + content[pos:]
    else:
        span_start, span_end, core = upgrade
        # deep：词前插来源锚点 + 高亮包链接（解释侧锚点在文末章节）；brief：只把原词升级为高亮
        head = (
            f'<a id="anno-{n}-src"></a>[{core}](#anno-{n})'
            if depth == "deep"
            else core
        )
        if kind == "source":  # 原词与插入点相邻（词后）：一次替换为「升级后的原词 + 简释」
            updated = content[:span_start] + head + inline + content[span_end:]
        else:  # "heading"：原词在**标题行内**升级，简释另插到正文段行末 → 按长度差平移插入点
            shifted = content[:span_start] + head + content[span_end:]
            pos = insert_pos + len(head) - (span_end - span_start)
            updated = shifted[:pos] + inline + shifted[pos:]

    if depth != "deep":
        return updated

    if not new_section or not new_section.get("title"):
        raise ValueError("depth=deep 时必须提供 new_section{title, content}")
    body = compress_headings(new_section.get("content", "")).rstrip("\n")
    # 锚点写在 `####` 标题行**行内**（行首仍为 `#`，标题语法不被破坏），便于跳转后立即可见
    section = f'#### {new_section["title"]} <a id="anno-{n}"></a>\n\n{body}'
    if bidirectional:  # 「↩」只放在文末深释末尾（原地简释不带回跳）
        section += f'\n\n[↩](#anno-{n}-src)'
    tail = updated if updated.endswith("\n") else updated + "\n"
    return tail + "\n" + section + "\n"


def insert_annotation(
    slug: str,
    anchor: str,
    insert_text: str,
    depth: str = "brief",
    new_section: dict | None = None,
) -> tuple[str, str]:
    """在主题笔记中定位插入解释并落盘，返回 `(更新后的全文, anchor_id)`。

    `anchor_id` = 本次**解释侧锚点 id**（`anno-{n}`），供前端扩写成功后滚动定位并高亮该解释（Plan §4.1/§8）。
    复用模块级 `_file_lock`，锁覆盖「读 → 改 → 写」整段（Plan §7）。
    """
    n = uuid4().hex[:8]
    with _file_lock:
        path = note_path(slug)
        content = path.read_text(encoding="utf-8")
        updated = apply_annotation(content, anchor, insert_text, depth, new_section, anno_id=n)
        path.write_text(updated, encoding="utf-8")
        return updated, f"anno-{n}"
