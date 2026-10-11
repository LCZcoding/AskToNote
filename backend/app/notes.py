"""T1.4 笔记文件读写：创建、读取、末尾追加 Q/A 区块。
T1.5 段落定位插入：按空行切段 → 源码 grep 优先（词后）→ 逐段脱标记兜底（段末）。

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
# T1.5 段落定位插入（Plan §4.1：定位由后端工具函数完成，优先词后、兜底段末）
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


def _locate(
    content: str, paragraphs: list[tuple[int, int]], anchor: str
) -> tuple[int, int, int, str, int]:
    """定位插入点，返回 `(插入位置, 段落 start, 段落 end, 命中方式, 原词 start)`。

    优先级（Plan §4.1）：源码命中正文段（词后）→ 脱标记命中正文段（段末）
    → 仅命中标题行时改取该标题下首个正文段（段末）；都找不到报错。

    命中方式：`"source"`（源码精确命中，词后）/ `"fallback"`（脱标记兜底，段末）
    / `"heading"`（仅命中标题行，重定向段末）。`原词 start` 仅 `"source"` 有意义，
    其余返回 `-1`（无源码精确位置 → 不包原词，降级单向，Spec FR-9c）。
    """
    # 1) 源码 grep 优先：命中正文段 → 词后插入
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        idx = ptext.find(anchor)
        if idx != -1:
            return start + idx + len(anchor), start, end, "source", start + idx
    # 2) 脱标记兜底：逐段脱去行内标记后命中正文段 → 段末插入
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        if anchor in _strip_inline_markup(ptext):
            return end, start, end, "fallback", -1
    # 3) 仅命中标题行 → 改取该标题下首个正文段（段末）
    for i, (start, end) in enumerate(paragraphs):
        ptext = content[start:end]
        if not _is_heading(ptext):
            continue
        if anchor in ptext or anchor in _strip_inline_markup(ptext):
            for bstart, bend in paragraphs[i + 1:]:
                if not _is_heading(content[bstart:bend]):
                    return bend, bstart, bend, "heading", -1
    raise AnchorNotFoundError(anchor)


def apply_annotation(
    content: str,
    anchor: str,
    insert_text: str,
    depth: str = "brief",
    new_section: dict | None = None,
) -> str:
    """在笔记全文中定位 `anchor` 并插入解释，返回插入后的全文（纯函数、不加锁）。

    - `depth="brief"`：仅在插入点插入 `insert_text`（括号简释）；
    - `depth="deep"`：插入 `insert_text`，并在目标段落之后紧跟一个 `####` 新章节
      （`new_section.content` 的行首标题压为 `###`）。

    同时写入**标注双向跳转标记**（Spec FR-9c）：原词源码精确命中且不在链接/标记内时，
    词前插来源锚点 `<a id="anno-{n}-src">`、词包成 `[原词](#anno-{n})`，解释侧插
    `<a id="anno-{n}">` 并在简释末尾追加「↩」回跳；否则**降级单向**（只插解释侧锚点）。
    `n = uuid4().hex[:8]`。
    """
    if not anchor or not anchor.strip():
        raise AnchorNotFoundError(anchor)
    paragraphs = _split_paragraphs(content)
    _insert_pos, para_start, para_end, kind, word_start = _locate(content, paragraphs, anchor)

    n = uuid4().hex[:8]
    explanation = f'<a id="anno-{n}"></a>{insert_text}'
    bidirectional = kind == "source" and not _is_in_markup(
        content[para_start:para_end], word_start - para_start, len(anchor)
    )
    if bidirectional:  # 双向：包原词 + 来源锚点 + 解释侧锚点 + 「↩」回跳
        word_end = word_start + len(anchor)
        before = (
            content[:word_start]
            + f'<a id="anno-{n}-src"></a>[{anchor}](#anno-{n})'
            + explanation
            + f'[↩](#anno-{n}-src)'
            + content[word_end:para_end]
        )
    else:  # 降级单向：只插解释侧锚点（不包原词、不加回跳），且一律插到**段末**
        # （in-markup 降级若插在词后会切断原词所在的行内标记，故与兜底一致取段末）
        before = content[:para_end] + explanation

    after = content[para_end:]
    if depth != "deep":
        return before + after

    if not new_section or not new_section.get("title"):
        raise ValueError("depth=deep 时必须提供 new_section{title, content}")
    body = compress_headings(new_section.get("content", "")).rstrip("\n")
    section = f"#### {new_section['title']}\n\n{body}"
    return before + "\n\n" + section + after


def insert_annotation(
    slug: str,
    anchor: str,
    insert_text: str,
    depth: str = "brief",
    new_section: dict | None = None,
) -> str:
    """在主题笔记中定位插入解释并落盘，返回更新后的全文。

    复用模块级 `_file_lock`，锁覆盖「读 → 改 → 写」整段（Plan §7）。
    """
    with _file_lock:
        path = note_path(slug)
        content = path.read_text(encoding="utf-8")
        updated = apply_annotation(content, anchor, insert_text, depth, new_section)
        path.write_text(updated, encoding="utf-8")
        return updated
