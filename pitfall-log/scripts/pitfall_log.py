#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pitfall_log — 踩坑日志的确定性维护工具（零依赖，Python 3.8+，跨平台）。

它只负责把「手维护会变错的东西」变成「从事件记录生成的东西」，三件事：

  ① 事件唯一 ID 与幂等登记——同一条坑重复提交，计数只加一次
  ② 两个指标自动维护——「同类案例数」与「实际帮助次数」分开算、都由本脚本生成，不手填
  ③ 升华闸门——候选 → 验证适用范围 → 提升；缺反例／边界／复核日期一律不放行

用法（`--root` 必填，不给默认当前目录：在错的地方跑会量到 0 条却打「无漂移」＝假绿灯）：

  py pitfall_log.py --root <log-root> init
  py pitfall_log.py --root <log-root> add --title <标题> --root-cause RT-0NN
                    [--kind full|lite] [--severity 红|橙|黄] [--date YYYY-MM-DD]
                    [--author <记录者>] [--tags a,b] [--summary <一句话后果>]
                    [--body-file <正文文本>] [--slug <覆盖>] [--also-root RT-0MM]
  py pitfall_log.py --root <log-root> hit --case <案例ID> --scenario <当时在做什么>
                    [--date YYYY-MM-DD] [--solved <解决了什么>]
  py pitfall_log.py --root <log-root> hit --root-cause RT-0NN --scenario <…>   # 只走根因层的命中
  py pitfall_log.py --root <log-root> counts [--json] [--prose] [--apply]
  py pitfall_log.py --root <log-root> check  [--json]
  py pitfall_log.py --root <log-root> promote --scan [--threshold 7] [--hits-threshold 3]
  py pitfall_log.py --root <log-root> promote --gate C-id [--apply]

退出码：0 成功／无漂移 ｜ 1 有漂移或闸门没过 ｜ 2 用法与目录错误
        3 候选不存在 ｜ 4 幂等冲突（同一事件 ID 撞上不同内容）｜ 5 抢锁失败

口径出处（不是本脚本自创）：
  · 事件去重键＝CloudEvents 的 source+id 语义（重复投递可同 id）；同键改内容＝报错＝Stripe
    idempotency key 语义（https://github.com/cloudevents/spec / https://docs.stripe.com/api/idempotent_requests）
  · 锁＝git lockfile 的 `O_CREAT|O_EXCL` + 原子 rename；读者永远看到旧版或新版，看不到半截
    （https://github.com/git/git/blob/master/lockfile.h）。⚠️ 两个已知边界：
    锁是**约定式**的（flock 尚且只是建议性锁，不守约定的写者照样能写）；
    `os.replace` 的原子性 Python 文档只按 POSIX 保证，Windows 侧未明文承诺，所以本脚本
    每次写盘后都**读回再算一遍**做验收，不符即回滚。
  · 轻/重双模板＝Google SRE 与 Atlassian 的事后复盘体裁（按后果触发、保持简短）；
    入库前先检索有无同根因＝ITIL Known Error / KEDB 的「先查再立」。

与记忆纪律 skill 同源，不另立编号体系：
  · 根因号 `RT-<三位数>` ＝记忆纪律的 `root_cause_signature`，共用 `roots/` 那套词表；
  · 同根因攒满 **7** 条才进升华候选（2026-10-04 定案，勿回退成 3）；
    单条被召回 `hits ≥ 3` 是另一个记数器，两者不同轴。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import time
from datetime import date, datetime

LOCK_NAME = ".pitfall.lock"
LOCK_STALE_SEC = 120
LOCK_WAIT_SEC = 5.0
INDEX_NAME = "pitfall-log.md"
CAND_DIR = "_candidates"
SEV_RED, SEV_ORANGE, SEV_YELLOW = "🔴", "🟠", "🟡"
FULL_SECTIONS = ("现象", "复现", "现场代码", "根因", "修法", "验证", "能否借鉴", "教训")
LITE_SECTIONS = ("现象", "根因", "能否借鉴", "教训")
# 2026-10-03 八段制之前的老五节条目：铁律「旧条目不追改」⇒ 只认不查（补「复现/验证」＝编证据）
LEGACY_SECTIONS = ("为什么", "解法", "来源")
# 八段制生效日（v1.5.0）：早于这天登记的历史条目一律算 legacy，不止老五节那一种形状
EIGHT_SECTION_SINCE = "2026-10-03"
SELF_ERROR_SECTION = "我自己引入的错"
CAND_FIELDS = ("适用范围", "反例", "边界", "复核日期")
# 提升件禁止出现的「无边界」措辞——局部经验不许写成对所有任务生效
UNIVERSAL_WORDS = ("所有任务", "任何项目", "任何情况", "全部场景", "所有场景", "无条件", "永远", "一律适用")

RT_RE = re.compile(r"RT-(\d{3})")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_MARK_RE = re.compile(r"<!--\s*pitfall-event:\s*(\S+?)(?:\s*\|\s*fp:\s*([0-9a-f]+))?\s*-->")
FP_CELL_RE = re.compile(r"fp:([0-9a-f]{6,12})")
CASE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]*?pitfalls/[^)/\\]+\.md)\)")
ROOT_ROW_RE = re.compile(r"^\|\s*\[?(RT-\d{3})\]?\s*(?:\([^)]*\))?\s*\|")
# 根因表行尾：`… | 案例数 | 🔥命中 <可选注记> |`
ROOT_TAIL_RE = re.compile(r"(\|\s*)(\d+)(\s*\|\s*🔥)(\d+)((?:[^|\n]|\|(?!\s*$))*\|)(\s*)$")
CASE_ROW_RE = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|")
CASE_TAIL_RE = re.compile(r"(🔥)(\d+)(\s*\|\s*\[[^\]]*\]\([^)]*pitfalls/[^)]*\)\s*\|\s*)$")


def die(msg, code=2):
    print("[error] %s" % msg, file=sys.stderr)
    sys.exit(code)


def say(msg=""):
    print(msg)


# ================================================================ 归一化与指纹
def norm_text(s):
    """去空白、标点、大小写差异——「同一事件」判同用，容得下两次手打的中英文标点差。"""
    return "".join(ch for ch in str(s).lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def slugify(title, maxlen=44):
    parts = re.split(r"[\s/\\:*?\"'<>|,，。、（）()\[\]{}!！?？@#\$%\^&\+~`=]+", str(title).strip())
    slug = "-".join(p for p in parts if p)
    slug = re.sub(r"[^\w一-鿿.-]", "", slug).strip("-.")
    return slug[:maxlen].rstrip("-.") or "untitled"


def event_fingerprint(title, root_cause, severity):
    """事件身份＝（标题, 归属根因, 后果分级）。同一件事由谁、在哪一天提交都算同一条。"""
    return hashlib.sha1("|".join([norm_text(title), (root_cause or "").upper(), severity or ""])
                        .encode("utf-8")).hexdigest()[:12]


def hit_fingerprint(day, scenario):
    return hashlib.sha1("|".join([day, norm_text(scenario)]).encode("utf-8")).hexdigest()[:12]


# ================================================================ 读写与锁
def read_lines(path):
    """返回 (行列表, 行尾)。行尾按文件里占多数的那种，写回时保持原样。"""
    with open(path, "rb") as f:
        raw = f.read().decode("utf-8-sig", errors="replace")
    crlf = raw.count("\r\n")
    lf = raw.count("\n") - crlf
    return raw.replace("\r\n", "\n").split("\n"), ("\r\n" if crlf > lf else "\n")


def store_eol(root):
    """新建文件跟索引用同一种行尾（一个库里混 CRLF/LF 会让后面所有 diff 变成全文件重写）。"""
    idx = os.path.join(root, INDEX_NAME)
    if os.path.isfile(idx):
        return read_lines(idx)[1]
    return "\n"


def atomic_write(path, lines, eol, backup=True):
    """临时文件 + os.replace 原子换：并发的读者要么看到旧版要么看到新版，不会看到半截。"""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.pitfall-tmp-%d" % (path, os.getpid())
    text = eol.join(lines)
    if not text.endswith(eol):
        text += eol
    # newline="" 是硬要求：行尾已手工拼好，再让 Python 翻译一遍会写成 \r\r\n（读回行数翻倍）
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if backup and os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)


