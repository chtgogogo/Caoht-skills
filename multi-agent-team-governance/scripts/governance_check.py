#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
治理体检脚本 —— 多Agent团队协作文档治理 skill 的内置自检工具。

用途：扫描项目根目录下的治理文件，自动找出"悄悄腐化"的协作问题：
  1. 过期锁：EDIT_LOCK 状态为 wip 且心跳超过 24 小时未更新（死锁风险）
  2. 重复决策：DECISIONS.md 待拍板区里高度相似的条目（应去重）
  3. 角色名漂移：信箱子文件夹 与 契约§1『成员与角色』表 不一致（普适升级 v2）
  4. 信箱积压：信箱信件中没有「状态:done」/「已处理」标记的未处理项
  5. 看板漂移：L4 看板存在时，提醒与 DECISIONS §4 人工核对（列出 blocked 任务）

用法：
  python scripts/governance_check.py --root <项目根目录>
  python scripts/governance_check.py            # 默认当前目录

退出码：0 = 健康（无问题）；1 = 发现问题；2 = 参数/路径错误。
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

LOCK_RE = re.compile(r"EDIT_LOCK:\s*(.+?)\s*(?:-->|\*/|$)", re.MULTILINE)
STATUS_RE = re.compile(r"状态[:：]\s*(\w+)", re.IGNORECASE)
HEARTBEAT_RE = re.compile(r"心跳[:：]\s*([\d]{4}-[\d]{2}-[\d]{2}[ T]?[\d]{2}:[\d]{2})")
WIP_TIMEOUT = timedelta(hours=24)


def now_ts():
    return datetime.now()


def parse_ts(s):
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def find(root, name):
    p = os.path.join(root, name)
    return p if os.path.isfile(p) else None


# ---------- 1. 过期锁 ----------
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


# ---------- 3. 角色名漂移（普适升级 v2） ----------
# 设计目标：以「信箱文件夹名」为权威角色名，契约§1『成员与角色』表为定义来源；
# 通过（a）仅扫描指定 section 的表格、（b）噪声过滤、（c）归一化比对，
# 彻底消除旧版把分隔线/D编号/文件路径/分类名误判为角色的 67 处误报。
HUMAN_KEYWORDS = ("用户", "主脑", "人", "user", "human", "team", "团队")
_SEP_CHARS = set("-: ")


def _norm_role(name):
    """归一化：去已知前缀(godot) + 去尾随数字，用于判定『同一角色的不同写法』。"""
    n = name.strip()
    low = n.lower()
    for pfx in ("godot",):
        if low.startswith(pfx):
            n = n[len(pfx):].strip()
    n = re.sub(r"\d+$", "", n).strip()
    return n


def _is_role_cell(first):
    """判断表格首列是否像『角色名』（普适噪声过滤）。"""
    if not first:
        return False
    if set(first) <= _SEP_CHARS:                          # 分隔线 --- / :---:
        return False
    if first in ("成员", "角色", "ID", "文件 / 模块", "区块", "任务",
                 "决策", "分类", "路径", "主责", "协作", "来源"):
        return False
    if re.match(r"^[DT]-\d+", first):                     # 决策/任务编号 D-08 / T-03
        return False
    if "/" in first or "\\" in first or ".md" in first:   # 路径/文件名
        return False
    if re.fullmatch(r"[\d]+", first):                     # 纯数字
        return False
    if len(first) > 16:                                   # 过长，不可能是角色名
        return False
    return True


def _section_table_first_col(text, section_header=None):
    """提取表格首列候选角色名。
    - 给定 section_header：仅扫描该二级标题下首个表格（权威来源，避免全表噪声）；
    - 否则扫描全文所有表格，但经 _is_role_cell 噪声过滤。"""
    lines = text.splitlines()
    start = 0
    found = False
    if section_header:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#") and section_header in ln:
                start = i + 1   # 跳过标题行本身，否则会把标题行当成"下一个#"提前 break
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
            break  # 离开该 section（仅当确实找到了目标 section 才拦截下一个#）
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

    # 契约§1『成员与角色』表为权威角色来源（仅该 section，杜绝全表噪声）
    contract_agent = {r for r in _section_table_first_col(contract_text, "成员与角色")
                      if not r.lower().startswith(HUMAN_KEYWORDS)}

    # 决策文件默认不参与角色定义（多为散文引用，易误报）；显式 --decisions-roles 才参与
    decision_agent = set()
    if check_decisions and decisions_text:
        decision_agent = {r for r in _section_table_first_col(decisions_text)
                          if not r.lower().startswith(HUMAN_KEYWORDS)}

    # 以归一化形态比对，揪出『同一角色的不同写法』（如 技术美术 vs 技术美术1）
    canon = mailbox_roles | contract_agent | decision_agent
    norm_map = {}
    for r in canon:
        norm_map.setdefault(_norm_role(r), set()).add(r)
    for norm, forms in sorted(norm_map.items()):
        if len(forms) > 1:
            problems.append("角色名写法不一致：归一化『%s』出现多种写法 %s（建议统一为信箱文件夹名）"
                            % (norm, "、".join(sorted(forms))))

    # 信箱 ↔ 契约 双向缺失提示（仅在无写法冲突时补充，避免重复噪声）
    cset = {_norm_role(r) for r in contract_agent}
    mset = {_norm_role(r) for r in mailbox_roles}
    for r in sorted(mailbox_roles):
        if _norm_role(r) not in cset:
            problems.append("信箱文件夹「%s」未在契约§1 角色表列出（建议补登或核对命名）" % r)
    for r in sorted(contract_agent):
        if _norm_role(r) not in mset:
            problems.append("契约§1 角色「%s」无对应信箱文件夹（建议建 沟通/%s/ 或核对）" % (r, r))
    return problems


