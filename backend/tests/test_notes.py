"""T1.4 笔记文件读写单测：创建、读取、末尾追加 Q/A 区块、写锁覆盖读改写。

用 `tmp_path` 隔离，不触碰真实的 backend/data/notes/。
（T1.5 将在此文件追加「段落定位插入」用例。）
"""
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
