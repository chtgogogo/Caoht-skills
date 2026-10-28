#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pitfall_log — 踩坑日志的确定性维护工具（零依赖，Python 3.8+，跨平台）。

它只负责把「手维护会变错的东西」变成「从事件记录生成的东西」，四件事：

  ① 事件唯一 ID 与幂等登记——同一条坑重复提交，计数只加一次
  ② 两个指标自动维护——「同类案例数」与「实际帮助次数」分开算、都由本脚本生成，不手填
  ③ 升华闸门——候选 → 验证适用范围 → 提升；缺反例／边界／复核日期一律不放行
  ④ 防腐闭环——读侧 check、退役/并档（没有删除通道的库必腐）、命中抽审、随包自证 selftest

用法（`--root` 必填，不给默认当前目录：在错的地方跑会量到 0 条却打「无漂移」＝假绿灯；selftest 除外）：

  py pitfall_log.py --root <log-root> init
  py pitfall_log.py --root <log-root> add --title <标题> --root-cause RT-0NN
                    [--kind full|lite] [--severity 红|橙|黄] [--date YYYY-MM-DD]
                    [--author <记录者>] [--tags a,b] [--summary <一句话后果>]
                    [--body-file <正文文本>] [--slug <覆盖>] [--also-root RT-0MM]
  py pitfall_log.py --root <log-root> hit --case <案例ID> --scenario <当时在做什么>
                    [--date YYYY-MM-DD] [--solved <解决了什么>]
  py pitfall_log.py --root <log-root> hit --root-cause RT-0NN --scenario <…>   # 只走根因层的命中
  py pitfall_log.py --root <log-root> counts [--json] [--prose] [--apply]
  py pitfall_log.py --root <log-root> check  [--json] [--deep]   # --deep＝跨工件对账（模板↔脚本常量）
  py pitfall_log.py --root <log-root> promote --scan [--threshold 7] [--hits-threshold 3]
  py pitfall_log.py --root <log-root> promote --gate C-id [--apply]
  py pitfall_log.py --root <log-root> retire --case <案例ID> --reason <理由>   # 退役＝软删进 archive/
  py pitfall_log.py --root <log-root> retire --rt RT-0NN --reason <理由>       # 空根因族退役（下挂案例先清零）
  py pitfall_log.py --root <log-root> merge-roots --into RT-0AA --from RT-0BB [--yes]   # 重复根因族并档
  py pitfall_log.py --root <log-root> audit [--sample K]                       # 命中抽审（只读，防虚报）
  py pitfall_log.py selftest                                                   # 随包自证（无需 --root）

退出码：0 成功／无漂移 ｜ 1 有漂移或闸门没过 ｜ 2 用法与目录错误
        3 候选不存在 ｜ 4 幂等冲突（同一事件 ID 撞上不同内容）｜ 5 抢锁失败
        ｜ selftest 有 FAIL 也是 1

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
import subprocess
import sys
import tempfile
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
# 索引有界（SKILL.md 原则的数值化）：案例数超过这个数就该退役冷条目，别把索引养成第二个全文。
# 唯一权威在脚本；正文与模板的职责是「指向工具」，不许另抄一份数字（抄了必漂）。
INDEX_BOUND_CASES = 50

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
            "has_yaml": bool(lines and lines[0].strip() == "---"),
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


def check_drift(root, deep=False):
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

    # ---- 防腐闭环（读侧／退役通道／版本史／提升后复核都从这里提示） ----
    if d["case_total"] > INDEX_BOUND_CASES:
        add("R11 案例数超界", "pitfalls/ 现 %d 条 ＞ %d ⇒ 索引有界原则：冷条目该退役了（retire --case … --reason …），别把索引养成第二个全文"
            % (d["case_total"], INDEX_BOUND_CASES), "警")
    for r in d["roots"]:
        if not r["has_yaml"]:
            add("R12 根因文件缺 YAML 头", "%s ⇒ cases/hits 派生数字刷不进去（rewrite 只报不改）" % r["id"], "警")
    in_repo, tracked = _git_state(root)
    if not in_repo:
        add("R13 无版本史", "log-root 不在任何 git 仓内 ⇒ 回滚只剩单代 .bak、泄改无审计；建议 git init（或把库挪进已跟踪的仓）", "警")
    elif not tracked:
        add("R13 版本史未覆盖索引", "在 git 仓内但 %s 未被跟踪 ⇒ 内容变更无从回溯（git add 提交一次即有史）" % INDEX_NAME, "警")
    cand_dirp = os.path.join(root, CAND_DIR)
    if os.path.isdir(cand_dirp):
        for name in sorted(os.listdir(cand_dirp)):
            if not name.lower().endswith(".md"):
                continue
            p = os.path.join(cand_dirp, name)
            cl, _ = read_lines(p)
            if not any("已提升" in x for x in cl[:12]):
                continue
            vals = cand_field_values(cl)
            joined = " ".join(vals.get("复核日期", []))
            m2 = re.search(r"\d{4}-\d{2}-\d{2}", joined)
            if not m2:
                add("R14 已提升候选缺复核日期", "%s 的提升记录必须带「下次复核日期」⇒ 否则这条铁律没有回归期，迟早变教条" % name)
                continue
            try:
                due = datetime.strptime(m2.group(0), "%Y-%m-%d").date()
            except ValueError:
                add("R14 复核日期不合法", "%s 的 %s 解析不了" % (name, m2.group(0)))
                continue
            if due < date.today():
                add("R14 已提升规则复核过期", "%s 复核日期 %s 已过 ⇒ 适用范围可能已失效：重验（补反例／改边界）后更新候选里的复核日期，或降级回候选" % (name, m2.group(0)))
    if deep:
        items.extend(_template_consistency(root))
    return items, d


