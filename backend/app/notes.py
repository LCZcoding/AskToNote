"""T1.4 笔记文件读写：创建、读取、末尾追加 Q/A 区块。

文件布局：`data/notes/{slug}.md`（相对运行目录 backend/，见 Plan §1/§2）。
写入约定：模块级 `threading.Lock` 把「读→改→写」整段包成临界区（Plan §7
「笔记文件写锁」），保证写笔记串行化、不会互相覆盖。
"""
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