# ---------- 4. 信箱积压 ----------
def check_mailbox_backlog(root):
    problems = []
    comm = os.path.join(root, "沟通")
    if not os.path.isdir(comm):
        return problems
    single = os.path.join(comm, "投递.md")
    if os.path.isfile(single):
        txt = open(single, encoding="utf-8").read()
        if "状态:done" not in txt and "已处理" not in txt:
            problems.append("轻量信箱 沟通/投递.md 无「已处理」标记，可能存在未读积压")
        return problems
    for role in os.listdir(comm):
        rp = os.path.join(comm, role)
        if not os.path.isdir(rp):
            continue
        for f in os.listdir(rp):
            if not f.endswith(".md"):
                continue
            txt = open(os.path.join(rp, f), encoding="utf-8").read()
            if "状态:done" not in txt and "已处理" not in txt:
                problems.append("信箱未处理: 沟通/%s/%s" % (role, f))
    return problems


# ---------- 5. 看板漂移 ----------
def check_board_drift(root, decisions_text):
    problems = []
    board = find(root, "状态看板.md")
    if not board:
        return problems, False
    btxt = open(board, encoding="utf-8").read()
    blocked = re.findall(r"\|\s*(T-\d+|.+?)\s*\|\s*.+?\s*\|\s*blocked\s*\|", btxt)
    if blocked:
        problems.append("L4 看板含 %d 个 blocked 任务，请核对 DECISIONS §4 执行跟踪是否同步标注阻塞原因" % len(blocked))
    return problems, True


def main():
    ap = argparse.ArgumentParser(description="多Agent团队协作治理体检")
    ap.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    ap.add_argument("--decisions-roles", action="store_true",
                    help="将 DECISIONS.md 也纳入角色名漂移比对（默认仅信箱+契约§1，避免散文引用误报）")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误：目录不存在 -> %s" % root)
        return 2

    now = now_ts()
    all_problems = []

    decisions = find(root, "DECISIONS.md")
    contract = find(root, "团队分工与协作契约.md")

    print("=== 治理体检报告 ===")
    print("扫描根目录: %s\n" % root)

    lock_hits = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith((".md", ".py", ".gd", ".ts", ".js", ".cs", ".cpp", ".gdshader")):
                try:
                    t = open(os.path.join(dirpath, fn), encoding="utf-8").read()
                except Exception:
                    continue
                if "EDIT_LOCK" in t:
                    for p in check_locks(t, now):
                        lock_hits.append("[%s] %s" % (fn, p))
    if lock_hits:
        all_problems += lock_hits
        print("[1] 编辑锁检查：发现 %d 处问题" % len(lock_hits))
        for p in lock_hits:
            print("    - " + p)
    else:
        print("[1] 编辑锁检查：OK（无过期/异常锁）")

    if decisions:
        dtxt = open(decisions, encoding="utf-8").read()
        dup = check_dup_decisions(dtxt)
        if dup:
            all_problems += dup
            print("\n[2] 重复决策检查：发现 %d 处" % len(dup))
            for p in dup:
                print("    - " + p)
        else:
            print("\n[2] 重复决策检查：OK（待拍板区无显著重复）")
    else:
        print("\n[2] 重复决策检查：跳过（未找到 DECISIONS.md）")

    if contract and decisions:
        ctxt = open(contract, encoding="utf-8").read()
        drift = check_role_drift(root, ctxt, dtxt, check_decisions=args.decisions_roles)
        if drift:
            all_problems += drift
            print("\n[3] 角色名漂移检查：发现 %d 处" % len(drift))
            for p in drift:
                print("    - " + p)
        else:
            print("\n[3] 角色名漂移检查：OK（信箱/契约§1 一致）")
    else:
        print("\n[3] 角色名漂移检查：跳过（缺少契约或 DECISIONS）")

    backlog = check_mailbox_backlog(root)
    if backlog:
        all_problems += backlog
        print("\n[4] 信箱积压检查：发现 %d 处" % len(backlog))
        for p in backlog:
            print("    - " + p)
    else:
        print("\n[4] 信箱积压检查：OK（无未处理信件）")

    board, exists = check_board_drift(root, dtxt if decisions else "")
    if exists:
        if board:
            all_problems += board
            print("\n[5] 看板漂移检查：")
            for p in board:
                print("    - " + p)
        else:
            print("\n[5] 看板漂移检查：OK（L4 看板无 blocked 任务）")
    else:
        print("\n[5] 看板漂移检查：跳过（无 L4 看板）")

    print("\n================")
    if all_problems:
        print("结论：发现 %d 处问题，建议处理后再推进协作。" % len(all_problems))
        return 1
    print("结论：治理健康，无发现问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
