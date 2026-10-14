#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
治理体检脚本 —— 多Agent团队协作文档治理 skill 的内置自检工具（v1.4）。

用途：扫描项目根目录下的治理文件，自动找出"悄悄腐化"的协作问题：
  1. 过期锁：文字锁 EDIT_LOCK 状态为 wip 且心跳超过租约（死锁风险）
  2. 重复决策：DECISIONS.md 待拍板区里高度相似的条目（应去重）
  3. 角色名漂移：信箱子文件夹 与 契约§1『成员与角色』表 不一致
  4. 信箱积压：**逐条**统计——同一文件里既有已处理又有待处理时，待处理那条必须报出来
  5. 看板漂移：L4 看板存在时列出 blocked 任务，提醒与 DECISIONS §4 同步
  6. 轻量信箱规模：投递.md 信件数/行数超阈值，提示升级标准模式
  7. 决策规模：DECISIONS.md 活跃 approved 条目超阈值，提示压缩历史区
  8. 决策事件一致性：supersedes / revoked 目标存在、失效条目不留在活跃区、
     被替代条目原文留档（不删历史）、活跃条目不引用已废编号（幽灵引用）
  9. 真实锁存储：.治理锁/ 里 lease 过期、JSON 损坏、无主锁（配合 scripts/edit_lock.py）

另外强制输出「检查覆盖率」与「未检查项」，并在一个治理文件都没有时判 **未配置**
（退出码 3），绝不打印"健康"——空集恒绿是本脚本 v1.3 及以前最危险的失效模式。

用法：
  py scripts/governance_check.py --root <项目根目录>
  py scripts/governance_check.py                      # 默认当前目录
  py scripts/governance_check.py --decision-view      # 只打印当前有效决策视图
  py scripts/governance_check.py --mailboxes-only     # 只跑信箱逐条检查

退出码：0 = 已配置且健康；1 = 发现问题；2 = 参数/路径错误；
        3 = 未配置（根目录下没有任何治理文件，体检无物可查，不等于健康）。
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

LOCK_RE = re.compile(r"EDIT_LOCK:\s*(.+?)\s*(?:-->|\*/|$)", re.MULTILINE)
STATUS_RE = re.compile(r"状态[:：]\s*([A-Za-z_]+|\w+)", re.IGNORECASE)
HEARTBEAT_RE = re.compile(r"心跳[:：]\s*([\d]{4}-[\d]{2}-[\d]{2}[ T]?[\d]{2}(?::[\d]{2})?)")
WIP_TIMEOUT = timedelta(hours=24)

