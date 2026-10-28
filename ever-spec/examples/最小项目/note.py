"""快记 note —— EverSpec 30 秒样例的被测模块（零依赖，Python 3 标准库）。"""
import json
import time
from pathlib import Path

STORE = Path(__file__).with_name("store.json")


def _load():
    if STORE.is_file():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return []


def _save(notes):
    STORE.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def add_note(content, now=None):
    """新增一条笔记：写入 store.json 并返回编号（自 1 起）。R-1。"""
    notes = _load()
    item = {"id": len(notes) + 1, "content": content,
            "ts": now or time.strftime("%Y-%m-%dT%H:%M:%S")}
    notes.append(item)
    _save(notes)
    return item["id"]


def add_tag(note_id, tag):
    """给笔记追加标签，同一标签只出现一次；返回该条标签列表。R-3。"""
    notes = _load()
    tags = notes[note_id - 1].setdefault("tags", [])
    if tag not in tags:
        tags.append(tag)
    _save(notes)
    return tags


def clear_notes():
    """清空全部笔记，返回清除条数。R-2。"""
    n = len(_load())
    _save([])
    return n