def acquire_lock(root):
    """独占锁：O_CREAT|O_EXCL 建 `.pitfall.lock`。取不到返回 (None, 原因)，一个字节都不改。"""
    path = os.path.join(root, LOCK_NAME)
    holder = "%s@%s pid=%d started=%s" % (os.path.basename(root), socket.gethostname(),
                                         os.getpid(), datetime.now().isoformat(timespec="seconds"))
    waited, backoff, grabbed = 0.0, 0.2, []
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, (holder + "\n").encode("utf-8", errors="replace"))
            os.close(fd)
            note = "已独占" if not grabbed else "已抢下残留锁（上一个：%s）" % "；".join(grabbed)
            return path, (("等待 %.1fs 后抢到锁；" % waited) if waited else "") + note
        except FileExistsError:
            age = None
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                pass
            if age is not None and age > LOCK_STALE_SEC:
                who = "未知"
                try:
                    who = open(path, encoding="utf-8", errors="replace").read().strip() or who
                except OSError:
                    pass
                try:
                    os.remove(path)
                except OSError:
                    return None, "残留锁 %s（%.0fs）删不掉（权限？）" % (path, age)
                grabbed.append("%.0fs 前的 %s" % (age, who))
                continue
            if waited >= LOCK_WAIT_SEC:
                who = "未知"
                try:
                    who = open(path, encoding="utf-8", errors="replace").read().strip() or who
                except OSError:
                    pass
                return None, ("锁被占用（%.0fs 前由 %s 建；本进程已等 %.0fs 未撤，残留锁判定阈值 %ds）%s"
                              % (age if age is not None else -1, who, waited, LOCK_STALE_SEC, path))
            time.sleep(backoff)
            waited += backoff
            backoff = min(backoff * 2, 1.5)
        except OSError as e:
            return None, "无法创建锁文件：%s（%s）" % (path, e.strerror or e)


def touch_lock(path):
    """心跳：残留锁的判据是「超过 LOCK_STALE_SEC 没人碰」，持锁期间必须刷 mtime，
    否则一个大库的写入会被第二个窗误判成崩溃残留而被抢走，互斥静默失效。"""
    if path:
        try:
            os.utime(path, None)
        except OSError:
            pass


def release_lock(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ================================================================ 库结构与解析
def require_store(root):
    if not os.path.isdir(root):
        die("库目录不存在：%s（先跑 init）" % root)
    missing = [d for d in ("roots", "pitfalls") if not os.path.isdir(os.path.join(root, d))]
    if missing:
        die("库缺子目录 %s：%s（先跑 init 或手工补齐）" % ("、".join(missing), root))
    idx = os.path.join(root, INDEX_NAME)
    if not os.path.isfile(idx):
        die("库缺索引文件 %s：%s" % (INDEX_NAME, root))
    return idx


def _md_files(dirpath):
    if not os.path.isdir(dirpath):
        return []
    return [os.path.join(dirpath, n) for n in sorted(os.listdir(dirpath))
            if n.lower().endswith(".md") and not n.startswith("_")]


def case_files(root):
    return _md_files(os.path.join(root, "pitfalls"))


def root_files(root):
    return _md_files(os.path.join(root, "roots"))


def parse_front(lines):
    """YAML 头字段（只认 `key: value` 一层）＋块结束行号。"""
    fields = {}
    end = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i + 1
                break
        if end:
            for raw in lines[1:end - 1]:
                m = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*)$", raw)
                if m:
                    fields.setdefault(m.group(1).lower(), m.group(2).strip())
    return fields, end


def inline_meta(lines, upto=12):
    """行内引用块形态：`> root: RT-013 ｜ hits: 1 ｜ last_hit: …`（历史条目里有这种）。

    🔴 读取范围＝标题行之后、第一个**小节**标题之前的那一小段：现役库那条 2026-09-26 的 HF 案例
    实际形状是 `# 坑：…` → 空行 → `> root: RT-013 ｜ …`，块引用**在标题下面**。
    旧写法一遇到第一个 `#` 就 break，等于永远读不到它 ⇒ 该案例既挂不上 RT-013、hits 也读不出来
    （2026-10-13 卡14 对账实测：现扫 RT-013 案例数 0／索引写 1）。现在改成数标题：
    第 1 个是标题本身，放行；第 2 个（小节）才停。
    """
    fields = {}
    headings = 0
    for raw in lines[:upto]:
        if re.match(r"^\s*#{1,6}\s", raw):
            headings += 1
            if headings >= 2:
                break
            continue
        for key in ("also_root", "root_cause", "root", "hits", "last_hit", "escalated"):
            m = re.search(r"(?:^|[\s>|])%s\s*:\s*([^|｜\n]+)" % key, raw)
            if m and key not in fields:
                fields[key] = m.group(1).strip()
    return fields


def _rt_list(value):
    if not value:
        return []
    return ["RT-%s" % m.group(1) for m in RT_RE.finditer(str(value).upper())]


def _int_of(value):
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else 0


def heading_texts(lines):
    out = []
    for raw in lines:
        m = re.match(r"^\s*#{2,3}\s*(.+)$", raw)
        if m:
            out.append(re.sub(r"^\d+[.、]\s*", "", m.group(1).strip()))
    return out


def has_section(headings, keyword):
    return any(keyword in h for h in headings)


def section_body(lines, keyword):
    """取某个二级/三级标题段落的正文（到下一个标题为止），跳过模板括注。"""
    grab, buf = False, []
    for raw in lines:
        m = re.match(r"^\s*#{1,3}\s*(.+)$", raw)
        if m:
            if grab:
                break
            grab = bool(re.search(keyword, m.group(1)))
            continue
        if grab:
            t = raw.strip()
            if not t or (t.startswith("（") and t.endswith("）")):
                continue
            buf.append(t)
    return buf


def hit_rows(lines):
    """「## 命中记录」表里的真实事件行；占位行（— | — | — 或空）不计。"""
    rows = []
    start = None
    for i, raw in enumerate(lines):
        if re.match(r"^\s*#{2,3}\s*命中记录", raw):
            start = i
            break
    if start is None:
        return rows
    j = start + 1
    last_data = None
    while j < len(lines) and not re.match(r"^\s*#{1,3}\s", lines[j]):
        raw = lines[j].strip()
        if raw.startswith("|"):
            cells = [c.strip() for c in raw.strip("|").split("|")]
            is_head = cells and re.sub(r"[\s:|-]", "", cells[0]) == "日期"
            is_sep = bool(re.match(r"^\|[\s:|-]+\|$", raw))
            if not is_head and not is_sep:
                if len(cells) >= 3 and not all(c in ("—", "-", "") for c in cells[:3]):
                    m = FP_CELL_RE.search(raw)
                    rows.append({"date": cells[0], "scenario": cells[1],
                                 "solved": cells[2], "line": j,
                                 "fp": m.group(1) if m else hit_fingerprint(cells[0], cells[1])})
                    last_data = j
        j += 1
    return rows


def detect_severity(lines, body_from):
    for raw in lines[body_from:]:
        s = raw.strip()
        if s.startswith("#"):
            for mark in (SEV_RED, SEV_ORANGE, SEV_YELLOW):
                if mark in s:
                    return mark
            return ""
    return ""


def parse_case(path):
    lines, _ = read_lines(path)
    fields, body_from = parse_front(lines)
    merged = dict(inline_meta(lines))
    for k, v in fields.items():
        if v:
            merged[k] = v
    roots = _rt_list(merged.get("root") or merged.get("root_cause"))
    also = _rt_list(merged.get("also_root"))
    mark = None
    for raw in lines:
        m = EVENT_MARK_RE.search(raw)
        if m:
            mark = (m.group(1), m.group(2) or "")
            break
    headings = heading_texts(lines)
    kind = str(merged.get("kind", "")).strip().lower()
    if kind not in ("full", "lite", "legacy"):
        cid = os.path.splitext(os.path.basename(path))[0]
        m_day = re.match(r"^(\d{4}-\d{2}-\d{2})", cid)
        pre_cutoff = bool(m_day) and m_day.group(1) < EIGHT_SECTION_SINCE
        old_shape = (not has_section(headings, "现象")
                     and sum(has_section(headings, s) for s in LEGACY_SECTIONS) >= 2)
        if pre_cutoff or old_shape:
            kind = "legacy"
        else:
            kind = "full" if has_section(headings, SELF_ERROR_SECTION) or len([h for h in headings if h != "命中记录"]) >= 8 else "lite"
    title = ""
    for raw in lines[body_from:]:
        if raw.strip().startswith("#"):
            title = re.sub(r"^#+\s*", "", raw.strip())
            break
    return {
        "path": path,
        "id": os.path.splitext(os.path.basename(path))[0],
        "roots": roots,
        "also_roots": also,
        "severity": detect_severity(lines, body_from),
        "title_line": title,
        "mark": mark,
        "hits_fm": _int_of(merged.get("hits")),
        "hit_rows": hit_rows(lines),
        "escalated": str(merged.get("escalated", "")).lower() in ("true", "yes", "1"),
        "headings": headings,
        "kind": kind,
        "author": str(merged.get("author", "")),
        "tags": str(merged.get("tags", "")),
    }


def parse_root(path):
    lines, _ = read_lines(path)
    fields, body_from = parse_front(lines)
    fname = os.path.basename(path)
    m = RT_RE.search(fname)
    rid = "RT-%s" % m.group(1) if m else ""
    title = ""
    for raw in lines[body_from:]:
        if raw.strip().startswith("#"):
            title = re.sub(r"^#+\s*", "", raw.strip())
            break
    return {"path": path, "file": fname, "id": rid, "title": title,
            "cases_fm": _int_of(fields.get("cases")), "hits_fm": _int_of(fields.get("hits")),
            "hit_rows": hit_rows(lines),
            "escalated": str(fields.get("escalated", "")).lower() in ("true", "yes", "1")}