def cmd_check(a):
    items, d = check_drift(a.root, deep=bool(getattr(a, "deep", False)))
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
                rec_lines = ["", "- 提升时间：%s（闸门通过，工具写入）" % date.today().isoformat(),
                             "- 提升去向：铁律区由各 agent 自己的 store CORE 承接——本工具不代写别人的 store"]
                # 提升后的铁律必须有回归期：把候选里的复核日期带进提升记录，check R14 会按时点回访
                dv = " ".join(cand_field_values(lines).get("复核日期", []))
                dm = re.search(r"\d{4}-\d{2}-\d{2}", dv)
                if dm:
                    rec_lines.append("- 下次复核：%s（到期没复核，check R14 会翻红）" % dm.group(0))
                lines[i + 1:i + 1] = rec_lines
                break
        atomic_write(path, lines, eol)
        srcs = _candidate_sources(path)
        # 按根因攒条数升上来的候选，来源行只列案例 ID ⇒ 根因族本体容易被漏掉背书。
        # 候选编号自带 RT 号（确定性形状），把它补进来源再走后面的 escalated/⭐ 逻辑。
        rt_m = RT_RE.search(a.candidate)
        if rt_m and rt_m.group(0) not in srcs:
            srcs = srcs + [rt_m.group(0)]
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


