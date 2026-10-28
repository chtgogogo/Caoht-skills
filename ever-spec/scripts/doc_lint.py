# -*- coding: utf-8 -*-
"""doc_lint —— 文档体系尺（EverSpec v2 施工期 · K05：MUST-01/07/08 入出边/孤儿/断链/supersede 双向/术语双定义 ＋ K14 waiver 审计机检）。

用法：
    py scripts/doc_lint.py check  --root <文档树根>     # 文档图五查＋waiver 审计（全查）
    py scripts/doc_lint.py waiver --root <项目根>       # 只跑 waiver 审计（K14 三态走查用）
    py scripts/doc_lint.py graph  --root <文档树根>     # 打印文档提及图（边表＋孤儿＋统计；K15 验收入边出边用）
    py scripts/doc_lint.py selftest [--tmp-root <目录>] # 自证（只写临时树）

退出码：0 全绿（可含警）｜1 有红｜2 根目录不对（退 2 不是"没问题"，是"没查"）。

- 严格闭合（MUST-08 入边闭合）：catalog.py 的提及式反向索引是「粗索引导航」，本尺管「严格闭合」——
  markdown 链接指向的相对路径不在盘上＝出边断链（D01 红）；反引号/冒号元信息的路径提及不在盘＝警（D17）。
- supersede 双向（MUST-06/15 咬合口，K04 字段约定）：文档级「取代：/被取代：」必须成对（D02）；
  取代链成环/自指红（D04）；已取代状态缺指针红（D03）；双向齐但被取代侧状态非「已取代」红（D16）。
  指针值＝文件路径形态（含 / 或扩展名）才算文档级归本尺；「取代：R-3」这类需求号指针归 spec_check（K12 断言面）。
- 术语双定义（MUST-01 单一事实源·机检面）：「口径：<术语>＝<值>」同名多出且值冲突＝红（D05）；
  值一致的多处声明＝警（D12，双写漂移隐患）；声明残缺（术语或值为空）＝红（D15）。
- waiver 审计（K14 点名，templates/hooks/README.md 的审计不变式）：每个归档变更包要么存续期内有绿读数，
  要么有 waiver 记录在案，两者皆无＝静默跳过红（D06）；七字段缺一红（D07）；过期未销账红（D08）；
  slug 与所在包目录不符红（D13）；已销账却无绿读数＝销空账红（D14）；有效至形状非法红（D10）。
- 孤儿（D11 警）：非入口、非生成物、零入边且父目录零提及的文档——导航缺失点名，可选增强警级不拦人。
- 运行读数（MUST-17）：每跑一次命令向本件 evidence/runs.log 追加一行（runs.log 第六支，细则 §6.7）；
  目标树零写入；写不进去静默跳过。环境变量 EVSPEC_RUNS_LOG 改落点（selftest 零写入纪律用）。
- 边界（别对外吹）：只查机械面——语义上「该不该链」「术语值谁对」不判；外链（http/盘符绝对路径）与
  跨树指针不查（归供应链登记四元组）；「绿读数在案」只判记录存在，不判时间窗口语义（人工面，细则 §6.8）。
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

VERSION = "1.0.0"

CHECKS = [
    ("D01", "出边断链：markdown 链接指向的相对路径在盘上不存在"),
    ("D02", "supersede 单向残缺（取代/被取代缺对侧，或对侧不在盘）"),
    ("D03", "已取代状态缺指针（终态必带取代/被取代行）"),
    ("D04", "supersede 环与自指（A 取代 B 且 B 取代 A／A 取代 A）"),
    ("D05", "术语双定义值冲突（同术语多处口径声明且值不一致）"),
    ("D06", "归档包静默跳过（无绿读数且无 waiver 在案）"),
    ("D07", "waiver 七字段缺一（填全才被钩子与审计认账）"),
    ("D08", "waiver 过期未销账（有效至已过且销账行无已销）"),
    ("D09", "文档状态值非法（草案/生效/已取代/废弃四态之外；写了就查）"),
    ("D10", "waiver 有效至形状非法（认账行必须是 ISO 日期）"),
    ("D11", "孤儿文档（非入口/生成物、零入边且父目录零提及；警）"),
    ("D12", "术语重复声明值一致（双写漂移隐患，收敛提示；警）"),
    ("D13", "waiver 变更包 slug 与所在包目录不符（认账行指鹿为马）"),
    ("D14", "已销账却无绿读数（销空账：销账前提是补绿）"),
    ("D15", "术语声明残缺（术语或值为空）"),
    ("D16", "双向齐但被取代侧状态非「已取代」（咬合矛盾）"),
    ("D17", "提及路径不在盘（反引号/冒号元信息提及形态，非链接；警）"),
]
CHECK_TITLES = dict(CHECKS)
ASSERT_FLOOR = 38   # selftest 断言分母闸：条数掉了就该红，防止有人悄悄删用例

# ---------------------------------------------------------------- 判据正则
# 🔴 每条单占一行：变异探针臂按行首锚点整行替换打哑，拆行=探针失锚（spec_check 同款纪律）。
DOC_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
PATH_IN_CODE = re.compile(r"`([\w\-./\\]+\.(?:md|py|json|txt|sh|yaml|yml|toml)|[\w\-./\\]+/)`")
DECL_PATH = re.compile(r"^\s*(?:[-*]\s*)?[^\s：:|][^：:|]{0,30}[：:]\s*([\w\-./\\]+\.(?:md|py|json|txt|sh|yaml|yml|toml))(?:\s*[（(][^）)]*[）)])?\s*(?:\|.*)?$")
SUPERSEDE_TAKE = re.compile(r"^\s*[-*]?\s*取代\s*[：:]\s*(.+?)\s*$")
SUPERSEDE_BY = re.compile(r"^\s*[-*]?\s*被取代\s*[：:]\s*(.+?)\s*$")
TERM_DECL = re.compile(r"^\s*(?:[-*]\s*)?口径\s*[：:]\s*([^\s：:=＝|]{1,24})\s*[=＝：:]\s*(.*?)\s*$")
DOC_STATE_LINE = re.compile(r"^\s*[-*]?\s*状态\s*[：:]\s*(.+?)\s*$")
DOC_TTL_LINE = re.compile(r"^\s*[-*]?\s*有效至\s*[：:]\s*(\S+)\s*$", re.M)
WAIVER_SETTLED = re.compile(r"已销")
STATE_ALLOWED = ("草案", "生效", "已取代", "废弃")
WAIVER_FIELDS = ("变更包", "日期", "签发人", "原因", "影响范围", "有效至", "销账")
TTL_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ENTRY_NAMES = {"skill.md", "readme.md", "agents.md"}
GENERATED_NAMES = {"catalog.md", "catalog.json"}
GENERATED_MARK = "生成物"
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", "evidence"}
TEXT_EXTS = {".md"}
TOKEN_RE = re.compile(r"[^\s，,；;。]+")
GREEN_MARK = re.compile(r'"exit_code"\s*:\s*0')

RUNSLOG_ON = True   # 🔴 单占一行：变异探针臂按行首锚点整行替换，拆行=探针失锚。


def read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def _is_external(tgt: str) -> bool:
    """外链与跨树指针不查（归供应链登记四元组）：URL、盘符绝对路径、/ 盘根、纯锚点。"""
    t = tgt.strip()
    if not t or t.startswith("#"):
        return True
    low = t.lower()
    if low.startswith(("http://", "https://", "mailto:", "ftp://")):
        return True
    if re.match(r"^[A-Za-z]:", t) or t.startswith("/"):
        return True
    return False


def _supersede_target(value: str):
    """取代/被取代行的值 → 文档级指针（含 / 或带扩展名的路径 token）；需求号（R-3 形态）返回 None 归 spec_check 管。"""
    m = TOKEN_RE.match(value.strip())
    if not m:
        return None
    tok = m.group(0).strip("（）()·")
    if _is_external(tok):
        return None
    if "/" in tok or re.search(r"\.(md|txt|py|json|sh|yaml|yml)$", tok, re.I):
        return tok
    return None


def _visible_lines(text: str):
    """边源行提取：代码围栏内的命令示例与引用块（> 开头）的沿革证词不进边源（C22 证词豁免同款思路）。
    返回与原文件同长的列表，豁免行/围栏行置 None（保行号）。"""
    out = []
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(None)
            continue
        out.append(None if (in_fence or ln.lstrip().startswith(">")) else ln)
    return out


def _is_generated(rel: str, first_chunk: str) -> bool:
    if Path(rel).name.lower() in GENERATED_NAMES:
        return True
    return GENERATED_MARK in first_chunk[:300] or "AUTO-BEGIN" in first_chunk[:300]


def build_doc_graph(root: Path):
    """扫描文档树 → 文件集＋严格边集＋目录提及集。排除断言不查的目录（证据落盘处非活文档）。"""
    root = Path(root)
    files, edges, dir_mentions = {}, [], set()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
            continue
        rel = p.relative_to(root).as_posix()
        parts = Path(rel).parts
        if any(seg in EXCLUDE_DIRS or seg.startswith("_tmp-") for seg in parts[:-1]):
            continue
        text = read_text(p)
        files[rel] = {"lines": _visible_lines(text), "path": p, "raw": text}
        if _is_generated(rel, text):
            continue
        for i, ln in enumerate(files[rel]["lines"], 1):
            if ln is None:
                continue
            for m in DOC_LINK.finditer(ln):
                tgt = m.group(2).split("#")[0]
                if _is_external(tgt):
                    continue
                is_dir = tgt.endswith("/")
                edges.append({"src": rel, "line": i, "tgt": tgt, "kind": "目录链接" if is_dir else "链接"})
            for m in PATH_IN_CODE.finditer(ln):
                tok = m.group(1)
                if _is_external(tok):
                    continue
                if tok.endswith("/"):
                    dir_mentions.add(tok)
                    edges.append({"src": rel, "line": i, "tgt": tok, "kind": "目录提及"})
                else:
                    edges.append({"src": rel, "line": i, "tgt": tok, "kind": "提及"})
            for m in DECL_PATH.finditer(ln):
                tgt = m.group(1)
                if _is_external(tgt):
                    continue
                edges.append({"src": rel, "line": i, "tgt": tgt, "kind": "元信息"})
    for e in edges:
        base = (root / e["src"]).parent
        cand = [base / e["tgt"]] if not e["tgt"].startswith("/") else []
        cand.append(root / e["tgt"])
        e["resolved"] = next((c.resolve() for c in cand if c.exists()), None)
    return {"root": root, "files": files, "edges": edges, "dir_mentions": dir_mentions}


def _doc_state(files, rel):
    for ln in files[rel]["lines"]:
        if ln is None:
            continue
        m = DOC_STATE_LINE.match(ln)
        if m:
            return m.group(1).strip()
    return None


def _supersede_pairs(files):
    """文档级取代声明 → [(src, kind, tgt_token, line)]；需求号指针（R-3 形态）不收（归 spec_check/K12）。"""
    pairs = []
    for rel, info in files.items():
        for i, ln in enumerate(info["lines"], 1):
            if ln is None:
                continue
            m = SUPERSEDE_TAKE.match(ln)
            if m:
                tgt = _supersede_target(m.group(1))
                if tgt:
                    pairs.append((rel, "取代", tgt, i))
                continue
            m = SUPERSEDE_BY.match(ln)
            if m:
                tgt = _supersede_target(m.group(1))
                if tgt:
                    pairs.append((rel, "被取代", tgt, i))
    return pairs


def _resolve_doc(files, src_rel, tgt_token):
    """取代指针按「项目根相对形态」解析（细则 §6.8 约定），回落到文档同目录。"""
    root = files["_root"] if isinstance(files, dict) else None
    return tgt_token


def collect_terms(files):
    """口径声明收集 → [(term, value, where)]；声明残缺（term/value 空）原样带出交给判据。"""
    terms = []
    for rel, info in files.items():
        for i, ln in enumerate(info["lines"], 1):
            if ln is None:
                continue
            m = TERM_DECL.match(ln)
            if m:
                terms.append((m.group(1).strip(), m.group(2).strip(), f"{rel}:{i}"))
    return terms


def find_waiver_dirs(root: Path):
    """归档变更包目录：changes/archive/ 下一层子目录（K14 位置纪律：waiver 随包归档）。"""
    arch = Path(root) / "changes" / "archive"
    if not arch.is_dir():
        return []
    return sorted(d for d in arch.iterdir() if d.is_dir() and not d.name.startswith("."))


def has_green_record(pkg_dir: Path) -> bool:
    """包内是否有绿读数在案：任一文本件含 check --json 的全绿行（机械面只判记录存在）。"""
    for p in pkg_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".log", ".txt", ".md", ".json"}:
            continue
        try:
            if p.stat().st_size > 200_000:
                continue
            if GREEN_MARK.search(read_text(p)):
                return True
        except OSError:
            continue
    return False


def audit_waivers(root: Path):
    """waiver 审计（K14 审计不变式）→ 红项列表 [(code, where, msg)]。"""
    reds = []
    today = date.today()
    for pkg in find_waiver_dirs(root):
        wpath = pkg / "waiver.md"
        green = has_green_record(pkg)
        if not wpath.is_file():
            if not green:
                reds.append(("D06", f"changes/archive/{pkg.name}",
                             "归档包既无绿读数（check --json 全绿行）又无 waiver 在案＝静默跳过，点名"))
            continue
        text = read_text(wpath)
        lines = text.splitlines()
        missing = [f for f in WAIVER_FIELDS
                   if not re.search(rf"^\s*[-*]?\s*{f}\s*[：:]", text, re.M)]
        if missing:
            reds.append(("D07", f"changes/archive/{pkg.name}/waiver.md",
                         f"七字段缺 {'、'.join(missing)}——七字段填全才被钩子与 waiver 审计认账"))
        mslug = re.search(r"^\s*[-*]?\s*变更包\s*[：:]\s*(\S+)", text, re.M)
        if mslug:
            slug = mslug.group(1).strip("。")
            if slug != pkg.name and not pkg.name.endswith("-" + slug):
                reds.append(("D13", f"changes/archive/{pkg.name}/waiver.md",
                             f"变更包写「{slug}」而所在包目录是「{pkg.name}」——认账行指鹿为马"))
        mttl = DOC_TTL_LINE.search(text)
        settled = bool(WAIVER_SETTLED.search(text))
        if mttl:
            raw = mttl.group(1)
            if not TTL_SHAPE.match(raw):
                reds.append(("D10", f"changes/archive/{pkg.name}/waiver.md",
                             f"有效至「{raw}」不是 ISO 日期（YYYY-MM-DD）——钩子认不了账"))
            else:
                try:
                    expired = date.fromisoformat(raw) < today
                except ValueError:
                    expired = False
                if expired and not settled:
                    reds.append(("D08", f"changes/archive/{pkg.name}/waiver.md",
                                 f"有效至 {raw} 已过且销账行无「已销」——过期未销账＝未豁免，钩子重新拦截"))
        if settled and not green:
            reds.append(("D14", f"changes/archive/{pkg.name}/waiver.md",
                         "销账行写「已销」但包内查无绿读数——销空账：销账的前提是补绿"))
        if not settled and not green:
            pass  # 有 waiver 在期即放行（在期性由 D08 把关）；绿读数是另一条合法出路
    return reds


def run_checks(root: Path):
    """全查：文档图五查＋waiver 审计。返回 items 列表 [(code, level, where, msg)]。"""
    items = []
    g = build_doc_graph(root)
    root = g["root"]
    files = g["files"]

    # D01/D17 出边闭合（MUST-08 严格面）：链接断链＝红；提及/元信息断链＝警（严格红只归链接形态）
    seen_edge = set()
    for e in g["edges"]:
        key = (e["src"], e["line"], e["tgt"])
        if key in seen_edge:
            continue
        if e["kind"] in ("链接", "目录链接"):
            if e["resolved"] is None:
                seen_edge.add(key)
                items.append(("D01", "红", f"{e['src']}:{e['line']}",
                              f"链接指向的 {e['tgt']} 在盘上找不到——出边断链（严格闭合面）"))
        elif e["resolved"] is None and e["kind"] != "目录提及":
            seen_edge.add(key)
            items.append(("D17", "警", f"{e['src']}:{e['line']}",
                          f"提及的 {e['tgt']} 不在盘上——提及形态只记警，确认是死指针就改成链接或删掉"))

    # supersede 双向（文档级；指针值＝路径形态，「取代：R-3」这类需求号归 spec_check/K12）
    def _resolve(src, tok):
        for base in ((root / src).parent, root):
            p = base / tok
            if p.exists():
                try:
                    return p.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    return p.resolve().as_posix()
        return None

    pairs = _supersede_pairs(files)
    takes = [(a, b, ln) for (a, k, b, ln) in pairs if k == "取代"]
    bys = [(a, b, ln) for (a, k, b, ln) in pairs if k == "被取代"]
    take_set = {(a, _resolve(a, b)) for a, b, _ in takes}
    by_set = {(a, _resolve(a, b)) for a, b, _ in bys}
    for a, b, ln in takes:
        br = _resolve(a, b)
        if br is None or br not in files:
            items.append(("D02", "红", f"{a}:{ln}",
                          f"取代指针指向的 {b} 不在盘上——被取代件缺席，单向残缺"))
        elif (br, a) not in by_set:
            items.append(("D02", "红", f"{a}:{ln}",
                          f"取代了 {b} 但对侧没有「被取代：{a}」行——supersede 单向残缺（双向纪律）"))
    for a, b, ln in bys:
        br = _resolve(a, b)
        if br is None or br not in files:
            items.append(("D02", "红", f"{a}:{ln}",
                          f"被取代指针指向的 {b} 不在盘上——取代件缺席，单向残缺"))
        elif (br, a) not in take_set:
            items.append(("D02", "红", f"{a}:{ln}",
                          f"声明被 {b} 取代但对侧没有「取代：{a}」行——supersede 单向残缺（双向纪律）"))

    # D03 终态必带指针：状态「已取代」却没有任何取代/被取代行
    for rel in files:
        if _doc_state(files, rel) == "已取代":
            has_ptr = any(a == rel for a, _, _, _ in pairs)
            if not has_ptr:
                items.append(("D03", "红", rel,
                              "状态写「已取代」但没有取代/被取代指针——历史版本失联，读者找不到现行版（§3 状态机表）"))

    # D04 环与自指：take 边图上 DFS 找环（A→B→A、自环、长环都红）
    graph_edges = {}
    for a, b, _ in takes:
        br = _resolve(a, b)
        if br:
            graph_edges.setdefault(a, set()).add(br)
    def _find_cycle():
        for start in graph_edges:
            stack, seen = [(start, [start])], set()
            while stack:
                node, path = stack.pop()
                for nxt in graph_edges.get(node, ()):
                    if nxt == start and len(path) >= 1:
                        return path + [nxt]
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append((nxt, path + [nxt]))
        return None
    cyc = _find_cycle()
    if cyc:
        items.append(("D04", "红", " → ".join(cyc),
                      "取代链成环——互相取代＝谁都不是现行版，语义死锁"))
    for a in graph_edges:
        if a in graph_edges[a]:
            items.append(("D04", "红", a, "文档取代它自己——自指"))

    # D16 双向齐但被取代侧状态非「已取代」（咬合矛盾：被取代了还现行）
    for a, b, _ in takes:
        br = _resolve(a, b)
        if br and br in files and (br, a) in by_set:
            st = _doc_state(files, br)
            if st is not None and st != "已取代":
                items.append(("D16", "红", br,
                              f"被 {a} 取代了状态却写「{st}」——双向咬合矛盾，两块同讲一个行为"))

    # D09 状态值收口（写了就查；四态同 spec_check C23 口径）
    for rel in files:
        for i, ln in enumerate(files[rel]["lines"], 1):
            if ln is None:
                continue
            m = DOC_STATE_LINE.match(ln)
            if m and m.group(1).strip() not in STATE_ALLOWED:
                items.append(("D09", "红", f"{rel}:{i}",
                              f"状态值「{m.group(1).strip()}」不在四态内（草案/生效/已取代/废弃）——值不收口＝机器认不出承诺面"))

    # D05/D12/D15 术语双定义（MUST-01 机检面）
    terms = collect_terms(files)
    by_term = {}
    for term, val, where in terms:
        by_term.setdefault(term, []).append((val, where))
    for term, decls in sorted(by_term.items()):
        empties = [w for v, w in decls if not v]
        if empties:
            items.append(("D15", "红", empties[0],
                          f"口径「{term}」声明了但值为空——口径残缺＝同词异解的门开着"))
            continue
        values = {v for v, _ in decls}
        if len(values) > 1:
            detail = "；".join(f"{w}＝「{v}」" for v, w in decls)
            items.append(("D05", "红", decls[0][1],
                          f"口径「{term}」{len(decls)} 处声明值不一致——同事实两值＝单一真相源破洞：{detail}"))
        elif len(decls) > 1:
            items.append(("D12", "警", decls[1][1],
                          f"口径「{term}」{len(decls)} 处重复声明且值一致——现在一致不代表永远一致，收敛到唯一权威处"))

    # D11 孤儿（警级点名，逐件）
    for rel in _orphan_list(g):
        items.append(("D11", "警", rel,
                      "孤儿文档：零入边且父目录零提及——没有路标能走到它（入口/生成物/子树已豁免）"))

    # waiver 审计（K14 审计不变式）
    for code, where, msg in audit_waivers(root):
        items.append((code, "红", where, msg))
    return items


def cmd_check(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    items = run_checks(root)
    reds = [i for i in items if i[1] == "红"]
    warns = [i for i in items if i[1] == "警"]
    if RUNSLOG_ON:
        _append_runs_log("doc_lint", "check", f"root={root}",
                         red=len(reds), warn=len(warns))
    print(f"[doc_lint v{VERSION}] root={root}")
    for code, level, where, msg in items:
        print(f"[{level}] {code} {CHECK_TITLES[code]}｜{where}｜{msg}")
    if not items:
        print(f"[全绿] {len(CHECKS)} 条断言零红零警")
    print(f"[汇总] 红 {len(reds)}｜警 {len(warns)}｜翻红断言 {len({i[0] for i in reds})}/{len(CHECKS)}")
    return 1 if reds else 0


def cmd_waiver(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    reds = audit_waivers(root)
    if RUNSLOG_ON:
        _append_runs_log("doc_lint", "waiver", f"root={root}", red=len(reds), warn=0)
    print(f"[doc_lint waiver v{VERSION}] root={root}")
    for code, where, msg in reds:
        print(f"[红] {code} {CHECK_TITLES[code]}｜{where}｜{msg}")
    if not reds:
        print("[全绿] waiver 审计零红（每个归档包：绿读数或 waiver 在案）")
    print(f"[汇总] 红 {len(reds)}")
    return 1 if reds else 0


def cmd_graph(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    g = build_doc_graph(root)
    if RUNSLOG_ON:
        _append_runs_log("doc_lint", "graph", f"root={root}")
    kinds = {}
    for e in g["edges"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"[doc_lint graph v{VERSION}] root={root}")
    print(f"[统计] 文档 {len(g['files'])} 件｜边 {len(g['edges'])} 条"
          f"（{'／'.join(f'{k} {v}' for k, v in kinds.items()) or '无边'}）｜目录提及 {len(g['dir_mentions'])} 处")
    orphans = _orphan_list(g) if len(g["files"]) else []
    for e in g["edges"]:
        dead = e.get("resolved") is None and e["kind"] != "目录提及"
        print(f"  {e['src']}:{e['line']} → {e['tgt']}（{e['kind']}）{'  ← 不在盘' if dead else ''}")
    if orphans:
        print(f"[孤儿] {len(orphans)} 件（零入边且父目录零提及，警级点名）：{'、'.join(orphans)}")
    else:
        print("[孤儿] 无")
    return 0


def _orphan_list(g):
    """孤儿判定（D11 的数据面）：入口/生成物/子树豁免＋入边或父目录传导豁免。"""
    root = g["root"]
    orphans = []
    for rel in g["files"]:
        low = Path(rel).name.lower()
        if low in ENTRY_NAMES or low in GENERATED_NAMES:
            continue
        parts = Path(rel).parts
        if parts[0] in {"specs", "changes", "templates", "evidence"}:
            continue
        has_in = any(e["src"] != rel and e.get("resolved") == (root / rel).resolve() for e in g["edges"])
        if has_in:
            continue
        ancestors = {"/".join(parts[:i]) + "/" for i in range(1, len(parts))}
        if ancestors & g["dir_mentions"]:
            continue
        orphans.append(rel)
    return orphans


def _append_runs_log(tool, action, where, red=None, warn=None, detail=""):
    try:
        log = Path(os.environ.get("EVSPEC_RUNS_LOG") or
                   (Path(__file__).resolve().parent.parent / "evidence" / "runs.log"))
        log.parent.mkdir(parents=True, exist_ok=True)
        parts = [datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), tool, action, where]
        if red is not None:
            parts.append(f"红 {red}｜警 {warn}")
        if detail:
            parts.append(detail)
        with log.open("a", encoding="utf-8") as f:
            f.write("｜".join(parts) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- selftest 夹具（合法文档树）

README_MD = """# 示例项目

