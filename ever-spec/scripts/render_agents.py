#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGENTS.md 生成区（render_agents）——「路由与现状」从手写改成生成物。

    py scripts/render_agents.py --root <项目根>            # 干跑：只打印将渲染的内容
    py scripts/render_agents.py --root <项目根> --apply    # 写回（整体覆盖 AUTO 块）
    py scripts/render_agents.py --root <项目根> --init --apply   # 首次接入：插入 AUTO 块骨架

为什么是"整块覆盖"而不是"增量 patch"（v1.20.0 采纳 Marrow AI Context 的做法）：
  增量更新的毛病是**生成件会落在半新半旧的状态**——这次改了这行、下次忘了那行，最后没人知道
  AUTO 块里哪些是真的。整体覆盖语义上只有两种结果：完全反映当前真相，或（失败时）完全不动。
  实现上也更简单：不 diff、不猜锚点，README 里任何一个字符变了就整段重写。

渲染三块（都能机读现采，不抄任何文档里的数字）：
  ① 现状读数：需求/卡/测试/走查/断言条数 ＋ 矩阵生成时间
  ② 改动落点速查：**文件 → 涉及需求 → 符号**（这是"下一个 AI 该读哪几个符号"的正表，
     答案来自 trace/matrix.json；表里没有的文件＝与本项目的条款无关，不必读）
  ③ 模块路由：模块地图表的「模块 → 关键文件 → 职责摘要」

人写区（红线/禁忌/代码风格）不动：脚本只碰两个 AUTO 标记之间的内容，缺标记时**拒绝猜**，
要插入必须显式 `--init`（默认 false），防止把人的正文吞掉。

退出码：0 成功｜1 写回失败｜2 用法/路径不对。
零第三方依赖。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
AGENTS = "AGENTS.md"
START = "<!-- AUTO-BEGIN"
END_LINE = "<!-- AUTO-END -->"
GEN_LINE = ("<!-- AUTO-BEGIN 由 scripts/render_agents.py 生成 · 禁手改（手改会被下次生成覆盖）"
            " · 整块覆盖，不做增量 patch -->")


def read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def load_matrix(root: Path):
    p = root / "trace" / "matrix.json"
    if not p.is_file():
        return None
    try:
        return json.loads(read_text(p))
    except json.JSONDecodeError:
        return None


def count_cards(root: Path):
    """返回 (L2 纲要卡数, 具体任务卡目录的 md 份数)。
    🔴 两者必须分开报：把「具体任务卡/ 下的回执、快审结论」一起数进来，
    会让「卡数」凭空翻三倍——那正是手写现状面最容易漂的一个数。"""
    l2 = 0
    d = root / "docs" / "pipeline" / "cards"
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            if "说明" in f.name:
                continue
            l2 += 1
    other = 0
    d2 = root / "docs" / "pipeline" / "具体任务卡"
    if d2.is_dir():
        other = sum(1 for _ in d2.rglob("*.md"))
    return l2, other


def matrix_fp(root: Path) -> str:
    """矩阵的 md5 前 12 位——写进生成区，让「AGENTS.md 落后于矩阵」可被 C17 机检。"""
    p = root / "trace" / "matrix.json"
    if not p.is_file():
        return "—"
    import hashlib
    return hashlib.md5(p.read_bytes()).hexdigest()[:12]


def count_state_cards(root: Path):
    p = root / "docs" / "pipeline" / "state.json"
    if not p.is_file():
        return 0
    try:
        d = json.loads(read_text(p))
        return len(d.get("cards") or {})
    except json.JSONDecodeError:
        return 0


def count_tests(root: Path):
    """后端 pytest 用例数（`def test_`）＋前端 node:test 用例数（`test(`）。"""
    py = 0
    d = root / "tests"
    if d.is_dir():
        for f in d.rglob("*.py"):
            py += len(re.findall(r"^\s*(?:async\s+)?def\s+test_", read_text(f), re.M))
    fe = 0
    for base in ("frontend/tests", "tests/node"):
        d = root / base
        if d.is_dir():
            for f in d.rglob("*.ts"):
                fe += len(re.findall(r"^\s*test\s*\(", read_text(f), re.M))
    return py, fe