# ---------- 信箱逐条解析用 ----------
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
SENDER_RE = re.compile(r"^\s*(?:[-*>]\s*)?来\s*源\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
RECV_RE = re.compile(r"^\s*(?:[-*>]\s*)?(?:接\s*收\s*方|目标|致)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
MSG_STATUS_RE = re.compile(r"^\s*(?:[-*>]\s*)?状\s*态\s*[:：]\s*([A-Za-z_\-]+|[^\s,;，；]+)", re.MULTILINE)
MSG_ID_RE = re.compile(r"(MSG[-_][\w\-]+|ID\s*[:：]\s*[\w\-]+)", re.IGNORECASE)
AT_RE = re.compile(r"@([^\s@，,、；;:：]+)")
TOPIC_RE = re.compile(r"^\s*(?:[-*>]\s*)?主\s*题\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
# v1.3 及以前的整文件判据用的就是这两个字面串，逐条解析时同样认
LEGACY_DONE_TOKENS = ("[已处理]", "（已处理）", "(已处理)")
DONE_STATES = {"done", "closed", "resolved", "完成", "已处理", "已完成", "已闭环", "已回复", "已解决"}
PENDING_STATES = {"pending", "open", "todo", "new", "wip", "doing", "blocked",
                  "未处理", "待处理", "未读", "待回复", "进行中", "受阻", "等待"}

DEC_HEAD_RE = re.compile(r"^#{2,4}\s*(D-\d+)\b(.*)$", re.MULTILINE)
SUPERSEDES_RE = re.compile(r"supersedes\s*[:：]?\s*(D-\d+)", re.IGNORECASE)
EVENT_LINE_RE = re.compile(r"事件\s*[:：]\s*(supersedes|revoked|替代|撤销)", re.IGNORECASE)
HIST_ITEM_RE = re.compile(r"^\s*[-*]\s*\**\s*(D-\d+)\b[^\n]*$", re.MULTILINE)
D_ID_RE = re.compile(r"\bD-\d+\b")

LOCK_STORE_DIRS = (".治理锁", ".governance-locks")
ARCHIVE_DIR_NAMES = ("已处理", "归档", "archive")

CHECKS_TOTAL = 9


def now_ts():
    return datetime.now()


def parse_ts(s):
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError):
        return ""


def find(root, name):
    p = os.path.join(root, name)
    return p if os.path.isfile(p) else None


# ---------- 1. 过期锁（文字锁）----------
def check_locks(text, now):
    problems = []
    for m in LOCK_RE.finditer(text):
        body = m.group(1)
        st = STATUS_RE.search(body)
        if not st or st.group(1).lower() != "wip":
            continue
        hb = HEARTBEAT_RE.search(body)
        if not hb:
            problems.append("发现 wip 锁但无心跳时间戳（无法判断是否过期，建议补全）: " + body[:60])
            continue
        ts = parse_ts(hb.group(1))
        if ts and now - ts > WIP_TIMEOUT:
            age = now - ts
            problems.append("过期锁(心跳 %s，已 %dh): %s" % (hb.group(1), age.total_seconds() // 3600, body[:50]))
    return problems


# ---------- 2. 重复决策 ----------
def _tokens(s):
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", s.lower()))


def check_dup_decisions(decisions_text):
    problems = []
    m = re.search(r"待拍板[\s\S]*?(?=\n## |\Z)", decisions_text)
    if not m:
        return problems
    block = m.group(0)
    rows = re.findall(r"^\|(.+)\|$", block, re.MULTILINE)
    titles = []
    for r in rows:
        cells = [c.strip() for c in r.split("|")]
        if len(cells) >= 2 and not cells[0].startswith("ID"):
            titles.append(cells[1])
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            a, b = _tokens(titles[i]), _tokens(titles[j])
            if not a or not b:
                continue
            inter = len(a & b)
            union = len(a | b)
            if union and inter / union >= 0.5:
                problems.append("疑似重复待拍板决策: 「%s」 ↔ 「%s」" % (titles[i][:30], titles[j][:30]))
    return problems


# ---------- 3. 角色名漂移 ----------
HUMAN_KEYWORDS = ("用户", "主脑", "人", "user", "human", "team", "团队")
_SEP_CHARS = set("-: ")


def _norm_role(name):
    n = name.strip()
    low = n.lower()
    for pfx in ("godot",):
        if low.startswith(pfx):
            n = n[len(pfx):].strip()
    n = re.sub(r"\d+$", "", n).strip()
    return n


def _is_role_cell(first):
    if not first:
        return False
    if set(first) <= _SEP_CHARS:
        return False
    if first in ("成员", "角色", "ID", "文件 / 模块", "区块", "任务",
                 "决策", "分类", "路径", "主责", "协作", "来源"):
        return False
    if re.match(r"^[DT]-\d+", first):
        return False
    if "/" in first or "\\" in first or ".md" in first:
        return False
    if re.fullmatch(r"[\d]+", first):
        return False
    if len(first) > 16:
        return False
    return True


def _section_table_first_col(text, section_header=None):
    lines = text.splitlines()
    start = 0
    found = False
    if section_header:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#") and section_header in ln:
                start = i + 1
                found = True
                break
    names = set()
    for ln in lines[start:]:
        s = ln.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and _is_role_cell(cells[0]):
                names.add(cells[0])
        elif section_header and found and s.startswith("#"):
            break
    return names


def check_role_drift(root, contract_text, decisions_text, check_decisions=False):
    problems = []
    mailbox_roles = set()
    comm = os.path.join(root, "沟通")
    if os.path.isdir(comm):
        for d in sorted(os.listdir(comm)):
            dp = os.path.join(comm, d)
            if os.path.isdir(dp) and not d.endswith(".md"):
                mailbox_roles.add(d)

    contract_agent = {r for r in _section_table_first_col(contract_text, "成员与角色")
                      if not r.lower().startswith(HUMAN_KEYWORDS)}
    decision_agent = set()
    if check_decisions and decisions_text:
        decision_agent = {r for r in _section_table_first_col(decisions_text)
                          if not r.lower().startswith(HUMAN_KEYWORDS)}

    canon = mailbox_roles | contract_agent | decision_agent
    norm_map = {}
    for r in canon:
        norm_map.setdefault(_norm_role(r), set()).add(r)
    for norm, forms in sorted(norm_map.items()):
        if len(forms) > 1:
            problems.append("角色名写法不一致：归一化『%s』出现多种写法 %s（建议统一为信箱文件夹名）"
                            % (norm, "、".join(sorted(forms))))

    cset = {_norm_role(r) for r in contract_agent}
    mset = {_norm_role(r) for r in mailbox_roles}
    for r in sorted(mailbox_roles):
        if _norm_role(r) not in cset:
            problems.append("信箱文件夹「%s」未在契约§1 角色表列出（建议补登或核对命名）" % r)
    for r in sorted(contract_agent):
        if _norm_role(r) not in mset:
            problems.append("契约§1 角色「%s」无对应信箱文件夹（建议建 沟通/%s/ 或核对）" % (r, r))
    return problems


# ---------- 4. 信箱积压（逐条统计 · v1.4）----------
def split_messages(text):
    """把一封信拆成消息块。返回 [(起始行号, 块文本), ...]，行号从 1 开始。

    判据顺序：
      1) 标题行开新条（首行标题视为文件名，不算一条消息）；
      2) 同一标题块里出现 ≥2 个『来源：』⇒ 那是轻量模式往一个标题下追加的多封信，按『来源：』再切；
      3) 全文无标题 ⇒ 按『来源：』切；
      4) 两者都没有 ⇒ 整文件当一条消息，并打「结构未标注」。
    """
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if HEADING_RE.match(ln)]
    first_content = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            first_content = i
            break
    if heads and heads[0] == first_content:
        heads = heads[1:]
    if not heads and SENDER_RE.search(text):
        heads = [m.start() for m in re.finditer(r"^\s*(?:[-*>]\s*)?来\s*源\s*[:：]", text, re.MULTILINE)]
        heads = [text[:h].count("\n") for h in heads]
    if not heads:
        return [(1, text)] if text.strip() else []

    bounds = heads + [len(lines)]
    blocks = []
    for idx in range(len(bounds) - 1):
        a, b = bounds[idx], bounds[idx + 1]
        chunk_lines = lines[a:b]
        chunk = "\n".join(chunk_lines)
        # 块内再按『来源：』切（轻量模式追加在同一标题下时）
        sub = [i for i, ln in enumerate(chunk_lines)
               if re.match(r"^\s*(?:[-*>]\s*)?来\s*源\s*[:：]", ln)]
        if len(sub) >= 2:
            sub_bounds = sub + [len(chunk_lines)]
            for k in range(len(sub_bounds) - 1):
                sa, sb = sub_bounds[k], sub_bounds[k + 1]
                sub_chunk = "\n".join(chunk_lines[sa:sb])
                if sub_chunk.strip():
                    blocks.append((a + sa + 1, sub_chunk))
        else:
            if chunk.strip():
                blocks.append((a + 1, chunk))
    return blocks


def _status_of(block, header_line=""):
    """返回 (状态, 判据说明)。认不出的状态一律按未处理计——宁可多报不漏报。"""
    m = MSG_STATUS_RE.search(block)
    if m:
        raw = m.group(1).strip().strip("*_` ")
        low = raw.lower()
        if low in DONE_STATES or raw in DONE_STATES:
            return "done", "状态标记=%s" % raw
        if low in PENDING_STATES or raw in PENDING_STATES:
            return "pending", "状态标记=%s" % raw
        return "pending", "状态标记=%s（不认识的取值，按未处理计）" % raw
    joined = header_line + "\n" + block
    if any(tok in joined for tok in LEGACY_DONE_TOKENS):
        return "done", "正文带 [已处理] 标记"
    if re.search(r"^\s*(?:[-*>#>\s]*)处理记录\s*[:：]", block, re.MULTILINE):
        return "done", "有『处理记录』行且无状态标记（按已处理计）"
    return "pending", "无状态标记（按未处理计）"


def message_record(path_rel, seq, start_line, block):
    head = block.splitlines()[0] if block.splitlines() else ""
    idm = MSG_ID_RE.search(head + "\n" + block[:400])
    mid = ""
    if idm:
        token = idm.group(1)
        token = re.sub(r"^\s*ID\s*[:：]\s*", "MSG-", token, flags=re.IGNORECASE)
        mid = token.strip().rstrip(":：").upper()
    recv = RECV_RE.search(block)
    at = AT_RE.search(head) or AT_RE.search(block)
    topic = TOPIC_RE.search(block)
    status, reason = _status_of(block, head)
    return {
        "id": mid or ("%s#%d" % (path_rel.replace("\\", "/"), seq)),
        "explicit_id": bool(mid),
        "to": (recv.group(1).strip() if recv else (at.group(1).strip() if at else "")),
        "to_source": ("字段" if recv else ("@提及" if at else "未标注")),
        "status": status,
        "status_reason": reason,
        "line": start_line,
        "file": path_rel.replace("\\", "/"),
        "topic": (topic.group(1).strip() if topic else re.sub(r"^#{1,6}\s*", "", head))[:40],
    }


def collect_mailbox_messages(root):
    """返回 (消息清单, 信件文件数, 归档跳过件数)。

    只扫信箱根目录、绝不递归 `已处理/`（归档信不算积压，也不读正文省 token）。
    角色目录下的 `已处理/`、`归档/`、`archive/` 三种子目录名都当归档位。
    """
    msgs, letters, archived = [], 0, 0
    comm = os.path.join(root, "沟通")
    if not os.path.isdir(comm):
        return msgs, letters, archived
    single = os.path.join(comm, "投递.md")
    if os.path.isfile(single):
        txt = read_text(single)
        blocks = split_messages(txt)
        letters += 1
        for seq, (ln, block) in enumerate(blocks, 1):
            msgs.append(message_record("沟通/投递.md", seq, ln, block))
        return msgs, letters, archived
    for role in sorted(os.listdir(comm)):
        rp = os.path.join(comm, role)
        if not os.path.isdir(rp):
            continue
        if role in ARCHIVE_DIR_NAMES:
            archived += len([f for f in os.listdir(rp) if f.endswith(".md")])
            continue
        for name in sorted(os.listdir(rp)):
            fp = os.path.join(rp, name)
            if os.path.isdir(fp):
                if name in ARCHIVE_DIR_NAMES:
                    archived += len([f for f in os.listdir(fp) if f.endswith(".md")])
                continue
            if not name.endswith(".md") or name.lower() == "readme.md":
                continue
            rel = "沟通/%s/%s" % (role, name)
            blocks = split_messages(read_text(fp))
            letters += 1
            for seq, (ln, block) in enumerate(blocks, 1):
                rec = message_record(rel, seq, ln, block)
                if not rec["to"]:
                    rec["to"] = role
                    rec["to_source"] = "信箱文件夹名"
                msgs.append(rec)
    return msgs, letters, archived


def check_mailbox_backlog(root):
    """逐条报积压：同文件混合状态时，只报待处理那几条，已处理的不参与。"""
    msgs, letters, archived = collect_mailbox_messages(root)
    problems = []
    if not msgs and letters > 0:
        problems.append("信箱里有 %d 个信件文件却一条消息都没解析出来 ⇒ 判尺作废，不给健康结论（请检查信件是否按『每条一个标题或一行来源』书写）" % letters)
        return problems, msgs, letters, archived
    for rec in msgs:
        if rec["status"] == "pending":
            problems.append("信箱积压: %s 第%d行 消息=%s 接收方=%s 主题=%s（%s）" % (
                rec["file"], rec["line"], rec["id"], rec["to"] or "未标注",
                rec["topic"] or "无主题", rec["status_reason"]))
    return problems, msgs, letters, archived


def mailbox_stats(msgs):
    done = sum(1 for m in msgs if m["status"] == "done")
    pending = sum(1 for m in msgs if m["status"] == "pending")
    no_id = sum(1 for m in msgs if not m["explicit_id"])
    no_to = sum(1 for m in msgs if not m["to"])
    return done, pending, no_id, no_to


# ---------- 5. 看板漂移 ----------
def check_board_drift(root, decisions_text):
    problems = []
    board = find(root, "状态看板.md")
    if not board:
        return problems, False
    btxt = read_text(board)
    blocked = re.findall(r"\|\s*(T-\d+|.+?)\s*\|\s*.+?\s*\|\s*blocked\s*\|", btxt)
    if blocked:
        problems.append("L4 看板含 %d 个 blocked 任务，请核对 DECISIONS §4 执行跟踪是否同步标注阻塞原因" % len(blocked))
    return problems, True


# ---------- 6. 轻量信箱规模 ----------
LIGHT_MAILBOX_ITEMS = 20
LIGHT_MAILBOX_LINES = 150


def check_light_mailbox_size(root):
    problems = []
    single = os.path.join(root, "沟通", "投递.md")
    if not os.path.isfile(single):
        return problems
    txt = read_text(single)
    lines = txt.splitlines()
    items = sum(1 for ln in lines if re.match(r"^#{2,3}\s", ln))
    if items > LIGHT_MAILBOX_ITEMS or len(lines) > LIGHT_MAILBOX_LINES:
        problems.append("轻量信箱 沟通/投递.md 已膨胀（约 %d 封信 / %d 行），超过阈值 %d 封——单文件信箱本质是中央派工单，请升级为标准模式（拆 沟通/<角色>/ 信箱文件夹）"
                        % (items, len(lines), LIGHT_MAILBOX_ITEMS))
    return problems


# ---------- 7. 决策活跃区规模 ----------
DECISIONS_ACTIVE_LIMIT = 30


def check_decisions_size(decisions_text):
    problems = []
    n = len(re.findall(r"状态[:：]\s*approved", decisions_text))
    if n > DECISIONS_ACTIVE_LIMIT:
        problems.append("DECISIONS.md 活跃 approved 条目已达 %d（阈值 %d）——请执行决策压缩：把活跃区里已失效的条目连原文一起整体并入 DECISIONS_归档.md（v1.4 起不删历史），主文件顶部留一行『历史决策见归档』"
                        % (n, DECISIONS_ACTIVE_LIMIT))
    return problems


# ---------- 8. 决策事件一致性（v1.4：不删历史）----------
def parse_decisions(decisions_text, archive_text=""):
    """返回 (活跃条目列表, 失效编号→事件, 全部已知编号集合)。

    条目正文边界＝下一个任意级标题（`^#{1,6}`）或分隔线 `---`，两者取近者。
    不这么切的话，最后一条会把 §2/§3/§6 全吞进来，历史区里的旧编号会被误当『幽灵引用』。
    """
    active = []
    heads = list(DEC_HEAD_RE.finditer(decisions_text))
    any_head = [m.start() for m in re.finditer(r"(?m)^#{1,6}\s", decisions_text)]
    rules = [m.start() for m in re.finditer(r"(?m)^\s*---\s*$", decisions_text)]
    bounds = sorted(any_head + rules)
    for i, m in enumerate(heads):
        start = m.end()
        stop = len(decisions_text)
        for b in bounds:
            if b >= start:
                stop = b
                break
        body = decisions_text[start:stop]
        st = re.search(r"状态\s*[:：]\s*([A-Za-z_]+|[\u4e00-\u9fff]+)", body)
        active.append({
            "id": m.group(1),
            "title": m.group(2).strip()[:40],
            "status": (st.group(1).lower() if st else ""),
            "supersedes": SUPERSEDES_RE.findall(body),
            "mentions": set(D_ID_RE.findall(re.sub(r"supersedes\s*[:：].*", "", body, flags=re.IGNORECASE))),
            "in_history": False,
        })
    hist_ids, retired = {}, {}
    for m in HIST_ITEM_RE.finditer(decisions_text):
        line = m.group(0)
        if "替代" in line or "supersedes" in line.lower() or "撤销" in line or "revoked" in line.lower():
            hist_ids[m.group(1)] = line.strip()
            ev = "revoked" if ("撤销" in line or "revoked" in line.lower()) else "superseded"
            has_body = bool(re.search(r"^\s*[-*]?\s*(原文|原结论|原文结论|原实现指向)",
                                      decisions_text[m.end():m.end() + 600], re.MULTILINE))
            retired[m.group(1)] = {"event": ev, "kept": has_body, "line": line.strip()[:60]}
    known = {a["id"] for a in active} | set(hist_ids) | set(D_ID_RE.findall(archive_text))
    for a in active:
        if a["id"] in hist_ids:
            a["in_history"] = True
    return active, retired, known


def check_decision_events(decisions_text, archive_text=""):
    problems = []
    active, retired, known = parse_decisions(decisions_text, archive_text)
    active_ids = {a["id"] for a in active}

    for a in active:
        if a["status"] in ("superseded", "revoked", "已替代", "已撤销"):
            problems.append("失效条目留在活跃区: %s 状态=%s ⇒ 应移入历史区并保留原文（v1.4 起不删历史）" % (a["id"], a["status"]))
        for tgt in a["supersedes"]:
            if tgt not in known:
                problems.append("替代事件指向不存在的条目: %s supersedes %s（该编号在 DECISIONS 与归档里都查不到）" % (a["id"], tgt))
            elif tgt in active_ids:
                problems.append("%s 声明替代 %s，但 %s 仍留在活跃区（应移入历史区）" % (a["id"], tgt, tgt))
    active_by_id = {a["id"]: a for a in active}
    for rid, info in sorted(retired.items()):
        if not info["kept"] and rid not in active_ids and rid not in archive_text:
            problems.append("历史被删: %s（%s）在历史区只有一行指针、原文正文已不在 DECISIONS.md 也不在归档 ⇒ 违反『不删历史』，请把原文补回历史区或归档"
                            % (rid, info["event"]))
    for aid, a in sorted(active_by_id.items()):
        for mt in sorted(a["mentions"]):
            if mt in retired and mt != aid:
                problems.append("幽灵引用: 活跃条目 %s 引用了已失效的 %s（%s）⇒ 请改为引用现行编号"
                                % (aid, mt, retired[mt]["event"]))
    return problems, active, retired, known


def build_decision_view(decisions_text, archive_text=""):
    """当前有效视图：活跃区 approved/todo；失效编号只作为折叠说明出现。"""
    active, retired, known = parse_decisions(decisions_text, archive_text)
    lines = []
    for a in sorted(active, key=lambda x: x["id"]):
        flag = ""
        if a["supersedes"]:
            flag = "（替代 %s）" % "、".join(a["supersedes"])
        lines.append("%-6s %-8s %s%s" % (a["id"], a["status"] or "未标状态", a["title"], flag))
    gone = ["%s %s" % (rid, "已替代" if info["event"] == "superseded" else "已撤销")
            for rid, info in sorted(retired.items())]
    return lines, gone


# ---------- 9. 真实锁存储（配合 edit_lock.py）----------
def lock_store_root(root):
    for name in LOCK_STORE_DIRS:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            return p
    return None


def check_lock_store(root, now):
    store = lock_store_root(root)
    if not store:
        return [], None
    problems = []
    held = expired = broken = 0
    locks_dir = os.path.join(store, "locks")
    if os.path.isdir(locks_dir):
        for fn in sorted(os.listdir(locks_dir)):
            if not fn.endswith(".json"):
                if fn.endswith(".takeover"):
                    problems.append("接管标记长期在场: %s ⇒ 上一次接管中途退出，请人工确认后删除该标记" % fn)
                continue
            raw = read_text(os.path.join(locks_dir, fn))
            try:
                data = json.loads(raw)
            except ValueError:
                broken += 1
                problems.append("锁文件损坏（JSON 解析失败）: %s —— 该目标的互斥已失效，别人可能同时在写" % fn)
                continue
            gen = data.get("generation")
            hb = parse_ts(str(data.get("heartbeat_at", "")))
            lease = data.get("lease_seconds", 3600)
            if hb is None:
                problems.append("锁无有效心跳时间戳: %s（holder=%s gen=%s）" % (fn, data.get("holder"), gen))
                continue
            if now - hb > timedelta(seconds=lease):
                expired += 1
                problems.append("租约过期的锁: %s holder=%s 代次=%s 心跳=%s 租约=%ss ⇒ 他人可用 takeover 显式接管（接管后旧写者代次作废，写入必被拒）"
                                % (data.get("target"), data.get("holder"), gen,
                                   data.get("heartbeat_at"), lease))
            else:
                held += 1
    queue_dir = os.path.join(store, "queue")
    queued = 0
    if os.path.isdir(queue_dir):
        for slug in os.listdir(queue_dir):
            sp = os.path.join(queue_dir, slug)
            if os.path.isdir(sp):
                queued += len([f for f in os.listdir(sp) if not f.endswith(".tmp")])
    stats = {"held": held, "expired": expired, "broken": broken, "queued": queued}
    return problems, stats


# ---------- 主流程 ----------
def governance_present(root):
    """根目录下是否有任何治理构件。"""
    items = {
        "DECISIONS.md": bool(find(root, "DECISIONS.md")),
        "团队分工与协作契约.md": bool(find(root, "团队分工与协作契约.md")),
        "沟通/": os.path.isdir(os.path.join(root, "沟通")),
        "状态看板.md": bool(find(root, "状态看板.md")),
        "真实锁存储": bool(lock_store_root(root)),
    }
    return items


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description="多Agent团队协作治理体检")
    ap.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    ap.add_argument("--decisions-roles", action="store_true",
                    help="将 DECISIONS.md 也纳入角色名漂移比对（默认仅信箱+契约§1，避免散文引用误报）")
    ap.add_argument("--decision-view", action="store_true", help="追加打印当前有效决策视图")
    ap.add_argument("--mailboxes-only", action="store_true", help="只跑信箱逐条检查")
    ap.add_argument("--no-color", action="store_true", help="兼容参数（本脚本不染色）")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误：目录不存在 -> %s" % root)
        return 2

    now = now_ts()
    items = governance_present(root)
    print("=== 治理体检报告（v1.4 逐条/覆盖率版）===")
    print("扫描根目录: %s\n" % root)

    if args.mailboxes_only:
        probs, msgs, letters, archived = check_mailbox_backlog(root)
        done, pending, no_id, no_to = mailbox_stats(msgs)
        print("[4] 信箱逐条统计：信件 %d 个 / 消息 %d 条（已处理 %d，待处理 %d）/ 归档跳过 %d 条"
              % (letters, len(msgs), done, pending, archived))
        for p in probs:
            print("    - " + p)
        if probs:
            print("\n结论：发现 %d 处积压。" % len(probs))
            return 1
        print("\n结论：信箱无积压。")
        return 0

    if not any(items.values()):
        print("[0] 治理构件清点：")
        for k, v in sorted(items.items()):
            print("    - %-24s %s" % (k, "在场" if v else "无"))
        print("\n结论：**未配置**（0/%d 项检查可跑）——本目录没有任何治理文件，"
              "这不等于『健康』。要起步请先按 references/示例-最小可运行治理包.md 建骨架。" % CHECKS_TOTAL)
        return 3

    all_problems = []
    ran, skipped = [], []

    lock_hits = []
    skip_dirs = {".git", "node_modules", "__pycache__"} | set(LOCK_STORE_DIRS)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and d not in ARCHIVE_DIR_NAMES]
        for fn in files:
            if fn.endswith((".md", ".py", ".gd", ".ts", ".js", ".cs", ".cpp", ".gdshader")):
                t = read_text(os.path.join(dirpath, fn))
                if "EDIT_LOCK" in t:
                    for p in check_locks(t, now):
                        lock_hits.append("[%s] %s" % (fn, p))
    if lock_hits:
        all_problems += lock_hits
        print("[1] 编辑锁检查：发现 %d 处问题" % len(lock_hits))
        for p in lock_hits:
            print("    - " + p)
    else:
        print("[1] 编辑锁检查：OK（无过期/异常文字锁）")
    ran.append("[1] 编辑锁")

    decisions = find(root, "DECISIONS.md")
    contract = find(root, "团队分工与协作契约.md")
    dtxt = read_text(decisions) if decisions else ""
    archive_path = find(root, "DECISIONS_归档.md")
    atxt = read_text(archive_path) if archive_path else ""

    if decisions:
        dup = check_dup_decisions(dtxt)
        if dup:
            all_problems += dup
            print("\n[2] 重复决策检查：发现 %d 处" % len(dup))
            for p in dup:
                print("    - " + p)
        else:
            print("\n[2] 重复决策检查：OK（待拍板区无显著重复）")
        ran.append("[2] 重复决策")
    else:
        print("\n[2] 重复决策检查：未配置（未找到 DECISIONS.md，此项未检查）")
        skipped.append("[2] 重复决策（缺 DECISIONS.md）")

    if contract and decisions:
        drift = check_role_drift(root, read_text(contract), dtxt, check_decisions=args.decisions_roles)
        if drift:
            all_problems += drift
            print("\n[3] 角色名漂移检查：发现 %d 处" % len(drift))
            for p in drift:
                print("    - " + p)
        else:
            print("\n[3] 角色名漂移检查：OK（信箱/契约§1 一致）")
        ran.append("[3] 角色名漂移")
    else:
        print("\n[3] 角色名漂移检查：未配置（缺契约或 DECISIONS，此项未检查）")
        skipped.append("[3] 角色名漂移（缺契约或 DECISIONS.md）")

    backlog, msgs, letters, archived = check_mailbox_backlog(root)
    done, pending, no_id, no_to = mailbox_stats(msgs)
    if os.path.isdir(os.path.join(root, "沟通")):
        if backlog:
            all_problems += backlog
            print("\n[4] 信箱逐条积压检查：信件 %d 个 / 消息 %d 条 ⇒ 待处理 %d 条（已处理 %d）"
                  % (letters, len(msgs), pending, done))
            for p in backlog:
                print("    - " + p)
        else:
            print("\n[4] 信箱逐条积压检查：OK（信件 %d 个 / 消息 %d 条，全部已处理）" % (letters, len(msgs)))
        if no_id or no_to:
            print("    · 书写规范提示：%d 条消息没有显式 ID、%d 条没有接收方 ⇒ 建议每条一个标题 + `ID:` + `接收方：`（逐条统计才不靠猜）"
                  % (no_id, no_to))
        ran.append("[4] 信箱逐条积压")
    else:
        print("\n[4] 信箱逐条积压检查：未配置（无 沟通/ 目录）")
        skipped.append("[4] 信箱积压（无 沟通/ 目录）")

    board, exists = check_board_drift(root, dtxt)
    if exists:
        if board:
            all_problems += board
            print("\n[5] 看板漂移检查：")
            for p in board:
                print("    - " + p)
        else:
            print("\n[5] 看板漂移检查：OK（L4 看板无 blocked 任务）")
        ran.append("[5] 看板漂移")
    else:
        print("\n[5] 看板漂移检查：未配置（无 L4 看板，可选层）")
        skipped.append("[5] 看板漂移（无 状态看板.md）")

    light = check_light_mailbox_size(root)
    if light:
        all_problems += light
        print("\n[6] 轻量信箱规模检查：发现 %d 处" % len(light))
        for p in light:
            print("    - " + p)
    else:
        print("\n[6] 轻量信箱规模检查：OK（未超阈值或未使用单文件信箱）")
    ran.append("[6] 轻量信箱规模")

    if decisions:
        size_p = check_decisions_size(dtxt)
        if size_p:
            all_problems += size_p
            print("\n[7] 决策规模检查：发现 %d 处" % len(size_p))
            for p in size_p:
                print("    - " + p)
        else:
            print("\n[7] 决策规模检查：OK（活跃条目未超阈值）")
        ran.append("[7] 决策规模")
    else:
        print("\n[7] 决策规模检查：未配置（未找到 DECISIONS.md）")
        skipped.append("[7] 决策规模（缺 DECISIONS.md）")

    if decisions:
        ev, active, retired, known = check_decision_events(dtxt, atxt)
        if ev:
            all_problems += ev
            print("\n[8] 决策事件一致性检查：发现 %d 处" % len(ev))
            for p in ev:
                print("    - " + p)
        else:
            print("\n[8] 决策事件一致性检查：OK（活跃 %d 条 / 失效留档 %d 条，无幽灵引用）"
                  % (len(active), len(retired)))
        ran.append("[8] 决策事件一致性")
    else:
        print("\n[8] 决策事件一致性检查：未配置（未找到 DECISIONS.md）")
        skipped.append("[8] 决策事件（缺 DECISIONS.md）")

    lock_probs, lock_stats = check_lock_store(root, now)
    if lock_stats is None:
        print("\n[9] 真实锁存储检查：未配置（无 .治理锁/ 目录——只用文字锁时锁的互斥靠自觉）")
        skipped.append("[9] 真实锁存储（未启用 edit_lock.py）")
    else:
        if lock_probs:
            all_problems += lock_probs
            print("\n[9] 真实锁存储检查：发现 %d 处" % len(lock_probs))
            for p in lock_probs:
                print("    - " + p)
        else:
            print("\n[9] 真实锁存储检查：OK（有效锁 %d，待合并件 %d）" % (lock_stats["held"], lock_stats["queued"]))
        ran.append("[9] 真实锁存储")

    if args.decision_view and decisions:
        view, gone = build_decision_view(dtxt, atxt)
        print("\n=== 当前有效决策视图（由 supersedes/撤销事件生成，不读历史正文）===")
        for line in view:
            print("    " + line)
        if gone:
            print("    —— 以下编号已失效，原文见历史区/归档，禁止据此开工：%s" % "、".join(gone))
        print("    活跃 %d 条 / 失效 %d 条" % (len(view), len(gone)))

    print("\n=== 检查覆盖率 ===")
    pct = (100.0 * len(ran) / CHECKS_TOTAL)
    print("实跑 %d/%d 项（%.0f%%）" % (len(ran), CHECKS_TOTAL, pct))
    print("  已跑: %s" % "、".join(ran))
    if skipped:
        print("  未检查: %s" % "、".join(skipped))
        print("  · 未检查项＝对应治理构件未配置或被跳过，不代表那一部分没问题；补齐构件后重跑。")
    print("  信箱统计: 信件 %d / 消息 %d（待处理 %d）/ 归档跳过 %d" % (letters, len(msgs), pending, archived))

    print("\n================")
    if all_problems:
        print("结论：发现 %d 处问题，建议处理后再推进协作。" % len(all_problems))
        return 1
    if not ran:
        print("结论：一项检查都没跑成，不给健康结论。")
        return 3
    print("结论：已配置的检查全部通过（覆盖率 %d/%d，未检查项见上）。" % (len(ran), CHECKS_TOTAL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