入口文档。操作指南见 [指南](docs/guide.md)（同件提及 `docs/guide.md`）；
接口说明 `references/api.md`；钩子纪律 `templates/`。

- 口径：超时＝30 秒
"""

GUIDE_MD = """# 操作指南

历史版本：[旧版指南](docs/guide-old.md)；最新约定见 `docs/guide-new.md`。
"""

GUIDE_OLD_MD = """# 指南（旧版）

- 状态：已取代
- 被取代：docs/guide-new.md
"""

GUIDE_NEW_MD = """# 指南（新版）

- 取代：docs/guide-old.md
- 状态：生效
"""

API_MD = """# 接口说明

入口见 [README](../README.md)。
"""

WAIVER_OK_TMPL = """# waiver：豁免跳过规格尺留痕（变更包 fix-a）

- 变更包：fix-a
- 日期：2026-10-20
- 签发人：凛
- 原因：带已知的 C03 红提交赶演示窗口
- 影响范围：C03 一条红；演示路径不经该模块
- 有效至：{ttl}
- 销账：补绿后销账
"""


def write_doc_tree(root: Path, ttl: str):
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "references").mkdir(exist_ok=True)
    (root / "templates").mkdir(exist_ok=True)
    pkg = root / "changes" / "archive" / "2026-10-20-fix-a"
    pkg.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(README_MD, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "docs" / "guide-old.md").write_text(GUIDE_OLD_MD, encoding="utf-8")
    (root / "docs" / "guide-new.md").write_text(GUIDE_NEW_MD, encoding="utf-8")
    (root / "references" / "api.md").write_text(API_MD, encoding="utf-8")
    (pkg / "waiver.md").write_text(WAIVER_OK_TMPL.format(ttl=ttl), encoding="utf-8")
    return root


# ---------------------------------------------------------------- selftest（地基绿 → 17 条 bad-sample 全红 → 警哨兵 → 反恒真 → 真出口 → 接线 → 变异臂 → 活体绑定 → 分母闸）

def cmd_selftest(args):
    import contextlib
    import io
    home = Path(__file__).resolve().parent
    skill_root = home.parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else skill_root / "_tmp-doclint-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["EVSPEC_RUNS_LOG"] = str(tmp_root / "runs.log")   # 运行读数落临时树，evidence/ 与 scripts/ 零触碰
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    results = []
    seq = [0]
    ttl_ok = (date.today() + timedelta(days=30)).isoformat()

    def mk_tree():
        seq[0] += 1
        return write_doc_tree(tmp_root / f"p{seq[0]}", ttl_ok)

    def patch(path, old, new, count=1):
        t = read_text(path)
        assert old in t, f"夹具补丁锚丢失：{old[:40]}"
        path.write_text(t.replace(old, new, count), encoding="utf-8")

    def reds_of(items):
        return sorted({i[0] for i in items if i[1] == "红"})

    def warns_of(items):
        return sorted({i[0] for i in items if i[1] == "警"})

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    _src_self = read_text(Path(__file__).resolve())

    def run_mutant(anchor_prefix, replacement, tree):
        hits = [l for l in _src_self.splitlines() if l.startswith(anchor_prefix)]
        if not hits:
            return None
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "doc_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<doc_mutant>", "exec"), g)
        except Exception:
            return None
        return g["run_checks"](tree)

    # —— 地基：合法树必须全绿，且图非空（防空壳假绿：边数是「真的在看」的最低证据）
    good = mk_tree()
    items0 = run_checks(good)
    g0 = build_doc_graph(good)
    green("S00 地基绿：合法树红 0 警 0 且文档图非空（边 ≥6，防空壳假绿）",
          not items0 and len(g0["edges"]) >= 6,
          f"红警={[(i[0], i[1]) for i in items0]} 边数={len(g0['edges'])}")

    # —— 17 条 bad-sample 全红（K05 卡面验收判据）：每条做坏只准点名的那根红（涟漪红＝诚实涟漪不算败）
    t01 = mk_tree()
    patch(t01 / "README.md", "[指南](docs/guide.md)", "[指南](docs/ghost.md)")
    r01 = reds_of(run_checks(t01))
    green("S01 红样本1/17：D01 链接断链（链接指向的文件不在盘）", "D01" in r01, f"实际红={r01}")

    t02 = mk_tree()
    patch(t02 / "README.md", "钩子纪律 `templates/`。", "钩子纪律 [模板](templates/ghost/)。")
    r02 = reds_of(run_checks(t02))
    green("S02 红样本2/17：D01 链接断链（目录链接不在盘）", "D01" in r02, f"实际红={r02}")

    t03 = mk_tree()
    (t03 / "docs" / "guide-old.md").unlink()
    r03 = reds_of(run_checks(t03))
    green("S03 红样本3/17：D02 取代单向残缺（被取代件不在盘）", "D02" in r03, f"实际红={r03}")

    t04 = mk_tree()
    patch(t04 / "docs" / "guide-new.md", "- 取代：docs/guide-old.md\n", "")
    r04 = reds_of(run_checks(t04))
    green("S04 红样本4/17：D02 被取代单向残缺（对侧缺取代行）", "D02" in r04, f"实际红={r04}")

    t05 = mk_tree()
    patch(t05 / "docs" / "guide-old.md", "- 被取代：docs/guide-new.md\n", "")
    r05 = reds_of(run_checks(t05))
    green("S05 红样本5/17：D03 已取代状态缺指针", "D03" in r05, f"实际红={r05}")

    t06 = mk_tree()
    patch(t06 / "docs" / "guide-old.md", "- 被取代：docs/guide-new.md",
          "- 被取代：docs/guide-new.md\n- 取代：docs/guide-new.md")
    (t06 / "docs" / "selfref.md").write_text("# 自指\n\n- 取代：docs/selfref.md\n", encoding="utf-8")
    r06 = reds_of(run_checks(t06))
    green("S06 红样本6/17：D04 supersede 环与自指", "D04" in r06, f"实际红={r06}")

    t07 = mk_tree()
    patch(t07 / "README.md", "- 口径：超时＝30 秒", "- 口径：超时＝30 秒\n- 口径：超时＝60 秒")
    r07 = reds_of(run_checks(t07))
    green("S07 红样本7/17：D05 术语双定义值冲突（同文件两处）", "D05" in r07, f"实际红={r07}")

    t08 = mk_tree()
    patch(t08 / "README.md", "- 口径：超时＝30 秒",
          "- 口径：超时＝30 秒\n- 口径：超时＝60 秒\n- 口径：超时＝45 秒")
    r08 = reds_of(run_checks(t08))
    green("S08 红样本8/17：D05 三处两值冲突（值集 3）", "D05" in r08, f"实际红={r08}")

    t09 = mk_tree()
    patch(t09 / "README.md", "- 口径：超时＝30 秒", "- 口径：超时＝30 秒\n- 口径：缓存＝")
    r09 = reds_of(run_checks(t09))
    green("S09 红样本9/17：D15 术语声明残缺（值为空）", "D15" in r09, f"实际红={r09}")

    t10 = mk_tree()
    (t10 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md").unlink()
    r10 = reds_of(run_checks(t10))
    green("S10 红样本10/17：D06 归档包静默跳过（无绿读数无 waiver）", "D06" in r10, f"实际红={r10}")

    t11 = mk_tree()
    patch(t11 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          "- 签发人：凛\n", "")
    r11 = reds_of(run_checks(t11))
    green("S11 红样本11/17：D07 waiver 七字段缺一", "D07" in r11, f"实际红={r11}")

    t12 = mk_tree()
    patch(t12 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          f"- 有效至：{ttl_ok}", "- 有效至：2026-01-01")
    r12 = reds_of(run_checks(t12))
    green("S12 红样本12/17：D08 waiver 过期未销账", "D08" in r12, f"实际红={r12}")

    t13 = mk_tree()
    patch(t13 / "docs" / "guide-new.md", "- 状态：生效", "- 状态：上线")
    r13 = reds_of(run_checks(t13))
    green("S13 红样本13/17：D09 文档状态值非法（四态之外）", "D09" in r13, f"实际红={r13}")

    t14 = mk_tree()
    patch(t14 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          f"- 有效至：{ttl_ok}", "- 有效至：下月底")
    r14 = reds_of(run_checks(t14))
    green("S14 红样本14/17：D10 waiver 有效至形状非法", "D10" in r14, f"实际红={r14}")

    t15 = mk_tree()
    patch(t15 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          "- 变更包：fix-a", "- 变更包：fix-b")
    r15 = reds_of(run_checks(t15))
    green("S15 红样本15/17：D13 waiver slug 与所在包目录不符", "D13" in r15, f"实际红={r15}")

    t16 = mk_tree()
    patch(t16 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          f"- 有效至：{ttl_ok}", "- 有效至：2026-01-01")
    patch(t16 / "changes" / "archive" / "2026-10-20-fix-a" / "waiver.md",
          "- 销账：补绿后销账", "- 销账：补绿后销账（已销 2026-10-25 凛）")
    r16 = reds_of(run_checks(t16))
    green("S16 红样本16/17：D14 已销账却无绿读数（销空账）", "D14" in r16, f"实际红={r16}")

    t17 = mk_tree()
    patch(t17 / "docs" / "guide-old.md", "- 状态：已取代", "- 状态：生效")
    r17 = reds_of(run_checks(t17))
    green("S17 红样本17/17：D16 双向齐但被取代侧状态非「已取代」", "D16" in r17, f"实际红={r17}")

    # —— 警级哨兵（孤儿/重复声明/提及断链：可选增强警级不拦人，点名不红）
    t18 = mk_tree()
    (t18 / "docs" / "notes.md").write_text("# 谁也没提我\n", encoding="utf-8")
    it18 = run_checks(t18)
    g18 = build_doc_graph(t18)
    green("S18 D11 孤儿警恰一点名（notes.md 零入边零传导），不红",
          warns_of(it18) == ["D11"] and not reds_of(it18) and g18 and _orphan_list(g18) == ["docs/notes.md"],
          f"红={reds_of(it18)} 警={warns_of(it18)} 孤儿={_orphan_list(g18)}")

    t19 = mk_tree()
    (t19 / "templates" / "sub-note.md").write_text("# 父目录被提及即可\n", encoding="utf-8")
    (t19 / "references" / "catalog.md").write_text("# 活目录（生成物）\n", encoding="utf-8")
    g19 = build_doc_graph(t19)
    green("S19 豁免哨兵：父目录传导（templates/ 被提及）与生成物名（catalog.md）不判孤儿",
          _orphan_list(g19) == [],
          f"孤儿={_orphan_list(g19)}")

    t20 = mk_tree()
    patch(t20 / "README.md", "- 口径：超时＝30 秒", "- 口径：超时＝30 秒\n- 口径：超时＝30 秒")
    it20 = run_checks(t20)
    green("S20 D12 术语重复声明值一致 ⇒ 警不红（双写漂移隐患）",
          warns_of(it20) == ["D12"] and not reds_of(it20), f"红={reds_of(it20)} 警={warns_of(it20)}")

    t21 = mk_tree()
    patch(t21 / "README.md", "接口说明 `references/api.md`", "接口说明 `references/api.md`、`docs/ghost.md`")
    it21 = run_checks(t21)
    green("S21 D17 提及路径不在盘 ⇒ 警不红（提及形态非链接，严格红只归链接）",
          warns_of(it21) == ["D17"] and not reds_of(it21), f"红={reds_of(it21)} 警={warns_of(it21)}")

    # —— 反恒真：畸形输入必须判否（防「永远红/永远绿」的坏尺子蒙对）
    vis = _visible_lines("```\n[x](ghost.md)\n```\n> 沿革：见 [x](ghost.md)\n[真](real.md)\n")
    edge_rows = [ln for ln in vis if ln is not None]
    ok_f1 = len(vis) == 5 and vis[1] is None and vis[3] is None and vis[4] is not None
    ok_f2 = _is_external("https://example.com/a.md") and _is_external("E:\\x\\a.md") and not _is_external("docs/a.md")
    ok_f3 = _supersede_target("R-3") is None and _supersede_target("docs/a.md") == "docs/a.md"
    green("S22 反恒真：围栏内链接/引用块证词不成边，外链与盘符绝对路径不查，需求号指针归 spec_check",
          ok_f1 and ok_f2 and ok_f3 and len(edge_rows) == 1,
          f"可见行={len(edge_rows)} f1={ok_f1} f2={ok_f2} f3={ok_f3}")

    # —— graph 命令：边表＋孤儿＋统计（K15 验收入边出边核对面）
    buf_g = io.StringIO()
    with contextlib.redirect_stdout(buf_g):
        rc_g = cmd_graph(argparse.Namespace(root=str(good)))
    out_g = buf_g.getvalue()
    green("S23 graph 命令：打印边表（含 README→guide 边与统计行）且退 0",
          rc_g == 0 and "docs/guide.md" in out_g and "[统计]" in out_g and "README.md" in out_g,
          f"退码={rc_g} 头行={out_g.splitlines()[1][:60] if out_g.count(chr(10)) > 1 else '（无）'}")

    # —— 真出口：退出码 0/1/2 实测（不只看函数返回值）
    buf_w = io.StringIO()
    with contextlib.redirect_stdout(buf_w):
        rc_w = cmd_waiver(argparse.Namespace(root=str(good)))
    green("S24 真出口：waiver 合法树退 0", rc_w == 0, f"退码={rc_w} 输出={buf_w.getvalue().splitlines()[-1][:50]}")

    buf_w2 = io.StringIO()
    with contextlib.redirect_stdout(buf_w2):
        rc_w2 = cmd_waiver(argparse.Namespace(root=str(t10)))
    green("S25 真出口：waiver 红树（静默跳过）退 1", rc_w2 == 1, f"退码={rc_w2}")

    rc_2 = cmd_check(argparse.Namespace(root=str(tmp_root / "p-not-exist")))
    green("S26 真出口：根目录不存在退 2（没查不是没问题）", rc_2 == 2, f"实际={rc_2}")

    buf_c = io.StringIO()
    with contextlib.redirect_stdout(buf_c):
        rc_c = cmd_check(argparse.Namespace(root=str(t01)))
    green("S27 真出口：check 红树退 1", rc_c == 1, f"退码={rc_c}")

    # —— runs.log 接线（MUST-17 · 第六支）：跑一次 check 追加恰一行
    rlog = tmp_root / "runs.log"

    def _rl_lines():
        return rlog.read_text(encoding="utf-8").splitlines() if rlog.is_file() else []

    n28 = len(_rl_lines())
    t28 = mk_tree()
    buf_r = io.StringIO()
    with contextlib.redirect_stdout(buf_r):
        cmd_check(argparse.Namespace(root=str(t28)))
    lines28 = _rl_lines()
    green("S28 runs.log 接线：跑一次 check 追加恰一行，行带 doc_lint/root/红警读数（MUST-17）",
          len(lines28) == n28 + 1 and "doc_lint" in lines28[-1] and f"root={t28}" in lines28[-1]
          and "红 0" in lines28[-1],
          f"行数 {n28}→{len(lines28)} 尾行={lines28[-1][:70] if lines28 else '（无）'}")

    # —— 变异探针臂：实现打坏，点名现象必须翻面；原样对照臂＝对应 S 用例本身
    m29 = run_mutant("DOC_LINK = ", 'DOC_LINK = re.compile(r"(?!)")', t01)
    green("S29 变异臂：DOC_LINK 打哑 ⇒ S01 断链现象消失（检测面被钉住）",
          m29 is not None and "D01" not in reds_of(m29), f"变异后红={reds_of(m29) if m29 else '（探针失锚）'}")

    m30 = run_mutant("TERM_DECL = ", 'TERM_DECL = re.compile(r"(?!)")', t07)
    green("S30 变异臂：TERM_DECL 打哑 ⇒ S07 术语冲突现象消失",
          m30 is not None and "D05" not in reds_of(m30), f"变异后红={reds_of(m30) if m30 else '（探针失锚）'}")

    m31 = run_mutant("SUPERSEDE_TAKE = ", 'SUPERSEDE_TAKE = re.compile(r"(?!)")', t03)
    green("S31 变异臂：SUPERSEDE_TAKE 打哑 ⇒ S03 单向残缺现象消失",
          m31 is not None and "D02" not in reds_of(m31), f"变异后红={reds_of(m31) if m31 else '（探针失锚）'}")

    m32 = run_mutant("DOC_TTL_LINE = ", 'DOC_TTL_LINE = re.compile(r"(?!)")', t12)
    green("S32 变异臂：DOC_TTL_LINE 打哑 ⇒ S12 过期未销现象消失",
          m32 is not None and "D08" not in reds_of(m32), f"变异后红={reds_of(m32) if m32 else '（探针失锚）'}")

    m33 = mk_tree()
    n33 = len(_rl_lines())
    mr33 = run_mutant("RUNSLOG_ON = ", "RUNSLOG_ON = False", m33)
    green("S33 变异臂：RUNSLOG_ON 打哑 ⇒ 读数不再追加（接线被钉住）",
          mr33 is not None and len(_rl_lines()) == n33,
          f"行数 {n33}→{len(_rl_lines())}")

    # —— 活体绑定：本件真根零红（真根自咬，漂移＝selftest 红；边数下限防假绿）
    rep_live = run_checks(skill_root)
    live_edges = len(build_doc_graph(skill_root)["edges"])
    green("S34 活体绑定：本件真根零红且文档图非空（边 ≥10）",
          not reds_of(rep_live) and live_edges >= 10,
          f"红={reds_of(rep_live)} 警={warns_of(rep_live)[:6]} 边数={live_edges}")

    # —— 收尾纪律：scripts/ 零写入＋临时树清理＋分母闸
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S35 scripts/ 目录零写入（兄弟文件 mtime 全等）",
          scripts_before == scripts_after,
          f"变化={[k for k in scripts_after if scripts_before.get(k) != scripts_after[k]]}")

    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S36 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")

    green(f"S37 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 1 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 1} 条")

    fails = [r for r in results if not r[1]]
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {len(results)} 条：PASS={len(results) - len(fails)} FAIL={len(fails)}")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="doc_lint —— 文档体系尺（断链/孤儿/supersede 双向/术语双定义/waiver 审计）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "waiver", "graph"):
        s = sub.add_parser(name)
        s.add_argument("--root", required=True)
        s.set_defaults(fn={"check": cmd_check, "waiver": cmd_waiver, "graph": cmd_graph}[name])
    st = sub.add_parser("selftest")
    st.add_argument("--tmp-root", default=None)
    st.set_defaults(fn=cmd_selftest)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
