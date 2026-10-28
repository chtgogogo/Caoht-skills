#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规格层机械校验器（spec_check）——交付流水线 ③/⑤/⑥/⑫ 的"能红的尺"。

只读不改：检查 specs/（主规格）＋ changes/（变更包）＋ docs/pipeline/（PRD/卡片/state.json）
之间咬合得上不上，19 条具名断言 C01–C19（C15/C16 = v1.19.0 OpenSpec 实测回流：
需求块场景 WHEN/THEN 成对、proposal 顶层三节锚；C17–C19 = v1.20.0 实测回流：
追踪矩阵活性、现状面声称计数、状态源唯一）。细则见 references/规格层-主规格变更与归档.md。

    py scripts/spec_check.py check --root <项目根> [--strict]
    py scripts/spec_check.py selftest [--tmp-root <目录>]   # 红例自证，只写临时树

退出码：0 全绿（可含警）｜1 有红（--strict 时含警）｜2 根目录/用法不对
"""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

VERSION = "1.2.0"

CHECKS = [
    ("C01", "规格层骨架在位且主规格有需求块"),
    ("C02", "验收指向的测试文件真在盘上"),
    ("C03", "每条需求有且只有一句 EARS 句式"),
    ("C04", "需求号不得重复定义（ADDED 不得已在主规格）"),
    ("C05", "卡片引用的需求号必须存在于主规格或增量"),
    ("C06", "主规格每条需求都被至少一张卡接住"),
    ("C07", "tasks.md 勾选与 state.json 卡状态一致"),
    ("C08", "变更包四件齐（proposal/design/tasks/增量 specs）"),
    ("C09", "归档闭环：卡全 done 的变更包必须已归档并回主规格"),
    ("C10", "MODIFIED/REMOVED 增量引用的需求号必须在主规格里"),
    ("C11", "PRD 含项目宪章 5–8 条，每条带阈值、零模糊词"),
    ("C12", "规格层与 PRD 禁裸占位「无／暂无」"),
    ("C13", "规格层禁行号锚点（只准文件＋标识符）"),
    ("C14", "每条需求有验收去向（卡号或测试文件）"),
    ("C15", "场景块：写了必须 WHEN/THEN 粗体成对（零场景文件记警不记红）"),
    ("C16", "proposal 顶层三节锚齐（为什么/改哪些需求/影响面）"),
    ("C17", "追踪矩阵活性（trace/matrix.json 里的符号/测试/卡必须真在盘）"),
    ("C18", "现状面声称计数与实测一致（需求号上限/断言数/走查条数）"),
    ("C19", "状态源唯一（归档后台账不得复活、视图必须带 AUTO 标记）"),
]
CHECK_TITLES = dict(CHECKS)
ASSERT_FLOOR = 37   # selftest 断言分母闸：条数掉了就该红，防止有人悄悄删用例（v1.20.0 随 C17–C19 用例同升）
ARCHIVE_NAME = "archive"

# ---------------------------------------------------------------- 判据正则

EARS_PATTERNS = [
    ("事件驱动 When", r"^(?:当|每当)[^。]*?时\s*[，,]\s*(?:则\s*)?(?:该\s*)?(?:本\s*)?系统应"),
    ("非期望行为 If-Then", r"^若[^。]{1,60}?则[^。]{0,60}?系统应"),
    ("状态驱动 While", r"^(?:在|当).{0,20}?(?:状态|期间|情况下)[，,]\s*系统应"),
    ("可选功能 Where", r"^(?:若|如).{0,20}?启用.{0,20}?[，,]\s*系统应"),
    ("普适型 Ubiquitous", r"^系统应"),
    ("事件驱动 When(en)", r"\bwhen\b.{1,200}?\bsystem\s+shall\b"),
    ("非期望行为 If(en)", r"\bif\b.{1,200}?\bsystem\s+shall\b"),
    ("状态驱动 While(en)", r"\bwhile\b.{1,200}?\bsystem\s+shall\b"),
    ("可选功能 Where(en)", r"\bwhere\b.{1,200}?\bsystem\s+shall\b"),
    ("普适型 Ubiquitous(en)", r"^the\s+system\s+shall\b"),
]
EARS_COMPILED = [(n, re.compile(p, re.I)) for n, p in EARS_PATTERNS]

META_PREFIX = re.compile(r"^\s*[-*]?\s*(状态|验收|关联|来源|备注|优先级|负责人|关键定位)\s*[：:]")
REQ_HEAD = re.compile(r"^#{2,5}\s+(R-[\w.\-]+)\s*(?:[|｜]\s*(.+?))?\s*$")
DELTA_HEAD = re.compile(r"^#{1,5}\s*(ADDED|MODIFIED|REMOVED)\s+Requirements\s*$", re.I)
CARD_ID = re.compile(r"卡\s*#?\s*([A-Z]?\d+)")
R_ID = re.compile(r"\bR-[\w.\-]+")
LINE_ANCHOR = re.compile(r"([\w\-./\\]+\.(?:py|md|js|ts|tsx|jsx|vue|sql|json|java|go|rs|html|css|sh)):(\d+)")
BARE_PLACEHOLDER = re.compile(r"^\s*(?:[-*|]\s*)?(?:无|暂无)\s*[。．]?\s*(?:\|\s*)?$")
FUZZY = re.compile(r"尽量|大致|合适|高效|高性能|友好|良好|合理|较快|差不多|应当挺好")
THRESHOLD = re.compile(r"\d|不得|禁止|必须|只允许|≤|≥|零|唯一")
TASK_LINE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(.*)$")
TASK_ID = re.compile(r"(?<![\w-])([A-Z]\d{1,2}|\d{1,3})(?![\w-])")
OUT_SHAPE = re.compile(r"^\[红\] C\d{2} .+｜\S+｜.{6,}$")
# v1.19.0：场景块（OpenSpec 同款，中文头为主、英文头兼容）与 proposal 三节锚
# 🔴 三节锚都有 `^` 锚——必须配 re.M 逐行锚定，否则只查文件首行（v1.19 首跑抓到）
SCEN_HEAD = re.compile(r"^#{2,5}\s*(?:场景|Scenario)\s*[:：]\s*(\S.*)$")
SCEN_BULLET = re.compile(r"^\s*[-*]\s*\*\*(when|then|and)\*\*", re.I)
C16_ALIASES = {
    "为什么": re.compile(r"^#{1,4}\s*(?:Why|为什么)\s*(?:[:：]|\s|$)", re.I | re.M),
    "改哪些需求": re.compile(r"^#{1,4}\s*(?:What\s+Changes|改哪些需求|要改什么|改动清单)\s*(?:[:：]|\s|$)", re.I | re.M),
    "影响面": re.compile(r"^#{1,4}\s*(?:Impact|影响面|影响范围)\s*(?:[:：]|\s|$)", re.I | re.M),
}


def read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def ears_types(text: str):
    """命中的 EARS 型名（去重）。返回空＝整段是散文；返回 >1＝一个块塞了多条断言。"""
    hits = []
    for line in text.splitlines():
        s = line.strip()
        if not s or META_PREFIX.match(s):
            continue
        for name, rx in EARS_COMPILED:
            if rx.search(s) and name not in hits:
                hits.append(name)
    return hits


def clause_text(block):
    """需求块的块级行（条款句＋元信息行，场景段之外的）拼回文本——C03 只查这一段。"""
    return "\n".join(ln for _, ln in block["clause"])


def parse_req_blocks(md_path, text, base_line=0):
    lines = text.splitlines()
    blocks, cur = [], None
    for i, line in enumerate(lines, 1):
        m = REQ_HEAD.match(line)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"id": m.group(1), "title": (m.group(2) or "").strip(),
                   "lines": [], "nos": [], "start": base_line + i, "file": md_path}
        elif cur is not None:
            cur["lines"].append(line)
            cur["nos"].append(base_line + i)
    if cur:
        blocks.append(cur)
    for b in blocks:
        b["body"] = "\n".join(b["lines"])
        b["clause"], b["scenarios"] = partition_scenarios(b["lines"], b["nos"])
    return blocks


def partition_scenarios(lines, nos):
    """需求块体 → (块级行, 场景段列表)。

    场景段＝`#### 场景：<名>`（或 `Scenario:`）起点，一路吞到下一个场景标头或块尾——
    **场景之后别再拖行**，拖进来的散文/多出来的断言会被 C15 判「混入」。场景之外的
    行（含前面的条款句与 8 类元信息行）都是块级行，按条款口径进 C03。
    """
    clause, scens, cur = [], [], None
    for no, ln in zip(nos, lines):
        m = SCEN_HEAD.match(ln)
        if m:
            if cur:
                scens.append(cur)
            cur = {"title": m.group(1).strip(), "lines": [], "nos": []}
        elif cur is None:
            clause.append((no, ln))
        else:
            cur["lines"].append(ln)
            cur["nos"].append(no)
    if cur:
        scens.append(cur)
    return clause, scens


def delta_sections(text):
    """增量文件按 ADDED/MODIFIED/REMOVED 标头切段，返回 {类型: (文本, 段起始行)}。"""
    lines = text.splitlines()
    marks = []
    for i, line in enumerate(lines, 1):
        m = DELTA_HEAD.match(line.strip())
        if m:
            marks.append((i, m.group(1).upper()))
    out = {}
    for idx, (start, kind) in enumerate(marks):
        end = marks[idx + 1][0] - 1 if idx + 1 < len(marks) else len(lines)
        out[kind] = ("\n".join(lines[start:end]), start)
    for k in ("ADDED", "MODIFIED", "REMOVED"):
        out.setdefault(k, ("", 0))
    return out


def task_items(text):
    """tasks.md → [(勾没勾, 卡号, 行号)]。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = TASK_LINE.match(line)
        if not m:
            continue
        for tid in TASK_ID.findall(m.group(2)):
            out.append((m.group(1).lower() == "x", tid, i))
    return out