# ================================================================ 派生指标（唯一尺子）
def derive(root):
    """两个指标都从这里出：索引表和根因文件里的数字只是它的投影。"""
    cases = [parse_case(p) for p in case_files(root)]
    rts = [parse_root(p) for p in root_files(root)]
    cases_by_root = {}
    mounts_by_root = {}
    hits_by_root = {}
    for r in rts:
        cases_by_root.setdefault(r["id"], [])
        mounts_by_root.setdefault(r["id"], 0)
        hits_by_root.setdefault(r["id"], 0)
        hits_by_root[r["id"]] += len(r["hit_rows"])
    for c in cases:
        for rid in c["roots"]:
            cases_by_root.setdefault(rid, []).append(c["id"])
            mounts_by_root[rid] = mounts_by_root.get(rid, 0) + 1
            hits_by_root[rid] = hits_by_root.get(rid, 0) + len(c["hit_rows"])
        for rid in c["also_roots"]:
            mounts_by_root[rid] = mounts_by_root.get(rid, 0) + 1
    return {
        "cases": cases,
        "roots": rts,
        "case_ids": [c["id"] for c in cases],
        "cases_by_root": cases_by_root,
        "mounts_by_root": mounts_by_root,
        "hits_by_case": {c["id"]: len(c["hit_rows"]) for c in cases},
        "hits_by_root": hits_by_root,
        "case_total": len(cases),
        "root_total": len(rts),
        "mount_total": sum(mounts_by_root.values()),
        "hit_total": sum(len(c["hit_rows"]) for c in cases) + sum(len(r["hit_rows"]) for r in rts),
    }


# ================================================================ 索引改写（只改数字）
def index_groups(lines):
    """案例清单按 `RT-0NN` 标题分组 ⇒ {RT号: (标题行, 组结束行)}。

    标题级别两/三/四级都认：现役共享库用 `## RT-022 …`，本工具模板用 `### RT-009 …`，
    只写 `###` 会让「分组指向缺失根因」这条判据在现役库上永远不响（形状假设＝判据盲区）。
    """
    out = {}
    cur, start = None, None
    for i, raw in enumerate(lines):
        m = re.match(r"^\s*#{2,4}\s*(RT-\d{3})", raw)
        if m:
            if cur:
                out[cur] = (start, i)
            cur, start = m.group(1), i
            continue
        if cur and re.match(r"^\s*##\s", raw):
            out[cur] = (start, i)
            cur = None
    if cur:
        out[cur] = (start, len(lines))
    return out


def root_table_rows(lines):
    """根因索引表里已有的 RT 号集合。"""
    out = set()
    for raw in lines:
        m = ROOT_ROW_RE.match(raw)
        if m:
            out.add(m.group(1))
    return out


def rewrite_index_counts(lines, d):
    """只替换 `| 案例数 | 🔥命中 |` 两个数字，不碰别人的文字。返回 (新行, 改动说明)。"""
    changed = []
    out = list(lines)
    for i, raw in enumerate(out):
        m = ROOT_ROW_RE.match(raw)
        if not m:
            continue
        rid = m.group(1)
        want_c = len(d["cases_by_root"].get(rid, []))
        want_h = d["hits_by_root"].get(rid, 0)
        t = ROOT_TAIL_RE.search(raw)
        if not t:
            changed.append("跳过 %s：行尾两列的形状不认识，工具没敢猜" % rid)
            continue
        new = raw[:t.start()] + "%s%d%s%d%s%s" % (t.group(1), want_c, t.group(3), want_h,
                                                  t.group(5), t.group(6))
        if new != raw:
            out[i] = new
            changed.append("根因表 %s：案例数 %s→%d，命中 🔥%s→🔥%d"
                           % (rid, t.group(2), want_c, t.group(4), want_h))
    for i, raw in enumerate(out):
        if not CASE_ROW_RE.match(raw):
            continue
        lm = CASE_LINK_RE.search(raw)
        t = CASE_TAIL_RE.search(raw)
        if not lm or not t:
            continue
        cid = os.path.splitext(os.path.basename(lm.group(1)))[0]
        if cid not in d["hits_by_case"] or int(t.group(2)) == d["hits_by_case"][cid]:
            continue
        out[i] = raw[:t.start()] + "🔥%d%s" % (d["hits_by_case"][cid], t.group(3))
        changed.append("案例清单 %s：命中 🔥%s→🔥%d" % (cid, t.group(2), d["hits_by_case"][cid]))
    return out, changed


def rewrite_root_front(path, rid, want_cases, want_hits):
    """改写 roots/RT-*.md 的 frontmatter cases/hits 两个数字。缺字段只报不加（不给别人文件塞新键）。"""
    lines, eol = read_lines(path)
    if not (lines and lines[0].strip() == "---"):
        return None, "缺 YAML 头 ⇒ 未动"
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "YAML 头没闭合 ⇒ 未动"
    seen = {"cases": False, "hits": False}
    notes = []
    dirty = False
    for i in range(1, end):
        for key, want in (("cases", want_cases), ("hits", want_hits)):
            m = re.match(r"^(\s*%s\s*:\s*)(\d+)(.*)$" % key, lines[i])
            if m:
                if int(m.group(2)) != want:
                    lines[i] = "%s%d%s" % (m.group(1), want, m.group(3))
                    dirty = True
                seen[key] = True
    for key, want in (("cases", want_cases), ("hits", want_hits)):
        if not seen[key]:
            notes.append("缺 %s 字段（现值应为 %d，未新增键）" % (key, want))
    note = "；".join(notes)
    # 数字本来就对 ⇒ 返回 None：否则每跑一次 --apply 就把三个根因文件重写一遍、
    # 每次留一个 .bak，「幂等」成了空话（diff 也永远不为零）。
    if not dirty:
        return None, (note or None)
    return lines, (("已改写" + ("；" + note if note else "")) if not note else note + "；已改写")


def rewrite_prose_counts(lines, d):
    """索引头部那句「当前状态：N 条案例文件 …」。默认不动（守最小差异），--prose 才刷。"""
    out = list(lines)
    notes = []
    for i, raw in enumerate(out):
        if "当前状态" not in raw:
            continue
        new = raw
        new = re.sub(r"(当前状态\*\*：)\s*\d+(\s*条案例文件)", r"\g<1>%d\g<2>" % d["case_total"], new)
        new = re.sub(r"\*\*\d+(\s*次挂载\*\*)", lambda m: "**%d%s" % (d["mount_total"], m.group(1)), new)
        new = re.sub(r"(\|\s*)\d+(\s*个根因)", lambda m: "%s%d%s" % (m.group(1), d["root_total"], m.group(2)), new)
        new = re.sub(r"累计命中\s*\d+(\s*次)", lambda m: "累计命中 %d%s" % (d["hit_total"], m.group(1)), new)
        if new != raw:
            out[i] = new
            notes.append("索引头部自述已按现扫更新：案例 %d／根因 %d／挂载 %d／命中 %d"
                         % (d["case_total"], d["root_total"], d["mount_total"], d["hit_total"]))
    return out, notes


# ================================================================ 模板与生成
FULL_BODY = """## 1. 现象

（一行上下文：当时在做什么、什么前提；再贴屏上原文或具体数字）

## 2. 复现

（最短命令序列；撞不出来就写「未能复现 + 当时的条件」，不许留空）

## 3. 现场代码

（`文件:行` + 关键片段，抄改之前那一版）

## 4. 根因（机制）

（本案例的具体机制；归属的根因族写在 frontmatter 的 root 字段，两者别混）

## 5. 修法

（代码级改动 + 为什么这样切）

## 6. 验证

（修后的实测输出 / 断言条数；「改好了」三个字不算验证）

## 7. 能否借鉴（通用规律）

（换个任务还会不会遇到；**适用条件要写清**——别把一次的经验写成永远）

## 8. 教训（一句可迁移纪律）

（一句下次能直接照做的命令句）

## 我自己引入的错

（没有就写「无」，不许删节）

## 来源

{source}

## 命中记录

| 日期 | 场景 | 解决了什么 |
|---|---|---|
"""

LITE_BODY = """## 现象

（一行上下文 + 屏上原文或具体数字）

## 根因（机制）

（为什么会坏；根因族写在 frontmatter 的 root 字段）

## 能否借鉴（通用规律）

（可迁移的规律 + **适用条件**——轻量不等于可以含糊）

## 教训（一句可迁移纪律）

（一句下次能直接照做的命令句）

## 来源

{source}

## 命中记录

| 日期 | 场景 | 解决了什么 |
|---|---|---|
"""


