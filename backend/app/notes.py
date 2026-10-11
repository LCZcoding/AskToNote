"""T1.4 笔记文件读写：创建、读取、末尾追加 Q/A 区块。
T1.5 段落定位插入：按空行切段 → 源码 grep 优先（词后）→ 逐段脱标记兜底（段末）。

文件布局：`data/notes/{slug}.md`（相对运行目录 backend/，见 Plan §1/§2）。
写入约定：模块级 `threading.Lock` 把「读→改→写」整段包成临界区（Plan §7
「笔记文件写锁」），保证写笔记串行化、不会互相覆盖。
"""
import re
import threading
from pathlib import Path

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
    """把正文中的**行首标题一律压为 `###`**（Plan §7；正文小节最高 `###`）。"""
    return _HEADING_PREFIX_RE.sub("### ", text)


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


def _locate(
    content: str, paragraphs: list[tuple[int, int]], anchor: str
) -> tuple[int, int, int]:
    """定位插入点，返回 `(插入位置, 目标段落 start, 目标段落 end)`。

    优先级（Plan §4.1）：源码命中正文段（词后）→ 脱标记命中正文段（段末）
    → 仅命中标题行时改取该标题下首个正文段（段末）；都找不到报错。
    """
    # 1) 源码 grep 优先：命中正文段 → 词后插入
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        idx = ptext.find(anchor)
        if idx != -1:
            return start + idx + len(anchor), start, end
    # 2) 脱标记兜底：逐段脱去行内标记后命中正文段 → 段末插入
    for start, end in paragraphs:
        ptext = content[start:end]
        if _is_heading(ptext):
            continue
        if anchor in _strip_inline_markup(ptext):
            return end, start, end
    # 3) 仅命中标题行 → 改取该标题下首个正文段（段末）
    for i, (start, end) in enumerate(paragraphs):
        ptext = content[start:end]
        if not _is_heading(ptext):
            continue
        if anchor in ptext or anchor in _strip_inline_markup(ptext):
            for bstart, bend in paragraphs[i + 1:]:
                if not _is_heading(content[bstart:bend]):
                    return bend, bstart, bend
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
    """
    if not anchor or not anchor.strip():
        raise AnchorNotFoundError(anchor)
    paragraphs = _split_paragraphs(content)
    insert_pos, _para_start, para_end = _locate(content, paragraphs, anchor)

    before = content[:insert_pos] + insert_text + content[insert_pos:para_end]
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
