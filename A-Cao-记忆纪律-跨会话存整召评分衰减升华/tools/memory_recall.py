#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_recall.py — 排序召回器（省 token 的核心工具）

只读记忆索引，对当前任务上下文排序，吐 top-K 命中路径。
默认**只输出索引派生的小行**（不读正文）；加 `--print` 才读命中详情正文。
AI 据此精准懒加载，避免每轮全读所有记忆。

设计要点：
  · 支持两种 store 结构：
      单索引型  <store>/MEMORY.md           （Engramory / WorkBuddy / DSH 实际在用）
      七库型    <store>/CORE.md + */index.md （记忆纪律 v3.5 规范结构）
  · 语料回退：正文 frontmatter 有 `triggers` 字段就用它（最准）；没有就用
      `name + description` + 索引 hook 行（真实 store 多为此形态）。
  · 排序 = 类型权重 ×1.0 + 关键词命中率 ×2.0 + 新近度 ×0.5
      （与记忆纪律 skill 的 V/R 打分同源，但不依赖 V 分——V 分常未填）

用法:
  python memory_recall.py "<本次任务上下文>" [--root <store|索引文件>] [--top 5] [--print] [--type feedback]

退出码: 0=有命中  1=未找到索引  2=参数错误
"""
import os
import re
import sys
from datetime import datetime, timezone

TYPE_WEIGHT = {
    "feedback": 1.0, "user": 0.8, "project": 0.7, "reference": 0.5,
    "pitfall": 0.9, "decision": 0.9, "constraint": 0.8, "work": 0.6,
    "profile": 0.8, "detail": 0.5,
}
DEFAULT_TYPE_WEIGHT = 0.4

# 索引行： - [标题](路径) · type · 日期 — hook   （各部分都可缺，容错）
LINE_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)"
    r"(?:\s*[·|]\s*(?P<type>[A-Za-z\u4e00-\u9fff_]+))?"
    r"(?:\s*[·|]\s*(?P<date>\d{4}-\d{2}-\d{2}))?"
    r"\s*(?:[—–-]{1,2}\s*(?P<hook>.*))?$"
)
MIN_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)")

INDEX_CANDIDATES = ["MEMORY.md", "CORE.md"]
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def tok(s):
    """中英混排分词：英文/数字整词 + 中文**二元组**（bigram）。

    为什么要 bigram 而不是逐字：逐字切会把「不知道怎么办」里的
    不/知/道/怎/么/办 全当关键词，稀释命中率，让真正相关的条目排不上来。
    二元组能保住「报错」「调试」「缓存」这类有区分度的词。
    """
    s = (s or "").lower()
    words = re.findall(r"[a-z0-9]{2,}", s)
    grams = []
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            grams.append(run)
        else:
            grams += [run[i:i + 2] for i in range(len(run) - 1)]
    return set(words + grams)


def resolve_index(root):
    """返回 (索引文件路径, 是否单索引型)。"""
    if os.path.isfile(root):
        return root, os.path.basename(root).upper() == "MEMORY.MD"
    for name in INDEX_CANDIDATES:
        cand = os.path.join(root, name)
        if os.path.isfile(cand):
            return cand, name.upper() == "MEMORY.MD"
    return None, False


def collect_index_files(store, single, index_path):
    """单索引型 → [主索引]；七库型 → [主索引] + 各子库 index.md"""
    files = [index_path]
    if not single:
        for dirpath, dirnames, filenames in os.walk(store):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "tools"]
            for fn in filenames:
                if fn == "index.md":
                    p = os.path.join(dirpath, fn)
                    if p != index_path:
                        files.append(p)
    return files


def parse_index(text, index_file, store):
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        m = LINE_RE.match(line) or MIN_RE.match(line)
        if not m:
            continue
        d = m.groupdict()
        d.setdefault("type", "")
        d.setdefault("date", "")
        d.setdefault("hook", "")
        # 类型没写 → 从路径推导（memory/<type>/xx.md 或 <库名>/xx.md）
        if not (d.get("type") or "").strip():
            parts = (d.get("path") or "").replace("\\", "/").split("/")
            if len(parts) >= 2:
                d["type"] = parts[-2] if parts[0] == "memory" else parts[0]
        d["_index"] = index_file
        d["_store"] = store
        rows.append(d)
    return rows


def load_extra_corpus(row, base):
    """读正文 frontmatter，补 triggers / description 作为检索语料。缺文件则返回空。"""
    p = os.path.join(base, row.get("path", ""))
    if not os.path.isfile(p):
        return "", False
    try:
        head = open(p, encoding="utf-8", errors="ignore").read(4000)
    except OSError:
        return "", False
    m = FM_RE.match(head)
    fm = m.group(1) if m else head[:1200]
    extra = []
    for key in ("triggers", "description", "name", "title"):
        for mm in re.finditer(r"^%s\s*:\s*(.+)$" % key, fm, re.M):
            extra.append(mm.group(1))
    return " ".join(extra), True


def score(row, q_tokens, extra, today):
    type_w = TYPE_WEIGHT.get((row.get("type") or "").lower(), DEFAULT_TYPE_WEIGHT)
    hay = tok(" ".join([
        row.get("title", ""), row.get("hook", ""), row.get("type", ""), extra,
    ]))
    if not q_tokens:
        kw = 0.0
    else:
        # 长 token 更具体 → 权重更高（2 字 bigram 记 1，3 字以上英文词记 1.5）
        hit = sum((1.5 if len(t) >= 3 else 1.0) for t in q_tokens if t in hay)
        total = sum((1.5 if len(t) >= 3 else 1.0) for t in q_tokens)
        kw = hit / total if total else 0.0
    recency = 0.0
    ds = (row.get("date") or "").strip()
    if len(ds) == 10:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            recency = max(0.0, 1.0 - (today - d).days / 365.0)
        except ValueError:
            pass
    return type_w * 1.0 + kw * 2.0 + recency * 0.5, kw


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    query = args[0]
    root = os.environ.get("MEMORY_ROOT") or os.environ.get("ENGRAMORY_ROOT") or os.getcwd()
    top, do_print, type_filter = 5, False, None
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]; i += 2
        elif a == "--top" and i + 1 < len(args):
            top = int(args[i + 1]); i += 2
        elif a == "--type" and i + 1 < len(args):
            type_filter = args[i + 1].lower(); i += 2
        elif a == "--print":
            do_print = True; i += 1
        else:
            i += 1

    index_path, single = resolve_index(root)
    if not index_path:
        print(f"[recall] 未找到索引（试过 MEMORY.md / CORE.md）：{root}", file=sys.stderr)
        return 1
    store = os.path.dirname(index_path)
    today = datetime.now(timezone.utc)
    q_tokens = tok(query)

    rows = []
    for f in collect_index_files(store, single, index_path):
        try:
            rows += parse_index(open(f, encoding="utf-8", errors="ignore").read(), f, store)
        except OSError:
            continue

    # 去重（同一路径在多个索引出现）
    seen, uniq = set(), []
    for r in rows:
        key = (r.get("_index"), r.get("path"))
        if key not in seen:
            seen.add(key); uniq.append(r)
    rows = uniq

    if type_filter:
        rows = [r for r in rows if (r.get("type") or "").lower() == type_filter]

    scored = []
    for r in rows:
        extra, exists = load_extra_corpus(r, os.path.dirname(r["_index"]))
        sc, kw = score(r, q_tokens, extra, today)
        if not exists:
            sc -= 0.5          # 详情文件缺失：降权但不丢弃（organize 会报断链）
        scored.append((sc, kw, exists, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = scored[:top]

    print(f"# 召回 top-{top}（索引 {len(rows)} 条 / 库 {store}）")
    print(f"# query = {query!r}")
    for sc, kw, exists, r in hits:
        flag = "" if exists else "  [详情缺失]"
        print(f"{sc:5.2f} kw={kw:.2f} | {r.get('type') or '?':10} | {r.get('path')}{flag}")
        print(f"        {r.get('title','')} — {r.get('hook','')}")

    if do_print:
        print("\n----- 命中详情 -----")
        for _, _, exists, r in hits:
            p = os.path.join(os.path.dirname(r["_index"]), r.get("path", ""))
            print(f"\n### {r.get('title','')} ({r.get('path')})")
            if exists:
                print(open(p, encoding="utf-8", errors="ignore").read())
            else:
                print("[详情缺失] 索引指向的文件不存在——请跑 memory_organize.py 清理断链")

    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
