#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规格层机械校验器（spec_check）——EverSpec（常新规格）的"能红的尺"。

只读不改：检查 specs/（主规格）＋ changes/（变更包）＋ PRD/工作项/状态账（有则查、无则降警）
之间咬合得上不上，30 条具名断言 C01–C30（C15/C16 = OpenSpec 实测回流：
需求块场景 WHEN/THEN 成对、proposal 顶层三节锚；C17–C19 = 追踪面实测回流：
追踪矩阵活性、现状面声称计数、状态源唯一；C20 = 阈值三件套·规格侧（⑤）：
验收行的阈值必须带测量命令或测试引用，缺则记警；C21 = 反提件禁入规格层（⑪）：
带「现状描述（反提）」标记的需求块混进 specs/ 即红——细则 §3.5；
C22 = 本件文档面断言计数一致（v1.5.0 热身卡）：SKILL.md/细则自称的「N 条断言」
与「C01–Cxx 只追加不顺延」必须与 CHECKS 实测一致，漂移即红，selftest S60 绑进自证；
C23/C24 = 文档状态机＋TBD 有效至（v1.8.0，K04 · MUST-06/13 地基）：
状态值收口四态（草案/生效/已取代/废弃，写了就查不写不红）、TBD 占位必须同块带
「有效至：YYYY-MM-DD」（到期判红归 C29 收口断言）——细则 §3；
C25–C28 = 变更溯源扩容（v1.9.0，K09 · MUST-12）：C08 零增量逃生口（proposal 声明
Skip-Specs 才放行空包，对标 OpenSpec skip_specs）＋C25 声明与事实矛盾红＋
C26 增量 ADDED/MODIFIED 块必带「来源：」行（provenance，缺＝「这条哪来的」无人能答）＋
C27 登记待决问题的块必须冻结在草案（歧义没解不许转生效）＋C28 proposal 写了
Capabilities 节则所列 specs/<能力> 必须真在盘（警；近重复能力名不机检——细则 §4）；
C29 = TBD 有效至过期（v1.10.0，K10 · MUST-13 收口）：带「有效至」的 TBD 过了到期日
还在赖 ⇒ 红——C24 管形状（有没有有效至行）、C29 管时效（日期过没过）——细则 §3。
C30 = supersede 双向咬合（v1.11.0，K12 · MUST-15/04）：需求块级「取代：/被取代：」必须成对
回指——写单边、对侧块不在场、对侧不回指、自指都红（双向缺一即红）；文档级路径指针归
doc_lint D02–D04，两尺按指针值形态分工（细则 §3 supersede 约定与 §6.8 分工）。
C21 扩容（v1.12.0，K11 · MUST-14）：扫描面首纳 reverse/——推断件治理三面＝缺「关键定位」
证据坐标红＋带行号锚红＋需求块来源指向的 reverse 草案缺「签收：」行（未签收转正）红，
扩容非新增（原「带反提标记禁入规格层」面不动）——细则 §3.5 逆向升格四步。
C31 锚点分级（v1.19.0，v3-K18）：锚点:v1 自标记需求块的关键定位必须为仓库内链接且目标
在场——链接化后文件改名/删除机械必红；存量缺省 v0 不查不红（迁移三步细则 §3 锚点分级）。
细则见 references/规格层规范.md。
无管线模式（项目里没有 docs/pipeline/）自动降警不拦人——selftest 哨兵 S-NP 钉死（v1.3.0 起）。
本件由交付流水线同名脚本 v1.2.0 于 2026-10-27 提取，自本版起在本 skill 内独立演化
（v1.4.0 起判据在本件追加，管线内嵌快照冻结不动）。

    py scripts/spec_check.py check --root <项目根> [--strict] [--json]   # --json＝stdout 单行机读 JSON（细则 §6.7）
    py scripts/spec_check.py selftest [--tmp-root <目录>]   # 红例自证，只写临时树

退出码：0 全绿（可含警）｜1 有红（--strict 时含警）｜2 根目录/用法不对
每次 check 向本件 evidence/runs.log 追加一行运行读数（MUST-17；目标项目零写入，细则 §6.7）。
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
import tempfile
from datetime import datetime
from pathlib import Path

VERSION = "1.13.0"

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
    ("C14", "每条需求有验收去向（卡号、测试文件或走查条目）"),
    ("C15", "场景块：写了必须 WHEN/THEN 粗体成对（零场景文件记警不记红）"),
    ("C16", "proposal 顶层三节锚齐（为什么/改哪些需求/影响面）"),
    ("C17", "追踪矩阵活性（trace/matrix.json 里的符号/测试/卡必须真在盘）"),
    ("C18", "现状面声称计数与实测一致（需求号上限/断言数/走查条数）"),
    ("C19", "状态源唯一（归档后台账不得复活、视图必须带 AUTO 标记）"),
    ("C20", "验收行的阈值必须带测量命令或测试引用（阈值三件套·规格侧）"),
    ("C21", "反提件治理（带标记禁入规格层；推断件必带证据坐标；未签收不转正）"),
    ("C22", "本件文档面断言计数与实测一致（条数＋冻结区间）"),
    ("C23", "状态字段值合法（草案/生效/已取代/废弃；写了就查，不写不红）"),
    ("C24", "TBD 占位必须同块带有效至（没有到期日的 TBD＝永远赖账）"),
    ("C25", "skip 声明与事实矛盾（声明零增量却带着 specs/ 增量＝声明是幌子）"),
    ("C26", "增量 ADDED/MODIFIED 块必带来源行（provenance：每条款带出处，缺＝溯源断链）"),
    ("C27", "登记待决问题的块必须冻结在草案（歧义没解不许转生效）"),
    ("C28", "proposal 写了 Capabilities 节：所列 specs/<能力> 必须真在盘（警；没写不查）"),
    ("C29", "TBD 有效至过期（到期日一过还在 TBD＝赖账坐实）"),
    ("C30", "supersede 双向咬合（取代/被取代必须成对回指，双向缺一即红）"),
    ("C31", "锚点:v1 的关键定位必须为仓库内链接且目标在场（锚点分级，存量 v0 不查）"),
]
CHECK_TITLES = dict(CHECKS)
ASSERT_FLOOR = 117   # selftest 断言分母闸：条数掉了就该红，防止有人悄悄删用例（v1.19.0 随 v3-K18 C31 锚点分级＋变异臂同升）
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

META_PREFIX = re.compile(r"^\s*[-*]?\s*(状态|验收|关联|来源|备注|优先级|负责人|关键定位|取代|被取代|有效至|待决问题|锚点)\s*[：:]")
# v1.8.0（K04 · MUST-06/13 地基，细则 §3）：文档状态机＋TBD 有效至。状态值收口四态——值不收口＝
# 「已完成/生效中/上线」各写各的，机器认不出承诺面；不写状态行不红（字段是增强约定，存量项目零冲击）。
# TBD 形状＝词边界 TBD（防误伤中文散文）；有效至形状＝ISO 日期，到期判红归 MUST-13 收口断言。
# 🔴 各占一行：变异探针臂（S72/S73/S74）按行首锚点整行替换，拆行=探针失锚。
# v1.9.0（K09）：前缀 11→12——收编「待决问题」（open-questions 同义词）进元信息：登记行不是断言，
# 不该进 C03 句式校验；它的机检面由 C27（歧义冻结）接管（细则 §4）。
STATE_LINE = re.compile(r"^\s*[-*]?\s*状态\s*[：:]\s*(.+?)\s*$")
STATE_LINE = re.compile(r"^\s*[-*]?\s*状态\s*[：:]\s*(.+?)\s*$")
STATE_ALLOWED = ("草案", "生效", "已取代", "废弃")
TBD_MARK = re.compile(r"\bTBD\b")
TTL_SHAPE = re.compile(r"^\s*[-*]?\s*有效至\s*[：:]\s*(\d{4}-\d{2}-\d{2})\s*$")
# v1.10.0（K10 · MUST-13 收口，细则 §3）：TBD 有效至过期判红——有效至日期早于今天 ⇒ C29 红，
# 今天及以后不红；形状过但日期值非法（如 2027-13-45）不咬（不越权，值合法性后续只追加）。
# 🔴 过期判定单点在此：变异探针臂（S91）按行首锚点「def _ttl_expired」整行替换，拆行=探针失锚。
# ⚠️ def 行必须全源码唯一——返回注解不可省（否则与 S91 调用处的替换文本字面量互为子串，replace 砍断字面量＝变异源码语法炸）。
def _ttl_expired(iso_date: str) -> bool:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").date() < datetime.now().date()
    except ValueError:
        return False
