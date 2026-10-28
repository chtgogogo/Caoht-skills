#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_recall.py — 有效记忆召回器（先过滤，再排序；不拿无关结果冒充成功召回）

给当前任务上下文找出**真正相关且仍然有效**的记忆，吐 top-K 路径 + 每条的入选原因。
默认只读索引；只有当索引里查不到「状态 / 有效期 / 分」时，才对**候选池**读正文 frontmatter
（每份 ≤1600 字节，且封顶 top×3 份），不再「逐条读前 4000 字符」。加 `--print` 才输出命中详情正文。

## 为什么必须先过滤（外部审查 §二.6 / 卡03）
旧版排完序直接取前 K：superseded/过期/断链/越界/零重合条目都能挤进 Top-K 且退 0——
把「没有合适记忆」伪装成「召回了」（完整工序表见 SKILL.md §4.1）。现按固定顺序过滤，每步计数：

  ① 权限范围   store 边界（realpath 内含判定）＋ 默认排除 archive/TRASH/FORGOTTEN/tools
  ② 有效状态   superseded / archived 出局；draft 降权 50%（SKILL.md §8）；promoted 保留并标注
  ③ 未过期     valid_until 是过去的日期 → 出局（未到期的只是「限期记忆」，保留）
  ④ 文件存在   索引行在、正文文件没了 → 出局并计入断链（旧版只降权 0.5 不丢弃）
  ⑤ 相关性门槛 分档：触发词整词入句=6 / ≥3 个不同查询片段落在触发词=6 /
                ≥2 个片段=3 / 只有 1 个片段=1。默认丢 rel<3（--min-rel 可调）。
                🔴 按**去重后的查询片段**数不按命中次数（巧合撞两次会凑 2 信号，
                把零相关抬成语义相关）。语料两遍：先只吃索引，过门槛不足 top 条
                才补读候选正文再判（防「触发词只在正文」的真库被漏召回）。
                零相关、未达门槛分别计数，no_match 时告诉你差多少。
  ⑥ 排序       与 SKILL.md §3 同一份公式：排序分 = (V/10) × 0.6 + R × 0.4
                R = 相关性（触发词精确命中 6 / 语义相关 3 / 弱相关 1）
                    + 新近性（7 天内 4 / 30 天内 2 / 更早 0）
                V 未登记 ⇒ 按 0 计并在原因里明写（§3 要求按量表打分，工具不凭感觉补基线分）

## 三态出口（外部审查 §二.6 建议 2）
  state=matched      退出码 0   至少一条过了全部闸门
  state=no_match     退出码 1   索引读到了，但没有一条过相关性门槛（本次没有合适记忆，不是故障）
  state=index_error  退出码 3   索引找不到 / 一行都解析不出而库里却有正文 / 索引文件读不了
  state=tool_error   退出码 4   工具自身抛异常 ⇒ 绝不复用 1，否则「崩了」被读成「库里没相关记忆」
  参数错误                        退出码 2
  🔴 退出码只回答「有没有量到」，不回答「量得对不对」，所以三态同时写进**人读的那一行**与 stderr。

## 两种 store 结构都支持（按结构判型，不看引导卡文件名）
  单索引型  <store>/MEMORY.md            （`- [标题](路径) · 类型 · 日期 — 钩子`）
  七库型    <store>/CORE.md + */index.md （bullet 体与 ID 表体混用均可）
  ID 表体的列**按表头映射**（触发词/结论/分/状态/日期/有效期/RT……与列序无关）；
  没有表头时退回 SKILL.md §8 的位置式默认 `| ID | 触发词 | 一句话结论 | 分 | 状态 |`。
  多段 ID（`pit-a-001` 这种）旧版正则不认 ⇒ 整行静默丢弃，本版认。

用法:
  py memory_recall.py "<本次任务上下文>" [--root <store 根|索引文件>] [--top 5] [--print]
                       [--type pitfall] [--min-rel 3] [--index-only] [--include-archived]
  --min-rel  相关性门槛（默认 3）：1=单个二元组偶然重合也要，6=只认触发词整词命中。
  --root 缺值 / 不认识的选项 ⇒ 退出码 2 并点名是哪个参数，绝不静默改用环境变量里的另一个库。