def module_rows(root: Path):
    """从模块地图表抽「模块 → 关键文件 → 职责摘要」（人读表的机读投影，不复制整行）。"""
    p = root / "docs" / "pipeline" / "模块地图.md"
    text = read_text(p)
    if not text:
        return []
    rows, header_seen = [], False
    for line in text.splitlines():
        if not line.startswith("|"):
            header_seen = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        if not header_seen:
            header_seen = cells[0] == "模块"
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        mod, duty, where = cells[0], cells[1], cells[2]
        files = sorted(set(re.findall(r"\]\((?:\.\./)*([\w\-./]+\.(?:py|ts|tsx|css|json|md))\)",
                                      line)))[:6]
        rows8 = None
        rows.append({"module": mod, "duty": duty[:52], "files": files, "where": where[:40]})
    return rows


def locate_table(matrix):
    """文件 → 涉及需求 → 符号（矩阵的逆向投影）。"""
    by_file = {}
    for rid, e in (matrix.get("requirements") or {}).items():
        for c in e.get("code") or []:
            f = c.get("file")
            if not f:
                continue
            slot = by_file.setdefault(f, {"reqs": [], "syms": []})
            if rid not in slot["reqs"]:
                slot["reqs"].append(rid)
            if c.get("symbol") and c["symbol"] not in slot["syms"]:
                slot["syms"].append(c["symbol"])
        for t in e.get("tests") or []:
            f = t.get("file")
            if not f:
                continue
            slot = by_file.setdefault(f, {"reqs": [], "syms": []})
            if rid not in slot["reqs"]:
                slot["reqs"].append(rid)
    return by_file


def rid_key(rid):
    m = re.match(r"R-(\d+)", rid)
    return (int(m.group(1)), rid) if m else (9999, rid)


def render(root: Path) -> str:
    matrix = load_matrix(root)
    py_tests, fe_tests = count_tests(root)
    n_state_cards = count_state_cards(root)
    n_l2, n_taskdocs = count_cards(root)
    reqs = (matrix or {}).get("requirements") or {}
    walk = (matrix or {}).get("walkthrough") or {}
    lines = [GEN_LINE, "", "## 现状（生成区 · 数字全部现采，别手抄）", ""]

    if matrix is None:
        lines += ["> ⚠️ 没有 `trace/matrix.json`：先跑 "
                  "`py <交付流水线 skill 根>/scripts/trace_matrix.py gen --root <项目根> --apply`。", ""]
    else:
        s = matrix.get("summary") or {}
        lines += [
            "| 项 | 读数 | 来源 |",
            "|---|---|---|",
            f"| 行为条款 | {s.get('requirements', len(reqs))} 条（{', '.join(sorted(reqs, key=rid_key)[:1])}"
            f"~{', '.join(sorted(reqs, key=rid_key)[-1:])}） | specs/ 现扫 |",
            f"| 有代码定位 / 有测试 / 有走查 | {s.get('with_code', 0)} / {s.get('with_tests', 0)}"
            f" / {s.get('with_walkthrough', 0)} | trace/matrix.json |",
            f"| 走查清单 | PRD 列 {s.get('walkthrough_items_in_prd', 0)} 条，矩阵覆盖"
            f" {s.get('walkthrough_items_covered', 0)} 条 | PRD 节内计数 |",
            f"| 任务卡 | L2 纲要卡 {n_l2} 张 / state.json 记账 {n_state_cards} 张"
            f" / `具体任务卡/` md {n_taskdocs} 份（含回执） | cards/＋state.json 现扫 |",
            f"| 用例 | 后端 {py_tests} 条 pytest / 前端 {fe_tests} 条 node:test | tests/ 现扫 |",
            f"| 矩阵生成时间 | {matrix.get('generated_at', '?')} | trace/matrix.json |",
            "",
            "> 状态唯一权威＝[docs/pipeline/state.json](docs/pipeline/state.json)；台账/任务板是它的视图，"
            "**禁手改**；本区块由脚本整块覆盖，手写内容请放在 AUTO 标记之外。",
            "",
        ]

    if matrix and reqs:
        by_file = locate_table(matrix)
        lines += ["## 改动落点速查（生成区 · 文件 → 涉及条款 → 符号）", "",
                  "> 表里没有的文件＝与本项目条款没有机读关联，**不必读**（这比全仓 grep 省一整轮）。"
                  "定位失效会被 `spec_check` 的 C17 当场判红。", "",
                  "| 文件 | 涉及条款 | 符号（关键定位） |", "|---|---|---|"]
        for f in sorted(by_file):
            slot = by_file[f]
            syms = "、".join(f"`{x}`" for x in slot["syms"][:6]) or "—"
            rl = "、".join(sorted(slot["reqs"], key=rid_key))
            lines.append(f"| `{f}` | {rl} | {syms} |")
        lines.append("")

    rows = module_rows(root)
    if rows:
        lines += ["## 模块路由（生成区 · 由 模块地图.md 投影）", "",
                  "| 模块 | 关键文件 | 管什么（摘要，全文见模块地图） |", "|---|---|---|"]
        for r in rows:
            files = "<br>".join(f"[{Path(x).name}]({x})" for x in r["files"]) or "—"
            lines.append(f"| {r['module']} | {files} | {r['duty']} |")
        lines.append("")

    lines += [f"> 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
              f"｜矩阵快照 {matrix_fp(root)}｜矩阵生成时间 {(matrix or {}).get('generated_at', '—')}",
              "> ⚠️ 本区块记录的「矩阵快照」与 `trace/matrix.json` 现值的 md5 不一致时，"
              "`spec_check` 的 C17 会点名——重跑本脚本即可。",
              END_LINE]
    return "\n".join(lines)