def build_case_text(title, root_cause, also_root, severity, day, author, tags, summary, body, kind, event_id, fp):
    src = " ｜ ".join([day, author or "—", tags or "—"])
    consequence = summary or "（一句话后果待补）"
    mark = "<!-- pitfall-event: %s | fp: %s -->" % (event_id, fp)
    fm = ["---", "root: %s" % root_cause]
    if also_root:
        fm.append("also_root: %s" % also_root)
    fm += ["hits: 0", "last_hit: —", "escalated: false", "---", ""]
    head = ["# 坑：%s —— %s%s" % (title, consequence, (" " + severity) if severity else ""), "", mark, ""]
    if body:
        tail = body.rstrip("\n").split("\n")
        if not any(re.match(r"^\s*#{2,3}\s*命中记录", x) for x in tail):
            tail += ["", "## 命中记录", "", "| 日期 | 场景 | 解决了什么 |", "|---|---|---|", ""]
        lines = fm + head + tail
    else:
        tmpl = LITE_BODY if kind == "lite" else FULL_BODY
        lines = fm + head + tmpl.format(source=src).rstrip("\n").split("\n")
    return "\n".join(lines) + "\n"


def cmd_init(a):
    root = a.root
    os.makedirs(os.path.join(root, "pitfalls"), exist_ok=True)
    os.makedirs(os.path.join(root, "roots"), exist_ok=True)
    os.makedirs(os.path.join(root, CAND_DIR), exist_ok=True)
    made = []
    idx = os.path.join(root, INDEX_NAME)
    if not os.path.isfile(idx):
        atomic_write(idx, ["# 踩坑日志（统一索引）", "",
                           "> 所有记录者共写共用。**两个指标（案例数／命中）由 `scripts/pitfall_log.py counts` 从事件记录生成，不手填。**",
                           "",
                           "## 🧭 根因索引（先看这里）", "",
                           "| 根因 | 一句话 | 案例数 | 命中 |",
                           "|---|---|---|---|", "",
                           "## 📋 案例清单（按根因分组，组内新 → 旧）", ""], "\n", backup=False)
        made.append(INDEX_NAME)
    assets = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets"))
    for src, dst in (("entry-template.md", os.path.join("pitfalls", "_template.md")),
                     ("entry-lite-template.md", os.path.join("pitfalls", "_template-lite.md")),
                     ("root-template.md", os.path.join("roots", "_template.md"))):
        sp, dp = os.path.join(assets, src), os.path.join(root, dst)
        if os.path.isfile(sp) and not os.path.isfile(dp):
            shutil.copyfile(sp, dp)
            made.append(dst)
    say("[init] 库=%s｜新建 %d 件：%s" % (root, len(made), "、".join(made) or "无（骨架已在场）"))
    return 0


# ================================================================ add
def find_by_fp(cases, fp):
    for c in cases:
        if c["mark"] and c["mark"][1] and c["mark"][1] == fp:
            return c
    return None


def root_title_of(root, rid):
    """取根因文件里的一句话根因（frontmatter title 优先，退回正文首个标题）。"""
    p = next((x for x in root_files(root) if os.path.basename(x).startswith(rid)), None)
    if not p:
        return ""
    r = parse_root(p)
    return re.sub(r"^RT-\d{3}\s*", "", r["title"] or "").strip()


def insert_case_row(lines, case, author, tags, root=None):
    """在案例清单对应分组下插一行（组内新→旧 ⇒ 插在表头分隔线后的第一行）；分组缺失则新建。"""
    rid = case["roots"][0] if case["roots"] else "RT-000"
    title = re.sub(r"^坑[：:]\s*", "", case["title_line"]) or case["id"]
    row = "| %s | %s | %s | %s | 🔥0 | [详情](pitfalls/%s.md) |" % (
        case["id"][:10], author or "—", tags or "—", title, case["id"])
    groups = index_groups(lines)
    if rid in groups:
        start, _ = groups[rid]
        pos = start + 1
        while pos < len(lines) and not lines[pos].strip().startswith("|"):
            pos += 1
        if pos < len(lines):
            pos += 1
            while pos < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[pos].strip()):
                pos += 1
        lines.insert(pos, row)
        return lines, "已在 %s 分组插入新行（组内新→旧）" % rid
    head = "### %s %s" % (rid, root_title_of(root, rid) if root else "")
    lines += ["", head.rstrip(), "",
              "| 日期 | 记录者 | 标签 | 一句话标题 | 命中 | 文件 |",
              "|---|---|---|---|---|---|", row, ""]
    return lines, "索引原无 %s 分组 ⇒ 已新建" % rid


def ensure_root_table_row(lines, rid, d, root=None):
    """根因索引表里若没有这个 RT 的行，补一行（数字按现扫给；一句话与链接取自根因文件本体）。"""
    if rid in root_table_rows(lines):
        return lines, "根因表已有 %s" % rid
    link = root_link_of(rid, root) if root else ""
    cell = "[%(rid)s](%(link)s)" % {"rid": rid, "link": link} if link else rid
    one = (root_title_of(root, rid) if root else "") or "（一句话根因待补）"
    row = "| %s | %s | %d | 🔥%d |" % (cell, one, len(d["cases_by_root"].get(rid, [])),
                                       d["hits_by_root"].get(rid, 0))
    for i, raw in enumerate(lines):
        if raw.strip().startswith("|---|") and i and "根因" in lines[i - 1]:
            lines.insert(i + 1, row)
            return lines, "根因表已补 %s 一行（链接%s／一句话：%s）" % (rid, "已指 roots 本体" if link else "缺失", one)
    lines += ["", "| 根因 | 一句话 | 案例数 | 命中 |", "|---|---|---|---|", row]
    return lines, "根因表整表缺失 ⇒ 已新建并补 %s" % rid


ROOT_BODY = """# {rid} {title}

## 一句话根因

{title}

## 识别信号

（出现哪些迹象就该往这里想——最有用的部分，待补）

- 

## 通用对策

（不针对任何具体案例的做法，待补）

1. 

## 挂载案例

| 日期 | 案例 | 命中 |
|---|---|---|

## 命中记录

| 日期 | 场景 | 解决了什么 |
|---|---|---|
"""


def build_root_text(rid, title):
    fm = ["---", "root_id: %s" % rid, "title: %s" % title, "cases: 0", "hits: 0",
          "escalated: false", "---", ""]
    return "\n".join(fm + ROOT_BODY.format(rid=rid, title=title).rstrip("\n").split("\n")) + "\n"


def ensure_root_file(root, rid, one_liner):
    """返回路径。one_liner 只由调用方在「确认这是新根因族」时给——绝不凭号凭空建文件。"""
    hit = next((p for p in root_files(root) if os.path.basename(p).startswith(rid)), None)
    if hit:
        return hit
    path = os.path.join(root, "roots", "%s-%s.md" % (rid, slugify(one_liner or rid)))
    atomic_write(path, build_root_text(rid, one_liner).split("\n"), store_eol(root), backup=False)
    return path


def root_link_of(rid, root):
    p = next((x for x in root_files(root) if os.path.basename(x).startswith(rid)), None)
    return "roots/%s" % os.path.basename(p) if p else ""


