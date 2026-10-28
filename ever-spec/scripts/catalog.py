#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""catalog —— 活目录生成器（EverSpec v2 施工期 · K06：MUST-07 机读/人读同源＋反向索引＋生成物哈希对账）。

零依赖（Python 3 标准库）。目标＝本件自己的知识资产（SKILL.md＋references/＋scripts/＋
templates/＋examples/），一次扫描产出两份同源派生物：

  references/catalog.json   机读面：逐件字节数＋sha256 全值＋入边/出边＋源集哈希
  references/catalog.md     人读面：三行头卡＋读数表＋反向索引表＋出边表＋源集哈希

三个命令：
  gen   --root <根> [--apply]   扫描→生成两份派生物（默认干跑：只打印人读面，不写盘）
  check --root <根>             复扫复算，与盘上派生物逐字节比对——手改派生物「让它更好看」
                                或源已变目录陈旧，都当场点名红（MUST-07 生成物哈希对账）
  selftest [--tmp-root X]       红例自证（只写临时树）

设计口径（诚实边界，先说清楚尺子量什么）：
- 反向索引＝「提及式粗索引」：在资产文本里找相对路径/目录路径的提及（substring 匹配），
  不做链接语义解析；提及整个目录＝提及该目录下全部资产（文档常引用目录而非单件）。
  严格的入出边闭合与孤儿判定归 doc_lint（K05），本件只做导航视图，不做对错裁决。
- evidence/ 不入册：append-only 流水，入册即永久陈旧；__pycache__、_tmp-*、点文件、
  派生物自身（catalog.md/json）也不入册。
- gen 幂等：派生物零时间戳（时间只进 runs.log），同树连跑字节级一致——幂等是
  「手改必被发现」的前提，产物带噪声就藏得住手改。
- runs.log 接线（MUST-17 同款）：gen/check 跑成一次就向本件 evidence/runs.log 追加一行
  读数；退 2 的「没查」不记；写不进（只读盘等）静默跳过，绝不影响主流程退出码；
  环境变量 EVSPEC_RUNS_LOG 改落点（selftest 用它重定向进临时树）。

退出码：0 绿｜1 红（手改/陈旧/缺件/坏 json）｜2 用法/根不对（退 2 是「没查」，不是「没问题」）。
路径口径：--root 由调用方传入，本件自身零绝对路径。
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
ASSERT_FLOOR = 25   # selftest 断言分母闸：条数掉了就该红

SCAN_DIRS = ("references", "scripts", "templates", "examples")
ROOT_FILES = ("SKILL.md",)
ART_MD = "references/catalog.md"
ART_JSON = "references/catalog.json"
ARTIFACTS = (ART_MD, ART_JSON)
SKIP_PREFIX = ("_tmp-", ".")            # 目录/文件名命中即跳过（__pycache__ 单列）
TEXT_CAP = 2_000_000                    # 提及扫描的单件上限（超过只哈希不扫文本）
FP_LEN = 12                             # 人读面指纹位数（sha256 前 N 位）

RUNSLOG_ON = True   # 🔴 单占一行：变异探针臂（S18）按行首锚点整行替换，拆行=探针失锚。


# ---------------------------------------------------------------- 扫描与索引

def _skipped(name: str) -> bool:
    return name.startswith(SKIP_PREFIX) or name == "__pycache__" or name.endswith(".tmp")


