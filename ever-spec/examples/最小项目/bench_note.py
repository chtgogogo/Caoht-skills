"""R-2 的测量命令：清空 200 条笔记须于 500ms 内完成，超限退 1（阈值三件套的第三件）。"""
import time

import note


def main():
    note.clear_notes()
    for i in range(200):
        note.add_note(f"压测笔记 {i}")
    t0 = time.perf_counter()
    note.clear_notes()
    ms = (time.perf_counter() - t0) * 1000
    ok = ms < 500
    print(f"clear 200 notes: {ms:.1f} ms（阈值 500ms）-> {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