def cmd_add(a):
    idx_path = require_store(a.root)
    day = a.date or date.today().isoformat()
    if not DATE_RE.match(day):
        die("--date 必须是 YYYY-MM-DD：%s" % day)
    sev = SEVERITY_MAPPER(a.severity)
    rc = (a.root_cause or "").upper().strip()
    if not RT_RE.fullmatch(rc):
        die("--root-cause 必须是三位数根因号（RT-0NN），收到：%r" % a.root_cause)
    if not a.title.strip():
        die("--title 不能为空")
    also = (a.also_root or "").upper().strip()
    if also and not RT_RE.fullmatch(also):
        die("--also-root 必须是 RT-0NN，收到：%r" % a.also_root)
    kind = a.kind
    if sev == SEV_RED and kind == "lite":
        say("[gate] 🔴 不可再生损失不许用轻量模板 ⇒ 自动升为完整复盘（八段）")
        kind = "full"
    body = ""
    if a.body_file:
        if not os.path.isfile(a.body_file):
            die("--body-file 不存在：%s" % a.body_file)
        with open(a.body_file, "rb") as f:
            body = f.read().decode("utf-8-sig", errors="replace")
    # 锁从「读现状」之前就开始：去重判定和写盘之间若放了缝，两个窗同时投同一事件会各写一份、
    # 索引各插一行——去重只在锁内才成立（git lockfile 的用法同理）。
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        existing_rt = next((p for p in root_files(a.root) if os.path.basename(p).startswith(rc)), None)
        if existing_rt is None:
            if not a.make_root:
                die("根因文件不在场：roots/%s-*.md 没有。先在 roots/ 找现成的同根因挂上去（归因优先）；"
                    "确认这确实是一个新根因族时，加 --make-root \"<一句话根因>\" 才建文件——"
                    "凭一个可能写错的号凭空立族，会把后来人的检索带偏" % rc)
            ensure_root_file(a.root, rc, a.make_root)
            say("[root] 已新建根因文件 %s（一句话根因：%s）" % (rc, a.make_root))
        d0 = derive(a.root)
        fp = event_fingerprint(a.title, rc, sev)
        dup = find_by_fp(d0["cases"], fp)
        if dup:
            say("[dedup] 同一事件已在册 ⇒ 幂等 no-op，计数不变：%s（指纹 %s）" % (dup["id"], fp))
            say("[dedup] 要补正文请改那个文件；本命令不覆盖已有内容（别人/别窗可能正在写）")
            return 0
        event_id = "%s-%s" % (day, a.slug or slugify(a.title))
        path = os.path.join(a.root, "pitfalls", event_id + ".md")
        if os.path.exists(path):
            old = parse_case(path)
            if old["mark"] and old["mark"][1] == fp:
                say("[dedup] 同 ID 同内容 ⇒ 幂等 no-op，计数不变：%s" % event_id)
                return 0
            die("同事件 ID 已有不同内容（%s；旧指纹 %s ≠ 新指纹 %s）——换 --slug 另立一条，绝不覆盖"
                % (event_id, old["mark"][1] if old["mark"] else "无标记", fp), 4)
        atomic_write(path, build_case_text(a.title, rc, also, sev, day, a.author, a.tags,
                                           a.summary, body, kind, event_id, fp).split("\n"),
                     store_eol(a.root), backup=False)
        back = parse_case(path)
        if back["mark"] is None or back["roots"] != [rc]:
            die("写后读回验收失败：%s（frontmatter 或事件标记没落上）——已留在盘上等你裁决，未动索引" % event_id, 1)
        idx_lines, eol = read_lines(idx_path)
        idx_lines, note1 = insert_case_row(idx_lines, back, a.author, a.tags, a.root)
        touch_lock(lock)
        d1 = derive(a.root)
        idx_lines, note2 = ensure_root_table_row(idx_lines, rc, d1, a.root)
        idx_lines, changed = rewrite_index_counts(idx_lines, d1)
        atomic_write(idx_path, idx_lines, eol)
        verify, _ = rewrite_index_counts(read_lines(idx_path)[0], derive(a.root))
        if verify != idx_lines:
            die("索引写后读回验收失败：再生成结果与刚落盘的不一致（未回滚，等你裁决）", 1)
        say("[add] 已登记案例 %s（%s 模板，挂 %s%s）"
            % (event_id, kind, rc, "＋副挂 " + also if also else ""))
        for n in (note1, note2, "根因表与案例清单数字已按现扫刷新（%d 处）" % len(changed)):
            say("[index] %s" % n)
        for r in d1["roots"]:
            if r["id"] != rc:
                continue
            lines, note = rewrite_root_front(r["path"], r["id"],
                                             len(d1["cases_by_root"].get(r["id"], [])),
                                             d1["hits_by_root"].get(r["id"], 0))
            if lines is not None:
                atomic_write(r["path"], lines, read_lines(r["path"])[1])
                say("[root] %s：%s" % (r["id"], note))
            elif note:
                say("[root] %s：%s（数字已对，未写盘）" % (r["id"], note))
    finally:
        release_lock(lock)
    return 0


def SEVERITY_MAPPER(value):
    v = str(value or "").strip()
    table = {"红": SEV_RED, "橙": SEV_ORANGE, "黄": SEV_YELLOW,
             "red": SEV_RED, "orange": SEV_ORANGE, "yellow": SEV_YELLOW,
             SEV_RED: SEV_RED, SEV_ORANGE: SEV_ORANGE, SEV_YELLOW: SEV_YELLOW, "": ""}
    if v in table:
        return table[v]
    die("--severity 只认 红/橙/黄 或 🔴/🟠/🟡，收到：%r" % value)


# ================================================================ hit
def append_hit_row(path, day, scenario, solved):
    """往「## 命中记录」表尾追加一行；同 (日期, 场景归一化) 已存在 ⇒ 幂等 no-op。"""
    lines, eol = read_lines(path)
    rows = hit_rows(lines)
    fp = hit_fingerprint(day, scenario)
    for r in rows:
        if r["fp"] == fp or (r["date"] == day and norm_text(r["scenario"]) == norm_text(scenario)):
            say("[dedup] 同一次命中已在册（第 %d 行）⇒ 计数不变" % (r["line"] + 1))
            return False, "重复提交 ⇒ no-op"
    if not any(re.match(r"^\s*#{2,3}\s*命中记录", ln) for ln in lines):
        lines += ["", "## 命中记录", "", "| 日期 | 场景 | 解决了什么 |", "|---|---|---|"]
    last = None
    anchor = None
    for i, ln in enumerate(lines):
        if not re.match(r"^\s*#{2,3}\s*命中记录", ln):
            continue
        j = i + 1
        while j < len(lines) and not re.match(r"^\s*#{1,3}\s", lines[j]):
            s = lines[j].strip()
            if s.startswith("|"):
                if re.match(r"^\|[\s:|-]+\|$", s):
                    anchor = j              # 分隔线：没有数据行时插在它后面
                elif s.strip("|").split("|")[0].strip() != "日期":
                    last = j                # 数据行：插在最后一行后面
            j += 1
    pos = (last + 1) if last is not None else ((anchor + 1) if anchor is not None else None)
    if pos is None:
        die("命中记录表定位失败（有段标题却没有表）：%s" % path)
    lines.insert(pos, "| %s | %s | %s <!-- fp:%s --> |" % (day, scenario, solved, fp))
    atomic_write(path, lines, eol)
    return True, "已追加（本条累计 %d 次）" % (len(rows) + 1)


def rewrite_case_front_hits(path, want):
    """案例 frontmatter 的 hits 是投影，不是第二只手写的数：缺键只报不塞（别人家的条目形状不一）。"""
    lines, eol = read_lines(path)
    if not (lines and lines[0].strip() == "---"):
        # 行内引用块形态（`> root: RT-013 ｜ hits: 1 ｜ …`，写在标题下面）：没有 YAML 头也要能刷数，
        # 否则这条案例的 hits 永远停在当年手写的值，R6「frontmatter 与命中记录不符」会变成常驻红。
        headings = 0
        for i, raw in enumerate(lines[:12]):
            if re.match(r"^\s*#{1,6}\s", raw):
                headings += 1
                if headings >= 2:
                    break
                continue
            m = re.search(r"(hits\s*:\s*)(\d+)", raw)
            if m and raw.lstrip().startswith(">"):
                if int(m.group(2)) == want:
                    return None, None
                lines[i] = raw[:m.start()] + "%s%d" % (m.group(1), want) + raw[m.end():]
                return lines, "行内 hits %s→%d" % (m.group(2), want)
        return None, "缺 YAML 头且无行内 hits ⇒ 未动（现值应为 %d）" % want
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "YAML 头没闭合 ⇒ 未动"
    for i in range(1, end):
        m = re.match(r"^(\s*hits\s*:\s*)(\d+)(.*)$", lines[i])
        if m:
            if int(m.group(2)) == want:
                return None, None
            lines[i] = "%s%d%s" % (m.group(1), want, m.group(3))
            return lines, "hits %s→%d" % (m.group(2), want)
    return None, "缺 hits 字段（现值应为 %d，未新增键）" % want


def refresh_counts(root, idx_path, write=True):
    """把两个指标从记录刷进索引、根因文件与案例 frontmatter；返回改动处数。"""
    d = derive(root)
    idx_lines, eol = read_lines(idx_path)
    new_idx, changed = rewrite_index_counts(idx_lines, d)
    notes = list(changed)
    if write and new_idx != idx_lines:
        atomic_write(idx_path, new_idx, eol)
    for r in d["roots"]:
        lines, note = rewrite_root_front(r["path"], r["id"],
                                         len(d["cases_by_root"].get(r["id"], [])),
                                         d["hits_by_root"].get(r["id"], 0))
        if note:
            notes.append("根因 %s：%s" % (r["id"], note))
        if lines is not None and write:
            _, e2 = read_lines(r["path"])
            atomic_write(r["path"], lines, e2)
    for c in d["cases"]:
        lines, note = rewrite_case_front_hits(c["path"], len(c["hit_rows"]))
        if note and "未新增键" in note:
            notes.append(note + "：" + c["id"])
        if lines is not None and write:
            _, e2 = read_lines(c["path"])
            atomic_write(c["path"], lines, e2)
            notes.append("案例 " + c["id"] + "：" + note)
    return len(changed), d, notes


def cmd_hit(a):
    idx_path = require_store(a.root)
    day = a.date or date.today().isoformat()
    if not DATE_RE.match(day):
        die("--date 必须是 YYYY-MM-DD：%s" % day)
    if not (a.case or a.root_cause):
        die("hit 需要 --case <案例ID> 或 --root-cause RT-0NN（只记根因层命中）二者之一")
    if a.case and a.root_cause:
        die("--case 与 --root-cause 互斥：案例层命中与根因层命中分开记，各 +1")
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        if a.root_cause:
            rid = a.root_cause.upper().strip()
            if not RT_RE.fullmatch(rid):
                die("--root-cause 必须是 RT-0NN，收到：%r" % a.root_cause)
            target = next((p for p in root_files(a.root) if os.path.basename(p).startswith(rid)), None)
            if not target:
                die("根因文件不存在：%s" % rid)
            _changed, note = append_hit_row(target, day, a.scenario, a.solved or "—")
            say("[hit] 根因层 %s：%s" % (rid, note))
        else:
            name = a.case[:-3] if a.case.endswith(".md") else a.case
            if "/" in name or "\\" in name:
                name = os.path.splitext(os.path.basename(name))[0]
            target = os.path.join(a.root, "pitfalls", name + ".md")
            if not os.path.isfile(target):
                die("案例文件不存在：%s" % target)
            _changed, note = append_hit_row(target, day, a.scenario, a.solved or "—")
            say("[hit] 案例 %s：%s" % (name, note))
        touch_lock(lock)
        n, d, extra = refresh_counts(a.root, idx_path, write=True)
        say("[hit] 数字已按现扫刷新（索引 %d 处）：案例 %d｜根因 %d｜挂载 %d｜命中合计 %d"
            % (n, d["case_total"], d["root_total"], d["mount_total"], d["hit_total"]))
        for x in extra:
            say("[hit] " + x)
    finally:
        release_lock(lock)
    return 0