def scan_assets(root: Path):
    """→ {相对路径(posix): sha256(hex)}。派生物自身不入册。"""
    out = {}
    for name in ROOT_FILES:
        p = root / name
        if p.is_file() and not _skipped(name):
            out[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(_skipped(part) for part in rel.split("/")) or rel in ARTIFACTS:
                continue
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build_edges(root: Path, files):
    """提及式反向索引 → (入边 {目标: set(来源)}, 出边 {来源: set(目标)})。

    提及键＝资产相对路径＋扫描面内全部目录路径；提及目录＝提及其下全部资产。
    自指（文件提自己）不成边——自夸不算引用。
    """
    if not files:
        return {}, {}
    dir_keys = set()
    for rel in files:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            dir_keys.add("/".join(parts[:i]))
    keys = [(k, True) for k in sorted(files)] + [(k, False) for k in sorted(dir_keys)]
    in_e, out_e = {}, {}
    for src in sorted(files):
        fp = root / src
        try:
            text = "" if fp.stat().st_size > TEXT_CAP else fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if not text:
            continue
        outs = set()
        for key, is_file in keys:
            if key not in text:
                continue
            targets = [key] if is_file else sorted(r for r in files if r.startswith(key + "/"))
            outs.update(t for t in targets if t != src)
        if outs:
            out_e[src] = outs
            for t in outs:
                in_e.setdefault(t, set()).add(src)
    return in_e, out_e


def build_statuses(root: Path, files):
    """状态列（K04 读取集消费方咬合口「目录生成器含状态列」）→ {相对路径: 状态或「—」}。

    取每件首个 `状态：` 行的值，原样进册不判对错（值合法性归 spec_check C23）；无状态行=「—」（K04：不写不红）。
    """
    st = {}
    for rel in sorted(files):
        fp = root / rel
        try:
            text = "" if fp.stat().st_size > TEXT_CAP else fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = re.search(r"^状态：\s*(\S[^\n]*)$", text, re.M)
        st[rel] = m.group(1).strip() if m else "—"
    return st


def source_set_hash(files) -> str:
    """源集哈希：对「路径\\t逐件sha256」排序拼接后取 sha256 全值——任一资产变一个字节即变。"""
    lines = "\n".join(f"{p}\t{files[p]}" for p in sorted(files))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _fp(h: str) -> str:
    return h[:FP_LEN]


# ---------------------------------------------------------------- 渲染（机读/人读同源）

def render_json(files, in_e, out_e, ssh, statuses) -> str:
    """机读面：与 render_md 同一次扫描同源；零时间戳（时间只进 runs.log）。"""
    data = {
        "tool": "catalog",
        "version": VERSION,
        "scan_dirs": list(SCAN_DIRS),
        "root_files": list(ROOT_FILES),
        "files_count": len(files),
        "edge_count": sum(len(v) for v in out_e.values()),
        "source_set_hash": ssh,
        "files": [{"path": p, "sha256": files[p], "status": statuses.get(p, "—"),
                   "in": sorted(in_e.get(p, ())),
                   "out": sorted(out_e.get(p, ()))}
                  for p in sorted(files)],
    }
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def render_md(files, in_e, out_e, ssh, statuses) -> str:
    """人读面：三行头卡＋读数表＋反向索引＋出边；排序全确定，同树连跑逐字节一致。"""
    lines = [
        "<!-- 活目录（生成物）：由 scripts/catalog.py 生成 · 禁手改——手改会被 check 当场判红（MUST-07 生成物哈希对账） -->",
        "",
        "# 活目录（生成物 · 机读/人读同源）",
        "",
        "> 何时读：接手挑「下一步读哪件」，或改完资产想确认目录没陈旧时；改了任何资产 check 会红，重跑 gen --apply 刷新。",
        "> 不读会怎样：靠手写地图找件——新件漏登记、旧件改了没人知（双真相源）。",
        "> 上层指针：SKILL.md 文件地图（人写路由）→ 本件（机读事实）；机读同源件＝`references/catalog.json`；细则 §6.5。",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 扫描面 | SKILL.md ＋ {'＋'.join(f'{d}/' for d in SCAN_DIRS)}（evidence/ 与派生物自身不入册） |",
        f"| 资产 | {len(files)} 件 |",
        f"| 提及边 | {sum(len(v) for v in out_e.values())} 条（提及式粗索引：相对路径/目录路径 substring；严格闭合归 doc_lint） |",
        f"| 源集哈希 | {_fp(ssh)} |",
        "",
        "## 反向索引（谁提及了它）",
        "",
        "| 资产 | 状态 | 指纹（sha256 前 12） | 被谁提及（入边） |",
        "|---|---|---|---|",
    ]
    for p in sorted(files):
        ins = "、".join(sorted(in_e.get(p, ()))) or "—"
        lines.append(f"| {p} | {statuses.get(p, '—')} | {_fp(files[p])} | {ins} |")
    lines += ["", "## 出边（它提及了谁）", "", "| 资产 | 提及了 |", "|---|---|"]
    for src in sorted(out_e):
        lines.append(f"| {src} | {'、'.join(sorted(out_e[src]))} |")
    lines += [
        "",
        f"> 源集哈希全值（外部锚用）：{ssh}",
        "> 零时间戳＝gen 幂等；目录陈旧（源变）或被手改都会被 `check` 点名红。状态列原样转抄资产「状态：」行，不判对错（值合法性归 spec_check C23）。",
        "",
    ]
    return "\n".join(lines)


def _write_exact(fp: Path, text: str):
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(fp)


# ---------------------------------------------------------------- runs.log 接线

def _append_runs_log(action, where, red=None, warn=None, detail=""):
    try:
        log = Path(os.environ.get("EVSPEC_RUNS_LOG") or
                   (Path(__file__).resolve().parent.parent / "evidence" / "runs.log"))
        log.parent.mkdir(parents=True, exist_ok=True)
        parts = [datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "catalog", action, where]
        if red is not None:
            parts.append(f"红 {red}｜警 {warn}")
        if detail:
            parts.append(detail)
        with log.open("a", encoding="utf-8") as f:
            f.write("｜".join(parts) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 命令

def _gate_root(raw) -> Path:
    root = Path(raw).resolve()
    if not root.is_dir():
        print(f"[没查] 根目录不对：{root} 不存在或不是目录 ⇒ 退 2")
        return None
    return root


def cmd_gen(args):
    root = _gate_root(args.root)
    if root is None:
        return 2
    files = scan_assets(root)
    in_e, out_e = build_edges(root, files)
    statuses = build_statuses(root, files)
    ssh = source_set_hash(files)
    md = render_md(files, in_e, out_e, ssh, statuses)
    js = render_json(files, in_e, out_e, ssh, statuses)
    edges = sum(len(v) for v in out_e.values())
    print(f"[catalog v{VERSION}] root={root}")
    print(f"[扫描] 资产 {len(files)} 件｜提及边 {edges} 条｜源集哈希 {_fp(ssh)}")
    if not args.apply:
        print("[干跑] 未写盘。下面是将写入人读面的内容：\n")
        print(md)
        if RUNSLOG_ON:
            _append_runs_log("gen", f"root={root}", red=0, warn=0, detail="干跑")
        return 0
    _write_exact(root / ART_MD, md)
    _write_exact(root / ART_JSON, js)
    back_md = (root / ART_MD).read_text(encoding="utf-8")
    back_js = (root / ART_JSON).read_text(encoding="utf-8")
    ok = back_md == md and back_js == js
    print(f"[已写] {ART_MD}（{len(back_md.splitlines())} 行）＋ {ART_JSON}")
    print(f"[读回] 逐字节一致={'是' if ok else '否'}")
    if not ok:
        print("[失败] 写后读回不符 ⇒ 退 1")
        if RUNSLOG_ON:
            _append_runs_log("gen", f"root={root}", red=1, warn=0, detail="--apply")
        return 1
    if RUNSLOG_ON:
        _append_runs_log("gen", f"root={root}", red=0, warn=0, detail="--apply")
    return 0


def _differs(a, b):
    return a != b


def cmd_check(args):
    root = _gate_root(args.root)
    if root is None:
        return 2
    print(f"[catalog v{VERSION}] root={root}")
    md_p, js_p = root / ART_MD, root / ART_JSON
    files = scan_assets(root)
    reds = []
    old_js = None
    if not md_p.is_file() or not js_p.is_file():
        missing = [a for a, p in ((ART_MD, md_p), (ART_JSON, js_p)) if not p.is_file()]
        reds.append(f"[红] 派生物缺件：{'、'.join(missing)} 不在盘——目录未生成，先跑 gen --apply")
    else:
        try:
            old_js = json.loads(js_p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            reds.append(f"[红] {ART_JSON} 不是合法 JSON（{type(e).__name__}）——被改坏或写坏")
        if old_js is not None:
            in_e, out_e = build_edges(root, files)
            statuses = build_statuses(root, files)
            ssh = source_set_hash(files)
            new_md = render_md(files, in_e, out_e, ssh, statuses)
            new_js = render_json(files, in_e, out_e, ssh, statuses)
            stale = old_js.get("source_set_hash") != ssh
            hint = "源已变、目录陈旧（重跑 gen --apply 刷新）" if stale else "源未变，属手改派生物"
            old_md = md_p.read_text(encoding="utf-8")
            old_js_text = js_p.read_text(encoding="utf-8")
            if _differs(old_md, new_md):
                reds.append(f"[红] {ART_MD} 与复算不一致——{hint}（手改生成物「让它更好看」=双真相源，MUST-07）")
            if _differs(old_js_text, new_js):
                reds.append(f"[红] {ART_JSON} 与复算不一致——{hint}（手改生成物「让它更好看」=双真相源，MUST-07）")
    if reds:
        for r in reds:
            print(r)
        print(f"[汇总] 红 {len(reds)}")
        if RUNSLOG_ON:
            _append_runs_log("check", f"root={root}", red=len(reds), warn=0)
        return 1
    print("[全绿] 派生物与现扫逐字节一致（无手改、无陈旧）")
    print(f"[汇总] 红 0｜资产 {len(files)} 件")
    if RUNSLOG_ON:
        _append_runs_log("check", f"root={root}", red=0, warn=0)
    return 0


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-catalog-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["EVSPEC_RUNS_LOG"] = str(tmp_root / "runs.log")   # 读数重定向进临时树，evidence/ 与 scripts/ 零触碰
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}

    results = []

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    def run(*cli):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(cli))
        return rc, buf.getvalue()

    _src_self = Path(__file__).resolve().read_text(encoding="utf-8")

    def run_mutant(anchor, replacement, cli):
        """变异探针：打坏实现本体后重载，点名现象必须翻面（原样对照臂=对应 S 用例）。"""
        hits = [l for l in _src_self.splitlines() if l.strip().startswith(anchor)]
        if not hits:
            return None, ""
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "catalog_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<catalog_mutant>", "exec"), g)
        except Exception:
            return None, ""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = g["main"](list(cli))
        return rc, buf.getvalue()

    # —— 合成树：6 资产，提及关系刻意设计（文件提及＋目录提及＋自指）
    def mk_proj(name):
        proj = tmp_root / name
        (proj / "references").mkdir(parents=True, exist_ok=True)
        (proj / "scripts").mkdir(parents=True, exist_ok=True)
        (proj / "templates").mkdir(parents=True, exist_ok=True)
        (proj / "examples" / "demo").mkdir(parents=True, exist_ok=True)
        (proj / "SKILL.md").write_text(
            "路由：读 references/细则.md 与 scripts/tool.py；模板在 templates/；样例在 examples/。\n",
            encoding="utf-8")
        (proj / "references" / "细则.md").write_text(
            "状态：生效\n命令：py scripts/tool.py gen。模板见 templates/t.md。样例见 examples/。\n",
            encoding="utf-8")
        (proj / "references" / "其他.md").write_text("这篇不提及任何路径。\n", encoding="utf-8")
        (proj / "scripts" / "tool.py").write_text(
            "# tool 自述：本文提 scripts/tool.py 属自指，不得成边。\n", encoding="utf-8")
        (proj / "templates" / "t.md").write_text("模板正文。\n", encoding="utf-8")
        (proj / "examples" / "demo" / "sample.md").write_text("样例正文。\n", encoding="utf-8")
        return proj

    proj = mk_proj("proj")
    rlog = tmp_root / "runs.log"

    def rlines():
        return rlog.read_text(encoding="utf-8").splitlines() if rlog.exists() else []

    def fp_cells(md_text):
        """人读面反向索引表的指纹列（第 3 格：| 资产 | 状态 | 指纹 | 入边 |）。"""
        cells = []
        for line in md_text.splitlines():
            if line.startswith(("| references/", "| scripts/", "| templates/", "| examples/", "| SKILL.md ")):
                cs = [c.strip() for c in line.strip("|").split("|")]
                if len(cs) >= 4:
                    cells.append(cs[2])
        return cells

    # —— S01 地基绿：gen --apply 退 0，check 退 0
    rc1, out1 = run("gen", "--root", str(proj), "--apply")
    rc1b, out1b = run("check", "--root", str(proj))
    green("S01 合成树 gen --apply 退 0 且 check 退 0（地基绿）",
          rc1 == 0 and rc1b == 0 and "[全绿]" in out1b and "资产 6 件" in out1b,
          f"gen退码={rc1} check退码={rc1b}")

    md_text = (proj / ART_MD).read_text(encoding="utf-8")
    js_data = json.loads((proj / ART_JSON).read_text(encoding="utf-8"))

    # —— S02 gen 幂等：连跑两次产物逐字节一致（幂等是「手改必被发现」的前提）
    md_a, js_a = (proj / ART_MD).read_bytes(), (proj / ART_JSON).read_bytes()
    run("gen", "--root", str(proj), "--apply")
    same = (proj / ART_MD).read_bytes() == md_a and (proj / ART_JSON).read_bytes() == js_a
    green("S02 gen 幂等：同树连跑，md/json 派生物逐字节一致", same, f"一致={same}")

    # —— S03 机读面：json 合法且字段齐
    green("S03 机读面：catalog.json 过 json.load，含 files/source_set_hash/逐件 sha256＋入边",
          isinstance(js_data.get("files"), list) and isinstance(js_data.get("source_set_hash"), str)
          and bool(js_data["files"]) and all("in" in f and "out" in f and "sha256" in f for f in js_data["files"]),
          f"件数={len(js_data.get('files', []))}")

    # —— S04 人读面：三行头卡＋禁手改声明＋反向索引表头，细则行带来源
    green("S04 人读面：md 含「何时读」头卡＋「禁手改」声明＋反向索引表头，且细则行带 SKILL.md",
          "何时读" in md_text and "禁手改" in md_text and "反向索引" in md_text
          and "references/细则.md" in md_text,
          f"md {len(md_text.splitlines())} 行")

    # —— S05 同源三处一致＋指纹 12 位：md 源集哈希 == json 源集哈希 == 复算
    files_now = {f["path"]: f["sha256"] for f in js_data.get("files", []) if "path" in f and "sha256" in f}
    ssh_recalc = source_set_hash(files_now)
    cells = fp_cells(md_text)
    fp_ok = bool(cells) and all(re.fullmatch(r"[0-9a-f]{12}", c) for c in cells)
    green("S05 机读/人读同源：md、json、复算三处源集哈希一致，且指纹列全 12 位十六进制",
          js_data.get("source_set_hash") == ssh_recalc and _fp(ssh_recalc) in md_text and fp_ok,
          f"复算={_fp(ssh_recalc)}｜指纹格={len(cells)}")

    # —— S06 反向索引入边：SKILL.md 提及细则 ⇒ 细则入边含 SKILL.md
    in_map = {f.get("path"): f.get("in", []) for f in js_data.get("files", [])}
    green("S06 反向索引：references/细则.md 的入边含 SKILL.md",
          "SKILL.md" in in_map.get("references/细则.md", []),
          f"入边={in_map.get('references/细则.md', [])}")

    # —— S06b 状态列（K04 读取集消费方咬合口「目录生成器含状态列」）：带状态行的资产状态进册，无状态行=「—」
    jf = {f.get("path"): f for f in js_data.get("files", [])}
    green("S06b 状态列：细则（状态：生效）在册状态=生效（json 字段＋md 行）；tool.py 无状态行=「—」",
          jf.get("references/细则.md", {}).get("status") == "生效"
          and jf.get("scripts/tool.py", {}).get("status") == "—"
          and any(l.startswith("| references/细则.md | 生效 |") for l in md_text.splitlines()),
          f"细则status={jf.get('references/细则.md', {}).get('status')}")

    # —— S07 出边：细则提及脚本/模板/样例 ⇒ 出边齐
    out_map = {f.get("path"): f.get("out", []) for f in js_data.get("files", [])}
    outs = out_map.get("references/细则.md", [])
    green("S07 出边：细则出边含 scripts/tool.py、templates/t.md、examples/demo/sample.md",
          all(x in outs for x in ("scripts/tool.py", "templates/t.md", "examples/demo/sample.md")),
          f"出边={outs}")

    # —— S08 自指不成边＋目录提及成边：tool.py 入边无自身；t.md 入边含 SKILL.md（目录提及）
    green("S08 自指不成边＋目录提及成边：tool.py 入边无自身；t.md 入边含 SKILL.md",
          "scripts/tool.py" not in in_map.get("scripts/tool.py", [])
          and "SKILL.md" in in_map.get("templates/t.md", []),
          f"tool.py入边={in_map.get('scripts/tool.py', [])}")

    # —— S09 手改派生物（md）被红：追加一行「让它更好看」→ check 退 1 且点名
    (proj / ART_MD).write_text(md_text + "| references/假件.md | 1 | abc | 手加 |\n", encoding="utf-8")
    rc9, out9 = run("check", "--root", str(proj))
    green("S09 手改 catalog.md ⇒ 退 1 且点名该件",
          rc9 == 1 and ART_MD in out9 and "[红]" in out9, f"退码={rc9}")

    # —— S10 手改派生物（json）被红
    run("gen", "--root", str(proj), "--apply")   # 先恢复绿
    js_raw = (proj / ART_JSON).read_text(encoding="utf-8")
    (proj / ART_JSON).write_text(js_raw.replace("细则", "细则改"), encoding="utf-8")
    rc10, out10 = run("check", "--root", str(proj))
    green("S10 手改 catalog.json ⇒ 退 1 且点名该件",
          rc10 == 1 and ART_JSON in out10, f"退码={rc10}")

    # —— S11 源变陈旧被红（不是手改也能抓：活目录的「活」）
    run("gen", "--root", str(proj), "--apply")
    (proj / "references" / "其他.md").write_text("这篇被改了一个字节。\n", encoding="utf-8")
    rc11, out11 = run("check", "--root", str(proj))
    green("S11 源已变 ⇒ 退 1 且提示陈旧（重跑 gen 刷新）",
          rc11 == 1 and "陈旧" in out11, f"退码={rc11}")

    # —— S12 派生物缺件被红
    run("gen", "--root", str(proj), "--apply")
    (proj / ART_MD).unlink()
    rc12, out12 = run("check", "--root", str(proj))
    green("S12 catalog.md 缺件 ⇒ 退 1 且点名缺件（先 gen --apply）",
          rc12 == 1 and "缺件" in out12 and ART_MD in out12, f"退码={rc12}")

    # —— S13 畸形 json 被红不崩（按红走，无栈现场）
    run("gen", "--root", str(proj), "--apply")
    (proj / ART_JSON).write_text("{这不是json", encoding="utf-8")
    rc13, out13 = run("check", "--root", str(proj))
    green("S13 catalog.json 畸形 ⇒ 退 1 且按红输出（不炸栈）",
          rc13 == 1 and "[红]" in out13 and "Traceback" not in out13, f"退码={rc13}")

    # —— S14 root 不在 ⇒ 退 2（没查≠没问题，且不记读数）
    n14 = len(rlines())
    rc14, _ = run("check", "--root", str(tmp_root / "无此根"))
    green("S14 根目录不存在 ⇒ 退 2 且不记读数",
          rc14 == 2 and len(rlines()) == n14, f"退码={rc14}")

    # —— S15 干跑零写入
    proj2 = mk_proj("proj2")
    rc15, _ = run("gen", "--root", str(proj2))
    green("S15 干跑（无 --apply）零写入：两份派生物都不在盘",
          rc15 == 0 and not (proj2 / ART_MD).exists() and not (proj2 / ART_JSON).exists(),
          f"退码={rc15}")

    # —— S16 runs.log 接线：gen 干跑恰 +1 行且带「干跑」
    n16 = len(rlines())
    rc16, _ = run("gen", "--root", str(proj2))
    d16 = rlines()[n16:]
    green("S16 runs.log 接线：gen 干跑 +1 行，行带 catalog 与「干跑」",
          rc16 == 0 and len(d16) == 1 and "catalog" in d16[0] and "干跑" in d16[0],
          f"行={d16[:1]}")

    # —— S17 --apply 行带 --apply 标记；check 绿行带「红 0」
    n17 = len(rlines())
    rc17, _ = run("gen", "--root", str(proj2), "--apply")
    rc17b, _ = run("check", "--root", str(proj2))
    d17 = rlines()[n17:]
    green("S17 gen --apply 读数带 --apply 标记；check 绿行带「红 0」",
          rc17 == 0 and rc17b == 0 and len(d17) == 2 and "--apply" in d17[0] and "红 0" in d17[1],
          f"行数={len(d17)}")

    # —— S18 变异臂①：RUNSLOG_ON 打哑 ⇒ 读数不再追加（S16 现象消失，接线被钉住）
    n18 = len(rlines())
    rc18, _ = run_mutant("RUNSLOG_ON = True", "RUNSLOG_ON = False", ["gen", "--root", str(proj2)])
    green("S18 变异臂：RUNSLOG_ON 打哑 ⇒ +0 行（S16 现象消失，接线被钉住）",
          rc18 == 0 and len(rlines()) == n18, f"变异后退码={rc18}")

    # —— S19 变异臂②：逐字节比对打哑 ⇒ 手改派生物假绿（S09 现象翻面，比对被钉住）
    run("gen", "--root", str(proj2), "--apply")
    good_md = (proj2 / ART_MD).read_text(encoding="utf-8")
    (proj2 / ART_MD).write_text(good_md + "| references/假件.md | 1 | abc | 手加 |\n", encoding="utf-8")
    rc19, out19 = run_mutant("return a != b", "    return False", ["check", "--root", str(proj2)])
    green("S19 变异臂：比对打哑 ⇒ 手改 md 退 0 假绿（S09 现象翻面=比对实现被钉住）",
          rc19 == 0 and "[红]" not in out19, f"变异后退码={rc19}")

    # —— S20 变异臂③：指纹截断 [:12]→[:8] ⇒ 产物指纹列变 8 位（指纹实现被钉住）
    rc20, _ = run_mutant("return h[:FP_LEN]", "    return h[:8]",
                         ["gen", "--root", str(proj2), "--apply"])
    fp20 = fp_cells((proj2 / ART_MD).read_text(encoding="utf-8"))
    green("S20 变异臂：指纹截断改 8 位 ⇒ 人读面指纹列全变 8 位无 12 位（指纹实现被钉住）",
          rc20 == 0 and bool(fp20) and all(re.fullmatch(r"[0-9a-f]{8}", c) for c in fp20)
          and not any(re.fullmatch(r"[0-9a-f]{12}", c) for c in fp20),
          f"变异后指纹格={fp20[:2]}")

    # —— S21 资产读数：册内 6 件、派生物不计入（自我排除）
    green("S21 册内资产恰 6 件且派生物自身不入册",
          len(js_data.get("files", [])) == 6 and not any("catalog" in f["path"] for f in js_data.get("files", [])),
          f"件数={len(js_data.get('files', []))}")

    # —— S22 分母闸 / S23 scripts 零写入 / S24 收尾清理
    green(f"S22 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 3 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 3} 条")
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S23 scripts/ 目录零写入（兄弟文件 mtime 全等）", scripts_before == scripts_after,
          f"变化={sorted(set(scripts_before) ^ set(scripts_after))}")
    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S24 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")

    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[catalog selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="EverSpec 活目录生成器（机读/人读同源＋反向索引＋哈希对账，零依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="扫描资产生成 catalog.md＋catalog.json（默认干跑）")
    g.add_argument("--root", required=True)
    g.add_argument("--apply", action="store_true", help="真写盘（默认干跑）")
    g.set_defaults(func=cmd_gen)
    c = sub.add_parser("check", help="复扫复算与盘上派生物逐字节比对（手改/陈旧=红）")
    c.add_argument("--root", required=True)
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest", help="红例自证（只写临时树）")
    s.add_argument("--tmp-root", default=None)
    s.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
