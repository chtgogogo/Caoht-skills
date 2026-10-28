#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dag —— 卡图工具（EverSpec v2 施工期 · K07：判环/拓扑/并行集/关键路径）。

零依赖（Python 3 标准库）。输入＝markdown 卡表：表头含「卡」与「depends」两列、
行首格为 K+数字 的行视为卡行（计划表 §六 形态）；depends 单元格里的〔…〕杂注
先剥除再抽 K 号——「与 K09 互斥已核」这类注记里的卡号不是依赖，抽进去就是假边。

四件事＋自证：
  check    判环（含自环）＋未知依赖＋重复卡号，干净才出读数 → 0 无环｜1 红｜2 用法/文件不在
  topo     拓扑序（Kahn，同层卡号升序——确定性输出）
  parallel 并行集（Kahn 分层＝最早可开工集，同层互不依赖）
  critical 关键路径（按卡数计最长链，并列取末端卡号最小）
  selftest 红例自证（只写临时树）

🔴 诚实口径（两件别搞混）：
- 「并行层」是纯依赖图的最早可开工集，**不含 writes 互斥折算**——计划表的「批」＝层
  ＋人工互斥折算（谁跟谁写同一文件），本工具不读 writes 列，别拿层当批。
- 「关键路径」权重＝卡数（每卡记 1），不含工期估计——排的是依赖深度，不是人日。

退出码：0 全绿｜1 有红（环/未知依赖/重复卡号/表里没卡）｜2 用法不对（文件不在/缺参）。
路径口径：--table 由调用方传入，本件自身零绝对路径；单元格内禁半角 |（markdown 表格通病）。
"""

import argparse
import contextlib
import heapq
import io
import re
import shutil
import sys
from pathlib import Path

VERSION = "1.0.0"
ASSERT_FLOOR = 23   # selftest 断言分母闸：条数掉了就该红
CARD_RE = re.compile(r"^K\d+$")
DEP_RE = re.compile(r"K\d+")
ANNO_RE = re.compile(r"〔[^〕]*〕")          # 〔…〕杂注剥除——注记里的卡号不是依赖
EMPTY_DEPS = {"无", "—", "-", ""}            # depends 写这些＝零依赖


class TableError(Exception):
    """文件级问题（不存在/读不了）→ 退 2；表内容问题走红不进这里。"""


def card_key(n: str):
    return (int(n[1:]), n)


def parse_rows(fp: Path):
    """→ [(卡, [依赖…])] 按文件序。同卡多行原样收（重复由 graph_problems 点名）。"""
    if not fp.is_file():
        raise TableError(f"表文件不存在：{fp}")
    cards, dep_idx, in_table = [], None, False
    for raw in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0] == "卡" and "depends" in cells:
            dep_idx = cells.index("depends")
            in_table = True
            continue
        if not in_table or dep_idx is None or not cells or not CARD_RE.match(cells[0]):
            continue
        cell = cells[dep_idx] if dep_idx < len(cells) else ""
        cell = ANNO_RE.sub("", cell)
        deps = [] if cell.strip() in EMPTY_DEPS else DEP_RE.findall(cell)
        cards.append((cells[0], deps))
    return cards


def graph_problems(cards):
    """结构面 → [(级别, 人话)] 与依赖边数：重复卡号、未知依赖。环在 _load 里查。"""
    problems = []
    seen, dups = set(), []
    for n, _ in cards:
        if n in seen:
            dups.append(n)
        seen.add(n)
    for n in sorted(set(dups), key=card_key):
        problems.append(("红", f"重复卡号：{n} 在表里出现多行——取哪行都说不清"))
    name_set = set(seen)
    edges = 0
    for c, deps in cards:
        for d in deps:
            edges += 1
            if d not in name_set:
                problems.append(("红", f"未知依赖：{c} 声称 depends {d}，卡表里没有这张卡"))
    return problems, edges


def find_cycle(graph):
    """DFS 三色标记 → 环路径（depends 方向，首尾同卡）或 None。确定性：起点按卡号升序。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    sys.setrecursionlimit(max(1000, 4 * len(graph) + 100))

    def dfs(n, stack):
        color[n] = GRAY
        stack.append(n)
        for d in graph[n]:
            if color.get(d) == GRAY:
                return stack[stack.index(d):] + [d]
            if color.get(d, WHITE) == WHITE:
                r = dfs(d, stack)
                if r:
                    return r
        color[n] = BLACK
        stack.pop()
        return None

    for n in sorted(graph, key=card_key):
        if color[n] == WHITE:
            r = dfs(n, [])
            if r:
                return r
    return None


