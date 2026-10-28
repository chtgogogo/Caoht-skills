#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reanchor —— 开窗重锚＋断点状态摘要（EverSpec v2 施工期 · K08：MUST-11 上下文还原＋MUST-10 断点可续＋过滤读法）。

零依赖（Python 3 标准库）。四个命令，全部**只读**（目标项目零写入，读数落本件 evidence/runs.log）：

  open  --root <项目根> [--readset open|promise|history] [--chain <链文件>] [--expect-tip <哈希>] [--json]
        开窗第一件事：①本步必读清单（按读取集过滤）②断点状态摘要（主规格四态计数／变更包
        tasks 勾选与增量块／矩阵／尺子最近读数）③证据链尾外部锚。JSON 面供脚本消费。
  list  --root <项目根> [--readset …] [--json]     扁平清单一行一条（过滤读法：省上下文，对标 list --specs）
  show  --root <项目根> <R-x> [--no-scenarios] [--json]   单条需求块原文（对标 show --json --no-scenarios）
  selftest [--tmp-root X]                          红例自证（只写临时树）

读取集（MUST-06 状态机的消费方，细则 §3 表；判据真源仍是 spec_check 的 STATE_ALLOWED）：
  open    开工/重锚档＝草案＋生效＋未标＋未知（接手要把在途的东西都看见）
  promise 对外承诺档＝生效＋未标（草案不进承诺面）
  history 溯因/读历史档＝已取代＋废弃
  「未标」＝块内没有 `状态：` 行（存量项目零冲击，按默认承诺读）；
  「未知」＝写了四态之外的值（C23 判红面），重锚不静默丢块——进 open 档并当场标注。

口径与诚实边界（先说清楚这把尺查不到什么）：
- 块解析、状态值收口、增量切段、tasks 勾选全部**复用同目录 spec_check.py**（同一份判据真源，
  不另抄正则）——两处口径分叉就等于两个真相源；spec_check 缺件时本件退 2（没查≠没问题）。
- 证据链只读**链尾哈希**（外部锚）＋行数；链是否自洽仍归 evidence_chain.py check（本件不重复判）。
- 矩阵只读摘要（需求条数＋文件指纹），时效/死指针仍归 trace_matrix.py check 与 C17。
- 本输出**不替代尺子**：输出末尾固定提示出门前跑 spec_check.py check。
- runs.log 接线（MUST-17 同款）：open/list/show 跑成一次追加一行；退 2（根/用法不对）不记；
  写不进（只读盘等）静默跳过，绝不影响退出码；环境变量 EVSPEC_RUNS_LOG 改落点。