退出码: 0=matched  1=no_match  2=参数错误  3=index_error
"""
import os
import re
import sys
from datetime import date, datetime

try:  # GBK 控制台防护（Codex/DSH 实测 2026-09-27：中文输出在 GBK 终端会崩）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INDEX_CANDIDATES = ["CORE.md", "MEMORY.md"]
EXCLUDED_DIRS = {"archive", "trash", "forgotten", "tools", "__pycache__", ".git"}
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
FM_READ_LIMIT = 1600       # 正文核对只读 frontmatter 区；读不到闭合线就明写「未读全」
VERIFY_POOL_FACTOR = 3     # 需要核对正文时，最多核对 top×3 份
DEFAULT_COLUMNS = ["id", "triggers", "conclusion", "score", "status"]
# 相关性门槛（R 分档见 SKILL.md §3：触发词精确 6 / 语义相关 3 / 弱相关 1）。
# 默认 3 ⇒ 只有「单个中文二元组偶然重合」（弱相关 1 分）的记录不进 Top-K。
# 旧版没有这道闸：任意查询都塞满 top-K，AI 拿到一堆不相干条目当记忆用。
MIN_REL_DEFAULT = 3.0
TERM_SPLIT_RE = re.compile(r"[，,、;；/|\s\[\]（）()]+")
MAX_BODY_SCAN = 400        # 第二遍补读正文语料的条数上限（超了就明写「没补，门槛按索引语料判」）
ONELINE_RE = re.compile(r"一句话结论[^\n]*\n+\s*(\S[^\n]{0,200})")

STATUS_ALIAS = {
    "active": "active", "有效": "active", "在用": "active", "正常": "active",
    "draft": "draft", "草稿": "draft", "推测": "draft",
    "superseded": "superseded", "已替代": "superseded", "被替代": "superseded",
    "已失效": "superseded", "失效": "superseded",
    "promoted": "promoted", "已升华": "promoted", "升华": "promoted", "铁律": "promoted",
    "archived": "archived", "已归档": "archived", "归档": "archived",
    "deleted": "superseded", "removed": "superseded", "废弃": "superseded",
}
DROPPED_STATUS = {"superseded", "archived"}

# 表头列名 → 字段（先匹配者胜：「一句话结论」落 conclusion 而不是 score）
COL_KEYS = [
    ("valid_until", ("有效期", "valid_until", "validuntil", "到期")),
    ("triggers", ("触发", "关键词", "trigger", "现象词")),
    ("conclusion", ("结论", "摘要", "一句话", "summary", "说明", "hook")),
    ("status", ("状态", "status")),
    ("date", ("日期", "时间", "更新", "date", "created")),
    ("rt", ("rt", "根因", "signature")),
    ("exclude", ("排除", "exclude")),
    ("score", ("分", "score", "v")),
    ("type", ("类型", "库", "type")),
    ("path", ("路径", "文件", "path")),
    ("id", ("id", "编号")),
]
DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*$")
ANY_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
LINE_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)?\[(?P<title>[^\]]*)\]\((?P<path>[^)]+)\)(?P<rest>.*)$")
IDROW_RE = re.compile(r"^\s*\|+\s*(?P<id>[A-Za-z][\w.\-]*\d[\w.\-]*)\s*\|(?P<cells>.*)$")
SEP_RE = re.compile(r"^\s*\|[\s|:\-]+\|?\s*$")
TRIG_RE = re.compile(r"[（(\[]\s*触发词\s*[：:]\s*(.+)$|触发词\s*[：:]\s*(.+)$")
V_TAG_RE = re.compile(r"RT[-_]?\w+")


def tok(s):
    """中英混排分词：英文/数字整词 + 中文二元组（bigram）。

    逐字切会把「不知道怎么办」拆成 6 个噪音 token 稀释命中率，
    二元组才保得住「报错」「调试」「缓存」这类有区分度的词。
    """
    s = (s or "").lower()
    words = re.findall(r"[a-z0-9_]{2,}", s)
    grams = []
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        grams += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return set(words + grams)


def norm_status(raw):
    """状态原文 → 规范五值之一；空→''，认不出→('unknown', 原值)。"""
    s = (raw or "").strip().lower()
    if not s or s in ("null", "-", "none"):
        return "", None
    if s in STATUS_ALIAS:
        return STATUS_ALIAS[s], None
    for k, v in STATUS_ALIAS.items():
        if k in s:
            return v, None
    return "unknown", (raw or "").strip()


def parse_iso(s):
    m = ANY_DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def find_store(start):
    d = start if os.path.isdir(start) else os.path.dirname(os.path.abspath(start))
    for _ in range(8):
        if any(os.path.isfile(os.path.join(d, n)) for n in INDEX_CANDIDATES):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def sub_index_files(store, include_archived=False):
    out = []
    skip = {".git", "__pycache__", "tools"} if include_archived else EXCLUDED_DIRS
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [x for x in dirnames if not x.startswith(".") and x.lower() not in skip]
        if os.path.abspath(dirpath) == os.path.abspath(store):
            continue
        if "index.md" in filenames:
            out.append(os.path.join(dirpath, "index.md"))
    return out


def resolve_index(root, include_archived=False):
    """返回 (store 根, 主索引, 是否单索引型)。判型看结构，不看引导卡文件名（pit-001）。"""
    store = find_store(root)
    if not store:
        return None, None, True
    if os.path.isfile(root):
        base = os.path.dirname(os.path.abspath(root))
        return store, os.path.abspath(root), not sub_index_files(base, include_archived)
    index_path = None
    for name in INDEX_CANDIDATES:
        cand = os.path.join(store, name)
        if os.path.isfile(cand):
            index_path = cand
            break
    return store, index_path, not sub_index_files(store, include_archived)


def map_header(cells):
    """表头行 → {列序号: 字段名}，认不出的列不进映射（留给数据行嗅探）。"""
    mapping, used = {}, set()
    for i, c in enumerate(cells):
        low = (c or "").strip().lower()
        if not low:
            continue
        if DATE_RE.match(low):
            mapping[i] = "date"
            continue
        for field, keys in COL_KEYS:
            if field in used:
                continue
            if any(k in low for k in keys):
                mapping[i] = field
                used.add(field)
                break
    return mapping


def sniff_columns(data_cells_list, mapping):
    """表头缺失/缺列时按数据内容补：ISO 日期→date，纯数字→score，状态词→status，RT-x→rt。"""
    for cells in data_cells_list:
        for i, c in enumerate(cells):
            if i in mapping:
                continue
            v = (c or "").strip()
            if not v:
                continue
            if DATE_RE.match(v):
                mapping[i] = "date"
            elif re.fullmatch(r"-?\d+(\.\d+)?", v) and "score" not in mapping.values():
                mapping[i] = "score"
            elif norm_status(v)[0] not in ("", "unknown") and "status" not in mapping.values():
                mapping[i] = "status"
            elif V_TAG_RE.search(v) and "rt" not in mapping.values():
                mapping[i] = "rt"
    return mapping


def new_row():
    return {"id": "", "path": "", "type": "", "date": "", "triggers": "", "conclusion": "",
            "status_raw": "", "status_indexed": "", "valid_until": "", "score": None,
            "rt": "", "exclude": "", "extra": "", "index": "", "base": "", "body": "",
            "broken": False, "drop_note": ""}


def put_field(row, field, cell, lib_type):
    cell = (cell or "").strip()
    if not cell:
        return
    if field == "triggers":
        row["triggers"] = (row["triggers"] + " " + cell).strip()
    elif field == "conclusion":
        row["conclusion"] = row["conclusion"] or cell
        row["extra"] = (row["extra"] + " " + cell).strip()
    elif field == "score":
        m = re.search(r"-?\d+(\.\d+)?", cell)
        if m and row["score"] is None:
            row["score"] = float(m.group(0))
    elif field == "status":
        row["status_raw"] = row["status_raw"] or cell
    elif field == "date":
        dm = parse_iso(cell)
        if dm and not row["date"]:
            row["date"] = dm.isoformat()
    elif field == "valid_until":
        dm = parse_iso(cell)
        if dm and not row["valid_until"]:
            row["valid_until"] = dm.isoformat()
    elif field == "rt":
        row["rt"] = row["rt"] or cell
    elif field == "exclude":
        row["exclude"] = (row["exclude"] + " " + cell).strip()
    elif field == "path":
        row["path"] = cell
    elif field == "type":
        row["type"] = row["type"] or cell
    elif field == "id":
        row["id"] = row["id"] or cell
    else:
        row["extra"] = (row["extra"] + " " + cell).strip()


def parse_table_block(block, index_file, idx_dir, lib_type, rows):
    """一段连续的 `|` 行 = 一个表格块：表头行只用于建列映射，数据行按映射取字段。"""
    data = []
    mapping = None
    for raw in block:
        if SEP_RE.match(raw):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if not any(cells):
            continue
        if IDROW_RE.match(raw):
            data.append(cells)
        else:
            hm = map_header(cells)
            if len([c for c in cells if c]) >= 2 and hm:
                mapping = hm            # 这是表头行
    if not data:
        return
    if mapping is None:
        mapping = {i: f for i, f in enumerate(DEFAULT_COLUMNS)}
    sniff_columns(data, mapping)
    for cells in data:
        rid = cells[0].strip()
        if not rid or not IDROW_RE.match("| %s |x |" % rid):
            continue                     # 首格不是记忆 ID 形状 ⇒ 说明性表格，不是索引行
        row = new_row()
        row["id"], row["path"], row["type"] = rid, rid + ".md", lib_type
        for i, c in enumerate(cells):
            if i == 0:
                continue
            put_field(row, mapping.get(i, "?"), c, lib_type)
        if not row["conclusion"]:
            row["conclusion"] = row["extra"]
        row["index"], row["base"] = index_file, idx_dir
        rows.append(row)


def parse_bullet_line(line, index_file, idx_dir, lib_type, rows):
    m = LINE_RE.match(line.strip())
    if not m:
        return
    row = new_row()
    row["id"], row["path"] = m.group("title").strip(), m.group("path").strip()
    rest = m.group("rest") or ""
    # 「· 类型 · 日期 — 钩子」：先按长破折号切出钩子，再在头部段里认类型与日期。
    # 状态只认**整段精确匹配**（旧写法用子串匹配，「正常命中」里的「正常」会被当成 status=active）。
    head, hook = (re.split(r"\s*[—–]\s*", rest, maxsplit=1) + [""])[:2]
    for seg in [s.strip() for s in re.split(r"\s*[·|]\s*", head) if s.strip()]:
        if DATE_RE.match(seg) and not row["date"]:
            row["date"] = parse_iso(seg).isoformat()
        elif seg.lower() in STATUS_ALIAS and not row["status_raw"]:
            row["status_raw"] = seg
        elif re.fullmatch(r"[A-Za-z_][\w.\-]*", seg) and len(seg) <= 14 and not row["type"]:
            row["type"] = seg
    hook = hook.strip()
    tm = TRIG_RE.search(hook) or TRIG_RE.search(rest)
    if tm:
        row["triggers"] = (tm.group(1) or tm.group(2) or "").strip()
        hook = TRIG_RE.sub("", hook).strip().rstrip("（(·—- \t")
    row["conclusion"] = hook
    row["extra"] = hook
    rt = V_TAG_RE.search(rest)
    if rt:
        row["rt"] = rt.group(0)
    sm = re.search(r"状态\s*[：:]\s*([A-Za-z\u4e00-\u9fff]+)", rest)
    if sm:
        row["status_raw"] = sm.group(1)
    vm = re.search(r"有效期\s*[：:]\s*(\d{4}-\d{2}-\d{2})", rest)
    if vm:
        row["valid_until"] = vm.group(1)
    sc = re.search(r"(?:分|score)\s*[：:=]\s*(\d+)", rest)
    if sc and row["score"] is None:
        row["score"] = float(sc.group(1))
    ex = re.search(r"排除\s*[：:]\s*(.+?)$", rest)
    if ex:
        row["exclude"] = ex.group(1).strip()
    if not row["type"]:
        segs = row["path"].replace("\\", "/").split("/")
        row["type"] = segs[-2] if len(segs) >= 2 else lib_type
    row["index"], row["base"] = index_file, idx_dir
    rows.append(row)


def parse_index(text, index_file, store):
    """统一出口：bullet 体 + ID 表体都翻成同一套字段（旧版两套正则各说各话，多段 ID 整行丢）。"""
    rows = []
    idx_dir = os.path.dirname(os.path.abspath(index_file))
    lib_type = os.path.basename(idx_dir)
    block = []
    for raw in text.splitlines():
        if raw.strip().startswith("|"):
            block.append(raw)
            continue
        if block:
            parse_table_block(block, index_file, idx_dir, lib_type, rows)
            block = []
        if raw.strip():
            parse_bullet_line(raw, index_file, idx_dir, lib_type, rows)
    if block:
        parse_table_block(block, index_file, idx_dir, lib_type, rows)
    return rows


FM_FIELDS = ("status", "valid_until", "superseded_by", "score", "last_hit",
             "root_cause_signature", "triggers", "promoted_to", "exclude", "type",
             "title", "description", "name")


def fm_fields(text):
    out = {}
    for key in FM_FIELDS:
        vals = [mm.group(1).strip() for mm in re.finditer(r"^%s\s*:\s*(.*)$" % key, text, re.M)]
        if vals:
            out[key] = vals[0]
    return out


def read_body_frontmatter(path, cache):
    """读正文开头 ≤FM_READ_LIMIT 字节。返回 (字节数, frontmatter 字段|None, 是否读全, 原文)。

    原文一并返回：「一句话结论」在 frontmatter 之后，光有字段拼不出检索语料
    （真库的索引行常常没有触发词列，语料只剩 hook 一句话，门槛会把真相关条目挡掉）。
    """
    if path in cache:
        return cache[path]
    n, fields, closed, text = 0, None, False, ""
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                chunk = f.read(FM_READ_LIMIT)
            n = len(chunk)
            text = chunk.decode("utf-8", errors="ignore").lstrip("\ufeff")
            m = FM_RE.match(text)
            if m:
                fields, closed = fm_fields(m.group(1)), True
            elif text.startswith("---"):
                fields = fm_fields(text)      # frontmatter 比限额长 ⇒ 只能按已读部分处理
            else:
                fields = None
        except OSError:
            fields = None
    cache[path] = (n, fields, closed, text)
    return cache[path]


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    query = args[0]
    if query.startswith("--"):
        # 把 --root 当查询吞掉，会静默改用环境变量指向的另一个库（查错库零报警）
        print("[error] 缺少查询上下文：第一个参数 %r 看着像选项。用法 "
              "py memory_recall.py \"<本次任务上下文>\" [--root ...]" % query, file=sys.stderr)
        return 2
    root, root_src = None, None
    for env in ("MEMORY_ROOT", "ENGRAMORY_ROOT"):
        if os.environ.get(env):
            root, root_src = os.environ[env], "环境变量 %s" % env
            break
    if root is None:
        root, root_src = os.getcwd(), "当前目录（未给 --root，也未设 MEMORY_ROOT）"
    top, do_print = 5, False
    type_filter, index_only, include_archived = None, False, False
    min_rel = MIN_REL_DEFAULT
    NEEDS_VALUE = {"--root", "--top", "--type", "--min-rel"}
    KNOWN = NEEDS_VALUE | {"--print", "--index-only", "--include-archived"}
    i = 1
    while i < len(args):
        a = args[i]
        if a not in KNOWN:
            print("[error] 不认识的参数 %r（可用：%s）" % (a, " ".join(sorted(KNOWN))), file=sys.stderr)
            return 2
        if a in NEEDS_VALUE and i + 1 >= len(args):
            # 悬空的 --root 若不报错，会静默改用环境变量指向的**另一个库**（查错库零报警）
            print("[error] %s 后面缺值" % a, file=sys.stderr)
            return 2
        if a == "--root":
            root, root_src, i = args[i + 1], "命令行 --root", i + 2
        elif a == "--top":
            try:
                top = max(1, int(args[i + 1]))
            except ValueError:
                print("[error] --top 需要整数，收到 %r" % args[i + 1], file=sys.stderr)
                return 2
            i += 2
        elif a == "--type":
            type_filter, i = args[i + 1].lower(), i + 2
        elif a == "--min-rel":
            try:
                min_rel = float(args[i + 1])
            except ValueError:
                print("[error] --min-rel 需要数字（1=弱相关也要 3=默认 6=只认触发词整词命中）"
                      "，收到 %r" % args[i + 1], file=sys.stderr)
                return 2
            i += 2
        elif a == "--print":
            do_print, i = True, i + 1
        elif a == "--index-only":
            index_only, i = True, i + 1
        else:
            include_archived, i = True, i + 1

    qtok = tok(query)
    if not qtok:
        print("[error] 查询上下文为空（一个可匹配的 token 都没有）", file=sys.stderr)
        return 2

    store, index_path, single = resolve_index(root, include_archived)
    if not index_path:
        msg = ("state=index_error｜未找到索引（试过 %s，并从 %s 向上回溯 store 根）"
               % (" / ".join(INDEX_CANDIDATES), root))
        print("[recall] " + msg, file=sys.stderr)
        print("[recall] " + msg)
        return 3

    store_real = os.path.realpath(store)
    skip_dirs = {".git", "__pycache__", "tools"} if include_archived else EXCLUDED_DIRS
    files = [index_path]
    if not single:
        files += [p for p in sub_index_files(store, include_archived) if p != index_path]
    rows, read_fail = [], []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                rows += parse_index(fh.read(), f, store)
        except OSError as e:
            read_fail.append("%s（%s）" % (os.path.relpath(f, store), e.strerror or "读取失败"))

    n_bodies = 0
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [x for x in dirnames if not x.startswith(".") and x.lower() not in skip_dirs]
        n_bodies += len([fn for fn in filenames
                         if fn.endswith(".md") and fn not in INDEX_CANDIDATES and fn != "index.md"])
    if not rows:
        why = "state=index_error｜索引解析出 0 行"
        if n_bodies:
            why += "，而库内有 %d 份正文 ⇒ 行格式本工具不认（不是「没有记忆」）" % n_bodies
        print("[recall] %s｜索引=%s｜store=%s" % (why, index_path, store), file=sys.stderr)
        print("[recall] %s｜索引=%s｜store=%s" % (why, index_path, store))
        if read_fail:
            print("[recall] 读取失败：%s" % "; ".join(read_fail))
        return 3

    dropped = {"越界": 0, "范围外目录": 0, "状态": 0, "已过期": 0, "断链": 0, "类型过滤": 0,
               "零相关": 0, "弱相关未达门槛": 0, "排除词": 0, "核对剔除": 0}
    # 正文核对阶段的剔除单独记账：它们也叫「状态/已过期/排除词」，若混进闸门计数，
    # 漏斗逐级相减就对不上（1 条被记两次，报表显示 7 条过闸而实际 6 条）。
    verify_why = {}

    def bump_verify(why):
        verify_why[why] = verify_why.get(why, 0) + 1
    today = date.today()

    # ① 权限范围
    scoped = []
    for r in rows:
        r["body"] = os.path.normpath(os.path.join(r["base"], r["path"]))
        real = os.path.realpath(r["body"])
        if not (real == store_real or real.startswith(store_real + os.sep)):
            dropped["越界"] += 1
            continue
        segs = [x.lower() for x in os.path.relpath(real, store_real).split(os.sep)][:-1]
        if not include_archived and any(s in EXCLUDED_DIRS for s in segs):
            dropped["范围外目录"] += 1
            continue
        scoped.append(r)
    n_scope = len(scoped)

    # ② 有效状态（索引列能答的先答，答不了的留给正文核对）
    alive = []
    for r in scoped:
        r["status_indexed"] = norm_status(r["status_raw"])[0]
        if r["status_indexed"] in DROPPED_STATUS:
            dropped["状态"] += 1
            continue
        alive.append(r)
    n_state = len(alive)

    # ③ 未过期（只判索引里已经写得到的日期；索引没写的在正文核对阶段补判）
    kept = []
    for r in alive:
        vu = parse_iso(r["valid_until"])
        if vu and vu < today:
            dropped["已过期"] += 1
            continue
        kept.append(r)
    n_fresh = len(kept)

    # ④ 文件存在
    alive = []
    for r in kept:
        if not os.path.isfile(r["body"]):
            r["broken"] = True
            dropped["断链"] += 1
            continue
        alive.append(r)
    if type_filter:
        for r in list(alive):
            if (r["type"] or "").lower() != type_filter:
                alive.remove(r)
                dropped["类型过滤"] += 1
    n_exist = len(alive)

    # ⑤ 相关性门槛（两遍：先只吃索引语料；索引语料凑不满 top 条时才补读正文语料再判一遍）
    nq = re.sub(r"\s+", " ", (query or "").lower()).strip()
    cache, bodies_read, bytes_read = {}, 0, 0

    def hits(corpus):
        """返回 (落在该语料上的**不同查询片段**集合, 被撑住的词数)。

        为什么按「不同片段」而不是「不同词」计数：同一个巧合（比如查询里的
        『完全』）在触发词列和正文里各撞一次，按词数会凑成 2 个信号，把零相关
        抬成语义相关。按**去重后的查询片段**数，一个巧合就只算一次。
        词本身也进片段集，好让 superseded / recall 这类英文整词能直接命中。
        """
        ts = [t for t in TERM_SPLIT_RE.split((corpus or "").lower()) if len(t) >= 2]
        words, n = set(), 0
        for t in ts:
            grams = set(t[i:i + 2] for i in range(len(t) - 1)) | {t}
            inter = qtok & grams
            if inter:
                n += 1
                words |= inter
        return words, n

    def relevance(r):
        trig = " ".join([r["triggers"] or "", r.get("trig_body") or ""])
        text = " ".join([r["conclusion"] or "", r["id"], r.get("oneline") or "",
                         " ".join(r.get("titleparts") or [])])
        full = [t for t in TERM_SPLIT_RE.split(trig) if len(t) >= 2 and t in nq]
        if full:
            return 6, "触发词整词命中", set(full)
        w1, n1 = hits(trig)
        w2, n2 = hits(text)
        g1, g2 = len(w1), len(w2)
        if g1 >= 3 or (g1 >= 2 and g2 >= 2):
            return 6, "触发词多点重合(%d 个片段)" % g1, w1 | w2
        if g1 >= 2 or g2 >= 2:
            return 3, "语义相关(%d 个片段沾边)" % max(g1, g2), (w1 if g1 >= g2 else w2)
        if g1 == 1 or g2 == 1:
            return 1, "弱相关(只有 1 个片段沾边)", (w1 or w2)
        w3, n3 = hits(" ".join([r["type"], r["path"], r["rt"], r["extra"]]))
        if w3:
            return 1, "弱相关(其它索引文本)", w3
        return 0, "无关键词重合", set()

    def merge_body(r, fields, closed, text):
        """把正文 frontmatter 与「一句话结论」并进判定字段和检索语料（重复调用无害）。"""
        if fields:
            if not r["status_indexed"]:
                st, unk = norm_status(fields.get("status"))
                if st:
                    r["status_final"] = st
                elif unk:
                    r["status_note"] = "状态无法识别(%s)⇒按 active" % unk
            vu = parse_iso(fields.get("valid_until"))
            if vu and not r["valid_until"]:
                r["valid_until"] = vu.isoformat()
            if r["score"] is None and r.get("v_body") is None:
                m = re.search(r"-?\d+(\.\d+)?", fields.get("score") or "")
                if m:
                    r["v_body"] = float(m.group(0))
            if not r.get("trig_body") and fields.get("triggers"):
                r["trig_body"] = fields["triggers"]
            if not r["exclude"] and fields.get("exclude"):
                r["exclude"] = fields["exclude"]
            lh = parse_iso(fields.get("last_hit"))
            if lh:
                r["last_hit"] = lh.isoformat()
            if not r["rt"] and fields.get("root_cause_signature"):
                r["rt"] = fields["root_cause_signature"]
            pt = fields.get("promoted_to")
            if pt and pt not in ("null", "-", "none"):
                r["promoted_to"] = pt
            parts = [fields.get(k) for k in ("title", "description", "name") if fields.get(k)]
            if parts:
                r["titleparts"] = (r.get("titleparts") or []) + parts
            if not closed:
                r["status_note"] = "frontmatter 超 %dB 未读全" % FM_READ_LIMIT
        elif not r.get("status_note") and not r["status_indexed"]:
            r["status_note"] = "正文无 frontmatter⇒按 active"
        if not r.get("oneline"):
            m = ONELINE_RE.search(text or "")
            if m:
                r["oneline"] = m.group(1).strip()

    def read_and_merge(r):
        nonlocal bodies_read, bytes_read
        n, fields, closed, text = read_body_frontmatter(r["body"], cache)
        bytes_read += n
        if fields is not None:
            bodies_read += 1
        merge_body(r, fields, closed, text)

    for r in alive:
        r["_rel"], r["_relwhy"], r["_words"] = relevance(r)
    n_pass_a = sum(1 for r in alive if r["_rel"] >= min_rel)
    no_trig = sum(1 for r in alive if not (r["triggers"] or "").strip())
    pass_b = ""
    if n_pass_a < top and not index_only:
        if len(alive) <= MAX_BODY_SCAN:
            for r in alive:
                read_and_merge(r)
            for r in alive:
                rel, why, w = relevance(r)
                if rel > r["_rel"]:
                    r["_rel"], r["_relwhy"], r["_words"] = rel, why, w
            pass_b = ("｜第二遍已补读正文语料（第一遍只 %d 条过门槛 < top %d）"
                      % (n_pass_a, top))
        else:
            pass_b = ("｜⚠️ 第一遍只有 %d 条过门槛，而库内 %d 条 > 补读上限 %d ⇒ 未补读正文语料，"
                      "本结果可能漏召回（把 triggers 写进索引列是根治法）"
                      % (n_pass_a, len(alive), MAX_BODY_SCAN))

    cands = []
    for r in alive:
        if r["exclude"] and (qtok & tok(r["exclude"])):
            dropped["排除词"] += 1
            continue
        if r["_rel"] == 0:
            dropped["零相关"] += 1
            continue
        if r["_rel"] < min_rel:
            dropped["弱相关未达门槛"] += 1
            continue
        cands.append(r)

    # ⑥ 排序（公式与 SKILL.md §3 同源）
    def recency(r):
        dd = parse_iso(r.get("last_hit") or r["date"])
        if not dd:
            return 0, "无日期⇒0 分"
        gap = (today - dd).days
        if gap < 0:
            return 0, "日期在未来⇒0 分"
        if gap <= 7:
            return 4, "%d 天内" % max(gap, 1)
        if gap <= 30:
            return 2, "30 天内"
        return 0, "更早"

    def finalize(r):
        st = r.get("status_final") or r["status_indexed"]
        v = r["score"] if r["score"] is not None else r.get("v_body")
        if v is None:
            v_src = "V 未登记⇒按 0 计"
            vv = 0.0
        else:
            v_src = "索引「分」列" if r["score"] is not None else "正文 score"
            vv = max(0.0, v)
        if st == "draft":
            vv *= 0.5
            v_src += "；draft⇒×0.5"
        rec, recwhy = recency(r)
        r["_v"], r["_v_src"], r["_rec"], r["_recwhy"] = vv, v_src, rec, recwhy
        r["_final"] = (vv / 10.0) * 0.6 + (r["_rel"] + rec) * 0.4

    def rank(pool):
        for r in pool:
            finalize(r)
        return sorted(pool, key=lambda r: (-r["_final"],
                                           -(parse_iso(r["date"]).toordinal() if r["date"] else 0),
                                           r["body"].replace("\\", "/")))

    ranked = None
    if index_only:
        ranked = rank(cands)
    else:
        prelim = rank(cands)
        pool_n = top * VERIFY_POOL_FACTOR
        pool, rest = prelim[:pool_n], prelim[pool_n:]
        keep = []
        for r in pool:
            # 状态/有效期/分：索引没给的必须问正文。第二遍已读过的走缓存，不再重复开文件。
            if not (r["status_indexed"] and r["valid_until"]
                    and (r["score"] is not None or r.get("v_body") is not None)):
                read_and_merge(r)
            st = r.get("status_final") or r["status_indexed"]
            if st in DROPPED_STATUS:
                bump_verify("状态=" + st)
                dropped["核对剔除"] += 1
                continue
            vu = parse_iso(r["valid_until"])
            if vu and vu < today:
                bump_verify("已过期")
                dropped["核对剔除"] += 1
                continue
            if r["exclude"] and (qtok & tok(r["exclude"])):
                bump_verify("命中排除词")
                dropped["核对剔除"] += 1
                continue
            keep.append(r)
        # 全局再排一次：核对过正文的条目可能因补到 V/状态而升或降，
        # 拼接（已核对组 + 未核对组）会让未核对的高分条沉在下面。
        ranked = rank(keep + rest)

    hits = ranked[:top]
    state = "matched" if hits else "no_match"

    print("# 召回 top-%d｜state=%s｜库 %s｜%s" % (top, state, store, "单索引型" if single else "七库型"))
    print("# 漏斗：索引 %d → 范围内 %d → 状态 %d → 未过期 %d → 正文在 %d → 相关 %d → 核对后 %d → 截 top %d"
          % (len(rows), n_scope, n_state, n_fresh, n_exist, len(cands), len(ranked), len(hits)))
    print("# query = %r" % query)
    filt = " ".join("%s%d" % (k, v) for k, v in dropped.items() if v)
    tail = ("（拦下明细：%s；核对剔除的原因=%s）" % (filt, "+".join(
        "%s%d" % (k, v) for k, v in sorted(verify_why.items()))) if dropped["核对剔除"]
        else "（拦下明细：%s）" % filt)
    print("# 先过滤再排序：每一步的规模由上式逐级相减得到，不是靠计数器累加%s" % tail)
    if index_only:
        print("# 正文核对：--index-only 已关闭（状态/有效期只信索引列，语料也只有索引）")
    elif bodies_read:
        print("# 正文核对：读 %d 份 / %d 字节%s" % (bodies_read, bytes_read, pass_b))
    else:
        print("# 正文核对：0 份（索引字段齐、索引语料够 top 条，无需读正文）%s" % pass_b)
    if no_trig and not index_only:
        print("# 索引齐备度：%d/%d 条索引行没有触发词列 ⇒ 相关性只能靠 hook/结论判，"
              "把 triggers 写进索引列（或跑 memory_organize.py 看清单）能少读正文也少漏召回"
              % (no_trig, len(alive)))
    if read_fail:
        print("# ⚠️ 索引读取失败（已跳过，计入 index_error 参考）：%s" % "; ".join(read_fail))

    for n, r in enumerate(hits, 1):
        st = r.get("status_final") or r["status_indexed"] or "未登记"
        tags = []
        if st == "draft":
            tags.append("draft⇒V×0.5")
        if st == "promoted" or r.get("promoted_to"):
            tags.append("已升华→CORE")
        if r["valid_until"]:
            tags.append("有效期至 %s" % r["valid_until"])
        if r["rt"]:
            tags.append(r["rt"].split("|")[0])
        if r.get("status_note"):
            tags.append(r["status_note"])
        words = "|".join(sorted(r["_words"])[:6])
        print("%2d) %5.2f | %-9s | %s" % (n, r["_final"], r["type"] or "?",
                                          os.path.relpath(r["body"], store).replace("\\", "/")))
        print("      标题=%s｜结论=%s" % (r["id"], (r["conclusion"] or "")[:80]))
        print("      原因=%s[%s]·V=%g(%s)·R=相关%d+新近%d(%s)·status=%s%s"
              % (r["_relwhy"], words, r["_v"], r["_v_src"], r["_rel"], r["_rec"], r["_recwhy"], st,
                 ("｜" + " ".join(tags)) if tags else ""))

    if do_print:
        print("\n----- 命中详情 -----")
        for r in hits:
            print("\n### %s (%s)" % (r["id"], os.path.relpath(r["body"], store).replace("\\", "/")))
            try:
                with open(r["body"], encoding="utf-8", errors="ignore") as fh:
                    print(fh.read())
            except OSError:
                print("[详情读取失败] 请跑 memory_organize.py 清理断链")

    if state == "no_match":
        extra = ""
        if dropped["弱相关未达门槛"]:
            extra = ("；另有 %d 条只有单个二元组偶然重合（门槛 min-rel=%g），"
                     "要看它们加 --min-rel 1" % (dropped["弱相关未达门槛"], min_rel))
        msg = ("state=no_match｜索引 %d 行、范围内 %d 条，但没有一条过相关性门槛"
               "（零相关拦下 %d 条%s）——本次没有合适记忆，按通用流程继续，不要拿无关条目凑数"
               % (len(rows), len(scoped), dropped["零相关"], extra))
        print("[recall] " + msg, file=sys.stderr)
        print("[recall] " + msg)
    return 0 if hits else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:            # 崩在工具里 ≠ 「没有匹配记忆」，必须给一个不同的退出码
        import traceback
        msg = ("[recall] state=tool_error｜工具自身异常，没有得出召回结论"
               "（这不是 no_match，别当成「库里没有相关记忆」）\n" + traceback.format_exc())
        print(msg, file=sys.stderr)
        print(msg)
        sys.exit(4)