def topo_order(graph):
    """Kahn 拓扑序（堆按卡号升序）。环表返回半截序——上游 _gate 已拦，循环图不出读数。"""
    indeg = {n: 0 for n in graph}
    dependents = {n: [] for n in graph}
    for n, deps in graph.items():
        for d in deps:
            indeg[n] += 1
            dependents[d].append(n)
    heap = [card_key(n) for n in graph if indeg[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        n = heapq.heappop(heap)[1]
        order.append(n)
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(heap, card_key(m))
    return order


def kahn_layers(graph):
    """并行集：层号＝1+依赖最大层（最早可开工集）。同层互不依赖；不含 writes 互斥折算。"""
    order = topo_order(graph)
    layer = {}
    for n in order:
        layer[n] = 1 + max((layer[d] for d in graph[n]), default=0)
    layers = {}
    for n in order:
        layers.setdefault(layer[n], []).append(n)
    return layers


def critical_path(graph):
    """按卡数计最长链（每卡权重 1）：DP 沿拓扑序；并列取末端卡号最小，回溯逐级取小号。"""
    order = topo_order(graph)
    length, prev = {}, {}
    for n in order:
        deps = graph[n]
        if deps:
            best = max(length[d] for d in deps)
            length[n] = best + 1
            prev[n] = min((d for d in deps if length[d] == best), key=card_key)
        else:
            length[n], prev[n] = 1, None
    if not length:
        return [], 0
    top = max(length.values())
    end = min((n for n in length if length[n] == top), key=card_key)
    chain = []
    while end is not None:
        chain.append(end)
        end = prev[end]
    return chain[::-1], top


def _load(fp: Path):
    """→ (cards, problems, edges)。problems 已含环；文件级问题抛 TableError。"""
    cards = parse_rows(fp)
    problems, edges = graph_problems(cards)
    if not problems:
        cyc = find_cycle({c: d for c, d in cards})
        if cyc:
            problems.append(("红", "环：" + " → ".join(cyc)))
    return cards, problems, edges


def _gate(fp: Path, what: str):
    """topo/parallel/critical 共用门：文件不在退 2，图不干净退 1 且不出读数。"""
    try:
        cards, problems, edges = _load(fp)
    except TableError as e:
        print(f"[没出] {e} ⇒ 退 2")
        return None
    if not cards:
        print("[红] 表里没找到卡表 ⇒ 退 1")
        return None
    if problems:
        for lv, msg in problems:
            print(f"[{lv}] {msg}")
        print(f"[止步] 图不干净，{what}没意义 ⇒ 退 1")
        return None
    return cards


def cmd_check(args):
    fp = Path(args.table)
    try:
        cards, problems, edges = _load(fp)
    except TableError as e:
        print(f"[没查] {e} ⇒ 退 2")
        return 2
    print(f"[dag v{VERSION}] table={fp}")
    if not cards:
        print("[红] 表里没找到卡表（要有表头含「卡」与「depends」两列的表，行首格 K+数字）⇒ 退 1")
        return 1
    for lv, msg in problems:
        print(f"[{lv}] {msg}")
    n = len(cards)
    if problems:
        print(f"[汇总] 红 {len(problems)}｜卡 {n}｜依赖边 {edges}")
        print("[止步] 图不干净，不出拓扑/并行/关键路径读数 ⇒ 退 1")
        return 1
    graph = {c: d for c, d in cards}
    layers = kahn_layers(graph)
    chain, clen = critical_path(graph)
    print(f"[全绿] 无环；卡 {n}｜依赖边 {edges}｜并行层 {len(layers)}｜关键路径 {clen} 卡：{' → '.join(chain)}")
    print(f"[汇总] 红 0｜卡 {n}")
    return 0


def cmd_topo(args):
    cards = _gate(Path(args.table), "拓扑序")
    if cards is None:
        return 1
    order = topo_order({c: d for c, d in cards})
    print(f"[拓扑序] {len(order)} 卡：{' → '.join(order)}")
    print("（Kahn；同层卡号升序，依赖总在前）")
    return 0


def cmd_parallel(args):
    cards = _gate(Path(args.table), "并行集")
    if cards is None:
        return 1
    layers = kahn_layers({c: d for c, d in cards})
    print(f"[并行层] 共 {len(layers)} 层（Kahn 最早可开工集；同层互不依赖。不含 writes 互斥折算——计划表的「批」＝层＋人工互斥，两回事）")
    for k in sorted(layers):
        members = sorted(layers[k], key=card_key)
        print(f"层{k}｜{len(members)} 卡｜{' '.join(members)}")
    return 0


def cmd_critical(args):
    cards = _gate(Path(args.table), "关键路径")
    if cards is None:
        return 1
    chain, clen = critical_path({c: d for c, d in cards})
    print(f"[关键路径] {clen} 卡：{' → '.join(chain)}")
    print("（按卡数计最长链，并列取末端卡号最小；权重＝卡数不含工期）")
    return 0


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-dag-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}

    results = []
    seq = [0]

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    def run(*cli):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(cli))
        return rc, buf.getvalue()

    TABLE_HEAD = ("| 卡 | 交付物 | depends | parallel_ok | writes | acceptance（二值） | evidence |\n"
                  "|---|---|---|---|---|---|---|\n")

    def mk_table(name, rows):
        d = tmp_root / name
        d.mkdir(parents=True, exist_ok=True)
        fp = d / "cards.md"
        fp.write_text(TABLE_HEAD + "".join(r + "\n" for r in rows), encoding="utf-8")
        return fp

    def row(card, deps):
        return f"| {card} | 交付物{card} | {deps} | ✓ | w | a | e |"

    _src_self = Path(__file__).resolve().read_text(encoding="utf-8")

    def run_mutant(anchor, replacement, cli):
        """变异探针：打坏实现本体后重载，点名现象必须翻面（原样对照臂=对应 S 用例）。"""
        hits = [l for l in _src_self.splitlines() if l.strip().startswith(anchor)]
        if not hits:
            return None, ""
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "dag_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<dag_mutant>", "exec"), g)
        except Exception:
            return None, ""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = g["main"](list(cli))
        return rc, buf.getvalue()

    # —— S01 地基：链＋独立卡，check 退 0 且读数齐
    fp1 = mk_table("t01", [row("K01", "无"), row("K02", "K01"), row("K03", "K02"), row("K04", "无")])
    rc1, out1 = run("check", "--table", str(fp1))
    green("S01 合法表（链＋独立卡）：check 退 0，无环且并行层 3",
          rc1 == 0 and "无环" in out1 and "并行层 3" in out1,
          f"退码={rc1}")

    # —— S02 三卡环必红且点名环路径（depends 方向）
    fp2 = mk_table("t02", [row("K01", "K03"), row("K02", "K01"), row("K03", "K02")])
    rc2, out2 = run("check", "--table", str(fp2))
    green("S02 三卡环（K01←K03←K02←K01）⇒ 退 1 且点名环路径",
          rc2 == 1 and "环：K01 → K03 → K02 → K01" in out2,
          f"退码={rc2} 环行={[l for l in out2.splitlines() if '环：' in l][:1]}")

    # —— S03 自环必红
    fp3 = mk_table("t03", [row("K01", "K01")])
    rc3, out3 = run("check", "--table", str(fp3))
    green("S03 自环（K01 depends K01）⇒ 退 1", rc3 == 1 and "环：K01 → K01" in out3, f"退码={rc3}")

    # —— S04 未知依赖必红且点名
    fp4 = mk_table("t04", [row("K01", "无"), row("K02", "K99")])
    rc4, out4 = run("check", "--table", str(fp4))
    green("S04 未知依赖 K99 ⇒ 退 1 且点名", rc4 == 1 and "未知依赖" in out4 and "K99" in out4, f"退码={rc4}")

    # —— S05 重复卡号必红
    fp5 = mk_table("t05", [row("K01", "无"), row("K01", "K02"), row("K02", "无")])
    rc5, out5 = run("check", "--table", str(fp5))
    green("S05 重复卡号（K01 两行）⇒ 退 1 且点名", rc5 == 1 and "重复卡号" in out5, f"退码={rc5}")

    # —— S06 文件不在退 2（没查≠全绿）；S06b 文件在但没卡表退 1
    rc6, _ = run("check", "--table", str(tmp_root / "无此表.md"))
    green("S06 表文件不存在 ⇒ 退 2", rc6 == 2, f"退码={rc6}")
    d6b = tmp_root / "t06b"
    d6b.mkdir(parents=True, exist_ok=True)
    fp6b = d6b / "cards.md"
    fp6b.write_text("这是一篇没有卡表的散文。\n", encoding="utf-8")
    rc6b, out6b = run("check", "--table", str(fp6b))
    green("S06b 文件在但没卡表 ⇒ 退 1（内容红，不是用法错）",
          rc6b == 1 and "没找到卡表" in out6b, f"退码={rc6b}")

    # —— S07 topo：菱形表拓扑序合法（行序故意打乱——桩的文件序必被拆穿）
    fp7 = mk_table("t07", [row("K04", "K02,K03"), row("K01", "无"), row("K02", "K01"), row("K03", "K01")])
    rc7, out7 = run("topo", "--table", str(fp7))
    green("S07 topo 菱形表（行序打乱）：退 0 且依赖总在前（K01→K02→K03→K04）",
          rc7 == 0 and "[拓扑序] 4 卡：K01 → K02 → K03 → K04" in out7, f"退码={rc7}")

    # —— S08 topo 确定性：同表连跑两次同输出
    rc8a, out8a = run("topo", "--table", str(fp7))
    rc8b, out8b = run("topo", "--table", str(fp7))
    green("S08 topo 确定性：连跑两次输出逐字相同",
          rc8a == rc8b == 0 and out8a == out8b, f"两次一致={out8a == out8b}")

    # —— S09 parallel：菱形分层＝最早可开工集（层2 同时容纳 K02/K03）
    rc9, out9 = run("parallel", "--table", str(fp7))
    l9 = {l.split("｜")[0]: l for l in out9.splitlines() if l.startswith("层")}
    green("S09 parallel 菱形表：共 3 层，层2={K02,K03} 层3={K04}",
          rc9 == 0 and "共 3 层" in out9 and "K02" in l9.get("层2", "") and "K03" in l9.get("层2", "")
          and "K04" not in l9.get("层2", "") and "K04" in l9.get("层3", ""),
          f"退码={rc9} 层行={sorted(l9)}")

    # —— S10 parallel：两链合并（K03 独立）不硬凑层数
    fp10 = mk_table("t10", [row("K01", "无"), row("K02", "K01"), row("K03", "无"), row("K04", "K02,K03")])
    rc10, out10 = run("parallel", "--table", str(fp10))
    green("S10 parallel 两链合并：仍 3 层（层1={K01,K03}）",
          rc10 == 0 and "共 3 层" in out10, f"退码={rc10}")

    # —— S11 critical：全链表关键路径＝4 卡且序列对
    fp11 = mk_table("t11", [row("K01", "无"), row("K02", "K01"), row("K03", "K02"), row("K04", "K03")])
    rc11, out11 = run("critical", "--table", str(fp11))
    green("S11 critical 全链表：4 卡 K01→K02→K03→K04",
          rc11 == 0 and "[关键路径] 4 卡：K01 → K02 → K03 → K04" in out11, f"退码={rc11}")

    # —— S12 critical 并列取末端卡号最小（确定性）
    fp12 = mk_table("t12", [row("K01", "无"), row("K02", "K01"), row("K03", "K01"),
                            row("K04", "K02"), row("K05", "K03")])
    rc12, out12 = run("critical", "--table", str(fp12))
    green("S12 critical 两条等长链并列：取末端小号（K01→K02→K04）",
          rc12 == 0 and "[关键路径] 3 卡：K01 → K02 → K04" in out12, f"退码={rc12}")

    # —— S13 depends「无／—／空」都解析为零依赖
    fp13 = mk_table("t13", [row("K01", "无"), row("K02", "—"), row("K03", ""), row("K04", "无")])
    rc13, out13 = run("check", "--table", str(fp13))
    green("S13 depends 无/—/空 三形态 ⇒ 全零依赖（依赖边 0，check 退 0）",
          rc13 == 0 and "依赖边 0" in out13, f"退码={rc13}")

    # —— S14 杂注剥除：〔…〕里的卡号不是依赖（抽进去就是假边）
    fp14 = mk_table("t14", [row("K01", "无"), row("K09", "无"), row("K04", "无"),
                            row("K02", "K01，K04〔审卡：与 K09 互斥已核〕")])
    rc14c, out14c = run("check", "--table", str(fp14))
    rc14t, out14t = run("topo", "--table", str(fp14))
    green("S14 depends 带杂注：恰抽 K01,K04（依赖边 2）且 topo 里 K02 排在 K09 前（K09 不是依赖）",
          rc14c == 0 and "依赖边 2" in out14c and rc14t == 0
          and "K02 → K09" in out14t and "K09 → K02" not in out14t,
          f"check退码={rc14c} topo={out14t.splitlines()[0][:60] if out14t else '（无）'}")

    # —— S15 check 读数面齐：卡/依赖边/并行层/关键路径四件都在全绿行
    green("S15 check 全绿行读数齐（卡 4｜依赖边 2｜并行层 3｜关键路径 3 卡）",
          "卡 4" in out1 and "依赖边 2" in out1 and "并行层 3" in out1 and "关键路径 3 卡" in out1,
          f"全绿行={[l for l in out1.splitlines() if l.startswith('[全绿]')][:1]}")

    # —— S16 环表上 topo 拒绝出读数（不出半截拓扑）
    rc16, out16 = run("topo", "--table", str(fp2))
    green("S16 环表跑 topo ⇒ 止步退 1，不出拓扑序",
          rc16 == 1 and "止步" in out16 and "[拓扑序]" not in out16, f"退码={rc16}")

    # —— S17 变异臂：环检测打哑 ⇒ S02 现象翻面（环表假绿）
    rc17, out17 = run_mutant("return stack[stack.index(d):] + [d]",
                             "                return None",
                             ["check", "--table", str(fp2)])
    green("S17 变异臂：环检测打哑 ⇒ 环表退 0（S02 现象消失=检测面被钉住）",
          rc17 == 0 and "环：" not in out17, f"变异后退码={rc17}")

    # —— S18 变异臂：分层打哑 ⇒ S09 现象翻面（全并一层）
    rc18, out18 = run_mutant("layer[n] = 1 + max((layer[d] for d in graph[n]), default=0)",
                             "        layer[n] = 1", ["parallel", "--table", str(fp7)])
    green("S18 变异臂：分层打哑 ⇒ 菱形表只报 1 层（S09 现象翻面=分层实现被钉住）",
          rc18 == 0 and "共 1 层" in out18, f"变异后退码={rc18}")

    # —— S19 变异臂：关键路径打哑 ⇒ S11 现象翻面（长度 1）
    rc19, out19 = run_mutant("length[n] = best + 1", "            length[n] = 1",
                             ["critical", "--table", str(fp11)])
    green("S19 变异臂：关键路径打哑 ⇒ 链长报 1（S11 现象翻面=DP 被钉住）",
          rc19 == 0 and "[关键路径] 1 卡" in out19, f"变异后退码={rc19}")

    # —— S20 分母闸 / S21 scripts 零写入 / S22 收尾清理
    green(f"S20 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 3 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 3} 条")
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S21 scripts/ 目录零写入（兄弟文件 mtime 全等）", scripts_before == scripts_after,
          f"变化={sorted(set(scripts_before) ^ set(scripts_after))}")
    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S22 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")

    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[dag selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="EverSpec 卡图工具（判环/拓扑/并行集/关键路径，零依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_text, func in [
        ("check", "判环＋未知依赖＋重复卡号，干净才出读数", cmd_check),
        ("topo", "拓扑序（Kahn，确定性）", cmd_topo),
        ("parallel", "并行集（Kahn 分层＝最早可开工集）", cmd_parallel),
        ("critical", "关键路径（按卡数计最长链）", cmd_critical),
        ("selftest", "红例自证（只写临时树）", cmd_selftest),
    ]:
        sp = sub.add_parser(name, help=help_text)
        if name == "selftest":
            sp.add_argument("--tmp-root", default=None)
        else:
            sp.add_argument("--table", required=True, help="卡表文件（markdown，表头含「卡/depends」两列）")
        sp.set_defaults(func=func)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