# ================================================================ counts / check
def cmd_counts(a):
    idx_path = require_store(a.root)
    d = derive(a.root)
    idx_lines, eol = read_lines(idx_path)
    new_idx, changed = rewrite_index_counts(idx_lines, d)
    if a.prose:
        new_idx, _notes = rewrite_prose_counts(new_idx, d)
    root_notes = []
    plan = []  # --apply 才落盘；干跑只把「准备改什么」打出来
    if new_idx != idx_lines:
        plan.append((idx_path, new_idx, eol, "索引 %d 处数字" % len(changed)))
    for r in d["roots"]:
        lines, note = rewrite_root_front(r["path"], r["id"],
                                         len(d["cases_by_root"].get(r["id"], [])),
                                         d["hits_by_root"].get(r["id"], 0))
        if lines is not None:
            plan.append((r["path"], lines, read_lines(r["path"])[1], "根因 %s：%s" % (r["id"], note)))
        elif note and note != "已改写":
            root_notes.append("%s：%s" % (r["id"], note))
    for c in d["cases"]:
        lines, note = rewrite_case_front_hits(c["path"], len(c["hit_rows"]))
        if lines is not None:
            plan.append((c["path"], lines, read_lines(c["path"])[1], "案例 %s：%s" % (c["id"], note)))
        elif note:
            root_notes.append(note + "（%s）" % c["id"])
    if a.json:
        say(json.dumps({"case_total": d["case_total"], "root_total": d["root_total"],
                        "mount_total": d["mount_total"], "hit_total": d["hit_total"],
                        "cases_by_root": {k: v for k, v in sorted(d["cases_by_root"].items())},
                        "hits_by_case": d["hits_by_case"],
                        "hits_by_root": d["hits_by_root"]}, ensure_ascii=False, indent=2))
    else:
        say("== 从事件记录现扫（唯一尺子 derive）==")
        say("案例文件 %d ｜ 根因 %d ｜ 挂载 %d（含副挂面）｜ 命中合计 %d"
            % (d["case_total"], d["root_total"], d["mount_total"], d["hit_total"]))
        for r in d["roots"]:
            say("  %-7s 案例数 %-2d（索引/文件写 %d）命中 %-2d（本文件记录 %d＋下挂案例命中 %d）%s"
                % (r["id"], len(d["cases_by_root"].get(r["id"], [])), r["cases_fm"],
                   d["hits_by_root"].get(r["id"], 0), len(r["hit_rows"]),
                   d["hits_by_root"].get(r["id"], 0) - len(r["hit_rows"]),
                   "⭐已升华" if r["escalated"] else ""))
        for c in d["cases"]:
            if c["hits_fm"] != len(c["hit_rows"]):
                say("  %-46s 案例 frontmatter hits=%d ≠ 命中记录 %d 行" % (c["id"], c["hits_fm"], len(c["hit_rows"])))
        if changed:
            say("-- 与索引现值的差异（%d 处）--" % len(changed))
            for c in changed:
                say("  " + c)
        if root_notes:
            say("-- 根因文件侧 --")
            for c in root_notes:
                say("  " + c)
    if not a.apply:
        if plan:
            say("-- 干跑：加 --apply 将改写 %d 个文件 --" % len(set(p for p, _l, _e, _w in plan)))
            for _p, _l, _e, how in plan:
                say("  " + how)
        else:
            say("[counts] 数字已与记录一致 ⇒ 无需写盘")
        say("[counts] 干跑（默认）：未改一个字节。")
        return 0
    if not plan:
        say("[counts] 数字已与记录一致 ⇒ 未写一个字节（幂等 no-op）")
        return 0
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        for p, lines, peol, how in plan:
            atomic_write(p, lines, peol)
            say("[counts] 已写 %s ｜ %s"
                % (os.path.relpath(p, a.root).replace(os.sep, "/"), how))
        back, _ = read_lines(idx_path)
        again, _ = rewrite_index_counts(back, derive(a.root))
        if again != back:
            if os.path.exists(idx_path + ".bak"):
                shutil.copy2(idx_path + ".bak", idx_path)
            die("写后读回验收失败：重算结果与刚落盘的不一致 ⇒ 索引已从 .bak 回滚", 1)
        say("[counts] 写后读回验收通过（现扫：案例 %d｜根因 %d｜挂载 %d｜命中 %d）"
            % (d["case_total"], d["root_total"], d["mount_total"], d["hit_total"]))
    finally:
        release_lock(lock)
    return 0


def check_drift(root):
    idx_path = require_store(root)
    d = derive(root)
    idx_lines, _ = read_lines(idx_path)
    items = []

    def add(rule, msg, level="红"):
        items.append({"rule": rule, "level": level, "msg": msg})

    listed = set()
    for raw in idx_lines:
        if CASE_ROW_RE.match(raw):
            m = CASE_LINK_RE.search(raw)
            if m:
                listed.add(os.path.splitext(os.path.basename(m.group(1)))[0])
    for cid in d["case_ids"]:
        if cid not in listed:
            add("R1 案例无索引行", "%s 在 pitfalls/ 在场，索引却没有这一行" % cid)
    for cid in sorted(listed - set(d["case_ids"])):
        add("R2 索引行指向缺失文件", cid)
    for i, raw in enumerate(idx_lines):
        m = ROOT_ROW_RE.match(raw)
        if not m:
            continue
        rid = m.group(1)
        t = ROOT_TAIL_RE.search(raw)
        if not t:
            add("R3 行尾形状不可解析", "索引第 %d 行的「案例数/命中」两列长得不认识，工具没敢猜：%s"
                % (i + 1, raw[:70]), "警")
            continue
        want_c = len(d["cases_by_root"].get(rid, []))
        want_h = d["hits_by_root"].get(rid, 0)
        if int(t.group(2)) != want_c:
            add("R3 案例数漂移", "%s 索引写 %s，现扫 %d" % (rid, t.group(2), want_c))
        if int(t.group(4)) != want_h:
            add("R3 命中漂移", "%s 索引写 🔥%s，现扫 %d" % (rid, t.group(4), want_h))
    for raw in idx_lines:
        if not CASE_ROW_RE.match(raw):
            continue
        lm, t = CASE_LINK_RE.search(raw), CASE_TAIL_RE.search(raw)
        if not lm or not t:
            continue
        cid = os.path.splitext(os.path.basename(lm.group(1)))[0]
        if cid in d["hits_by_case"] and int(t.group(2)) != d["hits_by_case"][cid]:
            add("R3 案例行命中漂移", "%s 索引写 🔥%s，现扫 %d" % (cid, t.group(2), d["hits_by_case"][cid]))
    for r in d["roots"]:
        want_c = len(d["cases_by_root"].get(r["id"], []))
        want_h = d["hits_by_root"].get(r["id"], 0)
        if r["cases_fm"] != want_c:
            add("R4 根因文件 cases 漂移", "%s 文件写 %d，现扫 %d" % (r["id"], r["cases_fm"], want_c))
        if r["hits_fm"] != want_h:
            add("R4 根因文件 hits 漂移", "%s 文件写 %d，现扫 %d（本文件命中记录 %d＋下挂案例命中 %d）"
                % (r["id"], r["hits_fm"], want_h, len(r["hit_rows"]), want_h - len(r["hit_rows"])))
    by_fp = {}
    no_mark = []
    for c in d["cases"]:
        if c["mark"] and c["mark"][1]:
            by_fp.setdefault(c["mark"][1], []).append(c["id"])
        else:
            no_mark.append(c["id"])
    if no_mark:
        # 46 条历史条目各报一行会把真正的新问题淹掉 ⇒ 聚合成一行，只点名前 5 条
        add("R5 无事件标记", "%d 条缺 `<!-- pitfall-event: … | fp: … -->`（例：%s%s）"
            "⇒ 计数靠现扫仍能成立，但重复提交幂等对它们无效；新条目必须带，历史条目可补"
            % (len(no_mark), "、".join(no_mark[:5]), "…" if len(no_mark) > 5 else ""), "警")
    for fp, ids in sorted(by_fp.items()):
        if len(ids) > 1:
            add("R5 同一事件重复登记", "指纹 %s 出现在 %s ⇒ 计数只算一次，但要人裁决合并" % (fp, "、".join(ids)))
    for c in d["cases"]:
        if c["hits_fm"] != len(c["hit_rows"]):
            add("R6 案例 hits 与记录不符", "%s frontmatter 写 %d，命中记录表实际 %d 行"
                % (c["id"], c["hits_fm"], len(c["hit_rows"])))
    for c in d["cases"]:
        if c["kind"] == "legacy":
            continue
        need = FULL_SECTIONS if c["kind"] == "full" else LITE_SECTIONS
        missing = [s for s in need if not has_section(c["headings"], s)]
        if c["severity"] == SEV_RED and c["kind"] == "lite":
            add("R7 🔴 用了轻量模板", "%s 不可再生损失必须走完整复盘（八段＋我自己引入的错）" % c["id"])
        elif missing:
            add("R7 缺段", "%s（%s 模板）缺：%s" % (c["id"], c["kind"], "、".join(missing)), "警")
    nums = sorted(int(RT_RE.search(os.path.basename(r["file"])).group(1)) for r in d["roots"]
                  if RT_RE.search(r["file"]))
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums] if nums else []
    if gaps:
        add("R8 根因编号空缺", "缺 RT-%s（编号只增不改 ⇒ 空缺要写明理由）" % "、RT-".join("%03d" % g for g in gaps), "警")
    head = "\n".join(idx_lines[:40])
    for m in re.finditer(r"(\d+)\s*条案例文件", head):
        if int(m.group(1)) != d["case_total"]:
            add("R9 头部自述漂移", "「%s 条案例文件」≠ 现扫 %d（counts --prose 可刷）" % (m.group(1), d["case_total"]))
    have_rts = set(r["id"] for r in d["roots"])
    for i, raw in enumerate(idx_lines):
        m = ROOT_ROW_RE.match(raw)
        if not m:
            continue
        rid = m.group(1)
        lm = re.match(r"^\|\s*\[(RT-\d{3})\]\(([^)]+\.md)\)", raw.strip())
        if not lm:
            add("R10 根因表行没链到本体", "索引第 %d 行 %s 只有号没有 roots/ 链接 ⇒ 检索落到索引就断"
                % (i + 1, rid), "警")
            continue
        target = os.path.normpath(os.path.join(root, lm.group(2).replace("/", os.sep)))
        if not os.path.isfile(target):
            add("R10 根因表链接指向缺失文件", "%s → %s（roots/ 里没有这个文件）" % (rid, lm.group(2)))
        if lm.group(1) != rid:
            add("R10 根因表链接与号不符", "第 %d 行行首是 %s，链接文字却写 %s" % (i + 1, rid, lm.group(1)))
    for rid in sorted(index_groups(idx_lines)):
        if rid not in have_rts:
            add("R10 分组指向缺失根因", "索引有 %s 案例分组（任意标题级别），roots/ 里却没有 %s 本体文件（凭空立族／文件被搬走）"
                % (rid, rid))
    return items, d