退出码：0 绿（可含警）｜1 红（--expect-tip 不符／show 查无此号）｜2 用法或根目录不对。
路径口径：--root 由调用方传入，本件自身零绝对路径。
"""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
ASSERT_FLOOR = 26   # selftest 断言分母闸：条数掉了就该红（v1.21.0 随 K22 复述三问 S26 同升）

# 读取集三档（MUST-06 状态机消费方；bucket 词表与 spec_check.STATE_ALLOWED 对齐＋两个本件自带桶）
READSETS = {
    "open": ("草案", "生效", "未标", "未知"),
    "promise": ("生效", "未标"),
    "history": ("已取代", "废弃"),
}
READSET_LABEL = {
    "open": "开工/重锚档：草案＋生效＋未标＋未知",
    "promise": "对外承诺档：生效＋未标（草案不进承诺面）",
    "history": "溯因/读历史档：已取代＋废弃",
}
BUCKETS = ("草案", "生效", "未标", "未知", "已取代", "废弃")
ACCEPT_LINE = re.compile(r"^\s*[-*]?\s*验收\s*[：:]\s*(.+?)\s*$")
DEFAULT_CHAINS = ("evidence.ndjson", "evidence/evidence.ndjson")
FP_LEN = 12


# ---------------------------------------------------------------- 兄弟件加载（判据真源复用）

def load_sibling(name):
    """同目录兄弟脚本（spec_check.py／evidence_chain.py）。缺件/坏件返回 None，调用方降级或退 2。"""
    p = Path(__file__).with_name(name)
    if not p.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"_evspec_{p.stem}", p)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ---------------------------------------------------------------- 需求块收集

def _bucket_of(block, spec):
    """块的状态桶 → (bucket, 原值)。无状态行=未标；四态之外=未知（不静默丢）。"""
    raw = ""
    for ln in block["lines"]:
        m = spec.STATE_LINE.match(ln)
        if m:
            raw = m.group(1).strip()
            break
    if not raw:
        return "未标", ""
    if raw in spec.STATE_ALLOWED:
        return raw, raw
    return "未知", raw


def _acceptance_of(block):
    for ln in block["lines"]:
        m = ACCEPT_LINE.match(ln)
        if m:
            return m.group(1)
    return ""


def collect_blocks(root):
    """→ (spec 模块, 块列表)。块字段：id/title/status/bucket/ability/file/line/header/raw/acceptance/scenarios。"""
    spec = load_sibling("spec_check.py")
    if spec is None:
        return None, None
    ctx = spec.build_ctx(root)
    out = []
    for f in ctx.spec_files:
        text = spec.read_text(f)
        lines = text.splitlines()
        try:
            inside = f.relative_to(ctx.specs_dir).parts
            ability = inside[0] if len(inside) > 1 else "(根)"
        except ValueError:
            ability = "(根)"
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            rel = f.as_posix()
        for b in spec.parse_req_blocks(f, text):
            bucket, raw_status = _bucket_of(b, spec)
            out.append({
                "id": b["id"], "title": b["title"], "bucket": bucket, "status": raw_status,
                "ability": ability, "file": rel, "line": b["start"],
                "header": lines[b["start"] - 1] if 0 < b["start"] <= len(lines) else f"#### {b['id']}",
                "raw": list(b["lines"]), "acceptance": _acceptance_of(b),
                "scenarios": len(b["scenarios"]),
            })
    out.sort(key=lambda x: (x["ability"], x["file"], x["line"]))
    return spec, out


# ---------------------------------------------------------------- 摘要（MUST-10 断点可续）

def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def change_summary(root, spec):
    """变更包摘要：未归档包逐个 tasks 勾选＋三件套在场＋增量块三动词计数（archive 排除）。"""
    ctx = spec.build_ctx(root)
    out = []
    for d in ctx.change_dirs:
        tasks = spec.task_items(spec.read_text(d / "tasks.md"))
        delta = {"ADDED": 0, "MODIFIED": 0, "REMOVED": 0}
        sub = d / "specs"
        if sub.is_dir():
            for f in sorted(sub.rglob("*.md")):
                sec = spec.delta_sections(spec.read_text(f))
                for kind in delta:
                    delta[kind] += len(spec.parse_req_blocks(f, sec[kind][0]))
        out.append({
            "name": d.name,
            "tasks_done": sum(1 for it in tasks if it[0]), "tasks_total": len(tasks),
            "proposal": (d / "proposal.md").is_file(), "design": (d / "design.md").is_file(),
            "delta": delta,
        })
    return out


def matrix_summary(root):
    """矩阵摘要：在盘与否＋需求条数＋文件指纹（md5 前 12）。时效/死指针归 trace_matrix check。"""
    p = root / "trace" / "matrix.json"
    if not p.is_file():
        return {"exists": False, "requirements": None, "file_md5": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        req = (data.get("summary") or {}).get("requirements")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        req = None
    return {"exists": True, "requirements": req, "file_md5": _md5(p)[:FP_LEN]}


def default_chain(root):
    for cand in DEFAULT_CHAINS:
        p = root / cand
        if p.is_file():
            return p
    return root / DEFAULT_CHAINS[0]


def chain_summary(path):
    """链摘要：只取行数与链尾哈希（外部锚）；链自洽仍归 evidence_chain.py check。"""
    ec = load_sibling("evidence_chain.py")
    if ec is None or not path.is_file():
        return {"file": str(path), "exists": path.is_file(), "lines": 0, "tip": None, "problems": 0}
    entries = ec.load_lines(path)
    clean = [d for _, d in entries if isinstance(d, dict) and "hash" in d]
    tip = clean[-1]["hash"] if clean else None
    return {"file": str(path), "exists": True, "lines": len(entries), "tip": tip,
            "problems": len(ec.chain_problems(entries)) if ec else 0}


def runs_log_last():
    p = Path(os.environ.get("EVSPEC_RUNS_LOG") or
             (Path(__file__).resolve().parent.parent / "evidence" / "runs.log"))
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def build_summary(root, spec, blocks):
    buckets = {b: 0 for b in BUCKETS}
    for x in blocks:
        buckets[x["bucket"]] += 1
    return {
        "abilities": len({x["ability"] for x in blocks}),
        "blocks": len(blocks),
        "buckets": buckets,
        "changes": change_summary(root, spec),
        "matrix": matrix_summary(root),
        "runs_log_last": runs_log_last(),
    }


# ---------------------------------------------------------------- 读取集过滤

def readset_allows(bucket: str, name: str) -> bool:
    """读取集判据：bucket 属不属于 name 档（口径＝READSETS 表，状态值合法性与 spec_check 同源）。"""
    return name in READSETS and bucket in READSETS[name]


# ---------------------------------------------------------------- runs.log 接线

RUNSLOG_ON = True   # 🔴 单占一行：变异探针臂（S19）按行首锚点整行替换，拆行=探针失锚。


def _append_runs_log(action, where, red=None, warn=None, detail=""):
    if not RUNSLOG_ON:
        return
    try:
        log = Path(os.environ.get("EVSPEC_RUNS_LOG") or
                   (Path(__file__).resolve().parent.parent / "evidence" / "runs.log"))
        log.parent.mkdir(parents=True, exist_ok=True)
        parts = [datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "reanchor", action, where]
        if red is not None:
            parts.append(f"红 {red}｜警 {warn}")
        if detail:
            parts.append(detail)
        with log.open("a", encoding="utf-8") as f:
            f.write("｜".join(parts) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 单块取文（--no-scenarios）

_SPEC_CACHE = None


def _spec_mod():
    global _SPEC_CACHE
    if _SPEC_CACHE is None:
        _SPEC_CACHE = load_sibling("spec_check.py")
    return _SPEC_CACHE


def _strip_scenarios(body):
    """剥场景段：场景标头起、到块尾或下个场景标头止（场景段内不当块级读，同 spec_check 口径）。"""
    spec = _spec_mod()
    out, skipping = [], False
    for ln in body:
        if spec is not None and spec.SCEN_HEAD.match(ln):
            skipping = True
            continue
        if skipping:
            continue
        out.append(ln)
    return out


def block_body(b, no_scen):
    """块体行；--no-scenarios 时剥掉场景段（省上下文，对标 show --no-scenarios）。"""
    return _strip_scenarios(b["raw"]) if no_scen else list(b["raw"])


# ---------------------------------------------------------------- 渲染

def _status_cell(b):
    if b["bucket"] == "未知":
        return f"未知（状态：{b['status']}）"
    return b["bucket"]


def _fmt_bucket_line(buckets):
    return "｜".join(f"{k} {buckets[k]}" for k in BUCKETS)


def render_open(root, readset, must, summary, chain, runs_last, reds):
    L = [f"[reanchor v{VERSION}] root={root}｜读取集={readset}（{READSET_LABEL[readset]}）｜必读 {len(must)} 条", ""]
    L.append("== 一、本步必读清单（按读取集过滤；同能力按文件行序）==")
    if not must:
        L.append("  （读取集为空：该档没有需求块——先确认 specs/ 是否已立约）")
    cur = None
    for b in must:
        if b["ability"] != cur:
            cur = b["ability"]
            L.append(f"  {cur}/")
        acc = f" → 验收：{b['acceptance']}" if b["acceptance"] else " → 验收：（未写）"
        L.append(f"    - {b['id']}｜{_status_cell(b)}｜{b['title'] or '（无标题）'}{acc}")
    L.append("")
    L.append("== 二、断点状态摘要（MUST-10：接手先看这里，禁凭记忆干活）==")
    L.append(f"  主规格：能力 {summary['abilities']} 个｜需求块 {summary['blocks']} 条｜"
             f"{_fmt_bucket_line(summary['buckets'])}")
    ch = summary["changes"]
    L.append(f"  变更包：{len(ch)} 个未归档")
    for c in ch:
        d = c["delta"]
        L.append(f"    - {c['name']}：tasks {c['tasks_done']}/{c['tasks_total']} 勾选｜"
                 f"proposal {'✓' if c['proposal'] else '✗'}｜design {'✓' if c['design'] else '✗'}｜"
                 f"增量块 {sum(d.values())}（ADDED {d['ADDED']}／MODIFIED {d['MODIFIED']}／REMOVED {d['REMOVED']}）")
    m = summary["matrix"]
    if m["exists"]:
        req = m["requirements"] if m["requirements"] is not None else "?"
        L.append(f"  矩阵：在盘（需求 {req} 条｜文件指纹 {m['file_md5']}）")
    else:
        L.append("  矩阵：缺失（可选增强：py scripts/trace_matrix.py gen --root <项目根> --apply）")
    L.append(f"  尺子读数：{'最近一行 ' + runs_last if runs_last else '无读数（本窗还没跑过尺子）'}")
    L.append("")
    L.append("== 三、证据链尾（MUST-10 断点可续的外部锚）==")
    if chain["exists"]:
        L.append(f"  链文件 {Path(chain['file']).name}：{chain['lines']} 条｜链尾 {chain['tip'] or '（无有效行）'}")
        if chain["problems"]:
            L.append(f"  🔴 链自身疑点 {chain['problems']} 处——跑 evidence_chain.py check 细看（本件只报链尾）")
        L.append("  → 抄进交接/台账；下次 evidence_chain.py check --tip <同一值>，对不上＝尾巴被动过")
    else:
        L.append("  链文件 未落链（可选增强：evidence_chain.py append；不影响开工）")
    if reds:
        L.append("")
        L.extend(reds)
    L.append("")
    L.append("== 四、开工复述三问（v1.21.0 · K22：接手窗先复述，答错补齐再动手——细则 §4 对账纪律）==")
    L.append("  ① 本步改什么：从必读清单＋断点摘要自答；答不出＝还没接住，回去读，禁凭印象开工")
    L.append("  ② 不许碰什么：非本步文件／他人窗口在途件／证词层（归档包·历史输出·已落链证据）")
    L.append("  ③ 怎么验：出门跑尺 `spec_check.py check --root <项目根>`；条款级验收看「验收：」行")
    L.append("")
    L.append(f"[提示] 本输出只做读取集过滤＋断点摘要，不替代尺子：出门前跑 py scripts/spec_check.py check --root {root}")
    return "\n".join(L)


# ---------------------------------------------------------------- 命令

def _gate_root(raw) -> Path:
    root = Path(raw).resolve()
    if not root.is_dir():
        print(f"[没查] 根目录不对：{root} 不存在或不是目录 ⇒ 退 2")
        return None
    return root


def _need_spec():
    spec = _spec_mod()
    if spec is None:
        print("[没查] 同目录缺 spec_check.py——尺子不在，重锚口径不可信 ⇒ 退 2")
    return spec


def cmd_open(args):
    root = _gate_root(args.root)
    if root is None:
        return 2
    spec = _need_spec()
    if spec is None:
        return 2
    _, blocks = collect_blocks(root)
    readset = args.readset
    must = [b for b in blocks if readset_allows(b["bucket"], readset)]
    summary = build_summary(root, spec, blocks)
    chain = chain_summary(Path(args.chain).resolve() if args.chain else default_chain(root))
    runs_last = summary["runs_log_last"]
    reds = []
    if args.expect_tip is not None and chain["tip"] != args.expect_tip:
        reds.append(f"[红] 外部锚不符：链尾现为 {chain['tip'] or '（无链/无有效行）'}，"
                    f"交接记录的是 {args.expect_tip}——链尾被截断或换过（链内查不出，靠外部锚）")
    warn = summary["buckets"]["未知"] + chain["problems"]
    if args.json:
        data = {"tool": "reanchor", "version": VERSION, "root": str(root), "readset": readset,
                "count": len(must), "must_read": must, "summary": summary, "chain": chain,
                "runs_log_last": runs_last, "red": len(reds), "warn": warn}
        print(json.dumps(data, ensure_ascii=True))
    else:
        print(render_open(root, readset, must, summary, chain, runs_last, reds))
    _append_runs_log("open", f"root={root}", red=len(reds), warn=warn, detail=f"读取集={readset}")
    return 1 if reds else 0


def cmd_list(args):
    root = _gate_root(args.root)
    if root is None:
        return 2
    spec = _need_spec()
    if spec is None:
        return 2
    _, blocks = collect_blocks(root)
    must = [b for b in blocks if readset_allows(b["bucket"], args.readset)]
    if args.json:
        print(json.dumps({"tool": "reanchor", "version": VERSION, "root": str(root),
                          "readset": args.readset, "count": len(must), "blocks": must},
                         ensure_ascii=True))
    else:
        print(f"[reanchor list v{VERSION}] root={root}｜读取集={args.readset}｜{len(must)} 条")
        for b in must:
            print(f"  {b['id']}｜{_status_cell(b)}｜{b['ability']}｜{b['title'] or '（无标题）'}"
                  f"｜验收：{b['acceptance'] or '（未写）'}｜{b['file']}:{b['line']}")
    _append_runs_log("list", f"root={root}", red=0, warn=0, detail=f"读取集={args.readset}")
    return 0


def cmd_show(args):
    root = _gate_root(args.root)
    if root is None:
        return 2
    spec = _need_spec()
    if spec is None:
        return 2
    _, blocks = collect_blocks(root)
    hit = next((b for b in blocks if b["id"].lower() == args.rid.lower()), None)
    if hit is None:
        known = "、".join(b["id"] for b in blocks) or "（无）"
        print(f"[红] 查无此号：{args.rid}——现有号：{known}")
        _append_runs_log("show", f"root={root}", red=1, warn=0, detail=f"块={args.rid}｜查无此号")
        return 1
    body = block_body(hit, args.no_scenarios)
    if args.json:
        print(json.dumps({"tool": "reanchor", "version": VERSION, "root": str(root), "id": hit["id"],
                          "title": hit["title"], "bucket": hit["bucket"], "status": hit["status"],
                          "ability": hit["ability"], "file": hit["file"], "line": hit["line"],
                          "acceptance": hit["acceptance"], "no_scenarios": bool(args.no_scenarios),
                          "text": "\n".join([hit["header"]] + body)}, ensure_ascii=True))
    else:
        print(f"[reanchor show v{VERSION}] {hit['file']}:{hit['line']}｜{hit['id']}｜{_status_cell(hit)}"
              f"{'｜已剥场景段' if args.no_scenarios else ''}")
        print(hit["header"])
        for ln in body:
            print(ln)
    _append_runs_log("show", f"root={root}", red=0, warn=0,
                     detail=f"块={hit['id']}" + ("｜--no-scenarios" if args.no_scenarios else ""))
    return 0


# ---------------------------------------------------------------- selftest

BOOKING_SPEC = """# 预约域规格

