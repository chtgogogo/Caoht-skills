#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_recall.py — 排序召回器（省 token 的核心工具）

只读记忆索引，对当前任务上下文排序，吐 top-K 命中路径。
默认**只输出索引派生的小行**（不读正文）；加 `--print` 才读命中详情正文。
AI 据此精准懒加载，避免每轮全读所有记忆。

设计要点：
  · 支持两种 store 结构，**按结构判型**（子库里有 index.md 即七库型），不看引导卡文件名：
      单索引型  <store>/MEMORY.md           （Engramory / 某宿主 / DSH 实际在用）
      七库型    <store>/CORE.md + */index.md （记忆纪律 v3.5 规范结构）
    → `--root` 直接传 store 根目录即可，无需绕到 CORE.md（旧版按文件名判型，
      七库型会被误判成单索引型而恒 0 命中）。
  · 语料回退：正文 frontmatter 有 `triggers` 字段就用它（最准）；没有就用
      `name + description` + 索引 hook 行（真实 store 多为此形态）。
  · 排序 = 类型权重 ×1.0 + 关键词命中率 ×2.0 + 新近度 ×0.5
      （与记忆纪律 skill 的 V/R 打分同源，但不依赖 V 分——V 分常未填）

用法:
  python memory_recall.py "<本次任务上下文>" [--root <store 根|索引文件>] [--top 5] [--print] [--type feedback]

退出码: 0=有命中  1=未找到索引或零命中  2=参数错误
"""
import os
import re
import sys
from datetime import datetime, timezone

# GBK 控制台防护：中文/emoji 输出在 Windows GBK 终端下乱码或崩溃（同型坑第2次，Codex/DSH 实测 2026-09-27，规则源统一修复）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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
MIN_RE = re.compile(r"^[\s|*\-]*\[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)")
# ID 表体索引行： | pitfall-3 | 触发词… | 一句话结论… | 45 | active |
# 真实 store（某宿主 125 条）多用此体：首格是记忆 ID 而非 markdown 链接，
# 不认则整库召不回来（→pit-001）。ID 必须"字母前缀+数字结尾"，据此自动跳过表头与分隔行。
IDROW_RE = re.compile(r"^\s*\|+\s*(?P<id>[A-Za-z][A-Za-z0-9]*[-_]?\d{1,4})\s*\|(?P<cells>.*)$")

# 引导卡候选：CORE.md 优先（七库型规范把路由表放 CORE），MEMORY.md 兼容单索引型与旧 store
INDEX_CANDIDATES = ["CORE.md", "MEMORY.md"]
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


def find_store(start):
    """从给定路径向上找 store 根（含 CORE.md / MEMORY.md 的最近目录）。"""
    d = start if os.path.isdir(start) else os.path.dirname(os.path.abspath(start))
    for _ in range(8):
        if any(os.path.isfile(os.path.join(d, n)) for n in INDEX_CANDIDATES):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def sub_index_files(store):
    """store 各子库的 index.md（跳过 store 根本身、隐藏目录与 tools）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "tools"]
        if os.path.abspath(dirpath) == os.path.abspath(store):
            continue
        if "index.md" in filenames:
            out.append(os.path.join(dirpath, "index.md"))
    return out


def resolve_index(root):
    """返回 (store 根, 主索引路径, 是否单索引型)。

    判型看**结构**（子库里有没有 index.md），不看引导卡的文件名：规范 §0.3 要求
    引导卡就叫 MEMORY.md，按文件名判型会把七库型 store 误判成单索引型 → 只读引导卡
    → 恒 0 命中（Qoder 2026-09-30 实测，pit-001）。
    """
    store = find_store(root)
    if not store:
        return None, None, True
    if os.path.isfile(root):
        index_path = root
        store = os.path.dirname(os.path.abspath(root))
    else:
        index_path = None
        for name in INDEX_CANDIDATES:
            cand = os.path.join(store, name)
            if os.path.isfile(cand):
                index_path = cand
                break
    return store, index_path, not sub_index_files(store)