def cmd_check(a):
    items, d = check_drift(a.root)
    reds = [x for x in items if x["level"] == "红"]
    warns = [x for x in items if x["level"] != "红"]
    if a.json:
        say(json.dumps({"summary": {"case_total": d["case_total"], "root_total": d["root_total"],
                                    "mount_total": d["mount_total"], "hit_total": d["hit_total"],
                                    "red": len(reds), "warn": len(warns)},
                        "items": items}, ensure_ascii=False, indent=2))
    else:
        say("== 一致性自检（索引 ↔ 事件记录 ↔ 根因文件）==")
        say("现扫：案例 %d ｜ 根因 %d ｜ 挂载 %d ｜ 命中合计 %d"
            % (d["case_total"], d["root_total"], d["mount_total"], d["hit_total"]))
        for x in items:
            say("  [%s] %s ｜ %s ｜ %s" % (x["level"], x["rule"], x["msg"], ""))
        if not items:
            say("  无漂移：三侧数字与事件记录逐条对得上。")
    say("[check] 红 %d ｜ 警 %d" % (len(reds), len(warns)))
    return 1 if reds else 0


# ================================================================ promote
CAND_TEMPLATE = """# 升华候选：{title}

- 候选编号：{cid}
- 来源：{src}
- 状态：候选（未提升）
- 建立：{today}

## 适用范围（必填）

{scope}

## 反例（必填——什么情况下这条不成立）

（待填）

## 边界（必填——超出什么范围就别用）

（待填）

## 复核日期（必填——到期没复核就退回候选）

（待填 YYYY-MM-DD）

## 提升记录

（工具在 --apply 过闸后写入）
"""


def case_scope(c):
    """案例「能否借鉴」段的正文＝它自己声明的适用条件；取不到就返回空（＝待补，不许当通用）。"""
    lines, _ = read_lines(c["path"])
    body = section_body(lines, r"能否借鉴|通用规律")
    return " ".join(body).strip()


def cmd_promote(a):
    require_store(a.root)
    d = derive(a.root)
    cdir = os.path.join(a.root, CAND_DIR)
    os.makedirs(cdir, exist_ok=True)
    if a.scan:
        made, kept = [], []
        for rid, ids in sorted(d["cases_by_root"].items()):
            if len(ids) < a.threshold:
                continue
            by_scope = {}
            for cid in ids:
                c = next(x for x in d["cases"] if x["id"] == cid)
                sc = case_scope(c)
                by_scope.setdefault(norm_text(sc) or "__待补__", {"ids": [], "scope": sc})["ids"].append(cid)
            merge_note = ("｜🔴 该根因下有 %d 种不同适用条件 ⇒ 分轨各立候选，不得合成一条无条件规则"
                          % len(by_scope)) if len(by_scope) > 1 else ""
            for gi, (key, grp) in enumerate(sorted(by_scope.items()), 1):
                cid_name = "C-%s-%02d-%s" % (rid, gi, hashlib.sha1(key.encode("utf-8")).hexdigest()[:6])
                path = os.path.join(cdir, cid_name + ".md")
                if os.path.isfile(path):
                    kept.append(cid_name)
                    continue
                title = "同根因 %s 已攒 %d 条（本候选只覆盖其中 %d 条＝适用条件一致的那批）%s" % (
                    rid, len(ids), len(grp["ids"]), merge_note)
                scope = ("- " + grp["scope"]) if grp["scope"] else "（待填：这批案例共同的适用条件）"
                atomic_write(path, CAND_TEMPLATE.format(title=title, cid=cid_name, src="、".join(grp["ids"]),
                                                        scope=scope, today=date.today().isoformat()
                                                        ).split("\n"), store_eol(a.root), backup=False)
                made.append(cid_name)
        for c in d["cases"]:
            h = len(c["hit_rows"])
            if h < a.hits_threshold or c["escalated"]:
                continue
            cid_name = "C-%s-h%d" % (c["id"], h)
            path = os.path.join(cdir, cid_name + ".md")
            if os.path.isfile(path):
                kept.append(cid_name)
                continue
            sc = case_scope(c)
            atomic_write(path, CAND_TEMPLATE.format(title="单条被召回 %d 次：%s" % (h, c["title_line"] or c["id"]),
                                                    cid=cid_name, src=c["id"],
                                                    scope=("- " + sc) if sc else "（待填）",
                                                    today=date.today().isoformat()).split("\n"),
                                    store_eol(a.root), backup=False)
            made.append(cid_name)
        say("[promote --scan] 新建候选 %d ｜ 已在册保留 %d（阈值：同根因 %d 条／单条召回 %d 次）"
            % (len(made), len(kept), a.threshold, a.hits_threshold))
        for x in made:
            say("  新候选：%s" % x)
        for x in kept:
            say("  保留：%s" % x)
        if not made and not kept:
            say("  本轮没有任何根因／案例达到阈值 ⇒ 候选区为空（这正是「固定次数自动升永久规则」被关掉的样子）")
        return 0
    if not a.candidate:
        die("promote 要么 --scan，要么 --gate <候选编号> [--apply]")
    path = os.path.join(cdir, a.candidate if a.candidate.endswith(".md") else a.candidate + ".md")
    if not os.path.isfile(path):
        say("[promote] 候选不存在：%s（先跑 promote --scan）" % a.candidate)
        return 3
    ok, notes = gate(path)
    say("== 升华闸门：%s ==" % a.candidate)
    for n in notes:
        say("  " + n)
    if not ok:
        say("[promote] 闸门没过 ⇒ 留在候选区继续留验，不提升")
        return 1 if a.apply else 0
    if not a.apply:
        say("[promote] 闸门已过（干跑）：加 --apply 才写 escalated／索引 ⭐／提升记录")
        return 0
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        lines, eol = read_lines(path)
        for i, raw in enumerate(lines):
            if raw.strip().startswith("- 状态："):
                lines[i] = "- 状态：已提升（%s）" % date.today().isoformat()
        for i, raw in enumerate(lines):
            if re.match(r"^\s*##\s*提升记录", raw):
                lines[i + 1:i + 1] = ["", "- 提升时间：%s（闸门通过，工具写入）" % date.today().isoformat(),
                                      "- 提升去向：铁律区由各 agent 自己的 store CORE 承接——本工具不代写别人的 store"]
                break
        atomic_write(path, lines, eol)
        srcs = _candidate_sources(path)
        touched = []
        for rid in [s for s in srcs if RT_RE.fullmatch(s)]:
            for p in root_files(a.root):
                if os.path.basename(p).startswith(rid):
                    if mark_field(p, "escalated", "true"):
                        touched.append(rid)
        for cid in [s for s in srcs if not RT_RE.fullmatch(s)]:
            p = os.path.join(a.root, "pitfalls", cid + ".md")
            if os.path.isfile(p) and mark_field(p, "escalated", "true"):
                touched.append(cid)
        idx_path = os.path.join(a.root, INDEX_NAME)
        idx_lines, ieol = read_lines(idx_path)
        star = 0
        for i, raw in enumerate(idx_lines):
            m = ROOT_ROW_RE.match(raw)
            if not m or m.group(1) not in srcs or "⭐" in raw:
                continue
            pos = raw.rstrip().rfind("|")
            if pos < 0:
                continue
            idx_lines[i] = raw[:pos].rstrip() + " ⭐已提升→%s |" % a.candidate
            star += 1
        for i, raw in enumerate(idx_lines):
            # 案例行的 ⭐ 只能加在标题格里：加在行尾会把 `…🔥N | [详情](…) |` 的尾巴撑坏，
            # 计数改写就再也认不出这一行（静默漏改比报错更难发现）。
            if not CASE_ROW_RE.match(raw) or "⭐" in raw:
                continue
            lm = CASE_LINK_RE.search(raw)
            if not lm:
                continue
            cid = os.path.splitext(os.path.basename(lm.group(1)))[0]
            if cid not in srcs:
                continue
            cells = raw.split("|")
            if len(cells) < 5:
                continue
            cells[4] = cells[4].rstrip() + " ⭐"
            idx_lines[i] = "|".join(cells)
            star += 1
        atomic_write(idx_path, idx_lines, ieol)
        back = read_lines(path)[0]
        if not any("已提升" in x for x in back):
            die("写后读回验收失败：候选状态没落上（未动其它文件）", 1)
        say("[promote] 已提升：候选状态改写＋来源 escalated 标记 %d 处＋索引加 ⭐ %d 处" % (len(touched), star))
    finally:
        release_lock(lock)
    return 0


