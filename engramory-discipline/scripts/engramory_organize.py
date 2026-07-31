#!/usr/bin/env python3
"""
engramory_organize.py — 记忆整理器（理得清，保召回质量）

扫描 MEMORY.md 索引与详情文件，产出整理计划；默认 dry-run（只报告不改动）。
加 --apply 才执行：归档冷记忆、移除断链、合并完全重复行（同路径）。

整理动作遵循纪律：只移不删（归档到 archive/），绝不物理删除，防误伤。

用法:
  python engramory_organize.py [--root <dir|MEMORY.md>] [--older-than 90] [--apply]

纯标准库，跨平台。
"""
import os
import re
import shutil
import sys
from datetime import datetime, timezone

LINE_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)"
    r"(?:\s*·\s*(?P<type>\w+))?"
    r"(?:\s*·\s*(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"\s*(?:—\s*(?P<hook>.*))?$"
)
MIN_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)")


def find_index(root):
    if root.endswith("MEMORY.md") or os.path.isfile(root):
        return root if os.path.isfile(root) else None
    cand = os.path.join(root, "MEMORY.md")
    return cand if os.path.isfile(cand) else None


def parse_index(text):
    rows, raw = [], []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            m = MIN_RE.match(line.strip())
            if not m:
                raw.append(line)
                continue
            d = m.groupdict(); d.setdefault("type", ""); d.setdefault("date", ""); d.setdefault("hook", "")
        else:
            d = m.groupdict()
        rows.append(d); raw.append(line)
    return rows, raw


def main():
    args = sys.argv[1:]
    root = os.environ.get("ENGRAMORY_ROOT", os.path.expanduser("~/.engramory"))
    older_than = 90
    apply = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--root":
            root = args[i + 1]; i += 2
        elif a == "--older-than":
            older_than = int(args[i + 1]); i += 2
        elif a == "--apply":
            apply = True; i += 1
        else:
            i += 1

    idx = find_index(root)
    if not idx:
        print(f"[organize] 未找到 MEMORY.md：{root}", file=sys.stderr)
        sys.exit(1)
    base = os.path.dirname(idx)
    archive_dir = os.path.join(base, "archive")
    today = datetime.now(timezone.utc)
    text = open(idx, encoding="utf-8").read()
    rows, raw = parse_index(text)

    broken, stale, dup_paths, seen_paths = [], [], [], set()
    plan = []
    for r in rows:
        p = r.get("path", "")
        full = os.path.join(base, p)
        if not os.path.isfile(full):
            broken.append(r); continue
        if p in seen_paths:
            dup_paths.append(r); continue
        seen_paths.add(p)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(full), tz=timezone.utc)
            if (today - mtime).days > older_than:
                stale.append(r)
        except OSError:
            pass

    print(f"# 整理扫描（root={base}, older_than={older_than}d, apply={apply}）")
    print(f"索引条目: {len(rows)} | 断链: {len(broken)} | 完全重复: {len(dup_paths)} | 冷记忆(> {older_than}d): {len(stale)}")

    if broken:
        print("\n[断链] 索引指向不存在的文件，将移除指针行:")
        for r in broken:
            print(f"  - {r.get('path')}  ({r.get('title','')})")
    if dup_paths:
        print("\n[完全重复] 同路径出现多次，将保留首条、移除其余:")
        for r in dup_paths:
            print(f"  - {r.get('path')}  ({r.get('title','')})")
    if stale:
        print(f"\n[冷记忆] 将归档到 archive/（不删除）:")
        for r in stale:
            print(f"  - {r.get('path')}  ({r.get('title','')})")

    if not apply:
        print("\n(dry-run) 未做任何改动。加 --apply 执行。")
        sys.exit(0)

    # 执行：归档冷记忆、移除断链与完全重复行
    os.makedirs(archive_dir, exist_ok=True)
    keep_paths, removed = set(), []
    new_lines = []
    for line in raw:
        m = MIN_RE.match(line.strip())
        if not m:
            new_lines.append(line); continue
        path = m.groupdict().get("path", "")
        full = os.path.join(base, path)
        # 断链
        if not os.path.isfile(full):
            removed.append(f"断链 {path}"); continue
        # 完全重复
        if path in keep_paths:
            removed.append(f"重复 {path}"); continue
        # 冷记忆 → 归档
        if any(s.get("path") == path for s in stale):
            dst = os.path.join(archive_dir, os.path.basename(path))
            shutil.move(full, dst)
            newpath = os.path.join("archive", os.path.basename(path))
            new_lines.append(line.replace(f"]({path})", f"]({newpath})"))
            removed.append(f"归档 {path} -> {newpath}")
            keep_paths.add(path)
            continue
        keep_paths.add(path)
        new_lines.append(line)

    with open(idx, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines).rstrip() + "\n")
    print("\n[已执行] 改动:")
    for x in removed:
        print(f"  - {x}")
    print(f"整理后索引条目: {len(keep_paths)}")


if __name__ == "__main__":
    main()