#### R-1 | 会议预约

当用户提交预约时，系统应创建预约记录。

- 状态：生效
- 验收：tests/test_booking.py

#### 场景：正常预约

- **WHEN** 用户提交有效时段
- **THEN** 返回预约编号

#### R-2 | 取消预约

若会议已开始则系统应拒绝取消。

- 状态：草案
- 验收：卡E1

#### R-3 | 时段冲突

当两个预约重叠时，系统应拒绝后一个。

- 验收：走查清单第 4 条

#### R-4 | 旧口径预约

当用户提交时，系统应直接落库。

- 状态：已取代
- 取代：R-1
- 验收：tests/test_old.py

#### R-5 | 试用条款

试用期内系统应放行。

- 状态：废弃
- 验收：tests/test_trial.py

#### R-6 | 上线口径

系统应记日志。

- 状态：上线
- 验收：tests/test_log.py
"""
NOTE_SPEC = """# 笔记域规格

#### R-9 | 笔记能力

系统应保存笔记。

- 状态：生效
- 验收：tests/test_note.py
"""
TASKS_MD = """# 任务

- [x] E1 立规格
- [x] E2 写脚本
- [ ] E3 补测试
- [ ] E4 归档
- [ ] E5 复跑
"""
DELTA_MD = """## ADDED Requirements