# v1.11.0（K12 · MUST-15/04，细则 §3）：需求块级 supersede 双向咬合。「取代：R-x」＝本块替代了它、
# 「被取代：R-x」＝本块被它替代——机检双向缺一即红（单边/对侧不在场/自指 ⇒ C30 红）；路径形态指针
# 是文档级、归 doc_lint D02–D04/D16，两尺按指针值形态分工互斥（细则 §6.8）。K04 已把两前缀收进
# META_PREFIX（解析层身份），本版在其上实现断言，未另造前缀体系。
# 🔴 两条各占一行：变异探针臂（S97/S98）按行首锚点整行替换，拆行=探针失锚。
SUP_TAKE = re.compile(r"^\s*[-*]?\s*取代\s*[：:]\s*(.+?)\s*$")
SUP_BY = re.compile(r"^\s*[-*]?\s*被取代\s*[：:]\s*(.+?)\s*$")
# v1.9.0（K09 · MUST-12 变更可溯源，细则 §4）：🔴 四条各占一行：变异探针臂（S85–S88）按行首锚点
# 整行替换，拆行=探针失锚。SKIP_MARK＝零增量逃生口声明（节标题或 =true 行都算显式声明，大小写不敏感）；
# SOURCE_LINE＝溯源行（冒号后要有内容，裸「无/暂无」不算溯源）；OPENQ_MARK＝待决问题登记行；
# CAPAB_HEAD＝proposal 的 Capabilities 节标头（group(1) 捕获井号个数，节边界按同级/更浅标头动态切）。
SKIP_MARK = re.compile(r"^#{1,4}\s*skip[-_ ]?specs\s*$|^\s*[-*]?\s*skip[-_ ]?specs\s*[：:]\s*(?:true|真|是)\s*$", re.I | re.M)
SOURCE_LINE = re.compile(r"^\s*[-*]?\s*来源\s*[：:]\s*(?!(?:无|暂无)\s*$)\S")
OPENQ_MARK = re.compile(r"^\s*[-*]?\s*(?:待决问题|open[-_ ]?questions?)\s*[：:]\s*\S", re.I)
CAPAB_HEAD = re.compile(r"^(#{1,4})\s*(?:capabilities|能力面|涉及能力)\s*(?:[:：]|$)", re.I | re.M)
CAPAB_PATH = re.compile(r"(?:^|[\s`*\-])specs/([\w\-]+)(?:/|[\s`*：:，,；)]|$)", re.M)
REQ_HEAD = re.compile(r"^#{2,5}\s+(R-[\w.\-]+)\s*(?:[|｜]\s*(.+?))?\s*$")
DELTA_HEAD = re.compile(r"^#{1,5}\s*(ADDED|MODIFIED|REMOVED)\s+Requirements\s*$", re.I)
CARD_ID = re.compile(r"卡\s*#?\s*([A-Z]?\d+)")
R_ID = re.compile(r"\bR-[\w.\-]+")
LINE_ANCHOR = re.compile(r"([\w\-./\\]+\.(?:py|md|js|ts|tsx|jsx|vue|sql|json|java|go|rs|html|css|sh)):(\d+)")
BARE_PLACEHOLDER = re.compile(r"^\s*(?:[-*|]\s*)?(?:无|暂无)\s*[。．]?\s*(?:\|\s*)?$")
FUZZY = re.compile(r"尽量|大致|合适|高效|高性能|友好|良好|合理|较快|差不多|应当挺好")
THRESHOLD = re.compile(r"\d|不得|禁止|必须|只允许|≤|≥|零|唯一")
# v1.4.0（⑤ 阈值三件套·规格侧）：验收行的阈值必须带得起一次测量。阈值形状＝数字紧邻比较符，
# 或数字紧邻计量单位（卡号/走查条数这类裸数字不算，防误伤）；测量形状＝「测量命令」标记、
# 命令动词（py/k6/curl…）、或 tests/…py 引用——测试本身就是 fitness function，pytest 即测量命令。
# 🔴 三条正则各占一行：selftest 变异探针臂（S51–S53）按行首锚点整行替换，拆行=探针失锚。
THRESH_SHAPE = re.compile(r"\d\s*(?:ms|毫秒|秒|分钟|小时|%|p9[059]|rps|qps|M[iK]?B|GB)|[≤≥≦≧<>]\s*=?\s*\d|\d\s*[≤≥≦≧]")
MEASURE_SHAPE = re.compile(r"测量命令|测量[:：]|\bmeasure\s*[:：]|\b(?:py|python3?|pytest|k6|curl|node|npm|npx|lighthouse|bench\w*|ab|wrk|jmeter|siege|bash|sh)\s+\S+|\bgo\s+test\b|\bcargo\s+test\b|tests?[\\/][\w\-./]+\.(?:py|js|ts|tsx|jsx)\b")
# v1.4.0（⑪ 棕地反提）：反提件头部的现状标记（细则 §3.5）。带它进 specs/＝把现状（含 bug）镀金成契约，C21 红。
REVERSE_MARK = re.compile(r"现状描述\s*[（(]\s*反提\s*[）)]|status\s*[:：]\s*现状描述", re.I)
# v1.12.0（K11 · MUST-14，细则 §3.5 逆向升格）：reverse/ 推断件治理三面（C21 扩容，扫描面首纳 reverse/）——
# ①推断件块缺「关键定位：文件＋标识符」证据坐标 ⇒ 红（证据坐标是反提的生命线，缺＝条款挂在空气上）；
# ②推断件带行号锚 ⇒ 红（坐标禁行号，同 C13 口径，快照必漂）；
# ③需求块「来源：」行指向的 reverse 草案缺「签收：」行（或不在盘）⇒ 红（未签收转正＝人审闸门被跳）。
# 签收行住草案不进主规格块（状态机四态是承诺面字段，现状标记住文件头——细则 §3.5 第②步红线）。
# 🔴 三条各占一行：变异探针臂（S105/S107/S108）按行首锚点整行替换，拆行=探针失锚。
KEYLOC_LINE = re.compile(r"^\s*[-*]?\s*关键定位\s*[：:]\s*\S")
SIGNOFF_LINE = re.compile(r"^\s*[-*]?\s*签收\s*[：:]\s*\S", re.M)  # re.M：face③ 用 search 扫整份草案，^ 必须逐行生效（v1.16.0 修，S104 哨兵抓出）
ANCHOR_MARK = re.compile(r"^\s*[-*]?\s*锚点\s*[：:]\s*v(\d+)\s*$", re.M)  # v1.19.0（v3-K18）：块级锚点分级标记，v1=链接化强制面
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")  # Markdown 内链形状（C31 识别面；S113 变异臂锚此行）
REV_SRC = re.compile(r"reverse[/\\][\w\-./\\]+")
# v1.6.0（specimen 收编）：验收去向认三形态——卡号／测试文件／走查清单条目（走查＝验收即演示动线，
# 同一张清单开发验收与现场演示各跑一次，细则 §3）。🔴 单占一行：变异探针臂（S62）按行首锚点整行替换，拆行=探针失锚。
ACC_TARGET = re.compile(r"卡\s*[A-Z]?\d+|tests?[\\/]|test_\w+|走查清单\s*第?\s*\d+")
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
# v1.5.0（热身卡）C22 燃料：本件文档面对自己尺子的声称——「N 条断言」「断言编号 C01–Cxx 只追加不顺延」。
# 这类自称写死在散文里，历史上真漂过：v2 委托书开工时 SKILL.md 与细则还留着「19 条 C01–C19」而 CHECKS 已 21 条，
# 修文后 SKILL.md 纪律 3 又残留 C01–C19（2026-10-27 实测在盘）。声称与实测不一致＝红：同一份盘上文件
# 可核的机械事实、无语义面，比 C18 项目面数字的警级更硬。selftest S60 把本件真根绑进自证（文档面漂移＝selftest 红）。
# 🔴 两条正则各占一行：变异探针臂（S58/S59）按行首锚点整行替换，拆行=探针失锚。
SELF_DOC_SCOPES = ("SKILL.md", "references/规格层规范.md")
DOC_COUNT_CLAIM = re.compile(r"(\d{1,3})\s*条?\s*(?:具名|机械)?断言")
DOC_FREEZE_RANGE = re.compile(r"C01\s*[–—\-~～]\s*C(\d{2})(?=[^\n]{0,8}只追加)")


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
            if (cd / "proposal.md").is_file() and SKIP_MARK.search(read_text(cd / "proposal.md")):
                pass    # 零增量逃生口（v1.9.0，对标 OpenSpec skip_specs）：proposal 显式声明 Skip-Specs 才放行纯流程变更包
            else:
                rep.red("C08", rel(root, cd),
                        "变更包缺 specs/ 增量（没有行为变化就别开变更包；纯流程变更可在 proposal 声明 Skip-Specs，细则 §4）")

    # C25 skip 声明与事实矛盾（v1.9.0）：声明了零增量却带着 specs/ 增量＝声明是幌子——
    # 豁免口被滥用＝静默漏检，比不声明更坏（声明会免掉 C08 的空包红，矛盾声明两头骗）。
    for cd in c.change_dirs:
        pp = cd / "proposal.md"
        if pp.is_file() and SKIP_MARK.search(read_text(pp)) \
                and (cd / "specs").is_dir() and any((cd / "specs").rglob("*.md")):
            rep.red("C25", rel(root, cd),
                    "proposal 声明了 Skip-Specs（零增量）但包里带着 specs/ 增量——声明与事实矛盾（细则 §4）")

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
        elif not ACC_TARGET.search(acc[0]):
            rep.red("C14", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 的验收行没指向卡号或测试文件：{acc[0].strip()[:44]}")

    # C20 阈值三件套·规格侧（v1.4.0）：验收行写了阈值，就必须带得起一次测量——
    # 测量命令或测试引用都算；两者皆无＝阈值只活在散文里，测试写不出红灯。
    # 警级：红绿判据最终由测试兜底，这里只当场点名（验收行全量扫，含增量 ADDED/MODIFIED）。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        for ln, no in zip(b["lines"], b["nos"]):
            s = ln.strip()
            if not re.match(r"^[-*]?\s*验收\s*[：:]", s):
                continue
            if THRESH_SHAPE.search(s) and not MEASURE_SHAPE.search(s):
                rep.warn("C20", f"{rel(root, b['file'])}:{no}",
                         f"{b['id']} 验收行含阈值但缺测量命令/测试引用（阈值三件套＝指标名＋数值＋测量命令）：{s[:40]}")

    # C21 反提件治理（v1.4.0 原面＋v1.12.0 K11 扩容三面，细则 §3.5 逆向升格四步）。
    # 原面：带「现状描述（反提）」标记的需求块出现在主规格/增量里＝没走「现有测试跑通＋人审签收」
    # 就把现状（可能含 bug）写成契约。红级。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        m21 = REVERSE_MARK.search(b["body"])
        if m21:
            rep.red("C21", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 带反提标记「{m21.group(0)}」却躺在规格层——现状描述≠承诺："
                    f"现有测试跑通＋人审签收后去标转正（细则 §3.5）")

    # C21 扩容面①②（v1.12.0，K11 · MUST-14）：扫描面首纳 reverse/——只查证据坐标与行号锚，
    # 不进句式/状态机/场景判据（反提件不是契约，细则 §3.5 分工句）。①推断件块缺「关键定位：文件＋
    # 标识符」＝条款挂在空气上，转正后无人能核对；②带行号锚＝快照必漂（同 C13 口径）。
    rev_dir = root / "reverse"
    for rf in sorted(rev_dir.rglob("*.md")) if rev_dir.is_dir() else []:
        rtext = read_text(rf)
        for i, ln in enumerate(rtext.splitlines(), 1):
            m21a = LINE_ANCHOR.search(ln)
            if m21a:
                rep.red("C21", f"{rel(root, rf)}:{i}",
                        f"reverse 推断件带行号锚「{m21a.group(0)}」——证据坐标只准文件＋标识符，"
                        f"行号是快照必漂（细则 §3.5 逆向升格第②步；同 C13 口径）")
        for rb in parse_req_blocks(rf, rtext):
            if not any(KEYLOC_LINE.match(l.strip()) for l in rb["lines"]):
                rep.red("C21", f"{rel(root, rf)}:{rb['start']}",
                        f"reverse 推断件 {rb['id']} 缺「关键定位：文件＋标识符」证据坐标——"
                        f"现状条款挂在空气上，转正后无人能核对（细则 §3.5 逆向升格第②步）")

    # C21 扩容面③（v1.12.0，K11 · MUST-14）：未签收不转正——需求块（主规格/增量 ADDED/MODIFIED）
    # 的「来源：」行指向 reverse/ 草案的，草案必须在盘且带「签收：」行（签收人＋日期写进草案，
    # 细则 §3.5 第④步）。草案缺签收＝人审闸门被跳；草案不在盘＝转正溯源断链。都红。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        for ln in b["lines"]:
            m21s = re.match(r"^\s*[-*]?\s*来源\s*[：:]\s*(.+?)\s*$", ln.strip())
            if not m21s:
                continue
            for ref in sorted(set(REV_SRC.findall(m21s.group(1)))):
                where21 = f"{rel(root, b['file'])}:{b['start']}"
                dpath = root / ref
                if not dpath.is_file():
                    rep.red("C21", where21,
                            f"{b['id']} 的来源指向 {ref}，盘上没有这份 reverse 草案——"
                            f"转正溯源断链（细则 §3.5 逆向升格第④步）")
                elif not SIGNOFF_LINE.search(read_text(dpath)):
                    rep.red("C21", where21,
                            f"{b['id']} 来源指向的 reverse 草案 {ref} 没有「签收：」行——"
                            f"未签收转正＝人审闸门被跳（细则 §3.5 逆向升格第④步）")

    # C31 锚点分级（v1.19.0，v3-K18）：锚点:v1 自标记的需求块，关键定位行必须是仓库内相对链接且目标在场
    # ——链接化后，代码文件改名/删除时机械必红（「用户改代码 Agent 会知道」的机械半边）；
    # 存量缺省 v0 不查不红（零冲击，迁移走细则 §3 锚点分级三步：AI 重排→红清零→人抽查）。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        m31 = ANCHOR_MARK.search(b["body"])
        if not m31 or m31.group(1) != "1":
            continue
        for ln, no31 in zip(b["lines"], b["nos"]):
            kl31 = re.match(r"^\s*[-*]?\s*关键定位\s*[：:]\s*(.+?)\s*$", ln.strip())
            if not kl31:
                continue
            link31 = MD_LINK.search(kl31.group(1))
            if not link31:
                rep.red("C31", f"{rel(root, b['file'])}:{no31}",
                        f"{b['id']} 锚点:v1 的关键定位是裸文本——要写成「[文件](相对路径) 的 标识符」，"
                        f"链接化后文件改名/删除才被尺子咬（细则 §3 锚点分级）")
                continue
            t31 = (b["file"].parent / link31.group(2).split("#")[0])
            if not t31.exists():
                rep.red("C31", f"{rel(root, b['file'])}:{no31}",
                        f"{b['id']} 锚点:v1 的关键定位链接目标 {link31.group(2)} 不在盘——"
                        f"文件改名/删除未回改规格（细则 §3 锚点分级；存量迁移三步同节）")

    # C23 文档状态机（v1.8.0，MUST-06 地基）：写了「状态：」就必须是四态之一——
    # 状态机的前提是值收口；不写状态行不红（不写不查，存量项目零冲击，S68 哨兵钉）。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        for ln, no in zip(b["lines"], b["nos"]):
            m23 = STATE_LINE.search(ln)
            if m23 and m23.group(1) not in STATE_ALLOWED:
                rep.red("C23", f"{rel(root, b['file'])}:{no}",
                        f"{b['id']} 状态值「{m23.group(1)}」不在四态内（草案/生效/已取代/废弃）"
                        f"——值不收口＝机器认不出承诺面（细则 §3 状态机）")

    # C24 TBD 有效至（v1.8.0，MUST-13 地基）：块内可写 TBD 标记「暂定等补」，但同块必须带
    # 「有效至：YYYY-MM-DD」——没有到期日的 TBD＝永远赖账。到期判红归 MUST-13 收口断言，这里只管形状。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        if not TBD_MARK.search(b["body"]):
            continue
        if not any(TTL_SHAPE.search(ln) for ln in b["lines"]):
            rep.red("C24", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 含 TBD 占位但没有「有效至：YYYY-MM-DD」行"
                    f"——没有到期日的 TBD＝永远赖账（细则 §3 TBD 约定）")

    # C29 TBD 有效至过期（v1.10.0，MUST-13 收口）：带「有效至：YYYY-MM-DD」的 TBD 块，日期早于
    # 今天 ⇒ 赖账坐实红——到期日是承诺的截止线，过线还在 TBD＝到期机制形同虚设。形状缺失归 C24
    # （不重复咬）；今天及以后不红；形状过但值非法不咬（_ttl_expired 单点判定，S91 钉）。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        if not TBD_MARK.search(b["body"]):
            continue
        m29 = next((mm for mm in (TTL_SHAPE.search(ln) for ln in b["lines"]) if mm), None)
        if m29 and _ttl_expired(m29.group(1)):
            rep.red("C29", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 的 TBD 有效至「{m29.group(1)}」已过期——到期日一过还在 TBD＝赖账坐实，"
                    f"补内容或销账（细则 §3 TBD 约定）")

    # C30 supersede 双向咬合（v1.11.0，K12 · MUST-15/04）：需求块级「取代：R-x」/「被取代：R-x」是
    # 需求号指针（路径形态归 doc_lint，细则 §6.8 分工）。双向缺一即红——写单边、对侧块不在场、
    # 对侧不回指、自指都翻红：单边声明＝历史版本失联，接手窗口溯因时找不到对侧。
    sup_index = {}
    for _b in spec_blocks + delta_blocks:
        sup_index.setdefault(_b["id"], []).append(_b)

    def _carries_ptr(blk, rx, rid):
        return any((mm := rx.match(l.strip())) and rid in R_ID.findall(mm.group(1))
                   for l in blk["lines"])

    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        for ln, no in zip(b["lines"], b["nos"]):
            m30t = SUP_TAKE.match(ln.strip())
            m30b = SUP_BY.match(ln.strip())
            if not m30t and not m30b:
                continue
            side30 = "取代" if m30t else "被取代"
            back30 = "被取代" if m30t else "取代"
            for tgt in R_ID.findall((m30t or m30b).group(1)):
                where30 = f"{rel(root, b['file'])}:{no}"
                if tgt == b["id"]:
                    rep.red("C30", where30,
                            f"{b['id']} 的取代指针指着自己——自指＝没改任何东西（细则 §3 supersede 约定）")
                    continue
                mates = sup_index.get(tgt, [])
                if not mates:
                    rep.red("C30", where30,
                            f"{b['id']} 的取代指针指向不存在的 {tgt}——对侧块不在场，双向无从咬合"
                            f"（被替代的历史块要以「已取代＋被取代」墓碑留在盘上，细则 §3 状态机表）")
                    continue
                if not any(_carries_ptr(mb, SUP_BY if m30t else SUP_TAKE, b["id"]) for mb in mates):
                    rep.red("C30", where30,
                            f"{b['id']} 标了「{side30}：{tgt}」但对侧没有「{back30}：{b['id']}」回指"
                            f"——supersede 单向残缺＝双向缺一（细则 §3 supersede 约定）")

    # C26 provenance 来源强制（v1.9.0，MUST-12·P0-6）：变更包是需求块出生的地方——增量
    # ADDED/MODIFIED 块必须带「来源：」行（第 12 元信息前缀，出处写到 PRD/纪要/对话原话坐标）。
    # 缺＝「这条哪来的」无人能答，脑补口径混进承诺面；裸「来源：无」不算溯源。主规格与 REMOVED
    # 段不查（存量项目零冲击；REMOVED 只写号没句子）。
    for b in [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        if not any(SOURCE_LINE.match(ln) for ln in b["lines"]):
            rep.red("C26", f"{rel(root, b['file'])}:{b['start']}",
                    f"增量 {b['kind']} 的 {b['id']} 缺「来源：」行"
                    f"（每条款带需求原话出处＝P0-6 provenance，验收时「这条哪来的」要答得上；细则 §4）")

    # C27 歧义冻结（v1.9.0，MUST-12·P0-6 后半）：块内登记了待决问题（open-questions）就必须
    # 冻结在「草案」态——歧义没解就敢「生效」＝把脑补口径写成系统承诺；状态行缺失同样红
    # （冻结要有状态可挂）。没登记标记的块不查（写了才查，存量零冲击）。
    for b in spec_blocks + [d for d in delta_blocks if d["kind"] in ("ADDED", "MODIFIED")]:
        if not any(OPENQ_MARK.match(ln.strip()) for ln in b["lines"]):
            continue
        st27 = next((m.group(1).strip() for m in
                     (STATE_LINE.search(ln) for ln in b["lines"]) if m), None)
        if st27 != "草案":
            rep.red("C27", f"{rel(root, b['file'])}:{b['start']}",
                    f"{b['id']} 登记了待决问题但{'状态行缺失' if st27 is None else f'状态是「{st27}」'}"
                    f"——歧义冻结：登记 open-questions 的块必须停在「草案」，解掉问题再转生效（细则 §4）")

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

    # C28 Capabilities 路径在场（v1.9.0，OpenSpec 同款回流·评估收敛为警级）：proposal 写了
    # Capabilities/能力面节，节内 specs/<能力> 路径必须真在盘（主规格或本包增量）。没写节不查
    # （可选增强）；近重复能力名（booking vs bookings）不机检——机械相似度误伤率高，语义面归人审
    # （评估结论与写法约定＝细则 §4）。
    for cd in c.change_dirs:
        pp = cd / "proposal.md"
        if not pp.is_file():
            continue
        ptxt = read_text(pp)
        mcap = CAPAB_HEAD.search(ptxt)
        if not mcap:
            continue
        lvl = len(mcap.group(1))
        rest28 = ptxt[mcap.end():]
        nxt28 = re.search(rf"^#{{1,{lvl}}}\s", rest28, re.M)
        sect28 = rest28[:nxt28.start()] if nxt28 else rest28
        for cap in sorted(set(CAPAB_PATH.findall(sect28))):
            if not (c.specs_dir / cap).is_dir() and not any(
                    (d / "specs" / cap).is_dir() for d in c.change_dirs):
                rep.warn("C28", rel(root, cd),
                         f"Capabilities 节列了 specs/{cap}，主规格与本包增量都没有这个能力目录"
                         f"（写错字或近重复能力名？proposal→specs 路径契约失真）")

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

    # C22 本件文档面断言计数一致（v1.5.0 热身卡）：SKILL.md／本细则里「N 条断言」「断言编号 C01–Cxx
    # 只追加不顺延」这类自称与 CHECKS 实测对不上 ⇒ 红。其他项目没有这两份文件 ⇒ 自动跳过
    # （判据只咬 EverSpec 自己的文档面；引号内与证词行豁免，同 C18 口径）。
    doc_max = max(int(cid[1:]) for cid, _ in CHECKS)
    for scope in SELF_DOC_SCOPES:
        f = root / scope
        if not f.is_file():
            continue
        for i, line in enumerate(read_text(f).splitlines(), 1):
            clean = QUOTED.sub(" ", line)
            if HIST_MARK.search(clean):
                continue
            mc = DOC_COUNT_CLAIM.search(clean)
            if mc and int(mc.group(1)) != len(CHECKS):
                rep.red("C22", f"{rel(root, f)}:{i}",
                        f"声称「{mc.group(0).strip()}」＝{mc.group(1)}，实测 CHECKS {len(CHECKS)} 条"
                        f"——加断言时文档面没跟（这类漂移曾真实在盘）")
            mr = DOC_FREEZE_RANGE.search(clean)
            if mr and int(mr.group(1)) != doc_max:
                rep.red("C22", f"{rel(root, f)}:{i}",
                        f"编号冻结区间写到 C{mr.group(1)}，实测最大编号 C{doc_max:02d}"
                        f"——冻结口径过期（只追加不顺延，上限跟着 CHECKS 走）")

    # v1.7.0（K02 · MUST-17）：本次运行读数落 runs.log（目标项目零写入；退 2 的"没查"不经过这里）。
    # 🔴 红警口径＝按条数（与人读汇总/--json 同源），不是去重后的断言码数——同一断言多条红警都算数（S65b 钉）。
    if RUNSLOG_ON:
        _append_runs_log("spec_check", "check", f"root={root}",
                         red=sum(1 for i in rep.items if i[1] == "红"),
                         warn=sum(1 for i in rep.items if i[1] == "警"))
    return rep


# ---------------------------------------------------------------- 命令

# v1.7.0（K02 · MUST-17 运行读数）：每跑一次 check 就向本件 evidence/runs.log 追加一行读数——
# 目标项目零写入（「只读不改」承诺不变），读数只落本件自己的 evidence/；写不进去（只读盘等）
# 静默跳过——读数尽力而为，绝不影响主流程退出码。环境变量 EVSPEC_RUNS_LOG 改落点
# （selftest 用它把读数重定向进临时树，S26 零写入同款纪律）。行格式单源＝细则 §6.7。
RUNSLOG_ON = True   # 🔴 单占一行：变异探针臂（S66）按行首锚点整行替换，拆行=探针失锚。


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


def cmd_check(args):
    root = Path(args.root).resolve()
    wants_json = bool(getattr(args, "json", False))
    if not root.is_dir():
        if wants_json:
            print(json.dumps({"tool": "spec_check", "version": VERSION, "root": str(root),
                              "error": "根目录不存在或不是目录（没查，不是没问题）", "exit_code": 2},
                             ensure_ascii=True))
        else:
            print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    rep = run_checks(root)
    reds = [i for i in rep.items if i[1] == "红"]
    warns = [i for i in rep.items if i[1] == "警"]
    exit_code = 1 if (reds or (args.strict and warns)) else 0
    if wants_json:
        # 机读面：stdout 只打这一行 JSON（ensure_ascii 纯 ASCII 保管道/编码安全）；退出码语义与人读面一致
        print(json.dumps({
            "tool": "spec_check", "version": VERSION, "root": str(root),
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "strict": bool(args.strict), "exit_code": exit_code,
            "red": len(reds), "warn": len(warns), "assertions": len(CHECKS),
            "items": [{"code": c, "level": lv, "where": w, "msg": m} for c, lv, w, m in rep.items],
        }, ensure_ascii=True))
        return exit_code
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
- 来源：PRD 核心功能列表·取消释放
- 验收：卡B3 tests/test_cancel.py

#### 场景：取消后重开时段

- **WHEN** 取消一条 09:00–10:00 的预约
- **THEN** 对同一会议室同一时段的新预约应成功

## MODIFIED Requirements

#### R-3 | 会议已开始不得取消

若预约的开始时刻早于当前时刻，则系统应拒绝该预约的取消请求。

- 状态：生效
- 来源：PRD 规则五
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
    os.environ["EVSPEC_RUNS_LOG"] = str(tmp_root / "runs.log")   # v1.7.0（K02）：运行读数落临时树，evidence/ 与 scripts/ 零触碰
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

    # 变异探针（真探针）：把本件源码按「锚点行 → 替换行」打补丁，在独立命名空间重载后跑同一棵树——
    # 实现被打坏，点名现象必须翻面；原样对照臂就是对应的 S 用例本身。补丁只进内存，源文件零写入（S26 兜底）。
    _src_self = read_text(Path(__file__).resolve())

    def run_mutant(anchor_prefix, replacement, tree):
        hits = [l for l in _src_self.splitlines() if l.startswith(anchor_prefix)]
        if not hits:
            return None
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "spec_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<spec_mutant>", "exec"), g)
        except Exception:
            return None
        return g["run_checks"](tree)

    # —— 地基：合法树必须全绿，否则后面全是假绿
    good = mk_tree()
    rep0 = run_checks(good)
    green("S00 合法树全绿（红 0 警 0）", not rep0.items,
          f"实际={[(i[0], i[1], i[3][:60]) for i in rep0.items]}")

    # —— 无管线模式哨兵（EverSpec 独立使用）：specs/changes/tests 在、docs/ 整棵缺失 ⇒ 必须零红（降警不拦人）
    np_tree = mk_tree()
    shutil.rmtree(np_tree / "docs")
    rep_np = run_checks(np_tree)
    green("S-NP 无管线树零红（docs/ 全缺只准警：C06 无卡/C07 无从对账/C11 无 PRD/C17 矩阵过期）",
          not rep_np.red_codes(),
          f"实际红={sorted(rep_np.red_codes())} 警={sorted(set(i[1] for i in rep_np.items))}")

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

    # —— v1.4.0 S46~S50：C20 阈值测量命令（⑤ 规格侧）＋ C21 反提件禁入规格层（⑪）
    st46 = mk_tree()
    patch(spec_of(st46), "- 验收：卡B1 tests/test_rules.py", "- 验收：卡B1 接口响应 p95<500ms")
    rep46 = run_checks(st46)
    green("S46 C20 验收行含阈值缺测量命令 ⇒ 记警（阈值三件套缺测量面）",
          any(i[0] == "C20" and i[1] == "警" for i in rep46.items)
          and not [i for i in rep46.items if i[0] == "C20" and i[1] == "红"],
          f"警={[(i[3][:52]) for i in rep46.items if i[0] == 'C20']}")

    st47 = mk_tree()
    patch(spec_of(st47), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 k6 run bench_api.js（阈值 p95<500ms）")
    rep47 = run_checks(st47)
    green("S47 C20 哨兵：阈值带测量命令（k6…）不报警",
          not [i for i in rep47.items if i[0] == "C20"],
          f"C20={[(i[1], i[3][:40]) for i in rep47.items if i[0] == 'C20']}")

    st48 = mk_tree()
    patch(spec_of(st48), "- **WHEN** 提交一个开始时刻早于 08:00 的预约",
          "- **WHEN** 提交一个开始时刻早于 08:00 的预约（另有 p95<500ms 指标）")
    rep48 = run_checks(st48)
    green("S48 C20 哨兵：阈值写在场景行（非验收行）不触发",
          not [i for i in rep48.items if i[0] == "C20"],
          f"C20={[(i[1], i[3][:40]) for i in rep48.items if i[0] == 'C20']}")

    st49 = mk_tree()
    patch(spec_of(st49), "- 状态：生效", "> status: 现状描述（反提）\n- 状态：生效")
    rep49 = run_checks(st49)
    green("S49 C21 反提标记混进主规格 ⇒ 红（现状描述≠承诺）",
          "C21" in rep49.red_codes(),
          f"红={[(i[3][:52]) for i in rep49.items if i[0] == 'C21']}")

    st50 = mk_tree()
    rev50 = st50 / "reverse"
    rev50.mkdir()
    # v1.12.0（K11）夹具升级：reverse/ 纳入 C21 扫描面后，合规推断件＝带证据坐标、无行号锚
    # （K12 S71 同款——旧夹具在新判据下成语义坏例，哨兵本意「合规反提件零红」不变）；
    # 块内不写「- 状态：」行（状态机四态是承诺面字段，现状标记住文件头——细则 §3.5 第②步红线）。
    (rev50 / "booking-反提草案.md").write_text(
        "# 反提草案 · booking\n\n> status: 现状描述（反提）——未经人审签收禁止转正\n\n"
        "#### R-d1 | 现状：过期预约走标记不删\n\n若预约已过期，则系统应将记录标记为过期而非物理删除。\n\n"
        "- 关键定位：booking.py 的 cancel_booking()\n- 拟验收：tests/test_cancel.py\n", encoding="utf-8")
    rep50 = run_checks(st50)
    green("S50 C21 哨兵：合规推断件躺在 reverse/ 零红（只查证据坐标与签收，不进句式/状态机判据——反提件不是契约）",
          not rep50.red_codes(),
          f"红={sorted(rep50.red_codes())}")

    # —— v1.4.0 S51~S53：变异探针臂（实现打坏 ⇒ 点名现象翻面；原样对照臂=S46/S47/S49）
    m46 = mk_tree()
    patch(spec_of(m46), "- 验收：卡B1 tests/test_rules.py", "- 验收：卡B1 接口响应 p95<500ms")
    mr1 = run_mutant("THRESH_SHAPE = ", 'THRESH_SHAPE = re.compile(r"(?!)")', m46)
    green("S51 变异臂：THRESH_SHAPE 打哑 ⇒ S46 现象消失（检测面被钉住）",
          mr1 is not None and not [i for i in mr1.items if i[0] == "C20"],
          f"变异后 C20={[(i[1], i[3][:36]) for i in (mr1.items if mr1 else []) if i[0] == 'C20']}")

    m47 = mk_tree()
    patch(spec_of(m47), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 k6 run bench_api.js（阈值 p95<500ms）")
    mr2 = run_mutant("MEASURE_SHAPE = ", 'MEASURE_SHAPE = re.compile(r"(?!)")', m47)
    green("S52 变异臂：MEASURE_SHAPE 打哑 ⇒ 合法行误报（S47 现象翻面）",
          mr2 is not None and any(i[0] == "C20" for i in mr2.items),
          f"变异后 C20={[(i[1], i[3][:36]) for i in (mr2.items if mr2 else []) if i[0] == 'C20']}")

    m49 = mk_tree()
    patch(spec_of(m49), "- 状态：生效", "> status: 现状描述（反提）\n- 状态：生效")
    mr3 = run_mutant("REVERSE_MARK = ", 'REVERSE_MARK = re.compile(r"(?!)")', m49)
    green("S53 变异臂：REVERSE_MARK 打哑 ⇒ C21 不再红（S49 现象消失）",
          mr3 is not None and "C21" not in mr3.red_codes(),
          f"变异后红={sorted(mr3.red_codes()) if mr3 else 'None'}")

    # —— v1.12.0（K11 · MUST-14）S100~S108：C21 扩容三面（reverse/ 推断件治理，扫描面首纳 reverse/）
    #   ＋四变异臂。扩容非新增：C21 原面（带标记进规格层）不动；新面＝①缺证据坐标 ②行号锚 ③未签收转正
    #   （含来源指向的草案不在盘）。判据口径＝细则 §3.5 逆向升格四步。
    def rev_tree(with_keyloc=True, with_signoff=False, keyloc_value="booking.py 的 cancel_booking()"):
        r = mk_tree()
        (r / "reverse").mkdir()
        lines = ["# 反提草案 · booking", "",
                 "> status: 现状描述（反提）——未经人审签收禁止转正", "",
                 "#### R-d1 | 现状：过期预约走标记不删", "",
                 "若预约已过期，则系统应将记录标记为过期而非物理删除。", ""]
        if with_keyloc:
            lines += [f"- 关键定位：{keyloc_value}"]
        lines += ["- 拟验收：tests/test_cancel.py", ""]
        if with_signoff:
            lines += ["## 人审签收（转正闸门）", "", "- 签收：天（2026-10-29）", ""]
        (r / "reverse" / "booking-反提草案.md").write_text("\n".join(lines), encoding="utf-8")
        return r

    st100 = rev_tree(with_keyloc=False)
    rep100 = run_checks(st100)
    green("S100 C21 扩容①：reverse 推断件块缺「关键定位」证据坐标 ⇒ 红（条款挂在空气上）",
          "C21" in rep100.red_codes(),
          f"红={[(i[3][:52]) for i in rep100.items if i[0] == 'C21']}")

    st101 = rev_tree(keyloc_value="booking.py:120 的 cancel_booking()")
    rep101 = run_checks(st101)
    green("S101 C21 扩容②：reverse 推断件带行号锚 ⇒ 红（证据坐标禁行号，同 C13 口径）",
          "C21" in rep101.red_codes(),
          f"红={[(i[3][:52]) for i in rep101.items if i[0] == 'C21']}")

    st102 = rev_tree()
    patch(spec_of(st102), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 来源：reverse/booking-反提草案.md")
    rep102 = run_checks(st102)
    green("S102 C21 扩容③：需求块来源指向的 reverse 草案缺「签收：」行 ⇒ 红（未签收转正＝人审闸门被跳）",
          "C21" in rep102.red_codes(),
          f"红={[(i[3][:52]) for i in rep102.items if i[0] == 'C21']}")

    st103 = mk_tree()
    patch(spec_of(st103), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 来源：reverse/ghost-草案.md")
    rep103 = run_checks(st103)
    green("S103 C21 扩容③b：来源指向的 reverse 草案不在盘 ⇒ 红（转正溯源断链）",
          "C21" in rep103.red_codes(),
          f"红={[(i[3][:52]) for i in rep103.items if i[0] == 'C21']}")

    st104 = rev_tree(with_signoff=True)
    patch(spec_of(st104), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 来源：reverse/booking-反提草案.md")
    rep104 = run_checks(st104)
    green("S104 C21 哨兵：草案补签收后来源转正零红（反误伤；签收行住草案，不进主规格块）",
          not rep104.red_codes(),
          f"红={sorted(rep104.red_codes())} 警={sorted(set(i[0] for i in rep104.items if i[1] == '警'))}")

    m100 = rev_tree(with_keyloc=False)
    mr100 = run_mutant("KEYLOC_LINE = ", 'KEYLOC_LINE = re.compile(r"")', m100)  # 永匹配打哑：识别面失明⇒缺坐标面消失（(?!) 永不匹配会放大红，方向相反——v1.16.0 修）
    green("S105 变异臂：KEYLOC_LINE 打哑 ⇒ S100 现象消失（证据坐标识别面被钉住）",
          mr100 is not None and "C21" not in mr100.red_codes(),
          f"变异后红={sorted(mr100.red_codes()) if mr100 else 'None'}")

    m101 = rev_tree(keyloc_value="booking.py:120 的 cancel_booking()")
    mr101 = run_mutant("LINE_ANCHOR = ", 'LINE_ANCHOR = re.compile(r"(?!)")', m101)
    green("S106 变异臂：LINE_ANCHOR 打哑 ⇒ S101 现象消失（行号锚识别面被钉住；C13 同源同哑不算败）",
          mr101 is not None and "C21" not in mr101.red_codes(),
          f"变异后红={sorted(mr101.red_codes()) if mr101 else 'None'}")

    m102 = rev_tree()
    patch(spec_of(m102), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 来源：reverse/booking-反提草案.md")
    mr102 = run_mutant("REV_SRC = ", 'REV_SRC = re.compile(r"(?!)")', m102)
    green("S107 变异臂：REV_SRC 打哑 ⇒ S102 现象消失（reverse 来源引用识别面被钉住）",
          mr102 is not None and "C21" not in mr102.red_codes(),
          f"变异后红={sorted(mr102.red_codes()) if mr102 else 'None'}")

    m104 = rev_tree(with_signoff=True)
    patch(spec_of(m104), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 来源：reverse/booking-反提草案.md")
    mr104 = run_mutant("SIGNOFF_LINE = ", 'SIGNOFF_LINE = re.compile(r"(?!)")', m104)
    green("S108 变异臂：SIGNOFF_LINE 打哑 ⇒ S104 哨兵翻红（签收识别面被钉住：已签认不出＝全当未签）",
          mr104 is not None and "C21" in mr104.red_codes(),
          f"变异后红={sorted(mr104.red_codes()) if mr104 else 'None'}")

    # —— v1.19.0（v3-K18）S109~S113：C31 锚点分级（锚点:v1 自标记块的关键定位必须链接化且目标在场；v0 存量零冲击）
    def anchor_tree(mark="v1", keyloc="- 关键定位：booking.py 的 cancel_booking()"):
        r = mk_tree()
        (r / "booking.py").write_text("def cancel_booking():\n    ...\n", encoding="utf-8")  # 链接目标的在盘桩
        body = "\n- 锚点：{m}".format(m=mark) if mark else ""
        patch(spec_of(r), "- 验收：卡B1 tests/test_rules.py",
              "- 验收：卡B1 tests/test_rules.py" + body + "\n- 关键定位占位待换")
        # 把占位行换成指定形状（在 R-1 块内）
        sp = spec_of(r)
        sp.write_text(sp.read_text(encoding="utf-8").replace("- 关键定位占位待换", keyloc), encoding="utf-8")
        return r

    st109 = anchor_tree("v1", "- 关键定位：booking.py 的 cancel_booking()")
    rep109 = run_checks(st109)
    green("S109 C31 面①：锚点:v1 的关键定位是裸文本非链接 ⇒ 红（链接化后改名/删除才被尺子咬）",
          "C31" in rep109.red_codes(),
          f"红={[(i[3][:52]) for i in rep109.items if i[0] == 'C31']}")

    st110 = anchor_tree("v1", "- 关键定位：[booking.py](../../booking.py) 的 cancel_booking()")
    rep110 = run_checks(st110)
    green("S110 C31 哨兵：锚点:v1 的关键定位为链接且目标在场 ⇒ 无 C31（链接化达标零冲击）",
          "C31" not in rep110.red_codes(),
          f"红={sorted(rep110.red_codes())}")

    st111 = anchor_tree("v1", "- 关键定位：[booking.py](../../booking_gone.py) 的 cancel_booking()")
    rep111 = run_checks(st111)
    green("S111 C31 面②：锚点:v1 的关键定位链接目标不在盘 ⇒ 红（文件改名/删除未回改规格）",
          "C31" in rep111.red_codes(),
          f"红={[(i[3][:52]) for i in rep111.items if i[0] == 'C31']}")

    st112 = anchor_tree(None, "- 关键定位：booking.py 的 cancel_booking()")
    rep112 = run_checks(st112)
    green("S112 C31 存量哨兵：未标锚点（v0）的裸文本关键定位 ⇒ 无 C31（存量零冲击，迁移走三步）",
          "C31" not in rep112.red_codes(),
          f"红={sorted(rep112.red_codes())}")

    m109 = anchor_tree("v1", "- 关键定位：[booking.py](../../booking.py) 的 cancel_booking()")
    mr110 = run_mutant("MD_LINK = ", 'MD_LINK = re.compile(r"(?!)")', m109)
    green("S113 变异臂：MD_LINK 打哑 ⇒ S110 哨兵翻红（链接识别面被钉住：链接认不出＝全当裸文本）",
          mr110 is not None and "C31" in mr110.red_codes(),
          f"变异后红={sorted(mr110.red_codes()) if mr110 else 'None'}")

    # —— v1.4.0 S54：开箱样例钉（examples/最小项目 永远可跑、零红退 0，30 秒上手不许坏）
    ex_dir = skill_root / "examples" / "最小项目"
    if ex_dir.is_dir():
        rep_ex = run_checks(ex_dir)
        buf_ex = io.StringIO()
        with contextlib.redirect_stdout(buf_ex):
            rc_ex = cmd_check(argparse.Namespace(root=str(ex_dir), strict=False))
        green("S54 开箱样例 examples/最小项目：零红且 cmd_check 退 0（30 秒上手不许坏）",
              not rep_ex.red_codes() and rc_ex == 0,
              f"红={sorted(rep_ex.red_codes())} 退码={rc_ex}")
    else:
        green("S54 开箱样例 examples/最小项目：零红且 cmd_check 退 0（30 秒上手不许坏）", False,
              "examples/最小项目 不在场——开箱样例被删或未建")

    # —— v1.5.0 S55~S60：C22 本件文档面断言计数一致（热身卡：「19 条 C01–C19」自称漂移曾真实在盘，
    #    v2 委托书开工时 SKILL.md 与细则共 3 处残留；先有用例后实现，红绿两跑落盘）
    doc_max_c = max(int(cid[1:]) for cid, _ in CHECKS)
    doc_ok = (f"# SKILL\n\n本尺共 {len(CHECKS)} 条具名断言。\n\n"
              f"断言编号 C01–C{doc_max_c:02d} 只追加不顺延。\n")
    st55 = mk_tree()
    (st55 / "SKILL.md").write_text(
        f"# SKILL\n\n本尺共 {len(CHECKS) + 7} 条具名断言。\n", encoding="utf-8")
    rep55 = run_checks(st55)
    green("S55 C22 文档声称条数 ≠ CHECKS 实测 ⇒ 红",
          "C22" in rep55.red_codes(),
          f"红={[(i[2], i[3][:46]) for i in rep55.items if i[0] == 'C22']}")

    st56 = mk_tree()
    (st56 / "references").mkdir()
    (st56 / "references" / "规格层规范.md").write_text(
        f"# 细则\n\n断言编号 C01–C{doc_max_c + 1:02d} 只追加不顺延。\n", encoding="utf-8")
    rep56 = run_checks(st56)
    green("S56 C22 编号冻结区间上限 ≠ 实测最大编号 ⇒ 红",
          "C22" in rep56.red_codes(),
          f"红={[(i[2], i[3][:46]) for i in rep56.items if i[0] == 'C22']}")

    st57 = mk_tree()
    (st57 / "SKILL.md").write_text(doc_ok, encoding="utf-8")
    (st57 / "references").mkdir()
    (st57 / "references" / "规格层规范.md").write_text(doc_ok, encoding="utf-8")
    rep57 = run_checks(st57)
    green("S57 C22 哨兵：条数与冻结区间都与实测一致 ⇒ 零 C22（反误伤）",
          not [i for i in rep57.items if i[0] == "C22"],
          f"C22={[(i[1], i[3][:40]) for i in rep57.items if i[0] == 'C22']}")

    m55 = mk_tree()
    (m55 / "SKILL.md").write_text(
        f"# SKILL\n\n本尺共 {len(CHECKS) + 7} 条具名断言。\n", encoding="utf-8")
    mr4 = run_mutant("DOC_COUNT_CLAIM = ", 'DOC_COUNT_CLAIM = re.compile(r"(?!)")', m55)
    green("S58 变异臂：DOC_COUNT_CLAIM 打哑 ⇒ S55 现象消失（检测面被钉住）",
          mr4 is not None and not [i for i in mr4.items if i[0] == "C22"],
          f"变异后 C22={[(i[1], i[3][:36]) for i in (mr4.items if mr4 else []) if i[0] == 'C22']}")

    m56 = mk_tree()
    (m56 / "references").mkdir()
    (m56 / "references" / "规格层规范.md").write_text(
        f"# 细则\n\n断言编号 C01–C{doc_max_c + 1:02d} 只追加不顺延。\n", encoding="utf-8")
    mr5 = run_mutant("DOC_FREEZE_RANGE = ", 'DOC_FREEZE_RANGE = re.compile(r"(?!)")', m56)
    green("S59 变异臂：DOC_FREEZE_RANGE 打哑 ⇒ S56 现象消失",
          mr5 is not None and not [i for i in mr5.items if i[0] == "C22"],
          f"变异后 C22={[(i[1], i[3][:36]) for i in (mr5.items if mr5 else []) if i[0] == 'C22']}")

    rep_self = run_checks(skill_root)
    green("S60 文档面自咬：本件真根 SKILL.md/细则的断言自称零 C22（漂移＝selftest 红，S54 同款活体绑定）",
          "C22" not in rep_self.red_codes(),
          f"C22={[(i[2], i[3][:52]) for i in rep_self.items if i[0] == 'C22']}")

    # —— v1.6.0 S61~S62：C14 验收去向认走查清单锚（specimen 实务收编：走查＝验收即演示动线，
    #    同一张清单开发验收与现场演示各跑一次；验收行可写「走查清单第 N 条」当去向）
    w61 = mk_tree()
    patch(spec_of(w61), "- 验收：卡B1 tests/test_rules.py", "- 验收：走查清单第 4、17 条")
    rep61 = run_checks(w61)
    green("S61 C14 验收行只带走查清单锚 ⇒ 不红（演示动线是合法去向）",
          "C14" not in rep61.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep61.items if i[0] == 'C14']}")

    w62 = mk_tree()
    patch(spec_of(w62), "- 验收：卡B1 tests/test_rules.py", "- 验收：走查清单第 4、17 条")
    mr6 = run_mutant("ACC_TARGET = ",
                     'ACC_TARGET = re.compile(r"卡\\s*[A-Z]?\\d+|tests?[\\\\/]|test_\\w+")', w62)
    green("S62 变异臂：ACC_TARGET 撤走查分支 ⇒ S61 现象翻红（新收编面被钉住）",
          mr6 is not None and "C14" in mr6.red_codes(),
          f"变异后 C14={[(i[1], i[3][:36]) for i in (mr6.items if mr6 else []) if i[0] == 'C14']}")

    # —— v1.7.0 S63~S66（K02）：check --json 机读面（对标 OpenSpec validate --json）＋ runs.log 运行读数（MUST-17）
    js_good = mk_tree()
    buf_j = io.StringIO()
    with contextlib.redirect_stdout(buf_j):
        rc_j = cmd_check(argparse.Namespace(root=str(js_good), strict=False, json=True))
    try:
        parsed = json.loads(buf_j.getvalue())
    except Exception:
        parsed = None
    green("S63 check --json：stdout 单行合法 JSON 且 红警/退出码/断言数 与树一致",
          parsed is not None and parsed.get("exit_code") == 0 and parsed.get("red") == 0
          and parsed.get("warn") == 0 and parsed.get("items") == [] and parsed.get("assertions") == len(CHECKS),
          f"退码={rc_j} parsed={str(parsed)[:90]}")

    js_bad = mk_tree()
    patch(spec_of(js_bad), "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。", "随便写点什么。")
    buf_j2 = io.StringIO()
    with contextlib.redirect_stdout(buf_j2):
        rc_j2 = cmd_check(argparse.Namespace(root=str(js_bad), strict=False, json=True))
    try:
        parsed2 = json.loads(buf_j2.getvalue())
    except Exception:
        parsed2 = None
    green("S64 check --json：红树退 1 且 JSON 红计数与退出码一致（机读面不漂移退出码语义）",
          rc_j2 == 1 and parsed2 is not None and parsed2.get("exit_code") == 1 and parsed2.get("red") == 1,
          f"退码={rc_j2} parsed={str(parsed2)[:90]}")

    rlog = tmp_root / "runs.log"

    def _rl_lines():
        return rlog.read_text(encoding="utf-8").splitlines() if rlog.is_file() else []

    n65 = len(_rl_lines())
    rl_tree = mk_tree()
    run_checks(rl_tree)
    lines65 = _rl_lines()
    green("S65 runs.log 接线：跑一次 check 追加恰一行，行带 spec_check/root/红警读数（MUST-17）",
          len(lines65) == n65 + 1 and "spec_check" in lines65[-1] and f"root={rl_tree}" in lines65[-1]
          and "红 0" in lines65[-1] and "警 0" in lines65[-1],
          f"行数 {n65}→{len(lines65)} 尾行={lines65[-1][:72] if lines65 else '（无）'}")

    m66 = mk_tree()
    n66 = len(_rl_lines())
    mr66 = run_mutant("RUNSLOG_ON = ", "RUNSLOG_ON = False", m66)
    green("S66 变异臂：RUNSLOG_ON 打哑 ⇒ 读数不再追加（S65 现象消失，接线被钉住）",
          mr66 is not None and len(_rl_lines()) == n66,
          f"行数 {n66}→{len(_rl_lines())} 变异返回={'None' if mr66 is None else 'rep'}")

    # S65b 读数口径钉（活体抓到的真 bug：首实现用去重码数，与人读汇总/--json 的条数口径漂移）：
    # 造一棵「同码双警」树（C03 同型连写＋模糊词 ⇒ 2 条 C03 警），读数必须按条数记；判据带
    # 「items≠codes」自警卫——夹具退化成不区分时此断言自己翻红，防止口径钉变成恒真。
    w65 = mk_tree()
    patch(spec_of(w65), "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。",
          "系统应在预约时间落在 08:00 至 20:00 之外时拒绝该次预约。\n系统应尽量高效地处理预约请求。")
    rep_w65 = run_checks(w65)
    w_items = sum(1 for i in rep_w65.items if i[1] == "警")
    w_codes = len(rep_w65.warn_codes())
    green("S65b runs.log 读数口径＝条数（与人读汇总/--json 同源），非去重码数",
          w_items != w_codes and f"红 0｜警 {w_items}" in _rl_lines()[-1],
          f"items={w_items} codes={w_codes} 尾行={_rl_lines()[-1][-48:]}")

    # —— v1.8.0（K04 · MUST-06/13 地基）S67~S74：文档状态机（C23）＋TBD 有效至（C24）＋元信息前缀扩容
    # 四态＝草案/生效/已取代/废弃（细则 §3 状态机表）；不写状态行不红＝存量项目零冲击；
    # TBD 必须同块带「有效至：YYYY-MM-DD」（到期判红归 MUST-13 收口断言，本版只管形状）。
    st_bad = mk_tree()
    patch(spec_of(st_bad), "- 状态：生效", "- 状态：已完成")
    repS67 = run_checks(st_bad)
    green("S67 C23 状态值不在四态内 ⇒ 红（草案/生效/已取代/废弃之外不认）",
          "C23" in repS67.red_codes(),
          f"红={[(i[0], i[3][:52]) for i in repS67.items if i[0] == 'C23']}")

    st_none = mk_tree()
    patch(spec_of(st_none), "- 状态：生效\n", "")
    repS68 = run_checks(st_none)
    green("S68 C23 哨兵：不写状态行不红（不写不查，存量项目零冲击）",
          "C23" not in repS68.red_codes() and not repS68.red_codes(),
          f"红={sorted(repS68.red_codes())}")

    st_tbd = mk_tree()
    patch(spec_of(st_tbd), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 备注：取消后的时段释放策略 TBD")
    repS69 = run_checks(st_tbd)
    green("S69 C24 TBD 无有效至 ⇒ 红（没有到期日的 TBD＝永远赖账）",
          "C24" in repS69.red_codes(),
          f"红={[(i[0], i[3][:52]) for i in repS69.items if i[0] == 'C24']}")

    st_tbd_ok = mk_tree()
    patch(spec_of(st_tbd_ok), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 备注：取消后的时段释放策略 TBD\n- 有效至：2027-01-31")
    repS70 = run_checks(st_tbd_ok)
    green("S70 C24 哨兵：TBD 带有效至不红（到期判红归收口断言，本版只管形状）",
          not repS70.red_codes(),
          f"红={sorted(repS70.red_codes())}")

    st_sup = mk_tree()
    patch(spec_of(st_sup), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 取代：R-1\n- 有效至：2027-01-31")
    patch(spec_of(st_sup), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 被取代：R-3")
    repS71 = run_checks(st_sup)
    green("S71 哨兵：双向成对的取代/被取代＋有效至行零红（K04 解析层收口；C30 上线后夹具改双向成对，咬合语义由 C30 承接）",
          not repS71.red_codes(),
          f"红={sorted(repS71.red_codes())}")

    mS72 = run_mutant("STATE_LINE = ", 'STATE_LINE = re.compile(r"(?!)")', st_bad)
    green("S72 变异臂：STATE_LINE 打哑 ⇒ S67 现象消失（状态机检测面被钉住）",
          mS72 is not None and "C23" not in mS72.red_codes(),
          f"变异红={sorted(mS72.red_codes()) if mS72 else 'None'}")

    mS73 = run_mutant("TBD_MARK = ", 'TBD_MARK = re.compile(r"(?!)")', st_tbd)
    green("S73 变异臂：TBD_MARK 打哑 ⇒ S69 现象消失（TBD 检测面被钉住）",
          mS73 is not None and "C24" not in mS73.red_codes(),
          f"变异红={sorted(mS73.red_codes()) if mS73 else 'None'}")

    mS74 = run_mutant("TTL_SHAPE = ", 'TTL_SHAPE = re.compile(r"(?!)")', st_tbd_ok)
    green("S74 变异臂：TTL_SHAPE 打哑 ⇒ S70 现象翻红（有效至识别面被钉住）",
          mS74 is not None and "C24" in mS74.red_codes(),
          f"变异红={sorted(mS74.red_codes()) if mS74 else 'None'}")

    # —— v1.9.0（K09 · MUST-12 变更可溯源）S75~S88：零增量逃生口（C08 豁免＋C25 声明矛盾红）、
    # provenance 来源强制（C26）、歧义冻结（C27）、Capabilities 路径在场（C28 警）＋四变异臂
    def add_skip(r):
        patch(r / "changes/add-cancel/proposal.md", "## 影响面",
              "## 影响面\nspecs/booking 的 R-3 改、R-5 加、R-1 删；无数据库结构变化。"
              "\n\n## Skip-Specs\n\n本变更为纯流程调整，无 specs 行为变化。")

    e75 = mk_tree()
    shutil.rmtree(e75 / "changes/add-cancel/specs")
    rep75 = run_checks(e75)
    green("S75 回归哨兵：空包无声明 ⇒ C08 照红（逃生口不许误豁免原红面）",
          "C08" in rep75.red_codes(),
          f"红={sorted(rep75.red_codes())}")

    e76 = mk_tree()
    shutil.rmtree(e76 / "changes/add-cancel/specs")
    add_skip(e76)
    rep76 = run_checks(e76)
    green("S76 C08 零增量逃生口：proposal 声明 Skip-Specs ⇒ 空包不红（对标 OpenSpec skip_specs）",
          not rep76.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep76.items if i[1] == '红']}")

    e77 = mk_tree()
    add_skip(e77)
    rep77 = run_checks(e77)
    green("S77 C25 skip 声明与事实矛盾：声明零增量却带着 specs/ 增量 ⇒ 红",
          "C25" in rep77.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep77.items if i[1] == '红']}")

    e78 = mk_tree()
    patch(e78 / "changes/add-cancel/specs/booking/spec.md",
          "- 来源：PRD 核心功能列表·取消释放\n", "")
    rep78 = run_checks(e78)
    green("S78 C26 增量 ADDED 块缺「来源：」行 ⇒ 红（每条款带出处，P0-6 provenance）",
          "C26" in rep78.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep78.items if i[1] == '红']}")

    e79 = mk_tree()
    patch(e79 / "changes/add-cancel/specs/booking/spec.md",
          "- 来源：PRD 规则五", "- 来源：无")
    rep79 = run_checks(e79)
    green("S79 C26 裸来源「来源：无」不算溯源 ⇒ 红（防糊弄面）",
          "C26" in rep79.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep79.items if i[1] == '红']}")

    e80 = mk_tree()
    patch(spec_of(e80), "- 状态：生效\n- 验收：卡B3 tests/test_cancel.py",
          "- 状态：生效\n- open-questions：会议开始边界取服务器时间还是提交时间\n- 验收：卡B3 tests/test_cancel.py")
    rep80 = run_checks(e80)
    green("S80 C27 歧义冻结：登记 open-questions 却停在生效 ⇒ 红（脑补口径不许写成承诺）",
          "C27" in rep80.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep80.items if i[1] == '红']}")

    e81 = mk_tree()
    patch(spec_of(e81), "- 状态：生效\n- 验收：卡B3 tests/test_cancel.py",
          "- 状态：草案\n- open-questions：会议开始边界取服务器时间还是提交时间\n- 验收：卡B3 tests/test_cancel.py")
    rep81 = run_checks(e81)
    green("S81 C27 哨兵：登记 open-questions 且冻结在草案 ⇒ 零红（冻结＝登记的合法归宿）",
          not rep81.red_codes(),
          f"红={sorted(rep81.red_codes())}")

    e82 = mk_tree()
    patch(spec_of(e82), "- 状态：生效\n- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- open-questions：会议开始边界取服务器时间还是提交时间")
    rep82 = run_checks(e82)
    green("S82 C27 冻结无载体：登记 open-questions 但状态行缺失 ⇒ 红（冻结要有状态可挂）",
          "C27" in rep82.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep82.items if i[1] == '红']}")

    def add_capab(r, body):
        patch(r / "changes/add-cancel/proposal.md", "## 影响面",
              f"## 影响面\nspecs/booking 的 R-3 改、R-5 加、R-1 删。\n\n## Capabilities\n\n{body}")

    e83 = mk_tree()
    add_capab(e83, "### New\n- ghost: specs/ghost/spec.md\n\n### Modified\n- booking: specs/booking/spec.md")
    rep83 = run_checks(e83)
    green("S83 C28 Capabilities 节列了不存在的 specs/ghost ⇒ 记警恰一（proposal→specs 路径契约点名）",
          sum(1 for i in rep83.items if i[0] == "C28" and i[1] == "警") == 1
          and "C28" not in rep83.red_codes(),
          f"C28={[(i[1], i[3][:44]) for i in rep83.items if i[0] == 'C28']}")

    e84 = mk_tree()
    add_capab(e84, "### Modified\n- booking: specs/booking/spec.md")
    rep84 = run_checks(e84)
    green("S84 C28 哨兵：Capabilities 列真实能力目录 ⇒ 零警零红（可选增强不误伤）",
          not [i for i in rep84.items if i[0] == "C28"],
          f"C28={[(i[1], i[3][:44]) for i in rep84.items if i[0] == 'C28']}")

    m85 = mk_tree()
    add_skip(m85)
    mr85 = run_mutant("SKIP_MARK = ", 'SKIP_MARK = re.compile(r"(?!)")', m85)
    green("S85 变异臂：SKIP_MARK 打哑 ⇒ S77 现象消失（逃生口识别面被钉住）",
          mr85 is not None and "C25" not in mr85.red_codes(),
          f"变异红={sorted(mr85.red_codes()) if mr85 else 'None'}")

    m86 = mk_tree()
    mr86 = run_mutant("SOURCE_LINE = ", 'SOURCE_LINE = re.compile(r"(?!)")', m86)
    green("S86 变异臂：SOURCE_LINE 打哑 ⇒ 地基树的合法来源行认不出、翻 C26 红（provenance 识别面被钉住）",
          mr86 is not None and "C26" in mr86.red_codes(),
          f"变异红={sorted(mr86.red_codes()) if mr86 else 'None'}")

    m87 = mk_tree()
    patch(spec_of(m87), "- 状态：生效\n- 验收：卡B3 tests/test_cancel.py",
          "- 状态：生效\n- open-questions：会议开始边界取服务器时间还是提交时间\n- 验收：卡B3 tests/test_cancel.py")
    mr87 = run_mutant("OPENQ_MARK = ", 'OPENQ_MARK = re.compile(r"(?!)")', m87)
    green("S87 变异臂：OPENQ_MARK 打哑 ⇒ S80 现象消失（歧义冻结检测面被钉住）",
          mr87 is not None and "C27" not in mr87.red_codes(),
          f"变异红={sorted(mr87.red_codes()) if mr87 else 'None'}")

    m88 = mk_tree()
    add_capab(m88, "### New\n- ghost: specs/ghost/spec.md")
    mr88 = run_mutant("CAPAB_HEAD = ", 'CAPAB_HEAD = re.compile(r"(?!)")', m88)
    green("S88 变异臂：CAPAB_HEAD 打哑 ⇒ S83 现象消失（Capabilities 检测面被钉住）",
          mr88 is not None and not [i for i in mr88.items if i[0] == "C28"],
          f"变异后 C28={[(i[1], i[3][:36]) for i in (mr88.items if mr88 else []) if i[0] == 'C28']}")

    # —— v1.10.0（K10 · MUST-13 收口）S89~S92：TBD 有效至过期判红（C29）。
    # C24 管形状（有没有有效至行）、C29 管时效（日期过没过）——到期日一过还在 TBD＝赖账坐实。
    st_tbd_exp = mk_tree()
    patch(spec_of(st_tbd_exp), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 备注：取消后的时段释放策略 TBD\n- 有效至：2020-01-01")
    repS89 = run_checks(st_tbd_exp)
    green("S89 C29 TBD 有效至已过期 ⇒ 红（到期日一过还在 TBD＝赖账坐实；C24 只管形状不重复咬）",
          "C29" in repS89.red_codes() and "C24" not in repS89.red_codes(),
          f"红={sorted(repS89.red_codes())}")

    green("S90 C29 哨兵：有效至未到期 ⇒ 零红（到期判红只咬过期件，未到期不红）",
          "C29" not in repS70.red_codes(),
          f"红={sorted(repS70.red_codes())}")

    mS91 = run_mutant("def _ttl_expired", "def _ttl_expired(iso_date):\n    return False", st_tbd_exp)
    green("S91 变异臂：_ttl_expired 打哑 ⇒ S89 现象消失（过期判定面被钉住）",
          mS91 is not None and "C29" not in mS91.red_codes(),
          f"变异红={sorted(mS91.red_codes()) if mS91 else 'None'}")

    st_tbd_bad = mk_tree()
    patch(spec_of(st_tbd_bad), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 备注：取消后的时段释放策略 TBD\n- 有效至：2027-13-45")
    repS92 = run_checks(st_tbd_bad)
    green("S92 C29 哨兵：有效至形状过但日期值非法 ⇒ 不红不炸（收口断言不越权，值合法性后续只追加）",
          "C29" not in repS92.red_codes(),
          f"红={sorted(repS92.red_codes())}")

    # —— v1.11.0（K12 · MUST-15/04）S93~S99：supersede 双向咬合（C30）＋两变异臂＋C22 三元等式钉。
    # 双向缺一即红：块写「取代：R-x」⇒ 对侧块在场且带「被取代：R-<本块>」回指，反向同理；单边、
    # 指针落空、自指都翻红。文档级路径指针归 doc_lint D02–D04/D16，两尺按指针值形态分工互斥（细则 §6.8）。
    e93 = mk_tree()
    patch(spec_of(e93), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 取代：R-1")
    rep93 = run_checks(e93)
    green("S93 C30 单边取代：R-3 标「取代：R-1」而 R-1 没有「被取代：R-3」回指 ⇒ 红（双向缺一即红）",
          "C30" in rep93.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep93.items if i[0] == 'C30']}")

    e94 = mk_tree()
    patch(spec_of(e94), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 被取代：R-3")
    rep94 = run_checks(e94)
    green("S94 C30 反向单边：R-1 标「被取代：R-3」而 R-3 没有「取代：R-1」⇒ 红（反向同咬）",
          "C30" in rep94.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep94.items if i[0] == 'C30']}")

    e95 = mk_tree()
    patch(spec_of(e95), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 取代：R-99")
    rep95 = run_checks(e95)
    green("S95 C30 对侧不在场：取代指针指向查无此块的 R-99 ⇒ 红（指针落空＝双向无从咬合）",
          "C30" in rep95.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep95.items if i[0] == 'C30']}")

    e96 = mk_tree()
    patch(spec_of(e96), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 取代：R-3")
    rep96 = run_checks(e96)
    green("S96 C30 自指：R-3 标「取代：R-3」⇒ 红（自己取代自己＝没改任何东西）",
          "C30" in rep96.red_codes(),
          f"红={[(i[0], i[3][:46]) for i in rep96.items if i[0] == 'C30']}")

    m93 = mk_tree()
    patch(spec_of(m93), "- 验收：卡B3 tests/test_cancel.py",
          "- 验收：卡B3 tests/test_cancel.py\n- 取代：R-1")
    mr93 = run_mutant("SUP_TAKE = ", 'SUP_TAKE = re.compile(r"(?!)")', m93)
    green("S97 变异臂：SUP_TAKE 打哑 ⇒ S93 现象消失（取代识别面被钉住）",
          mr93 is not None and "C30" not in mr93.red_codes(),
          f"变异红={sorted(mr93.red_codes()) if mr93 else 'None'}")

    m94 = mk_tree()
    patch(spec_of(m94), "- 验收：卡B1 tests/test_rules.py",
          "- 验收：卡B1 tests/test_rules.py\n- 被取代：R-3")
    mr94 = run_mutant("SUP_BY = ", 'SUP_BY = re.compile(r"(?!)")', m94)
    green("S98 变异臂：SUP_BY 打哑 ⇒ S94 现象消失（被取代识别面被钉住）",
          mr94 is not None and "C30" not in mr94.red_codes(),
          f"变异红={sorted(mr94.red_codes()) if mr94 else 'None'}")

    # RFC2119 标级批（K12）：C22 三元等式的中段钉进自证——CHECKS 条目数＝细则断言表行数
    # （自称数↔CHECKS 的等式由 S55/S60 的 C22 机制钉；后续加断言若漏加断言表行 ⇒ 此处当场翻红）
    rule_rows = [l for l in read_text(skill_root / "references" / "规格层规范.md").splitlines()
                 if re.match(r"^\|\s*C\d{2}\s*\|", l)]
    green("S99 C22 三元等式钉：CHECKS 条目数＝细则断言表行数（真根活体绑定，K12 RFC2119 标级批）",
          len(rule_rows) == len(CHECKS),
          f"表行={len(rule_rows)} CHECKS={len(CHECKS)}")

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
    ap = argparse.ArgumentParser(description="EverSpec 规格层机械校验器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("check", help="检查一个项目的规格层")
    a1.add_argument("--root", required=True)
    a1.add_argument("--strict", action="store_true", help="把警当红")
    a1.add_argument("--json", action="store_true", help="机读输出：stdout 只打一行 JSON 对象（字段见细则 §6.7）")
    a1.set_defaults(func=cmd_check)
    a2 = sub.add_parser("selftest", help="红例自证（只写临时树）")
    a2.add_argument("--tmp-root", default=None, help="临时树位置（默认 <skill根>/_tmp-selftest）")
    a2.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
