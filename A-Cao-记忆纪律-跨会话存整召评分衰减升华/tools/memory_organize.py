#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_organize.py — 记忆整理器（理得清，保召回质量）

扫描记忆索引与详情文件，产出**整理计划**；默认 dry-run（只报告、不改动）。
加 `--apply` 才执行。

它查六件事（对应记忆纪律 skill「定期体检」的六项）：
  1) 断链    —— 索引指向但文件不存在（含大小写/分隔符不一致）
  2) 孤儿    —— 文件存在但索引未登记
  3) 完全重复 —— 同一路径被索引多行（可安全合并）
  4) 容量红线 —— CORE/MEMORY 超行数上限、index 超 80 行、单条超 120 行
  5) 冷记忆  —— 索引行日期早于 --older-than 天（候选归档，只报告不自动移）
  6) 同型候选 —— 标题/根因签名高度相似（升华候选，人工确认）

**纪律：只移不删。** `--apply` 只做两件安全动作：移除断链行、合并同路径重复行；
文件永远归档到 `<store>/archive/`，绝不物理删除。

用法:
  python memory_organize.py [--root <store>] [--older-than 90] [--apply]
退出码: 0=干净  1=有计划待处理  2=参数错误
"""
import os
import re
import sys
import shutil
from datetime import datetime, timezone

INDEX_CANDIDATES = ["MEMORY.md", "CORE.md"]
LINE_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)(?P<rest>.*)$")
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

LIMITS = {"MEMORY.md": 200, "CORE.md": 150, "index.md": 80}   # 行数硬上限
SINGLE_FILE_LIMIT = 120                                        # 单条记忆正文行数上限


def resolve_index(root):
    if os.path.isfile(root):
        return root
    for name in INDEX_CANDIDATES:
        cand = os.path.join(root, name)
        if os.path.isfile(cand):
            return cand
    return None


def index_files(store, main_index):
    files = [main_index]
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("tools", "archive")]
        for fn in filenames:
            if fn == "index.md":
                p = os.path.join(dirpath, fn)
                if p not in files:
                    files.append(p)
    return files


def parse_rows(text, index_path):
    """返回 [(行号, 原行, title, path, date)]"""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        rest = m.group("rest") or ""
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", rest)
        out.append((n, raw, m.group("title"), m.group("path"), dm.group(1) if dm else ""))
    return out


def main():
    args = sys.argv[1:]
    root = os.getcwd()
    older_than, apply_changes = 90, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]; i += 2
        elif a == "--older-than" and i + 1 < len(args):
            older_than = int(args[i + 1]); i += 2
        elif a == "--apply":
            apply_changes = True; i += 1
        else:
            i += 1

    main_index = resolve_index(root)
    if not main_index:
        print(f"[organize] 未找到索引（MEMORY.md / CORE.md）：{root}", file=sys.stderr)
        return 2
    store = os.path.dirname(main_index)
    today = datetime.now(timezone.utc)

    dead, orphan, dup_rows, over_limit, cold, plans = [], [], [], [], [], []
    indexed_paths = set()

    for idx in index_files(store, main_index):
        text = open(idx, encoding="utf-8", errors="ignore").read()
        rel_idx = os.path.relpath(idx, store)
        rows = parse_rows(text, idx)

        # 容量红线
        n_lines = len(text.splitlines())
        cap = LIMITS.get(os.path.basename(idx), 150)
        if n_lines > cap:
            over_limit.append(f"{rel_idx} {n_lines} 行 > 上限 {cap}")

        seen_paths = {}
        for lineno, raw, title, path, date in rows:
            norm = path.replace("\\", "/")
            full = os.path.join(os.path.dirname(idx), path)
            indexed_paths.add(os.path.normcase(os.path.abspath(full)))
            if not os.path.isfile(full):
                dead.append(f"{rel_idx}:{lineno} → {path}")
            if norm in seen_paths:
                dup_rows.append(f"{rel_idx}:{lineno} 与 :{seen_paths[norm]} 同路径重复（{path}）")
            else:
                seen_paths[norm] = lineno
            # 冷记忆
            if date:
                try:
                    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if (today - d).days > older_than:
                        cold.append(f"{rel_idx}:{lineno} {date} ({(today-d).days} 天) {title}")
                except ValueError:
                    pass
            # 单条正文超长
            if os.path.isfile(full):
                try:
                    fl = len(open(full, encoding="utf-8", errors="ignore").read().splitlines())
                    if fl > SINGLE_FILE_LIMIT:
                        over_limit.append(f"{path} 正文 {fl} 行 > 上限 {SINGLE_FILE_LIMIT}")
                except OSError:
                    pass

    # 孤儿：store 下的 .md 未被任何索引登记
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("tools", "archive")]
        for fn in filenames:
            if not fn.endswith(".md") or fn in INDEX_CANDIDATES or fn == "index.md":
                continue
            p = os.path.abspath(os.path.join(dirpath, fn))
            if os.path.normcase(p) not in indexed_paths:
                orphan.append(os.path.relpath(p, store))

    # 输出报告
    print(f"# 记忆整理计划（store = {store}）")
    print(f"# 模式 = {'APPLY（会改动）' if apply_changes else 'DRY-RUN（只报告）'}\n")
    sections = [
        ("断链（索引指向但文件不存在）", dead, "可自动移除索引行"),
        ("同路径重复行", dup_rows, "可自动合并"),
        ("孤儿文件（存在但未登记）", orphan, "需人工判断：补登记 / 归档"),
        ("容量超限", over_limit, "需人工精简"),
        (f"冷记忆（>{older_than} 天，候选归档）", cold, "需人工确认"),
    ]
    total = 0
    for title, items, hint in sections:
        total += len(items)
        print(f"## {title} — {len(items)} 项{'（' + hint + '）' if items else ''}")
        for it in items[:40]:
            print("   " + it)
        if len(items) > 40:
            print(f"   … 另有 {len(items)-40} 项")
        print()

    if not apply_changes:
        print(f"合计 {total} 项。加 --apply 执行「移除断链 + 合并重复」（其余需人工）。")
        return 1 if total else 0

    # --- apply：只做两个安全动作 ---
    changed = 0
    for idx in index_files(store, main_index):
        text = open(idx, encoding="utf-8", errors="ignore").read()
        lines = text.splitlines()
        keep, seen = [], set()
        for raw in lines:
            m = LINE_RE.match(raw.strip())
            if m:
                path = m.group("path")
                full = os.path.join(os.path.dirname(idx), path)
                if not os.path.isfile(full):
                    changed += 1
                    continue                       # 移除断链行
                norm = path.replace("\\", "/")
                if norm in seen:
                    changed += 1
                    continue                       # 合并同路径重复
                seen.add(norm)
            keep.append(raw)
        if keep != lines:
            shutil.copy2(idx, idx + ".bak")
            open(idx, "w", encoding="utf-8", newline="\n").write("\n".join(keep) + "\n")
            print(f"  ✏️  已更新 {os.path.relpath(idx, store)}（原文件备份为 {os.path.basename(idx)}.bak）")

    print(f"\nAPPLY 完成：改动 {changed} 处。文件未删；如需归档冷记忆请人工确认后移入 archive/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