def state_card_status(state):
    """从 state.json 里挖 卡号→状态，两种形态都认：{"B1":{"state":"done"}} 与 [{"id":"B1",...}]。"""
    found = {}

    def norm(v):
        return v.strip().lower() if isinstance(v, str) else None

    def walk(node):
        if isinstance(node, dict):
            ident, st = norm(node.get("id") or node.get("card")), norm(node.get("state") or node.get("status"))
            if ident and st and re.fullmatch(r"[A-Z]?\d{1,2}|\d{1,3}", ident):
                found[ident] = st
            for k, v in node.items():
                if isinstance(v, dict):
                    st2 = norm(v.get("state") or v.get("status"))
                    if isinstance(k, str) and st2 and re.fullmatch(r"[A-Z]?\d{1,2}|\d{1,3}", k):
                        found[k] = st2
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(state)
    return found


# ---------------------------------------------------------------- 上下文

class Ctx:
    pass


def build_ctx(root: Path) -> Ctx:
    c = Ctx()
    c.root = root
    # 装了外部 SDD 工具（OpenSpec）就用它的前缀，否则用裸 specs/+changes/——细则 §1
    if (root / "openspec" / "specs").is_dir() or (root / "openspec" / "changes").is_dir():
        c.specs_dir, c.changes_dir, c.prefix = root / "openspec" / "specs", root / "openspec" / "changes", "openspec/"
    else:
        c.specs_dir, c.changes_dir, c.prefix = root / "specs", root / "changes", "."
    c.spec_files = sorted(c.specs_dir.rglob("*.md")) if c.specs_dir.is_dir() else []

    c.prd = next((p for cand in ("docs/pipeline/PRD.md", "PRD.md")
                  if (p := root / cand).is_file()), None)

    c.card_files = []
    for sub in ("docs/pipeline/cards", "docs/pipeline/具体任务卡"):
        d = root / sub
        if d.is_dir():
            c.card_files.extend(sorted(d.rglob("*.md")))
    c.card_files = sorted(set(c.card_files))

    c.state, c.state_err = None, None
    sp = root / "docs" / "pipeline" / "state.json"
    if sp.is_file():
        try:
            c.state = json.loads(read_text(sp))
        except json.JSONDecodeError as e:
            c.state_err = f"state.json 不是合法 JSON：{e}"

    c.change_dirs = sorted(d for d in (c.changes_dir.iterdir() if c.changes_dir.is_dir() else [])) \
        if c.changes_dir.is_dir() else []
    c.change_dirs = [d for d in c.change_dirs if d.is_dir() and d.name.lower() != ARCHIVE_NAME]

    c.map_file = root / "docs" / "pipeline" / "模块地图.md"
    return c


def rel(root, p):
    try:
        return Path(p).relative_to(root).as_posix()
    except ValueError:
        return str(p)


class Report:
    def __init__(self):
        self.items = []

    def red(self, code, where, msg):
        self.items.append((code, "红", where, msg))

    def warn(self, code, where, msg):
        self.items.append((code, "警", where, msg))

    def red_codes(self):
        return {i[0] for i in self.items if i[1] == "红"}

    def warn_codes(self):
        return {i[0] for i in self.items if i[1] == "警"}


# ---------------------------------------------------------------- C17–C19 燃料

def load_trace_module():
    """同目录兄弟脚本 trace_matrix.py（C17 用）。缺件/坏件返回 None，调用方降级记警。"""
    p = Path(__file__).with_name("trace_matrix.py")
    if not p.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_trace_matrix", p)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# 现状声明面：只有这些文件的数字是"对读者承诺的现状"。
# 🔴 不扫 CHANGELOG／回看报告／质检报告／evidence——那里的旧数字是**证词**，追改即造假。
CLAIM_SCOPES = (
    "AGENTS.md", "README.md", "CONTEXT.md", "SDD/README.md",
    "docs/pipeline/PRD.md", "docs/pipeline/模块地图.md", "docs/pipeline/上下文体检.md",
    "docs/pipeline/口径核查表.md",
)
# 🔴 交付记录清单/L2 轨迹本不在此列：它的自我声明就是"完成后打勾不删行"，整份是历史轨迹，
#    里面的旧数字是证词，追改即造假（同一原则见 CLAIM_SCOPES 上方注释）。
CLAIM_RULES = (
    # 需求号总量：必须带"条/共/全部/每条"这类总量词，否则「R-1~R-3、R-25、R-32」这种
    # 功能映射行会被误读成"项目只有 3 条需求"（2026-10-18 真项目首跑撞出的假阳）
    ("需求号上限", re.compile(r"R-1\s*[~～\-—至]\s*R-(\d+)\s*(?:条|共|全部|全量|每条|以内)")),
    ("断言条数", re.compile(r"(\d{1,3})\s*条\s*(?:具名|机械)?断言")),
    ("走查条数", re.compile(r"走查(?:清单)?\s*(\d{1,3})\s*条")),
    ("卡数", re.compile(r"卡库\s*(\d{1,3})\s*[张个]")),
    ("归档包数", re.compile(r"(\d{1,3})\s*个已归档变更包")),
)
# 引号里的东西是"引用历史读数"，不是现状声称（例：`小白手册"15 条校准依据"已改`）——匹配前整段剔除
QUOTED = re.compile(r"[“\"「『][^”\"」』\n]{0,60}[”\"」』]")
# 同理：显式标了"历史读数/证词/当时"的行是证词区，不拿它当现状承诺
HIST_MARK = re.compile(r"历史读数|历史：|证词|当时值|旧值")


