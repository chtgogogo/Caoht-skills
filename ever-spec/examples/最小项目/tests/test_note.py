"""R-1/R-3 的现有测试（EverSpec 30 秒样例）——有 pytest 用 pytest 跑，没有就 py tests/test_note.py。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import note


def test_add_note_writes_and_numbers():
    note.clear_notes()
    assert note.add_note("买牛奶") == 1
    assert note.add_note("取快递") == 2


def test_add_tag_dedup():
    note.clear_notes()
    nid = note.add_note("买牛奶")
    assert note.add_tag(nid, "urgent") == ["urgent"]
    assert note.add_tag(nid, "urgent") == ["urgent"]  # 重复标签去重（R-3）


if __name__ == "__main__":
    test_add_note_writes_and_numbers()
    test_add_tag_dedup()
    print("2 tests ok")
