#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
治理体检脚本 —— 多Agent团队协作文档治理 skill 的内置自检工具。

用途：扫描项目根目录下的治理文件，自动找出"悄悄腐化"的协作问题：
  1. 过期锁：EDIT_LOCK 状态为 wip 且心跳超过 24 小时未更新（死锁风险）
  2. 重复决策：DECISIONS.md 待拍板区里高度相似的条目（应去重）
  3. 角色名漂移：信箱子文件夹 / 契约§2 角色表 / DECISIONS§2 分工矩阵 三处角色名不一致
  4. 信箱积压：信箱信件中没有「状态:done」标记的未处理项
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
    # 抓待拍板区：从「待拍板」到下一个二级标题或文件尾
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
    # 两两比较 jaccard
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
def _table_first_col(text):
    names = set()
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] and cells[0] not in ("成员", "区块", "文件 / 模块"):
                names.add(cells[0])
    return names


def check_role_drift(root, contract_text, decisions_text):
    problems = []
    mailbox_roles = set()
    comm = os.path.join(root, "沟通")
    if os.path.isdir(comm):
        for d in os.listdir(comm):
            dp = os.path.join(comm, d)
            if os.path.isdir(dp) and d != "投递.md":
                mailbox_roles.add(d)
    contract_roles = _table_first_col(contract_text)
    decision_roles = _table_first_col(decisions_text)
    all_sets = {"信箱": mailbox_roles, "契约§2": contract_roles, "DECISIONS§2": decision_roles}
    union = set().union(*all_sets.values())
    for name in sorted(union):
        seen_in = [k for k, v in all_sets.items() if name in v]
        if len(seen_in) < 3:
            problems.append("角色名「%s」仅出现在 %s，三处不一致（信箱/契约§2/DECISIONS§2）" %
                            (name, "、".join(seen_in)))
    return problems


# ---------- 4. 信箱积压 ----------
def check_mailbox_backlog(root):
    problems = []
    comm = os.path.join(root, "沟通")
    if not os.path.isdir(comm):
        return problems
    # 轻量模式单文件
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
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误：目录不存在 -> %s" % root)
        return 2

    now = now_ts()
    all_problems = []

    decisions = find(root, "DECISIONS.md")
    contract = find(root, "团队分工与协作契约.md")

    # 锁：扫描全部 md（含代码注释风格的 # // 也含 EDIT_LOCK 字样）
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
        drift = check_role_drift(root, ctxt, dtxt)
        if drift:
            all_problems += drift
            print("\n[3] 角色名漂移检查：发现 %d 处" % len(drift))
            for p in drift:
                print("    - " + p)
        else:
            print("\n[3] 角色名漂移检查：OK（信箱/契约/DECISIONS 三处一致）")
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