def measured_counts(root, c, spec_blocks, delta_blocks):
    """实测分母（现采，不抄任何文档里的数）。"""
    rids = []
    for b in spec_blocks + delta_blocks:
        m = re.fullmatch(r"R-(\d+)", b["id"])
        if m:
            rids.append(int(m.group(1)))
    cards = 0
    if isinstance(c.state, dict) and isinstance(c.state.get("cards"), dict):
        cards = len(c.state["cards"])
    walk = 0
    if c.prd:
        text = read_text(c.prd)
        m = re.search(r"^#+\s*用户视角走查清单.*?$(.*)", text, re.M | re.S)
        if m:
            body = re.split(r"^#+\s", m.group(1), maxsplit=1, flags=re.M)[0]
            walk = len([l for l in body.splitlines() if re.match(r"^\s*\d+[.、)]\s*\S", l)])
    arch = c.changes_dir / ARCHIVE_NAME
    archived = len([d for d in arch.iterdir() if d.is_dir()]) if arch.is_dir() else 0
    return {"需求号上限": max(rids) if rids else 0, "断言条数": len(CHECKS), "走查条数": walk,
            "卡数": cards, "归档包数": archived}


# ---------------------------------------------------------------- 断言


def run_checks(root: Path, rep=None):
    rep = rep or Report()
    c = build_ctx(root)

    spec_blocks = []
    for f in c.spec_files:
        spec_blocks.extend(parse_req_blocks(f, read_text(f)))

    # C01
    if not c.specs_dir.is_dir():
        rep.red("C01", rel(root, c.specs_dir), f"主规格目录不存在（布局前缀 {c.prefix}）")
    elif not c.spec_files:
        rep.red("C01", rel(root, c.specs_dir), "主规格目录里一份 .md 都没有")
    elif not spec_blocks:
        rep.red("C01", rel(root, c.specs_dir), "主规格里没有一条 `#### R-xxx | 标题` 需求块")

    # 增量块
    delta_blocks, removed_ids, added_ids, touched_ids = [], [], [], []
    for cd in c.change_dirs:
        sd = cd / "specs"
        for f in sorted(sd.rglob("*.md")) if sd.is_dir() else []:
            text = read_text(f)
            secs = delta_sections(text)
            for kind in ("ADDED", "MODIFIED"):
                seg, base = secs[kind]
                for b in parse_req_blocks(f, seg, base_line=base):
                    b["kind"] = kind
                    delta_blocks.append(b)
                    touched_ids.append(b["id"])
                    if kind == "ADDED":
                        added_ids.append(b["id"])
                    if not ears_types(clause_text(b)):
                        rep.red("C03", f"{rel(root, f)}:{b['start']}",
                                f"增量 {kind} 里的 {b['id']} 没有 EARS 句")
            seg, base = secs["REMOVED"]
            for b in parse_req_blocks(f, seg, base_line=base):
                removed_ids.append(b["id"])

    # C05 认「历史号」：REMOVED 里的号曾经存在，否则归档之后老卡全部误红
    all_defs = {b["id"] for b in spec_blocks} | {b["id"] for b in delta_blocks} | set(removed_ids)
    spec_ids = [b["id"] for b in spec_blocks]

    # C02 验收指向的测试文件在不在盘上
    for b in spec_blocks + delta_blocks:
        for fp in re.findall(r"(tests?[\\/][\w\-./]+\.py)", b["body"]):
            if not (root / fp).is_file():
                rep.red("C02", f"{rel(root, b['file'])}:{b['start']}",
                        f"{b['id']} 的验收指向 {fp}，盘上没有——先落测试文件，或改指已存在的")

    # C03 主规格句式（v1.25.0 增两面警：同型多句聚合、模糊断言、超长句——机械尺的语义薄弱面点名）
    for b in spec_blocks:
        clause = clause_text(b)
        t = ears_types(clause)
        if not t:
            rep.red("C03", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 块内没有 EARS 句（五种都不命中＝散文）")
        elif len(t) > 1:
            rep.red("C03", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 命中 {len(t)} 种句式 {t}——一个需求块只准一句可判定断言")
        else:
            hits = [ln for ln in clause.splitlines()
                    if ln.strip() and not META_PREFIX.match(ln.strip()) and ears_types(ln)]
            if len(hits) > 1:
                rep.warn("C03", f"{rel(root, b['file'])}:{b['start']}",
                         f"{b['id']} 同型断言连写 {len(hits)} 句（机械尺只判得出跨型，同型塞两句查不出"
                         f"——人工确认是否该拆成两条需求）")
            for ln in hits:
                if FUZZY.search(ln):
                    rep.warn("C03", f"{rel(root, b['file'])}:{b['start']}",
                             f"{b['id']} 断言含模糊词（IEEE 29148「无歧义」面：句式合法但判据模糊——"
                             f"「系统应快速响应」这类写法测试写不出红灯，改成带数字/可观察行为的句式）")
                    break
                if len(ln.strip()) > 120:
                    rep.warn("C03", f"{rel(root, b['file'])}:{b['start']}",
                             f"{b['id']} 断言超长（>120 字）——多为多条件塞一句，五型正则的判型力在此衰减，"
                             f"考虑拆条件或挪场景块")
                    break

    # C04 重复定义
    dup = {}
    for rid in spec_ids:
        dup.setdefault(rid, 0)
        dup[rid] += 1
    for rid, n in dup.items():
        if n > 1:
            rep.red("C04", rel(root, c.specs_dir), f"{rid} 在主规格里定义了 {n} 次")
    for rid in added_ids:
        if rid in dup:
            rep.red("C04", rel(root, c.specs_dir),
                    f"增量的 ADDED {rid} 已在主规格里存在（要改就用 MODIFIED）")

    # C05 / C06 卡 ↔ 需求 双向
    card_refs = {}
    for f in c.card_files:
        m = CARD_ID.search(f.name) or CARD_ID.search(read_text(f))
        cid = m.group(1) if m else f.stem
        # 同卡号多文件（L2 纲要卡/L3 施工图/执行回执/evidence 共享 cid）取并集——
        # 赋值会互相覆盖，2026-10-16 真项目实撞：执行回执的 R 号集盖掉施工图的 → C06 假红 9 条
        card_refs[cid] = card_refs.get(cid, set()) | set(R_ID.findall(read_text(f)))
    referenced = set().union(*card_refs.values()) if card_refs else set()
    for cid, rs in sorted(card_refs.items()):
        for rid in sorted(rs - all_defs):
            rep.red("C05", rel(root, c.card_files[0].parent),
                    f"卡 {cid} 引用 {rid}，主规格与增量都没有（先写规格再写卡）")
    if card_refs:
        for rid in spec_ids:
            if rid not in referenced:
                rep.red("C06", rel(root, c.specs_dir),
                        f"{rid} 没有卡接住（切卡，或在 PRD 里明确它属未开工的 P1/P2 并从主规格移进增量）")
    else:
        rep.warn("C06", rel(root, c.root / "docs/pipeline/cards"),
                 "还没有任何卡片，卡接断言没燃料（⑤ 切卡后即生效）")

    # C07 勾与账
    status = state_card_status(c.state) if c.state else {}
    if c.state_err:
        rep.red("C07", "docs/pipeline/state.json", c.state_err)
    for cd in c.change_dirs:
        tp = cd / "tasks.md"
        if not tp.is_file():
            continue
        for ticked, tid, ln in task_items(read_text(tp)):
            if not status:
                rep.warn("C07", f"{rel(root, tp)}:{ln}", f"卡 {tid} 无从对账（没有 state.json 或里面没有卡）")
                continue
            st = status.get(tid)
            if st is None:
                rep.red("C07", f"{rel(root, tp)}:{ln}", f"tasks.md 列了卡 {tid}，state.json 查不到")
            elif ticked and st != "done":
                rep.red("C07", f"{rel(root, tp)}:{ln}", f"卡 {tid} 已打勾但 state.json 是 {st}（勾与账打架）")
            elif not ticked and st == "done":
                rep.red("C07", f"{rel(root, tp)}:{ln}", f"卡 {tid} 在 state.json 已 done 但 tasks.md 没勾")

    # C08 四件齐
    for cd in c.change_dirs:
        for need in ("proposal.md", "design.md", "tasks.md"):
            if not (cd / need).is_file():
                rep.red("C08", rel(root, cd), f"变更包缺 {need}")
        if not (cd / "specs").is_dir() or not any((cd / "specs").rglob("*.md")):
            rep.red("C08", rel(root, cd), "变更包缺 specs/ 增量（没有行为变化就别开变更包）")

    # C09 归档闭环
    if status:
        for cd in c.change_dirs:
            ids = {tid for _, tid, _ in task_items(read_text(cd / "tasks.md"))} if (cd / "tasks.md").is_file() else set()
            if ids and all(status.get(i) == "done" for i in ids):
                rep.red("C09", rel(root, cd),
                        f"卡全 done 却还挂在 changes/ ——增量并回主规格后移进 {ARCHIVE_NAME}/<日期>-{cd.name}/（细则 §4）")

    # C10 REMOVED/MODIFIED 必须指向存在的需求
    sid = set(spec_ids)
    for rid in removed_ids:
        if rid not in sid:
            rep.red("C10", rel(root, c.changes_dir), f"REMOVED 了主规格里不存在的 {rid}")
    for b in delta_blocks:
        if b["kind"] == "MODIFIED" and b["id"] not in sid:
            rep.red("C10", f"{rel(root, b['file'])}:{b['start']}", f"MODIFIED 了主规格里不存在的 {b['id']}")

    # C11 项目宪章
    if c.prd:
        ptxt = read_text(c.prd)
        m = re.search(r"^#{1,4}\s*(?:[\d一二三四五六七八九十]+[.、]\s*)?项目宪章\s*$", ptxt, re.M | re.I)
        if not m:
            rep.red("C11", rel(root, c.prd),
                    "PRD 没有「项目宪章」节（本项目的不可违背约束，铁律 21）")
        else:
            rest = ptxt[m.end():]
            nxt = re.search(r"^#{1,4}\s", rest, re.M)
            sect = rest[:nxt.start()] if nxt else rest
            items = [l for l in sect.splitlines() if re.match(r"^\s*(?:[-*]|\d+[.)])\s*\S", l)]
            if not 5 <= len(items) <= 8:
                rep.red("C11", rel(root, c.prd),
                        f"项目宪章 {len(items)} 条，不在 5–8 区间（少了盖不住决策，多了没人回看）")
            for it in items:
                if FUZZY.search(it):
                    rep.red("C11", rel(root, c.prd), f"宪章含模糊词，不可判定：{it.strip()[:44]}")
                elif not THRESHOLD.search(it):
                    rep.red("C11", rel(root, c.prd), f"宪章不含阈值（数字/单位/硬词），不可判定：{it.strip()[:44]}")
    else:
        rep.warn("C11", "docs/pipeline/PRD.md", "没找到 PRD，宪章与编号断言没燃料")

    # C12 裸占位 / C13 行号锚（扫规格层＋PRD＋模块地图；卡片允许带行号，见 阶段⑤ §5.1 第 3 节）
    scan = list(c.spec_files)
    for cd in c.change_dirs:
        scan.extend(sorted(cd.rglob("*.md")))
    if c.prd:
        scan.append(c.prd)
    if c.map_file.is_file():
        scan.append(c.map_file)
    for f in scan:
        for i, line in enumerate(read_text(f).splitlines(), 1):
            if BARE_PLACEHOLDER.match(line) and "经确认" not in line:
                rep.red("C12", f"{rel(root, f)}:{i}",
                        f"裸占位「{line.strip()}」——查过没有要写「经确认：无（日期，依据）」（铁律 20）")
            m = LINE_ANCHOR.search(line)
            if m:
                rep.red("C13", f"{rel(root, f)}:{i}",
                        f"行号锚点「{m.group(0)}」——只准写文件＋标识符（行号是快照必漂，铁律 16）")

    # C14 验收去向
    for b in spec_blocks:
        acc = [l for l in b["lines"] if re.match(r"^\s*[-*]?\s*验收\s*[：:]", l)]
        if not acc:
            rep.red("C14", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 没有「验收：」行（这条怎么算做完没人说）")
        elif not re.search(r"卡\s*[A-Z]?\d+|tests?[\\/]|test_\w+", acc[0]):
            rep.red("C14", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 的验收行没指向卡号或测试文件：{acc[0].strip()[:44]}")

    # C15 场景块（v1.19.0，OpenSpec 同款回流）：写了就必须粗体 WHEN/THEN 成对、条条是 bullet；
    # 零场景的需求不红——聚合记警当场点名（多分支行为建议逐条给场景，格式=细则 §3）
    no_scen_files = {}
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        if not b["scenarios"]:
            no_scen_files[b["file"]] = no_scen_files.get(b["file"], 0) + 1
            continue
        for sc in b["scenarios"]:
            kinds = set()
            for no, ln in zip(sc["nos"], sc["lines"]):
                s = ln.strip()
                if not s:
                    continue
                mb = SCEN_BULLET.match(s)
                if mb:
                    kinds.add(mb.group(1).lower())
                    continue
                rep.red("C15", f"{rel(root, b['file'])}:{no}",
                        f"{b['id']} 的场景「{sc['title'][:16]}」混入非 WHEN/AND/THEN 行：{s[:32]}")
            tip = "、".join(sorted(kinds)) if kinds else "空"
            if sc["nos"] and not ({"when", "then"} <= kinds):
                rep.red("C15", f"{rel(root, b['file'])}:{sc['nos'][0]}",
                        f"{b['id']} 的场景「{sc['title'][:16]}」WHEN/THEN 不成对（只有 {tip}）——粗体 **WHEN**/**THEN** 各至少一条")
    for f, cnt in no_scen_files.items():
        rep.warn("C15", rel(root, f),
                 f"该文件 {cnt} 条需求没写场景块（可选增强；多分支行为建议逐条给 WHEN/THEN，格式=细则 §3）")

    # C16 proposal 顶层三节锚（OpenSpec 同款回流；缺节记警不记红——结构自定义可容忍，但当场点名；
    # proposal 整件缺失归 C08 的红，这里不越位）
    for cd in c.change_dirs:
        pp = cd / "proposal.md"
        if not pp.is_file():
            continue
        missing = [name for name, rx in C16_ALIASES.items() if not rx.search(read_text(pp))]
        if missing:
            rep.warn("C16", rel(root, cd),
                     f"proposal 顶层缺 {'、'.join(missing)} 节（三节锚：为什么/改哪些需求/影响面，英文同义可）")

    # C17 追踪矩阵活性（v1.20.0）：矩阵是项目自己声明的"该读哪几个符号"，
    # 声明了就必须真——符号改名/测试搬走/卡号不存在 ⇒ 红（死指针比没有矩阵更坏：它会把人指引到空气里）。
    # 矩阵本身不存在只记警：这是可选增强，不许借此炸老项目。
    tm = load_trace_module()
    if tm is None:
        rep.warn("C17", "trace/matrix.json",
                 "兄弟脚本 trace_matrix.py 不在场，矩阵活性校验跳过（可选增强；缺件不阻断）")
    else:
        for level, where, msg in tm.lint_matrix(root):
            (rep.red if level == "红" else rep.warn)("C17", where, msg)
        # 生成件之间的时效链（外部评审指出的真空档）：AGENTS.md 的生成区是矩阵的投影，
        # 矩阵变了而生成区没重跑 ⇒ 两处同一信息已经分叉，却没有任何判据会响。
        ap = root / "AGENTS.md"
        mp = root / "trace" / "matrix.json"
        if ap.is_file() and mp.is_file():
            body = read_text(ap)
            m = re.search(r"矩阵快照 ([0-9a-f]{8,})", body)
            if m:
                cur = hashlib.md5(mp.read_bytes()).hexdigest()
                if not cur.startswith(m.group(1)):
                    rep.warn("C17", "AGENTS.md",
                             f"生成区记的矩阵快照是 {m.group(1)}，当前矩阵已是 {cur[:12]}"
                             f"——重跑 scripts/render_agents.py --apply")

    # C18 现状面声称计数（v1.20.0）：手写「26 条／14 条断言／11 张卡」这类数字与实测不一致就点名。
    # 只扫"现状声明面"，历史报告与 CHANGELOG 里的旧数字是**证词**，追改即造假；
    # 记警不记红——数字口径的最终判定权归人（铁律 20 第②条同款）。
    meas = measured_counts(root, c, spec_blocks, delta_blocks)
    for scope in CLAIM_SCOPES:
        f = root / scope
        if not f.is_file():
            continue
        seen = set()
        for i, line in enumerate(read_text(f).splitlines(), 1):
            clean = QUOTED.sub(" ", line)   # 引号内是历史读数的引用，不算现状声称
            if HIST_MARK.search(clean):
                continue                    # 显式证词行整行豁免
            for name, rx in CLAIM_RULES:
                m = rx.search(clean)
                if not m or name in seen:
                    continue
                got = meas.get(name, 0)
                if not got:
                    continue          # 这项没有实测对象（如项目没走查清单）⇒ 不查，避免凭空造分母
                claimed = int(m.group(1))
                if claimed == got:
                    continue
                seen.add(name)
                rep.warn("C18", f"{rel(root, f)}:{i}",
                         f"声称「{m.group(0)}」＝{claimed}，实测 {got}——现状面数字过期"
                         f"（历史证词区不追改，只改现状面）")

    # C19 状态源唯一（v1.20.0）：三处状态并存的病根是"机器认不出哪份是生成物"。
    # 归档后再冒出活台账、视图没有 AUTO 标记、另有一份手写 STATE.md ⇒ 点名（警级：不阻断老项目）。
    dp = root / "docs" / "pipeline"
    if dp.is_dir():
        archived = sorted(dp.glob("台账_归档_*.md"))
        if archived and (dp / "台账.md").is_file():
            rep.warn("C19", "docs/pipeline",
                     f"归档后台账又出现活台账（{len(archived)} 份 台账_归档_* ＋ 1 份 台账.md）——两份会各写各的")
        for v in (dp / "台账.md", dp / "任务板.md"):
            if v.is_file() and "AUTO" not in read_text(v):
                rep.warn("C19", rel(root, v), "视图里没有 AUTO 标记，生成物与手写物机器认不出（禁手改落不了地）")
        if (root / "STATE.md").is_file() and (dp / "state.json").is_file():
            rep.warn("C19", "STATE.md", "手写 STATE.md 与 state.json 并存——并账后废弃，或改成一行指针")
    return rep


# ---------------------------------------------------------------- 命令

def cmd_check(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    rep = run_checks(root)
    reds = [i for i in rep.items if i[1] == "红"]
    warns = [i for i in rep.items if i[1] == "警"]
    print(f"[spec_check v{VERSION}] root={root}")
    for code, level, where, msg in rep.items:
        print(f"[{level}] {code} {CHECK_TITLES[code]}｜{where}｜{msg}")
    if not rep.items:
        print(f"[全绿] {len(CHECKS)} 条断言零红零警")
    print(f"[汇总] 红 {len(reds)}｜警 {len(warns)}｜翻红断言 {len({i[0] for i in reds})}/{len(CHECKS)}")
    if reds or (args.strict and warns):
        return 1
    return 0


# ---------------------------------------------------------------- 夹具（selftest 用的最小合法项目）

GOOD_SPEC = """# 主规格 · 预约 booking

> 系统当前行为的唯一真相源；范围与优先级看 PRD，进度看 state.json。

#### R-1 | 时间段必须落在允许窗口内

系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。

- 状态：生效
- 验收：卡B1 tests/test_rules.py

#### 场景：超出营业时段

- **WHEN** 提交一个开始时刻早于 08:00 的预约
- **THEN** 返回参数错误并标明允许窗口

#### R-3 | 会议已开始不得取消

若预约的开始时刻早于当前时刻，则系统应拒绝该预约的取消请求。

- 状态：生效
- 验收：卡B3 tests/test_cancel.py

#### 场景：对已开始的会议提交取消

- **WHEN** 对开始时刻早于当前时刻的预约提交取消
- **THEN** 系统应拒绝并说明"会议已开始"
"""

GOOD_PRD = """# PRD · 校验夹具

### 项目宪章

1. 现场演示必须零外网依赖：断网时启动命令数量不得变化。
2. 运行时依赖的许可证只允许 MIT 或 BSD 系，禁止引入 GPL 依赖。
3. 错误反馈必须是中文人话，且不得出现堆栈信息。
4. 页面刷新与服务重启后，会议室与预约记录必须仍可从 SQLite 读到。
5. 启动命令必须只有 1 条，端口固定 8000。
6. 任何时间判断必须由后端单一函数给出，禁止第二处重复实现。
7. 参会人数必须是 1 至所选会议室容量之间的整数。
8. 参会人数不得超过所选会议室容量。

### 核心功能列表

- 时区策略：经确认：无（2026-10-15，依据：全项目只用本地时间，未见 UTC 转换）
"""

GOOD_CARD1 = """# 卡 B1：预约校验

- 需求号：R-1
- 前置依赖：无
- 验收：pytest tests/test_rules.py 全绿
"""

GOOD_CARD3 = """# 卡 B3：取消预约

- 需求号：R-3
- 验收：pytest tests/test_cancel.py 全绿
"""

GOOD_STATE = '{"rev": 1, "cards": {"B1": {"state": "done"}, "B3": {"state": "doing"}}}'

GOOD_PROPOSAL = """# 提案 add-cancel

## 为什么
规则五要求已开始不能取消。

## 改哪些需求
- R-3 R-5 R-1

## 影响面
specs/booking 的 R-3 改、R-5 加、R-1 删；施工卡 B1/B3；无数据库结构变化。
"""

GOOD_DESIGN = """# 设计 add-cancel

技术方案：进行中/已结束按当前时间派生，不入库。引用 docs/pipeline/技术选型记录.md 与模块地图。
"""

GOOD_TASKS = """# 任务清单 add-cancel

- [x] B1
- [ ] B3
"""

GOOD_DELTA = """# 增量 booking

## ADDED Requirements

#### R-5 | 取消后时间段释放

当一次预约被取消时，系统应将该时间段对后续预约重新开放。

- 状态：生效
- 验收：卡B3 tests/test_cancel.py

#### 场景：取消后重开时段

- **WHEN** 取消一条 09:00–10:00 的预约
- **THEN** 对同一会议室同一时段的新预约应成功

## MODIFIED Requirements

#### R-3 | 会议已开始不得取消

若预约的开始时刻早于当前时刻，则系统应拒绝该预约的取消请求。

- 状态：生效
- 验收：卡B3 tests/test_cancel.py

#### 场景：已开始会议仍拒绝取消

- **WHEN** 对开始时刻早于当前时刻的预约提交取消
- **THEN** 返回 400 并说明"会议已开始"

## REMOVED Requirements

### R-1
"""


def cmd_selftest(args):
    """造合法树 → 全绿；再逐条做坏 → 每条只准点名的那根断言翻红；最后证明判据不是恒真。"""
    home = Path(__file__).resolve().parent
    skill_root = home.parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else skill_root / "_tmp-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}

    results = []
    seq = [0]

    def mk_tree():
        seq[0] += 1
        root = tmp_root / f"p{seq[0]}"
        (root / "specs" / "booking").mkdir(parents=True)
        (root / "docs" / "pipeline" / "cards").mkdir(parents=True)
        (root / "tests").mkdir(parents=True)
        (root / "changes" / "add-cancel" / "specs" / "booking").mkdir(parents=True)
        (root / "specs" / "booking" / "spec.md").write_text(GOOD_SPEC, encoding="utf-8")
        (root / "docs" / "pipeline" / "PRD.md").write_text(GOOD_PRD, encoding="utf-8")
        (root / "docs" / "pipeline" / "cards" / "卡B1_预约校验.md").write_text(GOOD_CARD1, encoding="utf-8")
        (root / "docs" / "pipeline" / "cards" / "卡B3_取消预约.md").write_text(GOOD_CARD3, encoding="utf-8")
        (root / "docs" / "pipeline" / "state.json").write_text(GOOD_STATE, encoding="utf-8")
        (root / "tests" / "test_rules.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (root / "tests" / "test_cancel.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        cd = root / "changes" / "add-cancel"
        (cd / "proposal.md").write_text(GOOD_PROPOSAL, encoding="utf-8")
        (cd / "design.md").write_text(GOOD_DESIGN, encoding="utf-8")
        (cd / "tasks.md").write_text(GOOD_TASKS, encoding="utf-8")
        (cd / "specs" / "booking" / "spec.md").write_text(GOOD_DELTA, encoding="utf-8")
        # C17 的燃料：合法树自带一份新鲜矩阵（缺兄弟脚本时跳过——C17 自己会记警，不许把夹具搞成必炸）
        _tm = load_trace_module()
        if _tm is not None:
            (root / "trace").mkdir(exist_ok=True)
            (root / "trace" / "matrix.json").write_text(
                json.dumps(_tm.gen(root), ensure_ascii=False, indent=2), encoding="utf-8")
        return root

    def spec_of(root):
        return root / "specs" / "booking" / "spec.md"

    def patch(path, old, new):
        path.write_text(read_text(path).replace(old, new), encoding="utf-8")

    def case(desc, expect, mutate):
        """做一次坏 → 点名的那根断言必须红（其余跟着红＝诚实涟漪，不算失败）。
        反恒真靠 S00＋S18：合法树必须零红零警，否则「永远红」的坏尺子会把这些用例全蒙对。"""
        root = mk_tree()
        mutate(root)
        rep = run_checks(root)
        reds = rep.red_codes()
        ok = expect in reds
        results.append((desc, ok, f"期望红=[{expect}] 实际红={sorted(reds)}"))
        return root

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    # —— 地基：合法树必须全绿，否则后面全是假绿
    good = mk_tree()
    rep0 = run_checks(good)
    green("S00 合法树全绿（红 0 警 0）", not rep0.items,
          f"实际={[(i[0], i[1], i[3][:60]) for i in rep0.items]}")

    # —— 14 条断言逐条能红
    case("S01 C01 主规格目录整棵缺失", "C01", lambda r: shutil.rmtree(r / "specs"))
    case("S02 C02 验收指向的测试文件没了", "C02",
         lambda r: patch(spec_of(r), "tests/test_rules.py", "tests/gone.py"))
    case("S03 C03 需求写成散文", "C03",
         lambda r: patch(spec_of(r), "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。",
                         "预约时间要在上班时间内，别太离谱。"))
    case("S04 C03 一个块塞两句断言（跨型才可判定，细则 §3 边界 1 同款）", "C03",
         lambda r: patch(spec_of(r), "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。\n",
                         "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。\n当用户重复提交同一预约时，系统应返回冲突提示。\n"))
    case("S05 C04 主规格重复定义同一号", "C04",
         lambda r: spec_of(r).write_text(read_text(spec_of(r)) +
                "\n#### R-3 | 又一个取消规则\n\n系统应在用户点击取消时提示确认。\n\n- 验收：卡B3\n", encoding="utf-8"))
    case("S06 C05 卡引用了不存在的需求号", "C05",
         lambda r: patch(r / "docs/pipeline/cards/卡B3_取消预约.md", "R-3", "R-99"))
    case("S07 C06 需求没有卡接", "C06",
         lambda r: patch(r / "docs/pipeline/cards/卡B3_取消预约.md", "R-3", "R-1"))
    case("S08 C07 勾了但账上没 done", "C07",
         lambda r: (r / "docs/pipeline/state.json").write_text(
             '{"cards": {"B1": {"state": "doing"}, "B3": {"state": "doing"}}}', encoding="utf-8"))
    case("S09 C08 变更包缺 design.md", "C08",
         lambda r: (r / "changes/add-cancel/design.md").unlink())
    case("S10 C09 卡全 done 却未归档", "C09",
         lambda r: (r / "docs/pipeline/state.json").write_text(
             '{"cards": {"B1": {"state": "done"}, "B3": {"state": "done"}}}', encoding="utf-8"))
    case("S11 C10 REMOVED 指向不存在的号", "C10",
         lambda r: patch(r / "changes/add-cancel/specs/booking/spec.md", "### R-1", "### R-77"))
    case("S12 C11 宪章含模糊词", "C11",
         lambda r: patch(r / "docs/pipeline/PRD.md",
                         "3. 错误反馈必须是中文人话，且不得出现堆栈信息。", "3. 错误提示要尽量友好。"))
    case("S13 C11 宪章只剩 4 条", "C11",
         lambda r: (r / "docs/pipeline/PRD.md").write_text(
             "# PRD\n\n### 项目宪章\n\n1. 端口固定 8000。\n2. 禁止引入 GPL 依赖。\n3. 必须中文人话报错。\n4. 启动命令只有 1 条。\n\n### 功能\n\n- x\n",
             encoding="utf-8"))
    case("S14 C11 宪章节整段缺失", "C11",
         lambda r: patch(r / "docs/pipeline/PRD.md", "### 项目宪章", "### 项目说明"))
    case("S15 C12 裸占位「无」", "C12",
         lambda r: spec_of(r).write_text(read_text(spec_of(r)) + "\n- 备注：\n  无\n", encoding="utf-8"))
    case("S16 C13 规格里写行号锚点", "C13",
         lambda r: spec_of(r).write_text(read_text(spec_of(r)) +
                "\n- 关键定位：api/auth.py:88 的 login()\n", encoding="utf-8"))
    case("S17 C14 需求没有验收去向", "C14",
         lambda r: patch(spec_of(r), "- 验收：卡B3 tests/test_cancel.py\n", "- 状态：生效\n"))

    # —— C15/C16（v1.19.0）：能红、能警、能不误伤
    case("S30 C15 场景缺 THEN（不成对）", "C15",
         lambda r: patch(spec_of(r), "- **THEN** 返回参数错误并标明允许窗口\n", ""))
    case("S31 C15 场景里混入散文行", "C15",
         lambda r: patch(spec_of(r), "#### 场景：超出营业时段\n",
                         "#### 场景：超出营业时段\n\n先看看现在几点。\n"))
    prop = mk_tree()
    patch(prop / "changes/add-cancel/proposal.md", "## 影响面", "## 波及面")
    repp = run_checks(prop)
    green("S34 C16 proposal 缺影响面节记警不记红（恰一条警）",
          not repp.red_codes() and sum(1 for i in repp.items if i[1] == "警" and i[0] == "C16") == 1,
          f"红={sorted(repp.red_codes())} 警={[(i[0], i[3][:40]) for i in repp.items if i[1] == '警']}")
    case("S36 C15 增量 ADDED 场景缺 THEN", "C15",
         lambda r: patch(r / "changes/add-cancel/specs/booking/spec.md",
                         "- **THEN** 对同一会议室同一时段的新预约应成功\n", ""))

    # —— C17–C19（v1.20.0）：能红、能警、能不误伤
    stl = mk_tree()
    mp = stl / "trace" / "matrix.json"
    mm = json.loads(mp.read_text(encoding="utf-8"))
    mm["requirements"]["R-1"]["code"].append({"file": "specs/booking/spec.md", "symbol": "查无此符号xyz"})
    mp.write_text(json.dumps(mm, ensure_ascii=False), encoding="utf-8")
    rep37 = run_checks(stl)
    green("S37 C17 矩阵里的符号是死指针 ⇒ 红",
          "C17" in rep37.red_codes(),
          f"红={[(i[0], i[3][:52]) for i in rep37.items if i[1] == '红']}")

    nom = mk_tree()
    (nom / "trace" / "matrix.json").unlink()
    rep38 = run_checks(nom)
    green("S38 C17 没有矩阵 ⇒ 记警不记红（可选增强，不许炸老项目）",
          "C17" not in rep38.red_codes()
          and sum(1 for i in rep38.items if i[1] == "警" and i[0] == "C17") == 1,
          f"红={sorted(rep38.red_codes())} 警={[(i[0], i[3][:36]) for i in rep38.items if i[1] == '警']}")

    WALK_SECT = "## 用户视角走查清单\n\n1. 打开首页。\n2. 建一场。\n3. 取消一场。\n\n### 核心功能列表"
    cl = mk_tree()
    patch(cl / "docs/pipeline/PRD.md", "### 核心功能列表", WALK_SECT)
    (cl / "AGENTS.md").write_text("# AGENTS\n\n走查清单 9 条逐条真点。\n", encoding="utf-8")
    rep39 = run_checks(cl)
    green("S39 C18 声称走查 9 条而实测 3 条 ⇒ 记警并点名 AGENTS.md",
          sum(1 for i in rep39.items if i[1] == "警" and i[0] == "C18") == 1
          and any("AGENTS.md" in i[2] for i in rep39.items if i[0] == "C18"),
          f"警={[(i[0], i[2], i[3][:52]) for i in rep39.items if i[1] == '警']}")

    ok18 = mk_tree()
    patch(ok18 / "docs/pipeline/PRD.md", "### 核心功能列表", WALK_SECT)
    (ok18 / "AGENTS.md").write_text("# AGENTS\n\n走查清单 3 条逐条真点。\n", encoding="utf-8")
    rep40 = run_checks(ok18)
    green("S40 C18 哨兵：声称 3 条＝实测 3 条，不报警",
          not [i for i in rep40.items if i[0] == "C18"],
          f"实际={[(i[0], i[3][:44]) for i in rep40.items if i[0] == 'C18']}")

    st19 = mk_tree()
    dp19 = st19 / "docs" / "pipeline"
    (dp19 / "台账_归档_20261017.md").write_text("# 台账（归档）\n\n<!-- AUTO -->\n", encoding="utf-8")
    (dp19 / "台账.md").write_text("# 台账\n\n<!-- AUTO -->\n", encoding="utf-8")
    rep41 = run_checks(st19)
    green("S41 C19 归档后台账又冒出活台账 ⇒ 记警",
          any(i[0] == "C19" for i in rep41.items if i[1] == "警"),
          f"警={[(i[0], i[3][:52]) for i in rep41.items if i[1] == '警']}")

    # —— v1.25.0 S42~S45：C03 三面警（同型多句/模糊断言/超长句）＋合法哨兵
    st42 = mk_tree()
    sz42 = spec_of(st42)
    patch(sz42, "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。",
          "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。\n"
          "系统应尽量高效地处理预约请求。")
    rep42 = run_checks(st42)
    green("S42 C03 断言含模糊词 ⇒ 记警（IEEE 29148 无歧义面，v1.25.0）",
          any(i[0] == "C03" and i[1] == "警" for i in rep42.items),
          f"警={[(i[3][:52]) for i in rep42.items if i[0] == 'C03' and i[1] == '警']}")
    st43 = mk_tree()
    sz43 = spec_of(st43)
    patch(sz43, "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。",
          "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。\n"
          "系统应在预约缺少联系人时拒绝该次预约。")
    rep43 = run_checks(st43)
    green("S43 C03 同型断言连写两句 ⇒ 记警（跨型才判红，同型点名叫人工拆）",
          any(i[0] == "C03" and i[1] == "警" for i in rep43.items),
          f"警={[(i[3][:52]) for i in rep43.items if i[0] == 'C03' and i[1] == '警']}")
    st44 = mk_tree()
    sz44 = spec_of(st44)
    patch(sz44, "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。",
          "系统应在预约时间落在 08:00 至 20:00 之外且该会议室当日已完成预约数量已经达到管理员在系统设置中配置的每日上限"
          "且提交预约的用户不存在于系统黑名单且该时段不存在等待审批的其他冲突申请且当前系统处于维护窗口期之外时，"
          "拒绝该次预约并返回完整原因。")
    rep44 = run_checks(st44)
    green("S44 C03 断言超长（>120 字）⇒ 记警（判型力衰减点名）",
          any(i[0] == "C03" and i[1] == "警" for i in rep44.items),
          f"警={[(i[3][:52]) for i in rep44.items if i[0] == 'C03' and i[1] == '警']}")
    st45 = mk_tree()
    rep45 = run_checks(st45)
    green("S45 C03 哨兵：合法单句断言（含「必须」标题）不触发三面警",
          not [i for i in rep45.items if i[0] == "C03" and i[1] == "警"],
          f"警={[(i[3][:52]) for i in rep45.items if i[0] == 'C03' and i[1] == '警']}")

    zero = mk_tree()
    sz = spec_of(zero)
    patch(sz, """#### 场景：超出营业时段

- **WHEN** 提交一个开始时刻早于 08:00 的预约
- **THEN** 返回参数错误并标明允许窗口

""", "")
    repz = run_checks(zero)
    green("S32 C15 零场景记警不记红（整文件恰一条警）",
          not repz.red_codes() and sum(1 for i in repz.items if i[1] == "警" and i[0] == "C15") == 1,
          f"红={sorted(repz.red_codes())} 警={[(i[0], i[3][:40]) for i in repz.items if i[1] == '警']}")
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc_w0 = cmd_check(argparse.Namespace(root=str(zero), strict=False))
        rc_w1 = cmd_check(argparse.Namespace(root=str(zero), strict=True))
    green("S35 真出口：仅警的树非 strict 退 0、strict 退 1", rc_w0 == 0 and rc_w1 == 1,
          f"退码={rc_w0}/{rc_w1}")
    en_sent = mk_tree()
    patch(spec_of(en_sent), "- **THEN** 返回参数错误并标明允许窗口",
          "- **THEN** reject when the start is outside the window and the system shall show the allowed hours")
    repE = run_checks(en_sent)
    green("S33 哨兵：场景英文长 bullet（when…system shall）不误触 C03 双型", not repE.red_codes(),
          f"红={[(i[0], i[3][:50]) for i in repE.items if i[1] == '红']}")

    # —— 哨兵：合法写法不许误报（判据太宽＝没人再信它）
    sent = mk_tree()
    sent_spec = spec_of(sent)
    patch(sent_spec, "- 备注：\n", "")
    sent_spec.write_text(read_text(sent_spec) +
        "\n#### R-9 | 英文增量与合法交代\n\n"
        "The system shall reject a booking whose duration exceeds 4 hours.\n\n"
        "- 备注：经确认：无（2026-10-15，依据：grep requirements.txt 未命中网络库）\n"
        "- 关键定位：api/auth.py 的 login()\n"
        "- 验收：卡B3\n", encoding="utf-8")
    patch(sent / "docs/pipeline/cards/卡B3_取消预约.md", "R-3", "R-3 R-9")
    reps = run_checks(sent)
    green("S18 哨兵：经确认无＋文件＋标识符＋英文 EARS 不误报", not reps.red_codes(),
          f"红={[(i[0], i[3][:50]) for i in reps.items if i[1] == '红']}")

    # —— 反查：尺子不是恒真（散文必须判不出句式）
    green("S19 反查：散文喂进 ears_types 返回空", ears_types("预约时间要在上班时间内，别太离谱。") == [],
          f"实际={ears_types('预约时间要在上班时间内，别太离谱。')}")
    five = ["系统应记录每一次预约。",
            "当用户提交预约时，系统应校验时间段。",
            "在会议进行期间，系统应锁定该会议室。",
            "若启用演示模式，系统应读取配置里的时间偏移。",
            "若时间段已被占用，则系统应返回冲突提示。"]
    counts = [ears_types(s) for s in five]
    names = [c[0] if len(c) == 1 else "×".join(c) or "空" for c in counts]
    green("S20 反查：EARS 五型各命中且只命中 1 次", all(len(c) == 1 for c in counts), f"counts={counts}")
    green("S21 反查：五种型名互不串台（命中集合恰为五个不同型）",
          len({n for n in names}) == 5, f"types={names}")

    # —— 真出口：退出码 0/1/2，不是函数返回值
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_good = cmd_check(argparse.Namespace(root=str(good), strict=False))
    green("S22 真出口：合法树 cmd_check 退 0 且打「全绿」", rc_good == 0 and "[全绿]" in buf.getvalue(),
          f"退码={rc_good}")
    broken = mk_tree()
    patch(spec_of(broken), "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。", "随便写点什么。")
    bad_buf = io.StringIO()
    with contextlib.redirect_stdout(bad_buf):
        rc_bad = cmd_check(argparse.Namespace(root=str(broken), strict=False))
        rc_bad2 = cmd_check(argparse.Namespace(root=str(broken), strict=True))
    green("S23 真出口：做坏树 cmd_check 退 1（strict 与否都 1）", rc_bad == 1 and rc_bad2 == 1,
          f"实际={rc_bad}/{rc_bad2}")
    with contextlib.redirect_stdout(io.StringIO()):
        rc_noroot = cmd_check(argparse.Namespace(root=str(tmp_root / "不存在的项目"), strict=False))
    green("S24 真出口：根目录不存在退 2（不许当成全绿）", rc_noroot == 2, f"实际={rc_noroot}")
    # 输出面：先要正例（红行非空），再逐条判形状，最后带一条畸形行反证判据不是恒真
    red_lines = [ln for ln in bad_buf.getvalue().splitlines() if ln.startswith("[红]")]
    shaped = all(OUT_SHAPE.match(ln) for ln in red_lines)
    green("S25 输出面：红行非空＋每条＝「C 号 标题｜位置｜人话」＋至少一条带 文件:行",
          bool(red_lines) and shaped and any(re.search(r"｜\S+\.md:\d+｜", ln) for ln in red_lines),
          f"红行数={len(red_lines)} 首行={red_lines[0] if red_lines else '（空）'}")
    green("S25b 反查：S25 的形状判据能否掉畸形行（缺 C 号／缺人话段都不算过）",
          not OUT_SHAPE.match("[红] 没有编号 缺字段") and not OUT_SHAPE.match("[红] C99 标题｜位置｜短"),
          f"两条畸形行均被判否")

    # —— 非破坏：selftest 不许碰真实库
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S26 scripts/ 目录零写入（兄弟文件 mtime 全等）", scripts_before == scripts_after,
          f"变化={sorted(set(scripts_before) ^ set(scripts_after))}")
    green("S27 临时树落在指定 tmp-root，不在 scripts/ 里", home not in tmp_root.parents and tmp_root.is_dir(),
          f"tmp_root={tmp_root}")

    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S28 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")
    # 本条自己此刻还没进 results，所以分母 +1
    green(f"S29 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 1 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 1} 条")

    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[spec_check selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


# ---------------------------------------------------------------- 入口

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="交付流水线规格层机械校验器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("check", help="检查一个项目的规格层")
    a1.add_argument("--root", required=True)
    a1.add_argument("--strict", action="store_true", help="把警当红")
    a1.set_defaults(func=cmd_check)
    a2 = sub.add_parser("selftest", help="红例自证（只写临时树）")
    a2.add_argument("--tmp-root", default=None, help="临时树位置（默认 <skill根>/_tmp-selftest）")
    a2.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