# ================================================================ git 状态与跨工件对账（防腐）
def _git_state(path):
    """（在 git 仓内?, 索引被跟踪?）。无版本史的库回滚只剩单代 .bak、泄改无审计——这正是「库腐烂没人看得见」的形态。"""
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        if proc.returncode != 0 or proc.stdout.strip() != b"true":
            return False, False
        proc2 = subprocess.run(["git", "ls-files", "--", INDEX_NAME], cwd=path,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        return True, bool(proc2.stdout.strip())
    except Exception:
        return False, False


def _template_consistency(root):
    """跨工件对账（check --deep）：库内模板与脚本常量必须同源。

    「全处同步铁律」此前只靠人记＋md5 对账；改了 FULL_SECTIONS 没改模板（或反之）当场在这里翻红
    ——正是自家反复咬过的那种「恒真判据／改一半」的新面。
    """
    out = []
    for p, need in ((os.path.join(root, "pitfalls", "_template.md"), FULL_SECTIONS),
                    (os.path.join(root, "pitfalls", "_template-lite.md"), LITE_SECTIONS)):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if not os.path.isfile(p):
            out.append({"rule": "R15 模板不在场", "level": "警",
                        "msg": "%s 缺 ⇒ init 补齐后新条目才有形状可比" % rel})
            continue
        tl, _ = read_lines(p)
        missing = [s for s in need if not has_section(heading_texts(tl), s)]
        if missing:
            out.append({"rule": "R15 模板常量漂移", "level": "红",
                        "msg": "%s 缺段 %s ⇒ 模板与脚本常量不同源（改了一半，条目将按另一套形状检查）"
                               % (rel, "、".join(missing))})
    assets = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets"))
    for src, need in (("entry-template.md", FULL_SECTIONS), ("entry-lite-template.md", LITE_SECTIONS),
                      ("candidate-template.md", CAND_FIELDS)):
        p = os.path.join(assets, src)
        if not os.path.isfile(p):
            continue  # 精简包可能不带 assets；库内模板已对过账，不重复罚
        tl, _ = read_lines(p)
        missing = [s for s in need if not has_section(heading_texts(tl), s)]
        if missing:
            out.append({"rule": "R15 assets 模板常量漂移", "level": "红",
                        "msg": "随包 assets/%s 缺 %s（与脚本常量不同源）" % (src, "、".join(missing))})
    return out


# ================================================================ 退役（软删通道——没有删除通道的库只进不出，必腐）
def _move_to_archive(src, subdir):
    """移到同父 archive/ 子目录；重名不覆盖，加序号后缀。archive/ 下的文件不进现扫（_md_files 不递归）。"""
    dst_dir = os.path.join(os.path.dirname(src), subdir)
    os.makedirs(dst_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    dst = os.path.join(dst_dir, stem + ext)
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(dst_dir, "%s-%02d%s" % (stem, n, ext))
    shutil.move(src, dst)
    return dst


def drop_case_rows(lines, cid):
    """把索引里指向该案例的所有行摘掉；分组因此空了连分组块一起拆（空分组是退役留下的壳）。"""
    drop = set()
    for i, raw in enumerate(lines):
        if not CASE_ROW_RE.match(raw):
            continue
        lm = CASE_LINK_RE.search(raw)
        if lm and os.path.splitext(os.path.basename(lm.group(1)))[0] == cid:
            drop.add(i)
    if not drop:
        return list(lines), "索引里本就没有 %s 的行" % cid
    out = [raw for i, raw in enumerate(lines) if i not in drop]
    return _prune_empty_groups(out), "索引 %d 行已摘除" % len(drop)


def drop_root_row(lines, rid):
    out, hit = [], False
    for raw in lines:
        m = ROOT_ROW_RE.match(raw)
        if m and m.group(1) == rid and not hit:
            hit = True
            continue
        out.append(raw)
    return out, ("根因表 %s 行已删" % rid if hit else "根因表本就没有 %s 的行" % rid)


def _prune_empty_groups(lines):
    out = list(lines)
    for _rid, (s, e) in sorted(index_groups(out).items(), key=lambda kv: -kv[1][0]):
        if not any(CASE_ROW_RE.match(x) for x in out[s:e]):
            del out[s:e]
    return out


def _append_retire_note(lines, day, what, dst_rel, reason):
    """退役留审计线：索引尾部追加一笔（列表行形状，不撞任何表格解析器；退役必须带理由——
    无理由的删除等于把别人的证据链抹掉）。"""
    out = list(lines)
    if not any(x.strip() == "## 退役记录" for x in out):
        out += ["", "## 退役记录", ""]
    out.append("- %s 退役 %s → %s ｜ 理由：%s" % (day, what, dst_rel, reason))
    return out


def cmd_retire(a):
    idx_path = require_store(a.root)
    if bool(a.case) == bool(a.rt):
        die("retire 需要 --case <案例ID> 或 --rt RT-0NN（二选一），外加 --reason <退役理由>")
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        if a.case:
            name = a.case[:-3] if a.case.endswith(".md") else a.case
            name = os.path.splitext(os.path.basename(name))[0]
            src = os.path.join(a.root, "pitfalls", name + ".md")
            if not os.path.isfile(src):
                die("案例文件不存在：%s（已在 archive/ 的条目别再退一次）" % src, 2)
            idx_lines, eol = read_lines(idx_path)
            idx_lines, note1 = drop_case_rows(idx_lines, name)
            dst = _move_to_archive(src, "archive")
            what, dst_rel = name, os.path.relpath(dst, a.root).replace(os.sep, "/")
            say("[retire] 案例本体已移入 %s" % dst_rel)
        else:
            rid = a.rt.upper().strip()
            if not RT_RE.fullmatch(rid):
                die("--rt 必须是 RT-0NN，收到：%r" % a.rt, 2)
            src = next((p for p in root_files(a.root) if os.path.basename(p).startswith(rid)), None)
            if not src:
                die("根因文件不存在：%s" % rid, 2)
            d0 = derive(a.root)
            mounted = list(d0["cases_by_root"].get(rid, []))
            also_users = [c["id"] for c in d0["cases"] if rid in c["also_roots"]]
            if d0["mounts_by_root"].get(rid, 0) > 0:
                die("根因 %s 仍挂着案例 ⇒ 不能退役（别人引用的号，本体一搬全断链）。"
                    "先把下挂案例 retire 或 merge-roots 到别的族（主挂：%s%s）"
                    % (rid, "、".join(mounted) or "无",
                       "；副挂：" + "、".join(also_users) if also_users else ""), 2)
            idx_lines, eol = read_lines(idx_path)
            idx_lines, note1 = drop_root_row(idx_lines, rid)
            dst = _move_to_archive(src, "archive")
            what, dst_rel = rid, os.path.relpath(dst, a.root).replace(os.sep, "/")
            say("[retire] 根因本体已移入 %s" % dst_rel)
        idx_lines = _prune_empty_groups(idx_lines)
        idx_lines = _append_retire_note(idx_lines, date.today().isoformat(), what, dst_rel, a.reason)
        atomic_write(idx_path, idx_lines, eol)
        back, _ = read_lines(idx_path)
        if not any(("退役 %s " % what) in x or ("退役 %s｜" % what) in x or ("退役 %s |" % what) in x for x in back):
            if os.path.exists(idx_path + ".bak"):
                shutil.copy2(idx_path + ".bak", idx_path)
            die("退役记录写后读回验收失败 ⇒ 索引已从 .bak 回滚（注明理由的那一行没落上）", 1)
        touch_lock(lock)
        refresh_counts(a.root, idx_path, write=True)
        say("[retire] %s；数字已按现扫刷新，条目本体连同历史命中记录都在 archive/ 里（退役可逆：把文件搬回＋counts --apply）"
            % note1)
    finally:
        release_lock(lock)
    return 0


# ================================================================ merge-roots（重复根因族并档）
def _repoint_case(path, frm, into):
    """把一条案例的挂载从 frm 改到 into：YAML 的 root/root_cause/also_root 与行内引用块两种形态都认。
    改后若 root 与 also_root 撞成同一个号，副挂行删掉（双挂同一个号没有意义）。"""
    lines, eol = read_lines(path)
    has_yaml = bool(lines and lines[0].strip() == "---")
    yaml_end = 0
    if has_yaml:
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                yaml_end = i
                break
    headings = 0
    notes = []
    root_now = None
    for i, raw in enumerate(lines):
        in_fm = has_yaml and i < yaml_end
        is_inline = (not has_yaml) and raw.lstrip().startswith(">")
        if not in_fm and not is_inline:
            if not has_yaml and re.match(r"^\s*#{1,6}\s", raw):
                headings += 1
                if headings >= 2:
                    break
            continue
        key_m = re.match(r"^(\s*(also_root|root_cause|root)\s*:\s*)(.*?)(\s*(#.*)?)$", raw)
        if key_m:
            key, val = key_m.group(2), key_m.group(3).strip()
            new_val = re.sub(r"RT-%s\b" % re.escape(frm[3:]), "RT-%s" % into[3:], val)
            if key == "also_root" and _rt_list(new_val) == _rt_list(into) and root_now == _rt_list(into):
                lines[i] = None  # 副挂挤掉了——root 已是 into，双挂同号无意义
                continue
            if key in ("root", "root_cause"):
                root_now = _rt_list(new_val)
            if new_val != val:
                lines[i] = key_m.group(1) + new_val + key_m.group(4)
                notes.append("%s: %s→%s" % (key, val, new_val))
            continue
        if is_inline and ("RT-%s" % frm[3:]) in raw:
            swapped = re.sub(r"((?:also_root|root_cause|root)\s*[:：]\s*[^|｜\n]*?)RT-%s\b" % re.escape(frm[3:]),
                             lambda m: m.group(1) + "RT-%s" % into[3:], raw)
            if swapped != raw:
                lines[i] = swapped
                notes.append("行内挂载已改")
    kept = [x for x in lines if x is not None]
    dropped = len(lines) - len(kept)
    if notes or dropped:
        atomic_write(path, kept, eol)
    return ("、".join(notes) + ("；副挂同号已删" if dropped else "")) if (notes or dropped) else "挂载本就不在 %s" % frm


def _merge_index_groups(lines, frm, into, into_title):
    groups = index_groups(lines)
    g1, g2 = groups.get(frm), groups.get(into)
    if not g1:
        return list(lines), "索引原无 %s 分组（无需搬行）" % frm
    s1, e1 = g1
    if g2:
        s2, e2 = g2
        rows1 = [x for x in lines[s1:e1] if CASE_ROW_RE.match(x)]
        rows2 = [x for x in lines[s2:e2] if CASE_ROW_RE.match(x)]
        both = rows1 + rows2
        both.sort(key=lambda x: x.strip().split("|")[1].strip(), reverse=True)
        head2 = [x for x in lines[s2:e2] if not CASE_ROW_RE.match(x)]
        if s1 < s2:
            out = lines[:s1] + lines[e1:s2] + head2 + both + lines[e2:]
        else:
            out = lines[:s2] + head2 + both + lines[e2:s1] + lines[e1:]
        return out, "%s 组 %d 行并入 %s 组（组内按日期新→旧）" % (frm, len(rows1), into)
    out = list(lines)
    out[s1] = re.sub(r"^(#{2,4})(\s*)RT-\d{3}[^\n]*$",
                     lambda m: "%s%s%s %s" % (m.group(1), m.group(2), into, into_title), lines[s1])
    return out, "索引无 %s 分组 ⇒ 原组改名为 %s（行就地保留）" % (into, into)


def cmd_merge(a):
    require_store(a.root)
    into, frm = (a.into or "").upper().strip(), (a.frm or "").upper().strip()
    for x in (into, frm):
        if not RT_RE.fullmatch(x):
            die("merge-roots 需要两个三位数根因号：--into RT-0AA --from RT-0BB，收到 %r" % x)
    if into == frm:
        die("--into 与 --from 是同一个号（%s）" % into)
    p_into = next((p for p in root_files(a.root) if os.path.basename(p).startswith(into)), None)
    p_frm = next((p for p in root_files(a.root) if os.path.basename(p).startswith(frm)), None)
    if not p_into:
        die("保留根因 %s 的本体文件不存在（roots/%s-*.md）" % (into, into))
    if not p_frm:
        die("撤并根因 %s 的本体文件不存在（roots/%s-*.md）" % (frm, frm))
    d0 = derive(a.root)
    affected = sorted(set(d0["cases_by_root"].get(frm, []))
                      | {c["id"] for c in d0["cases"] if frm in c["also_roots"]})
    plan = ["改挂载 %d 条案例：%s" % (len(affected), "、".join(affected) or "无（直接并档空族）"),
            "索引：%s 表行删除、案例分组并入/改名" % frm,
            "本体：%s 档案进 roots/archive/（旧正文需人工往 %s 搬，工具不代搬）" % (frm, into)]
    if not a.yes:
        say("== merge-roots 干跑（加 --yes 才动盘）==")
        for x in plan:
            say("  " + x)
        return 0
    lock, why = acquire_lock(a.root)
    if not lock:
        die("抢锁失败：%s" % why, 5)
    say("[lock] %s" % why)
    try:
        for cid in affected:
            p = os.path.join(a.root, "pitfalls", cid + ".md")
            note = _repoint_case(p, frm, into)
            back = parse_case(p)
            if cid in d0["cases_by_root"].get(frm, []) and frm in back["roots"] + back["also_roots"]:
                die("案例 %s 改挂后仍指向 %s（写后读回验收失败：%s）" % (cid, frm, note), 1)
            say("[merge] 案例 %s：%s" % (cid, note))
        idx_path = os.path.join(a.root, INDEX_NAME)
        idx_lines, ieol = read_lines(idx_path)
        idx_lines, note_g = _merge_index_groups(idx_lines, frm, into, root_title_of(a.root, into))
        idx_lines, note_t = drop_root_row(idx_lines, frm)
        atomic_write(idx_path, idx_lines, ieol)
        dst = _move_to_archive(p_frm, "archive")
        touch_lock(lock)
        target = p_into
        t_lines, teol = read_lines(target)
        t_lines += ["", "> 合并记录：%s %s 并入本族；旧本体在 roots/archive/%s——其「一句话根因／识别信号／通用对策」"
                       "与本族是两个视角，需人工挑着搬，工具不代搬。" % (date.today().isoformat(), frm, os.path.basename(dst))]
        atomic_write(target, t_lines, teol)
        refresh_counts(a.root, idx_path, write=True)
        say("[merge] %s；%s；旧本体已移入 %s；数字已按现扫刷新" % (note_g, note_t, dst))
    finally:
        release_lock(lock)
    return 0


# ================================================================ audit（命中抽审——只读）
def cmd_audit(a):
    require_store(a.root)
    d = derive(a.root)
    rows = []
    for c in d["cases"]:
        for r in c["hit_rows"]:
            rows.append((r["date"], "案例 " + c["id"], r["scenario"], r["solved"], r["fp"]))
    for r0 in d["roots"]:
        for r in r0["hit_rows"]:
            rows.append((r["date"], "根因 " + r0["id"], r["scenario"], r["solved"], r["fp"]))
    rows.sort(key=lambda x: x[0], reverse=True)
    k = max(1, a.sample)
    say("== 命中抽审（列最近 %d 条，共 %d 记录｜库内合计命中 %d 次）=="
        % (min(k, len(rows)), len(rows), d["hit_total"]))
    for x in rows[:k]:
        say("  %s ｜ %s ｜ 场景：%s ｜ 解决：%s ｜ 指纹 %s" % x)
    if not rows:
        say("  库内还没有任何命中记录。")
    say("[audit] 回访判据（只提醒，不代判）：问题解决之前记录确实被看见且产生助力才算命中；"
        "报虚了就改/删那一行（命中记录是唯一分母），改完跑 counts --apply。定期抽审是 GAO 对 NASA 教训系统的同款处方——没有使用监控的库会静默边缘化。")
    return 0


# ================================================================ selftest（随包自证——合成库先红后绿）
def _probe_teeth(run, tmp, cid, before):
    """进程内变异探针：把场景归一化钉成常量 ⇒ 同日、不同场景的两次正常命中会被误判成同一次而吞掉。
    观察到「+1 而不是 +2」＝行为确被改坏、且改变了我们已断言过的健康行为（A05/A06）⇒ 断言有牙。"""
    orig = norm_text
    globals()["norm_text"] = lambda s: "const"
    try:
        run(["--root", tmp, "hit", "--case", cid, "--date", "2031-06-01", "--scenario", "探针独立场景甲"])
        run(["--root", tmp, "hit", "--case", cid, "--date", "2031-06-01", "--scenario", "探针独立场景乙"])
        after = derive(tmp)["hits_by_case"].get(cid, 0)
        return (after - before) == 1
    finally:
        globals()["norm_text"] = orig


def cmd_selftest(a):
    started = datetime.now()
    tmp = tempfile.mkdtemp(prefix="pitfall-selftest-")
    results = []

    def rec(name, ok, note=""):
        results.append(bool(ok))
        say("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" ｜ " + note) if note else ""))

    def run(argv):
        ns = build_parser().parse_args(argv)
        if ns.cmd != "selftest":
            ns.root = os.path.abspath(ns.root)
        try:
            return ns.func(ns) or 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1

    def R(*argv):
        return run(["--root", tmp] + list(argv))

    def d_of():
        return derive(tmp)

    say("== pitfall selftest ｜ 合成库 %s ==" % tmp)

    # ---- 骨架与登记 ----
    rec("A01 init 建骨架", R("init") == 0 and os.path.isfile(os.path.join(tmp, INDEX_NAME))
        and all(os.path.isdir(os.path.join(tmp, x)) for x in ("roots", "pitfalls", CAND_DIR))
        and os.path.isfile(os.path.join(tmp, "pitfalls", "_template.md")))
    sev, day0 = "红", "2030-01-01"
    rec("A02 add 登记＋事件标记",
        R("add", "--title", "编码家族回归：子进程吃到 GBK", "--root-cause", "RT-001",
          "--make-root", "中文跨进程边界编码错配", "--severity", sev, "--summary", "脚本输出乱码",
          "--date", day0, "--author", "selftest", "--tags", "编码,测试") == 0
        and d_of()["case_total"] == 1 and bool(d_of()["cases"][0]["mark"]))
    rec("A03 同事件重复提交幂等", R("add", "--title", "编码家族回归：子进程吃到 GBK", "--root-cause", "RT-001",
                                    "--severity", sev, "--date", "2030-01-02") == 0 and d_of()["case_total"] == 1)
    rec("A04 同ID换内容拒绝(退出码4)", R("add", "--title", "换内容试探", "--root-cause", "RT-001", "--severity", sev,
                                        "--slug", slugify("编码家族回归：子进程吃到 GBK"), "--date", day0) == 4)

    # ---- 命中 ----
    cid = d_of()["case_ids"][0]
    R("hit", "--case", cid, "--date", "2030-01-03", "--scenario", "检索RT-001先试", "--solved", "直接套用对策")
    R("hit", "--case", cid, "--date", "2030-01-03", "--scenario", "检索RT-001先试")
    rec("A05 同一次命中去重", len(parse_case(case_files(tmp)[0])["hit_rows"]) == 1)
    R("hit", "--case", cid, "--date", "2030-01-04", "--scenario", "换个场景再命中")
    rec("A06 不同次命中累加", len(parse_case(case_files(tmp)[0])["hit_rows"]) == 2)
    rec("A07 两层互斥拒绝(退出码2)", R("hit", "--case", cid, "--root-cause", "RT-001", "--scenario", "x") == 2)
    R("hit", "--root-cause", "RT-001", "--date", "2030-01-05", "--scenario", "只读根因层就解决")
    rec("A08 根因层独立计数", len(parse_root(root_files(tmp)[0])["hit_rows"]) == 1)

    # ---- counts 修复循环 ----
    idx = os.path.join(tmp, INDEX_NAME)
    lines0, e0 = read_lines(idx)
    dirty = list(lines0)
    for i, raw in enumerate(dirty):
        if CASE_ROW_RE.match(raw):
            dirty[i] = CASE_TAIL_RE.sub(lambda m: m.group(1) + "9" + m.group(3), raw)
            break
    atomic_write(idx, dirty, e0, backup=False)
    rec("A09 counts 干跑检测且不改盘", R("counts") == 0 and "🔥9" in "\n".join(read_lines(idx)[0]))
    rec("A10 counts --apply 修复", R("counts", "--apply") == 0
        and "🔥9" not in "\n".join(read_lines(idx)[0]))
    rec("A11 check 全绿", R("check") == 0)

    # ---- 升华闸门 ----
    for k in range(2, 8):
        R("add", "--title", "同根因第%d条%d" % (k, k), "--root-cause", "RT-001", "--severity", "黄",
          "--kind", "lite", "--date", "2030-02-%02d" % (k + 1), "--summary", "后果%d" % (k - 1))
    rec("A12 攒到阈值7条", len(d_of()["cases_by_root"].get("RT-001", [])) >= 7)
    rec("A13 promote --scan 生成候选", R("promote", "--scan") == 0 and
        len([n for n in sorted(os.listdir(os.path.join(tmp, CAND_DIR))) if n.endswith(".md")]) >= 1)
    cands = sorted(n for n in os.listdir(os.path.join(tmp, CAND_DIR)) if n.endswith(".md"))
    candp = os.path.join(tmp, CAND_DIR, cands[0])
    cl, ce = read_lines(candp)
    ok0, notes0 = gate(candp)
    rec("A14 四要素未填闸门不放行", not ok0 and any("缺「" in x for x in notes0))

    def _fill(sec, text):
        for j, x in enumerate(cl):
            if re.match(r"^\s*##\s*%s" % sec, x):
                cl.insert(j + 1, text)
                return
    _fill("适用范围", "仅 Windows 控制台管线传中文时适用（selftest 合成范围）")
    _fill("反例", "Linux 下 LANG=utf-8 时该症状不出现（合成反例）")
    _fill("边界", "只覆盖控制台子进程编码，其它跨进程通道不在内")
    _fill("复核日期", "2031-12-31")
    atomic_write(candp, cl, ce)
    rec("A15 填齐四要素干跑过闸", R("promote", "--gate", cands[0][:-3]) == 0)
    rec("A16 --apply 提升落盘", R("promote", "--gate", cands[0][:-3], "--apply") == 0
        and parse_root(root_files(tmp)[0])["escalated"] and "⭐" in "\n".join(read_lines(idx)[0]))
    rec("A17 提升记录带下次复核日期", "2031-12-31" in "\n".join(read_lines(candp)[0]))
    cl2, _ = read_lines(candp)
    cl2b = list(cl2)
    for i, x in enumerate(cl2b):
        if x.strip().startswith("## 复核日期"):
            cl2b.insert(i + 1, "2020-01-01（自我降级测试）")
            break
    atomic_write(candp, cl2b, "\n", backup=False)
    items, _d = check_drift(tmp)
    rec("A18 过期复核翻红 R14", any(x["rule"].startswith("R14") for x in items if x["level"] == "红"))
    cl3 = [x for x in cl2b if "自我降级测试" not in x]
    atomic_write(candp, cl3, "\n", backup=False)

    # ---- 防腐闭环 ----
    items, _d = check_drift(tmp)
    rec("A19 无 git 仓给警 R13", any(x["rule"].startswith("R13") for x in items))
    total0 = d_of()["case_total"]
    victim = d_of()["case_ids"][1]
    rec("A20 退役进 archive", R("retire", "--case", victim, "--reason", "与既有条目重复（selftest）") == 0
        and d_of()["case_total"] == total0 - 1
        and os.path.isfile(os.path.join(tmp, "pitfalls", "archive", victim + ".md")))
    idx_txt = "\n".join(read_lines(idx)[0])
    rec("A21 退役理由留审计线", "退役记录" in idx_txt and victim in idx_txt)
    rec("A22 退役后 check 绿", R("check") == 0)
    rec("A23 有挂载的根因不许退役", R("retire", "--rt", "RT-001", "--reason", "x") == 2)
    R("add", "--title", "另一族独立坑", "--root-cause", "RT-002", "--make-root", "依赖隐式上下文导致路径错",
      "--severity", "黄", "--date", "2030-03-01", "--summary", "走错目录")
    R("add", "--title", "另一族第二坑", "--root-cause", "RT-002", "--severity", "黄",
      "--date", "2030-03-02", "--summary", "再走错")
    rec("A24 merge-roots 干跑不动盘", R("merge-roots", "--into", "RT-001", "--from", "RT-002") == 0
        and any(os.path.basename(p).startswith("RT-002") for p in root_files(tmp)))
    code = R("merge-roots", "--into", "RT-001", "--from", "RT-002", "--yes")
    d3 = d_of()
    rec("A25 --yes 并档收尾", code == 0
        and not any(os.path.basename(p).startswith("RT-002") for p in root_files(tmp))
        and not any("RT-002" in c["roots"] + c["also_roots"] for c in d3["cases"])
        and os.path.isfile(os.path.join(tmp, "roots", "archive",
                                        [n for n in os.listdir(os.path.join(tmp, "roots", "archive")) if n.startswith("RT-002")][0]))
        and not any(ROOT_ROW_RE.match(x) and "RT-002" in x for x in read_lines(idx)[0]))
    rec("A26 merge 后 check 绿", R("check") == 0)
    rec("A27 check --deep 模板同源", R("check", "--deep") == 0)
    tpl = os.path.join(tmp, "pitfalls", "_template.md")
    tl, te = read_lines(tpl)
    atomic_write(tpl, [x for x in tl if not (x.strip().startswith("#") and "现场代码" in x)], te)
    rec("A28 模板与常量不同源翻红 R15", R("check", "--deep") == 1)
    rec("A29 audit 只读列命中", R("audit", "--sample", "3") == 0)
    before_hits = d_of()["hits_by_case"].get(cid, 0)
    rec("A30 判据有牙（指纹探针）", _probe_teeth(run, tmp, cid, before_hits))

    bad = results.count(False)
    used = (datetime.now() - started).total_seconds()
    say("== selftest 汇总：%d/%d PASS ｜ 用时 %.1fs ｜ 合成库：%s =="
        % (len(results) - bad, len(results), used, tmp))
    if bad:
        say("[selftest] FAIL %d 条 ⇒ 退出码 1；合成库保留在 %s 供排障（排完自行删）" % (bad, tmp))
        return 1
    shutil.rmtree(tmp, ignore_errors=True)
    say("[selftest] 全绿，合成库已清理")
    return 0


# ================================================================ CLI
def build_parser(argv=None):
    p = argparse.ArgumentParser(prog="pitfall_log.py", description="踩坑日志确定性维护工具（计数从事件记录生成）")
    p.add_argument("--root", default="",
                   help="log-root 目录（除 selftest 外必填；不给默认当前目录＝防空集假绿）")
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

    sp = sub.add_parser("check", help="索引 ↔ 事件记录 ↔ 根因文件 一致性自检（读侧入口动作）")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--deep", action="store_true", help="跨工件对账：库内/随包模板与脚本常量同源（改一半当场报红 R15）")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("promote", help="候选 → 验证适用范围 → 提升")
    sp.add_argument("--scan", action="store_true")
    sp.add_argument("--gate", dest="candidate", default="")
    sp.add_argument("--apply", dest="apply", action="store_true")
    sp.add_argument("--threshold", type=int, default=7)
    sp.add_argument("--hits-threshold", dest="hits_threshold", type=int, default=3)
    sp.set_defaults(func=cmd_promote)

    sp = sub.add_parser("retire", help="退役＝软删进 archive/（理由必填；退役可逆，案例行从索引摘除、数字现扫刷新）")
    sp.add_argument("--case", default="", help="要退役的案例 ID（YYYY-MM-DD-slug）")
    sp.add_argument("--rt", default="", help="要退役的空根因号 RT-0NN（下挂案例与副挂必须先清零）")
    sp.add_argument("--reason", required=True, help="退役理由（无理由的删除等于抹掉别人的证据链）")
    sp.set_defaults(func=cmd_retire)

    sp = sub.add_parser("merge-roots", help="撤并重复根因族：改挂载→搬清单行→归档旧本体（旧正文需人工搬）")
    sp.add_argument("--into", required=True, help="保留的根因号 RT-0AA")
    sp.add_argument("--from", dest="frm", required=True, help="撤并的根因号 RT-0BB")
    sp.add_argument("--yes", action="store_true", help="不加只预演")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("audit", help="命中抽审：列最近 K 条命中记录供人工回访（只读，防虚报）")
    sp.add_argument("--sample", type=int, default=5)
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("selftest", help="随包自证：合成库跑全套断言＋指纹探针（无需 --root；FAIL 退出码 1）")
    sp.set_defaults(func=cmd_selftest)

    return p


def main(argv):
    # Windows 中文管道（GBK）下 emoji 输出会 UnicodeEncodeError（3.8–3.14 全中招，3.15 靠 PEP 686 才幸免）
    # ⇒ 入口统一 UTF-8+replace：字符不丢、程序不崩，落到文件里的自证读数永远可解析
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    if args.cmd == "selftest":
        return args.func(args) or 0
    if not args.root:
        die("--root 必填（不给默认当前目录：在错的地方跑会量到 0 条却打「无漂移」＝假绿灯）")
    args.root = os.path.abspath(args.root)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