def collect_index_files(store, single, index_path):
    """单索引型 → [主索引]；七库型 → [主索引] + 各子库 index.md"""
    files = [index_path]
    if not single:
        for p in sub_index_files(store):
            if p != index_path:
                files.append(p)
    return files


def parse_index(text, index_file, store):
    rows = []
    idx_dir = os.path.dirname(os.path.abspath(index_file))
    for raw in text.splitlines():
        line = raw.strip()
        m = LINE_RE.match(line) or MIN_RE.match(line)
        if m:
            d = m.groupdict()
            d.setdefault("type", "")
            d.setdefault("date", "")
            d.setdefault("hook", "")
            # 类型没写 → 从路径推导（memory/<type>/xx.md 或 <库名>/xx.md）
            if not (d.get("type") or "").strip():
                parts = (d.get("path") or "").replace("\\", "/").split("/")
                if len(parts) >= 2:
                    d["type"] = parts[-2] if parts[0] == "memory" else parts[0]
        else:
            m2 = IDROW_RE.match(line)
            if not m2:
                continue
            path = m2.group("id") + ".md"
            cells = [c.strip() for c in m2.group("cells").split("|") if c.strip()]
            # 该 ID 在同目录没有对应正文 → 不是索引行（表头/正文里的散落表格），跳过
            if not cells or not os.path.isfile(os.path.join(idx_dir, path)):
                continue
            date = ""
            for c in cells:
                dm = re.search(r"\d{4}-\d{2}-\d{2}", c)
                if dm:
                    date = dm.group(0)
                    break
            d = {"title": m2.group("id"), "path": path,
                 "type": os.path.basename(idx_dir), "date": date,
                 "hook": " ".join(cells)}
        d["_index"] = index_file
        d["_store"] = store
        rows.append(d)
    return rows


def load_extra_corpus(row, base):
    """读正文 frontmatter，补 triggers / description 作为检索语料，并取 created 作日期回填。

    返回 (语料附加串, 正文是否存在, created 日期)。
    ID 表体索引行没有日期列，新鲜度只能从正文 frontmatter 拿。
    """
    p = os.path.join(base, row.get("path", ""))
    if not os.path.isfile(p):
        return "", False, ""
    try:
        head = open(p, encoding="utf-8", errors="ignore").read(4000)
    except OSError:
        return "", False, ""
    m = FM_RE.match(head)
    fm = m.group(1) if m else head[:1200]
    extra = []
    for key in ("triggers", "description", "name", "title"):
        for mm in re.finditer(r"^%s\s*:\s*(.+)$" % key, fm, re.M):
            extra.append(mm.group(1))
    created = ""
    for mm in re.finditer(r"^created\s*:\s*(.+)$", fm, re.M):
        dm = re.search(r"\d{4}-\d{2}-\d{2}", mm.group(1))
        if dm:
            created = dm.group(0)
        break
    return " ".join(extra), True, created


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

    store, index_path, single = resolve_index(root)
    if not index_path:
        print(f"[recall] 未找到索引（试过 CORE.md / MEMORY.md，并向上回溯 store 根）：{root}", file=sys.stderr)
        return 1
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
        extra, exists, created = load_extra_corpus(r, os.path.dirname(r["_index"]))
        if not (r.get("date") or "").strip():
            r["date"] = created          # 索引行无日期 → 用正文 created 参与新鲜度计算
        sc, kw = score(r, q_tokens, extra, today)
        if not exists:
            sc -= 0.5          # 详情文件缺失：降权但不丢弃（organize 会报断链）
        scored.append((sc, kw, exists, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = scored[:top]

    print(f"# 召回 top-{top}（索引 {len(rows)} 条 / 库 {store} / {'单索引型' if single else '七库型'}）")
    print(f"# query = {query!r}")
    if not rows:
        print("[recall] 索引 0 条：确认 index.md 行格式为 "
              "`- [标题](文件.md) · 类型 — 钩子 (YYYY-MM-DD)`（表格体 organize 会误报孤儿，见 pit-001）",
              file=sys.stderr)
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