def splice(text: str, block: str, init: bool):
    """把 block 整体放进两个标记之间。返回 (新全文, 动作说明)；无法定位且未 --init 时返回 (None, 原因)。"""
    s = text.find(START)
    e = text.find(END_LINE)
    if s != -1 and e != -1 and e > s:
        return text[:s] + block + text[e + len(END_LINE):], "覆盖已有 AUTO 块"
    if not init:
        return None, "AGENTS.md 里没有 AUTO 标记（加 --init 插入骨架，或先手工放一对标记）"
    # 首次接入：插在第一个二级标题之前（标题行与导语之后），没有二级标题就追加到末尾
    m = re.search(r"^##\s", text, re.M)
    at = m.start() if m else len(text)
    head = text[:at]
    tail = text[at:]
    if head and not head.endswith("\n"):
        head += "\n"
    return head + "\n" + block + "\n\n" + tail, "首次插入 AUTO 块"


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="AGENTS.md 生成区渲染器（整块覆盖）")
    ap.add_argument("--root", required=True)
    ap.add_argument("--apply", action="store_true", help="真写盘（默认干跑）")
    ap.add_argument("--init", action="store_true", help="首次接入：没有 AUTO 标记时插入骨架")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    ap_file = root / AGENTS
    if not ap_file.is_file():
        print(f"[缺件] {AGENTS} 不存在（本脚本只维护已有文件，不凭空造人写区）⇒ 退 2")
        return 2

    block = render(root)
    new_text, action = splice(read_text(ap_file), block, args.init)
    if new_text is None:
        print(f"[拒绝写] {action} ⇒ 退 2")
        return 2

    print(f"[render_agents v{VERSION}] root={root}")
    print(f"[动作] {action}｜生成区 {len(block.splitlines())} 行")
    if not args.apply:
        print("[干跑] 未写盘。下面是将写入生成区的内容：\n")
        print(block)
        return 0

    tmp = ap_file.with_suffix(".md.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(ap_file)
    back = read_text(ap_file)
    ok = back.count(START) == 1 and back.count(END_LINE) == 1 and "## 现状（生成区" in back
    print(f"[已写] {AGENTS}（{len(back)} 字符）")
    print(f"[读回] 标记各 1 处={'是' if ok else '否'}｜生成区在位={'是' if '## 现状（生成区' in back else '否'}")
    if not ok:
        print("[失败] 写后读回不符（标记重复或生成区缺失）⇒ 退 1；原文可从 git 取回")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