def _candidate_sources(path):
    lines, _ = read_lines(path)
    for raw in lines:
        m = re.match(r"^\s*-\s*来源[：:]\s*(.+)$", raw)
        if m:
            return [x.strip() for x in re.split(r"[、,，]", m.group(1)) if x.strip()]
    return []


def mark_field(path, key, value):
    lines, eol = read_lines(path)
    if not (lines and lines[0].strip() == "---"):
        return False
    for i in range(1, min(len(lines), 20)):
        if lines[i].strip() == "---":
            break
        m = re.match(r"^(\s*%s\s*:\s*)(.*)$" % key, lines[i])
        if m:
            lines[i] = "%s%s" % (m.group(1), value)
            atomic_write(path, lines, eol)
            return True
    return False


def cand_field_values(lines):
    """按 `## 适用范围（…）` 这类标题切候选文件，返回 {字段: 正文行列表}。"""
    vals, cur = {}, None
    for raw in lines:
        m = re.match(r"^\s*##\s*(.+?)$", raw)
        if m:
            name = re.sub(r"（.*$", "", m.group(1)).strip()
            cur = name if name in CAND_FIELDS else None
            if cur:
                vals.setdefault(cur, [])
            continue
        if cur:
            t = raw.strip()
            if t:
                vals[cur].append(t)
    return vals


def gate(path):
    """闸门四要素齐＋反例不许「无」＋措辞不许无边界＋复核日期有效＋同根因不同范围不许合并。"""
    lines, _ = read_lines(path)
    vals = cand_field_values(lines)
    notes, ok = [], True
    for k in CAND_FIELDS:
        body = vals.get(k, [])
        joined = " ".join(body).strip()
        # 模板占位一律算没填：不只「（待填…）」——从案例正文抄来的「（换个任务还会不会…）」同样是没写字的骨架
        if not body or joined in ("—", "-", "") or (joined.startswith("（") and joined.endswith("）")):
            notes.append("✗ 缺「%s」——四要素不齐不得提升（占位文案不算填过）" % k)
            ok = False
            continue
        notes.append("✓「%s」已填：%s" % (k, joined[:70]))
        vals[k] = joined
    if not ok:
        return False, notes
    if re.match(r"^(无|没有|暂无|—)", vals["反例"].replace("- ", "").strip()):
        notes.append("✗ 反例写「无」＝没验证过它什么时候不成立 ⇒ 不放行（候选必须能证伪）")
        return False, notes
    banned = [w for w in UNIVERSAL_WORDS if w in vals["适用范围"]]
    if banned:
        notes.append("✗ 适用范围含无边界措辞「%s」⇒ 局部经验不许写成对所有任务生效" % "、".join(banned))
        return False, notes
    m = re.search(r"\d{4}-\d{2}-\d{2}", vals["复核日期"])
    if not m:
        notes.append("✗ 复核日期不是 YYYY-MM-DD")
        return False, notes
    try:
        due = datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        notes.append("✗ 复核日期无法解析：%s" % m.group(0))
        return False, notes
    if due < date.today():
        notes.append("✗ 复核日期 %s 已过期 ⇒ 先复核再提升" % m.group(0))
        return False, notes
    notes.append("✓ 复核日期 %s 有效" % m.group(0))
    rid = RT_RE.search(os.path.basename(path))
    if rid:
        for name in sorted(os.listdir(os.path.dirname(path))):
            if name == os.path.basename(path) or not name.startswith("C-%s-" % rid.group(0)):
                continue
            other = cand_field_values(read_lines(os.path.join(os.path.dirname(path), name))[0])
            if not any("已提升" in x for x in read_lines(os.path.join(os.path.dirname(path), name))[0][:8]):
                continue
            oscope = " ".join(other.get("适用范围", [])).strip()
            if oscope and norm_text(oscope) != norm_text(vals["适用范围"]):
                notes.append("✗ 同根因 %s 下已提升的「%s」适用范围与本条不同 ⇒ 不得合成一条无条件规则；"
                             "本条只能在自己的范围内另立一条" % (rid.group(0), name))
                return False, notes
    notes.append("✓ 闸门全过：可提升（提升去向见候选文件「提升记录」）")
    return True, notes


# ================================================================ CLI
def build_parser(argv=None):
    p = argparse.ArgumentParser(prog="pitfall_log.py", description="踩坑日志确定性维护工具（计数从事件记录生成）")
    p.add_argument("--root", required=True, help="log-root 目录（必填，不给默认当前目录＝防空集假绿）")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    sp = sub.add_parser("init", help="建最小可运行骨架（索引＋roots/＋pitfalls/＋_candidates/＋模板）")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", help="登记一条案例（事件唯一 ID ＋ 幂等去重 ＋ 原子写）")
    sp.add_argument("--title", required=True)
    sp.add_argument("--root-cause", dest="root_cause", required=True)
    sp.add_argument("--make-root", dest="make_root", default="",
                    help="该 RT 号在 roots/ 里没有本体文件时，用它给的一句话根因新建（不填＝拒绝，防凭错号凭空立族）")
    sp.add_argument("--also-root", dest="also_root", default="")
    sp.add_argument("--kind", choices=("full", "lite"), default="full")
    sp.add_argument("--severity", default="")
    sp.add_argument("--date", default="")
    sp.add_argument("--author", default="")
    sp.add_argument("--tags", default="")
    sp.add_argument("--summary", default="")
    sp.add_argument("--body-file", dest="body_file", default="")
    sp.add_argument("--slug", default="")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("hit", help="记一次命中（同一次重复提交不重复计数）")
    sp.add_argument("--case", default="")
    sp.add_argument("--root-cause", dest="root_cause", default="")
    sp.add_argument("--date", default="")
    sp.add_argument("--scenario", required=True)
    sp.add_argument("--solved", default="")
    sp.set_defaults(func=cmd_hit)

    sp = sub.add_parser("counts", help="从事件记录生成两个指标（默认干跑）")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--prose", action="store_true", help="顺带刷新索引头部自述数字（默认不动＝守最小差异）")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_counts)

    sp = sub.add_parser("check", help="索引 ↔ 事件记录 ↔ 根因文件 一致性自检")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("promote", help="候选 → 验证适用范围 → 提升")
    sp.add_argument("--scan", action="store_true")
    sp.add_argument("--gate", dest="candidate", default="")
    sp.add_argument("--apply", dest="apply", action="store_true")
    sp.add_argument("--threshold", type=int, default=7)
    sp.add_argument("--hits-threshold", dest="hits_threshold", type=int, default=3)
    sp.set_defaults(func=cmd_promote)
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    args.root = os.path.abspath(args.root)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