#### R-10 | 标签能力

当用户加标签时，系统应保存标签。

- 状态：生效
- 验收：tests/test_tag.py
"""


def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-reanchor-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["EVSPEC_RUNS_LOG"] = str(tmp_root / "runs.log")
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}

    results = []

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    def run(*cli):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(cli))
        return rc, buf.getvalue()

    def run_json(*cli):
        rc, out = run(*cli)
        try:
            return rc, json.loads(out), out
        except Exception:
            return rc, {}, out

    _src_self = Path(__file__).resolve().read_text(encoding="utf-8")

    def run_mutant(anchor, replacement, cli):
        """变异探针：打坏实现本体后独立命名空间重载，点名现象必须翻面（原样对照臂=对应 S 用例）。"""
        hits = [l for l in _src_self.splitlines() if l.strip().startswith(anchor)]
        if not hits:
            return None, ""
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "reanchor_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<reanchor_mutant>", "exec"), g)
        except Exception:
            return None, ""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = g["main"](list(cli))
        return rc, buf.getvalue()

    def mk_proj(name):
        proj = tmp_root / name
        (proj / "specs" / "booking").mkdir(parents=True, exist_ok=True)
        (proj / "specs" / "note").mkdir(parents=True, exist_ok=True)
        (proj / "specs" / "booking" / "spec.md").write_text(BOOKING_SPEC, encoding="utf-8")
        (proj / "specs" / "note" / "spec.md").write_text(NOTE_SPEC, encoding="utf-8")
        ch = proj / "changes"
        (ch / "add-tag" / "specs" / "note").mkdir(parents=True, exist_ok=True)
        (ch / "archive" / "legacy-pkg").mkdir(parents=True, exist_ok=True)
        (ch / "add-tag" / "proposal.md").write_text("# Why\n要加标签。\n", encoding="utf-8")
        (ch / "add-tag" / "design.md").write_text("# 设计\n略。\n", encoding="utf-8")
        (ch / "add-tag" / "tasks.md").write_text(TASKS_MD, encoding="utf-8")
        (ch / "add-tag" / "specs" / "note" / "spec.md").write_text(DELTA_MD, encoding="utf-8")
        (ch / "archive" / "legacy-pkg" / "tasks.md").write_text("- [x] E1 老包\n", encoding="utf-8")
        (proj / "trace").mkdir(parents=True, exist_ok=True)
        (proj / "trace" / "matrix.json").write_text(
            json.dumps({"summary": {"requirements": 7}}, ensure_ascii=False), encoding="utf-8")
        (proj / "a1.txt").write_text("证件一\n", encoding="utf-8")
        (proj / "a2.txt").write_text("证件二\n", encoding="utf-8")
        return proj

    def mk_chain(proj):
        """用 evidence_chain 的真哈希函数造两条链（不重抄哈希口径）。"""
        ec = load_sibling("evidence_chain.py")
        if ec is None:
            return None
        entries, prev = [], ec.GENESIS
        for i, (art, act) in enumerate((("a1.txt", "selftest-a1"), ("a2.txt", "selftest-a2")), 1):
            d = {"prev": prev, "seq": i, "action": act, "artifact": art,
                 "digest": ec.sha256_bytes((proj / art).read_bytes()), "ts": "2026-10-28T00:00:00"}
            d["hash"] = ec.line_hash(d)
            entries.append(d)
            prev = d["hash"]
        (proj / "evidence.ndjson").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
        return prev

    proj = mk_proj("proj")
    TIP = mk_chain(proj)
    rlog = tmp_root / "runs.log"

    def rlines():
        return rlog.read_text(encoding="utf-8").splitlines() if rlog.exists() else []

    def snap(root):
        return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}

    def ids(js):
        return {b.get("id") for b in js.get("must_read", []) if isinstance(b, dict)}

    # —— S01 地基绿：open 退 0，三节都在（必读清单／断点摘要／链尾）
    rc1, out1 = run("open", "--root", str(proj))
    green("S01 地基绿：open 退 0 且三节齐（本步必读清单＋断点状态摘要＋证据链尾）",
          rc1 == 0 and "本步必读" in out1 and "断点状态摘要" in out1 and "证据链尾" in out1,
          f"退码={rc1}")

    # —— S02 open 档（开工/重锚）：草案＋生效＋未标＋未知进，已取代/废弃不进
    rc2, js2, out2 = run_json("open", "--root", str(proj), "--json")
    green("S02 读取集 open 档：含 R-1/R-2/R-3/R-6/R-9（生效/草案/未标/未知），不含 R-4/R-5（已取代/废弃）",
          rc2 == 0 and ids(js2) == {"R-1", "R-2", "R-3", "R-6", "R-9"}, f"必读={sorted(ids(js2))}")

    # —— S03 promise 档：草案不进承诺面
    rc3, js3, _ = run_json("open", "--root", str(proj), "--readset", "promise", "--json")
    green("S03 读取集 promise 档：只含生效＋未标（R-1/R-3/R-9），草案 R-2 被挡",
          rc3 == 0 and ids(js3) == {"R-1", "R-3", "R-9"}, f"必读={sorted(ids(js3))}")

    # —— S04 history 档：只读历史（已取代/废弃）
    rc4, js4, _ = run_json("open", "--root", str(proj), "--readset", "history", "--json")
    green("S04 读取集 history 档：只含 R-4/R-5，现行与草案全挡",
          rc4 == 0 and ids(js4) == {"R-4", "R-5"}, f"必读={sorted(ids(js4))}")

    # —— S05 未知状态值不静默丢：进 open 档并标「未知（状态：上线）」（人读面＝S01 的 out1，同一份事实）
    b6 = next((b for b in js2.get("must_read", []) if b.get("id") == "R-6"), {})
    green("S05 未知状态值（状态：上线）：进 open 档、bucket=未知、人读面点名原值",
          b6.get("bucket") == "未知" and "未知（状态：上线）" in out1, f"R-6={b6.get('bucket')}")

    # —— S06 list 扁平清单：一行一条（对 open 档 5 条），机读面同源
    rca, outa = run("list", "--root", str(proj))
    rcb, jsb, _ = run_json("list", "--root", str(proj), "--json")
    flat = [l for l in outa.splitlines() if re.match(r"^\s*R-[\w.\-]+\s*｜", l)]
    green("S06 list 扁平清单：人读 5 行（=open 档条数）、行含状态与验收去向；--json count 与之相等",
          rca == 0 and rcb == 0 and len(flat) == 5 and jsb.get("count") == len(flat)
          and all("｜" in l for l in flat) and any("tests/test_booking.py" in l for l in flat),
          f"行数={len(flat)}｜json_count={jsb.get('count')}")

    # —— S07 show 单块：只打该块
    rc7, out7 = run("show", "--root", str(proj), "R-1")
    green("S07 show 单块：含 R-1 条款句与验收行，不含别的块",
          rc7 == 0 and "当用户提交预约时" in out7 and "取消预约" not in out7, f"退码={rc7}")

    # —— S08 show --no-scenarios：剥掉场景段（过滤读法）；不加开关时场景在
    rc8, out8 = run("show", "--root", str(proj), "R-1")
    rc8b, out8b = run("show", "--root", str(proj), "R-1", "--no-scenarios")
    green("S08 过滤读法 --no-scenarios：场景段剥掉（无 WHEN 行）而条款句/验收行仍在；不加开关时场景在",
          rc8 == 0 and rc8b == 0 and "WHEN" in out8 and "WHEN" not in out8b
          and "当用户提交预约时" in out8b and "tests/test_booking.py" in out8b,
          f"原退码={rc8}｜剥离退码={rc8b}")

    # —— S09 show 查无此号 ⇒ 退 1 点名（不静默当成功）
    rc9, out9 = run("show", "--root", str(proj), "R-99")
    green("S09 show 未知号 ⇒ 退 1 且点名该号", rc9 == 1 and "R-99" in out9, f"退码={rc9}")

    # —— S10 断点摘要：变更包 tasks 勾选如实（2/5）＋archive 包不算未归档
    green("S10 断点摘要变更包：tasks 2/5 勾选如实、三件套在场；archive/legacy-pkg 不计入未归档",
          "tasks 2/5" in out1 and "add-tag" in out1 and "legacy-pkg" not in out1, "见 S01 人读面")

    # —— S11 主规格四态计数如实（7 条：生效 2／草案 1／未标 1／未知 1／已取代 1／废弃 1）
    green("S11 主规格计数：能力 2 个、需求块 7 条、六桶计数如实",
          "能力 2 个" in out1 and "需求块 7 条" in out1
          and "草案 1｜生效 2｜未标 1｜未知 1｜已取代 1｜废弃 1" in out1, "见 S01 人读面")

    # —— S12 链尾外部锚：行数＋链尾全值（与人读面一致，且与 evidence_chain 计算同源）
    ch12 = js2.get("chain") or {}
    green("S12 证据链尾：2 条、链尾＝真链末行哈希全值（人读面同值）",
          ch12.get("lines") == 2 and ch12.get("tip") == TIP and TIP and TIP in out2,
          f"链尾={(ch12.get('tip') or '')[:20]}…")

    # —— S13 链缺件 ⇒ 打「未落链」且退 0（可选增强不拦开工）
    (proj / "evidence.ndjson").rename(proj / "evidence.ndjson.bak")
    rc13, out13 = run("open", "--root", str(proj))
    (proj / "evidence.ndjson.bak").rename(proj / "evidence.ndjson")
    green("S13 未落链：chain 缺件 ⇒ 输出「未落链」、退 0（不拦开工）",
          rc13 == 0 and "未落链" in out13, f"退码={rc13}")

    # —— S14 外部锚复核：--expect-tip 相符退 0、不符退 1 并点名
    rc14a, out14a = run("open", "--root", str(proj), "--expect-tip", TIP)
    rc14b, out14b = run("open", "--root", str(proj), "--expect-tip", "sha256:" + "0" * 64)
    green("S14 --expect-tip 外部锚：相符退 0；不符退 1 且点名「外部锚不符」",
          rc14a == 0 and rc14b == 1 and "外部锚不符" in out14b, f"相符={rc14a}｜不符={rc14b}")

    # —— S15 runs.log 接线：open 跑一次追加一行（工具名/动作/读取集）
    n15 = len(rlines())
    rc15, _ = run("open", "--root", str(proj))
    d15 = rlines()[n15:]
    green("S15 runs.log 接线：open +1 行，行含 reanchor｜open｜root=｜读取集=open",
          rc15 == 0 and len(d15) == 1 and "reanchor" in d15[0] and "｜open｜" in d15[0]
          and "读取集=open" in d15[0], f"行={d15[:1]}")

    # —— S16 list / show 同样接线
    n16 = len(rlines())
    run("list", "--root", str(proj))
    run("show", "--root", str(proj), "R-1")
    d16 = rlines()[n16:]
    green("S16 runs.log 接线：list 与 show 各 +1 行（共 +2，动作名各就各位）",
          len(d16) == 2 and "｜list｜" in d16[0] and "｜show｜" in d16[1], f"行数={len(d16)}")

    # —— S17 退 2（根不对）不记读数
    n17 = len(rlines())
    rc17, _ = run("open", "--root", str(tmp_root / "无此根"))
    green("S17 根目录不存在 ⇒ 退 2 且不记读数（没查成不算跑过）",
          rc17 == 2 and len(rlines()) == n17, f"退码={rc17}")

    # —— S18 变异臂①：读取集判据打哑 ⇒ open 档混进已取代块（S02 现象翻面）
    rc18, out18 = run_mutant("return name in READSETS and bucket in READSETS[name]", "    return True",
                             ["open", "--root", str(proj), "--json"])
    try:
        js18 = json.loads(out18)
    except Exception:
        js18 = {}
    green("S18 变异臂：读取集判据打哑（全放行）⇒ open 档含已取代 R-4（S02 现象翻面=过滤实现被钉住）",
          "R-4" in ids(js18), f"变异后必读={sorted(ids(js18))}")

    # —— S19 变异臂②：RUNSLOG_ON 打哑 ⇒ 读数不再追加
    n19 = len(rlines())
    rc19, _ = run_mutant("RUNSLOG_ON = True", "RUNSLOG_ON = False", ["open", "--root", str(proj)])
    green("S19 变异臂：RUNSLOG_ON 打哑 ⇒ +0 行（S15 现象消失，接线被钉住）",
          len(rlines()) == n19, f"变异后行数差={len(rlines()) - n19}")

    # —— S20 变异臂③：--no-scenarios 剥离打哑 ⇒ 场景仍在（S08 现象翻面）
    rc20, out20 = run_mutant("return _strip_scenarios(b[\"raw\"]) if no_scen else list(b[\"raw\"])",
                             "    return list(b[\"raw\"])",
                             ["show", "--root", str(proj), "R-1", "--no-scenarios"])
    green("S20 变异臂：--no-scenarios 剥离打哑 ⇒ 场景 WHEN 仍在（S08 现象翻面=剥离实现被钉住）",
          "WHEN" in out20, f"变异后退码={rc20}")

    # —— S21 只读承诺：open/list/show 跑完项目树零写入
    snap_a = snap(proj)
    run("open", "--root", str(proj))
    run("list", "--root", str(proj))
    run("show", "--root", str(proj), "R-1")
    snap_b = snap(proj)
    green("S21 只读承诺：open/list/show 三次跑完项目路径与 mtime 全等（目标项目零写入）",
          snap_a == snap_b, f"变化={sorted(set(snap_a) ^ set(snap_b))[:4]}")

    # —— S22 矩阵摘要：在盘报需求条数与指纹；缺件报「缺失」不红（可选增强）
    mp = proj / "trace" / "matrix.json"
    saved = mp.read_text(encoding="utf-8")
    mp.unlink()
    rc22, out22 = run("open", "--root", str(proj))
    mp.write_text(saved, encoding="utf-8")
    green("S22 矩阵摘要：在盘报「需求 7 条」，缺件报「缺失」且退 0（可选增强不炸老项目）",
          "需求 7 条" in out1 and rc22 == 0 and "缺失" in out22, f"缺件退码={rc22}")

    # —— S26 复述三问（v1.21.0 · K22）：open 输出第四节在案，三问词面齐（接手窗先复述再动手）
    green("S26 复述三问：open 输出含「开工复述三问」节＋改什么/不许碰/怎么验三词面（细则 §4 对账纪律）",
          "开工复述三问" in out1 and "本步改什么" in out1 and "不许碰什么" in out1 and "怎么验" in out1,
          f"out1 含复述节={'开工复述三问' in out1}")

    # —— S23 分母闸 / S24 scripts 零写入 / S25 收尾清理
    green(f"S23 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 3 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 3} 条")
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S24 scripts/ 目录零写入（兄弟文件 mtime 全等）", scripts_before == scripts_after,
          f"变化={sorted(set(scripts_before) ^ set(scripts_after))}")
    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S25 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")

    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[reanchor selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="EverSpec 开窗重锚＋断点状态摘要（只读，零依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open", help="开窗重锚包：必读清单＋断点摘要＋链尾锚")
    o.add_argument("--root", required=True)
    o.add_argument("--readset", default="open", choices=sorted(READSETS))
    o.add_argument("--chain", default=None, help="证据链文件（默认 <root>/evidence.ndjson）")
    o.add_argument("--expect-tip", default=None, help="外部锚：链尾哈希应为该值（不符退 1）")
    o.add_argument("--json", action="store_true", help="只打一行 JSON（机读面）")
    o.set_defaults(func=cmd_open)
    l = sub.add_parser("list", help="扁平清单（一行一条，过滤读法）")
    l.add_argument("--root", required=True)
    l.add_argument("--readset", default="open", choices=sorted(READSETS))
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)
    s = sub.add_parser("show", help="单条需求块原文（可剥场景段）")
    s.add_argument("--root", required=True)
    s.add_argument("rid")
    s.add_argument("--readset", default="open", choices=sorted(READSETS))
    s.add_argument("--no-scenarios", action="store_true", help="剥掉场景段（省上下文）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)
    t = sub.add_parser("selftest", help="红例自证（只写临时树）")
    t.add_argument("--tmp-root", default=None)
    t.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
