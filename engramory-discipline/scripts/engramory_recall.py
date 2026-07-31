#!/usr/bin/env python3
"""
engramory_recall.py — 排序召回器（省 token 的核心工具）

只读 MEMORY.md 索引，对当前任务上下文排序，吐出 top-K 命中条目的路径。
加 --print 才读取并输出命中详情文件的正文；不加则只输出索引派生的小行，
AI 据此精准懒加载，避免每轮全读所有记忆。

用法:
  python engramory_recall.py "<本次任务上下文>" [--root <dir|MEMORY.md>] [--top 5] [--print] [--type feedback]

纯标准库，跨平台。索引行格式（容错）:
  - [中文标题](memory/feedback/slug.md) · feedback · 2026-07-30 — 一句话 hook
"""
import os
import re
import sys
from datetime import datetime, timezone

TYPE_WEIGHT = {"feedback": 1.0, "user": 0.8, "project": 0.6, "reference": 0.5}
DEFAULT_TYPE_WEIGHT = 0.4


def tok(s: str):
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", s.lower())


def find_index(root: str) -> str | None:
    if root.endswith("MEMORY.md") or os.path.isfile(root):
        return root if os.path.isfile(root) else None
    cand = os.path.join(root, "MEMORY.md")
    return cand if os.path.isfile(cand) else None


LINE_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)"
    r"(?:\s*·\s*(?P<type>\w+))?"
    r"(?:\s*·\s*(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"\s*(?:—\s*(?P<hook>.*))?$"
)
MIN_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)")


def parse_index(text: str):
    rows = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            m = MIN_RE.match(line.strip())
            if not m:
                continue
            d = m.groupdict()
            d.setdefault("type", "")
            d.setdefault("date", "")
            d.setdefault("hook", "")
        else:
            d = m.groupdict()
        rows.append(d)
    return rows


def score(row, q_tokens, qtext, today):
    type_w = TYPE_WEIGHT.get((row.get("type") or "").lower(), DEFAULT_TYPE_WEIGHT)
    hay = tok(row.get("title", "") + " " + row.get("hook", "") + " " + (row.get("type") or ""))
    if not q_tokens:
        kw = 0.0
    else:
        hit = sum(1 for t in q_tokens if t in hay)
        kw = hit / len(q_tokens)
    recency = 0.0
    ds = row.get("date") or ""
    if len(ds) == 10:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days = (today - d).days
            recency = max(0.0, 1.0 - days / 365.0)
        except ValueError:
            recency = 0.0
    return type_w * 1.0 + kw * 2.0 + recency * 0.5


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python engramory_recall.py \"<任务上下文>\" [--root DIR] [--top 5] [--print] [--type feedback]")
        sys.exit(2)
    query = args[0]
    root = os.environ.get("ENGRAMORY_ROOT", os.path.expanduser("~/.engramory"))
    top = 5
    do_print = False
    type_filter = None
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--root":
            root = args[i + 1]; i += 2
        elif a == "--top":
            top = int(args[i + 1]); i += 2
        elif a == "--print":
            do_print = True; i += 1
        elif a == "--type":
            type_filter = args[i + 1].lower(); i += 2
        else:
            i += 1

    idx = find_index(root)
    if not idx:
        print(f"[recall] 未找到 MEMORY.md：{root}", file=sys.stderr)
        sys.exit(1)
    base = os.path.dirname(idx)
    today = datetime.now(timezone.utc)
    q_tokens = tok(query)

    rows = parse_index(open(idx, encoding="utf-8").read())
    # 索引行未写 · type 时，从路径 memory/<type>/... 推导，恢复类型亲和排序
    for r in rows:
        if not (r.get("type") or "").strip():
            parts = (r.get("path") or "").split("/")
            if len(parts) >= 2 and parts[0] == "memory":
                r["type"] = parts[1]
    if type_filter:
        rows = [r for r in rows if (r.get("type") or "").lower() == type_filter]
    scored = sorted(
        ((score(r, q_tokens, query, today), r) for r in rows),
        key=lambda x: x[0], reverse=True,
    )
    top_rows = scored[:top]
    print(f"# 召回 top-{top}（共 {len(rows)} 条索引） query={query!r}")
    for sc, r in top_rows:
        print(f"{sc:5.2f} | {r.get('type') or '?':9} | {r.get('path')} | {r.get('title','')} — {r.get('hook','')}")
    if do_print:
        print("\n----- 命中详情 -----")
        for _, r in top_rows:
            p = os.path.join(base, r.get("path", ""))
            if os.path.isfile(p):
                print(f"\n### {r.get('title','')} ({r.get('path')})")
                print(open(p, encoding="utf-8").read())
            else:
                print(f"\n### {r.get('title','')} — [详情缺失] {r.get('path')}")


if __name__ == "__main__":
    main()
