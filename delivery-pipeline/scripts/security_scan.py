#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安检常驻化扫描器（security_scan）——交付流水线 ⑬ L1 六项的工具化面（v1.28.0）。

把安全基线检查.md §二 L1 清单里可 grep 的六项固化为规则（判据原文见该文件，本脚本是
它的机器面，不是第二套标准）：#1 密钥硬编码、#2 弱默认值、#7 注入形态、#8 输出直插、
#12 调试残留（分重/轻两级）、#15 本机绝对路径（历史区单列不阻断但点名计数）。
工具只覆盖形态面：命中 = 该项嫌疑成立，**判级与处置仍按细则人工走查**——
工具说"没抓到"不等于该项通过，七项触发式加检与人工面照旧。

    py scripts/security_scan.py scan --root <项目根> [--json] [--append <安检报告.md>]
    py scripts/security_scan.py check-signoff --report <安检报告.md> [--last-eval YYYY-MM-DD] [--strict-cooldown]
    py scripts/security_scan.py selftest [--tmp-root <目录>]

退出码：scan 0 无阻断（warn 可有）｜1 有阻断｜2 用法错。check-signoff 0 过｜1 红｜2 用法错。
selftest 0 全过｜2 有断言挂。零第三方依赖（Python 3 标准库）。
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

VERSION = "1.0.0"

# ---------------------------------------------------------------- 扫描范围

# 默认排除目录：依赖树/构建产物/证据落盘区/归档历史（evidence 里贴的上一轮违规输出不算现行为）
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    "coverage", ".idea", ".vscode", "evidence", "_archive", "archive", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "vendor", "bower_components",
}
# 生成物按文件名排除（安检报告自己的历史区里粘着上轮违规摘录，扫它=自我回声）
EXCLUDE_FILES = {"安检报告.md"}
# 历史区判定：命中进 history 桶（点名计数、不进退出码——证词不追改，铁律 22⑤ 口径）
HISTORY_FILE_RE = re.compile(
    r"(?:^|[/\\])(?:CHANGELOG|修订记录|台账_归档|.*归档.*)(?:\.|$)|_archive[/\\]|changes[/\\]archive[/\\]",
    re.IGNORECASE,
)
SCAN_SUFFIXES = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".env", ".md", ".txt", ".sh", ".bash", ".bat", ".ps1",
    ".sql", ".html", ".htm", ".css", ".scss", ".less", ".xml", ".properties", ".rb",
    ".go", ".java", ".php", ".c", ".h", ".cpp", ".cs",
}
MAX_FILE_BYTES = 1_000_000  # 超过 1MB 跳过并点名（防二进制/锁文件噪音）

# ---------------------------------------------------------------- 规则正则
# 每条规则 = (编号, 名称, 级别 fail|warn, [正则…])。改任何一条先跑 selftest（X01–X07 会当场翻红）。

RULE_SECRETS_PATTERNS = [
    # OpenAI 样态密钥（sk- 长串；sk-12345 这类短测试串不算）
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    # AWS AccessKeyId 样态
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # PEM 私钥头
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    # 赋值长串：password/secret/token 类键名 = 引号内 ≥8 字符字面量
    # （env 引用形态 os.environ.get(...) / process.env.X 不带引号字面量，天然不命中）
    re.compile(
        r"""(?ix)\b(?:secret|password|passwd|pwd|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key|signing[_-]?key)\b
            \s*[:=]\s*["']([^"']{8,})["']"""
    ),
]
# 占位值白名单：命中赋值长串但值明显是占位（教学样例/待填槽），降为不计
SECRET_PLACEHOLDER_RE = re.compile(
    r"""(?ix)^(?:your[_-].*|xxx+|<[a-z_]+>|\{\{.*\}\}|\$\{.*\}|<your.*|示例|占位|placeholder|dummy.*|example.*)$"""
)

RULE_WEAK_DEFAULT_PATTERNS = [
    # 弱默认清单：空值 / secret / changeme / admin / 123456 / test / default / password / example 等
    # （细则 #2 弱默认清单的字面量部分；"与项目或框架同名"人工判，工具不猜）
    re.compile(
        r"""(?ix)\b(?:secret|password|passwd|pwd|api[_-]?key|admin[_-]?pass|db[_-]?pass(?:word)?|token|access[_-]?key)\b
            \s*[:=]\s*["'](?:"" |'' |secret|changeme|change[_-]?me|admin|123456|12345678|test|default|password|example|dummy|placeholder)["']"""
    ),
]

RULE_INJECTION_PATTERNS = [
    # fail 面：SQL 执行口的字符串组装形态（f-string / 加号拼接 / % 取模）
    (re.compile(r"\.execute\s*\(\s*f[\"']", re.IGNORECASE), "fail", "execute(f-string SQL 拼接)"),
    (re.compile(r"\.execute\s*\(\s*[\"'][^\"']*[\"']\s*\+", re.IGNORECASE), "fail", "execute(字符串拼接)"),
    (re.compile(r"\.execute\s*\(\s*[\"'][^\"']*%s[^\"']*[\"']\s*%\s", re.IGNORECASE), "fail", "execute(% 取模拼接)"),
    # warn 面：动态执行/动态 SQL 形态本身只是嫌疑，输入源是否用户可控人工判（细则 #7 阻断标准是"eval 用户输入"）
    (re.compile(r"(?<![\w.])eval\s*\("), "warn", "eval() 调用点"),
    (re.compile(r"(?<![\w.])exec\s*\("), "warn", "exec() 调用点"),
    (re.compile(r"shell\s*=\s*True"), "warn", "subprocess shell=True"),
]

RULE_XSS_PATTERNS = [
    (re.compile(r"\bv-html\b"), "fail", "v-html 直插"),
    (re.compile(r"\.innerHTML\s*=(?!\s*[\"']{2})"), "fail", "innerHTML 直插"),
    (re.compile(r"\.outerHTML\s*=(?!\s*[\"']{2})"), "fail", "outerHTML 直插"),
    (re.compile(r"dangerouslySetInnerHTML"), "fail", "dangerouslySetInnerHTML"),
    (re.compile(r"document\.write\s*\("), "fail", "document.write 直插"),
]

RULE_RESIDUE_HEAVY = [
    # 重度：TODO/FIXME 里点名密钥密码；注释行里键名=值形态（疑似真实凭据被注释掉）
    re.compile(r"(?i)(?:todo|fixme|xxx).{0,40}(?:密码|密钥|password|secret|token|api[_-]?key)"),
    re.compile(r"(?i)(?:#|//|/\*|<!--).*\b(?:secret|password|api[_-]?key|private[_-]?key)\b\s*[:=]\s*\S"),
    re.compile(r"(?i)(?:#|//|/\*|<!--).{0,40}(?:生产|prod(?:uction)?|线上).{0,40}(?:https?://|服务器|地址)"),
]
RULE_RESIDUE_LIGHT = [
    re.compile(r"^\s*print\s*\("),                      # py 调试 print
    re.compile(r"(?m)^\s*console\.log\s*\("),           # js 调试输出
    re.compile(r"\bdebugger\b\s*;?"),
    re.compile(r"\bpdb\.set_trace\s*\(\s*\)"),
    re.compile(r"^\s*breakpoint\s*\(\s*\)"),
]

RULE_ABS_PATH_PATTERNS = [
    # 盘符路径：真实形态 E:\x 与源码转义字面 E:\\x 都算（[\\/]+ 吞连续分隔符）；后面跟非空白才成路径
    re.compile(r"[A-Za-z]:[\\/]+\S"),
    re.compile(r"(?<![A-Za-z:])/(?:home|Users|root)/[A-Za-z0-9_.\-]+"),
]

# ---------------------------------------------------------------- 规则执行


def _line_no(text, pos):
    return text.count("\n", 0, pos) + 1


def _snippet(text, pos, width=60):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    line = text[start:end].strip()
    return line[:width] + ("…" if len(line) > width else "")


def _is_comment_line(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    head = text[start:pos].strip()
    return head.startswith(("#", "//", "/*", "*", "<!--"))


def run_rule(rule_id, text):
    """对单文件文本跑一条规则，返回 [(line_no, snippet, level, note)]。"""
    hits = []
    if rule_id == "L1#1":
        for pat in RULE_SECRETS_PATTERNS:
            for m in pat.finditer(text):
                val = None
                if m.groups():
                    val = m.group(1)
                if val is not None and SECRET_PLACEHOLDER_RE.match(val.strip()):
                    continue  # 占位值（教学样例/待填槽）不算可用凭据
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), "fail", "疑似可用凭据样态"))
    elif rule_id == "L1#2":
        for pat in RULE_WEAK_DEFAULT_PATTERNS:
            for m in pat.finditer(text):
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), "fail", "安全项弱默认值（部署面覆盖人工核）"))
    elif rule_id == "L1#7":
        for pat, level, note in RULE_INJECTION_PATTERNS:
            for m in pat.finditer(text):
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), level, note))
    elif rule_id == "L1#8":
        for pat, level, note in RULE_XSS_PATTERNS:
            for m in pat.finditer(text):
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), level, note))
    elif rule_id == "L1#12":
        for pat in RULE_RESIDUE_HEAVY:
            for m in pat.finditer(text):
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), "fail", "重度残留（真实样态）"))
        for pat in RULE_RESIDUE_LIGHT:
            for m in pat.finditer(text):
                if rule_id == "L1#12" and "print" in pat.pattern and _is_comment_line(text, m.start()):
                    continue
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), "warn", "轻度残留（print/console.log/debugger）"))
    elif rule_id == "L1#15":
        for pat in RULE_ABS_PATH_PATTERNS:
            for m in pat.finditer(text):
                hits.append((_line_no(text, m.start()), _snippet(text, m.start()), "fail", "本机绝对路径（对外一律相对化）"))
    return hits


RULE_META = [
    ("L1#1", "密钥/凭据硬编码"),
    ("L1#2", "配置弱默认值"),
    ("L1#7", "注入形态（SQL 拼接/eval/shell）"),
    ("L1#8", "输出直插（XSS 形态）"),
    ("L1#12", "调试残留（重/轻两级）"),
    ("L1#15", "本机绝对路径"),
]


def iter_target_files(root):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.name in EXCLUDE_FILES:
            continue
        if p.suffix.lower() not in SCAN_SUFFIXES:
            continue
        is_history = bool(HISTORY_FILE_RE.search(str(rel))) or any(
            part.lower() in {"evidence", "_archive", "archive", "归档"} for part in rel.parts
        )
        yield p, rel, is_history


def scan_root(root):
    """返回 (per_rule_hits, skipped, history_hits)。
    per_rule_hits: {rule_id: [(rel_path, line, snippet, level, note)]}
    history_hits: [(rule_id, rel_path, line)]（历史区命中，点名不阻断）
    """
    per_rule = {rid: [] for rid, _ in RULE_META}
    history = []
    skipped = []
    for p, rel, is_history in iter_target_files(root):
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                skipped.append((str(rel), "超 1MB"))
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            skipped.append((str(rel), str(e)))
            continue
        for rid, _ in RULE_META:
            for line_no, snippet, level, note in run_rule(rid, text):
                if is_history:
                    history.append((rid, str(rel), line_no))
                else:
                    per_rule[rid].append((str(rel), line_no, snippet, level, note))
    return per_rule, skipped, history


def scan_exit_code(per_rule):
    worst = 0
    for rid, _ in RULE_META:
        for _, _, _, level, _ in per_rule[rid]:
            if level == "fail":
                worst = 1
    return worst


# ---------------------------------------------------------------- 安检报告追加区与签核机检

APPEND_SECTION_ANCHOR = "## 7. 历史判定追加区"
SIGNOFF_KEYS = ("签核人", "签核日期", "异议")


def build_append_block(per_rule, skipped, history, exit_code, date_stamp):
    """把一次 scan 的结果表排成 markdown 节（追加进安检报告历史判定区）。"""
    lines = [f"### {date_stamp}（security_scan v{VERSION}，退出码 {exit_code}）", ""]
    lines.append("| 规则 | 结论 | 命中数 | 首条位置 |")
    lines.append("|---|---|---|---|")
    for rid, name in RULE_META:
        hits = per_rule[rid]
        fails = [h for h in hits if h[3] == "fail"]
        warns = [h for h in hits if h[3] == "warn"]
        if fails:
            concl, first = "阻断", f"{hits[0][0]}:{hits[0][1]}"
        elif warns:
            concl, first = "建议", f"{hits[0][0]}:{hits[0][1]}"
        else:
            concl, first = "未命中", "—"
        extra = f"（另 warn {len(warns)} 条）" if fails and warns else ""
        lines.append(f"| {rid} {name} | {concl}{extra} | {len(hits)} | {first} |")
    if skipped:
        lines.append("")
        lines.append(f"跳过 {len(skipped)} 件：" + "；".join(f"{s[0]}（{s[1]}）" for s in skipped[:5]) +
                     ("…" if len(skipped) > 5 else ""))
    if history:
        lines.append("")
        lines.append(f"历史区命中 {len(history)} 条（点名不阻断）："
                     + "；".join(f"{h[1]}:{h[2]}（{h[0]}）" for h in history[:5])
                     + ("…" if len(history) > 5 else ""))
    lines.append("")
    return "\n".join(lines)


def append_to_report(report_path, block):
    """把 block 追加进报告 §7 历史判定追加区；无该节则在文末建节。返回追加后全文。"""
    report_path = Path(report_path)
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else "# 安检报告（第⑬步）\n"
    if APPEND_SECTION_ANCHOR in text:
        text = text.rstrip("\n") + "\n" + block
    else:
        text = text.rstrip("\n") + "\n\n" + APPEND_SECTION_ANCHOR + "\n\n" + block
    report_path.write_text(text, encoding="utf-8")
    return text


def check_signoff(report_path, last_eval=None, strict_cooldown=False):
    """安检报告签核三栏机检（compliance 场景；判据=安全基线检查.md §五 头部三栏）。
    返回 (exit_code, [断言结论行])。"""
    report_path = Path(report_path)
    results = []
    if not report_path.exists():
        return 2, [f"[FAIL] SO0 报告在盘：{report_path} 不存在"]
    text = report_path.read_text(encoding="utf-8")

    def grab(key):
        m = re.search(rf"签核人\s*[：:]\s*(.*)|签核日期\s*[：:]\s*(\S+)|异议\s*[：:]\s*(.*)", text)
        # 逐键独立抓，避免一条正则串键
        m2 = re.search(rf"{key}\s*[：:]\s*([^\n]*)", text)
        return m2.group(1).strip() if m2 else None

    signer = grab("签核人")
    sdate = grab("签核日期")
    dissent = grab("异议")
    code = 0

    if signer in (None, "") or re.fullmatch(r"(?i)(待签|tbd|todo|暂无|待定|—|-)", signer or ""):
        results.append(f"[FAIL] SO1 签核人已填且非占位：命中={signer!r}（compliance 场景必填，占位=没签）")
        code = 1
    else:
        results.append(f"[PASS] SO1 签核人已填：{signer}")

    if not sdate or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sdate or ""):
        results.append(f"[FAIL] SO2 签核日期格式 YYYY-MM-DD：命中={sdate!r}")
        code = 1
    else:
        results.append(f"[PASS] SO2 签核日期格式合法：{sdate}")

    if dissent is None or dissent == "":
        results.append("[FAIL] SO3 异议栏已填（无异议写「无」，禁空白——禁空白禁「暂无」铁律 20① 同款）")
        code = 1
    else:
        results.append(f"[PASS] SO3 异议栏：{dissent[:30]}")

    if last_eval:
        if sdate and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sdate or ""):
            if sdate < last_eval:
                results.append(f"[FAIL] SO4 签核日期 ≥ 最后一次评测日期：签核 {sdate} < 评测 {last_eval}（K4 同款口径：签在评测之前=签的不是这个版本）")
                code = 1
            elif strict_cooldown and sdate == last_eval:
                results.append(f"[FAIL] SO5 隔天冷却（compliance 高风险发布）：签核 {sdate} == 评测 {last_eval}，须隔一个自然日再签（对治 automation bias）")
                code = 1
            else:
                rel = "隔天（SO5 冷却过）" if strict_cooldown else "同日或之后（未启用 --strict-cooldown）"
                results.append(f"[PASS] SO4/SO5 日期序通过：签核 {sdate} {rel} 评测 {last_eval}")
        else:
            results.append("[FAIL] SO4 无法比对：签核日期缺失或格式不合法")
            code = 1
    return code, results


# ---------------------------------------------------------------- selftest（合成夹具；X 系断言＝变异探针常驻形态）

ASSERT_FLOOR = 20  # 断言分母闸：条数掉了就红，防悄悄删用例

SECRET_SAMPLE = 'API_KEY = "sk-abcdef1234567890abcdef1234567890abcd"\n'
SECRET_CLEAN = 'API_KEY = os.environ.get("API_KEY", "")\n'
WEAK_SAMPLE = 'DB_PASSWORD = "123456"\n'
WEAK_CLEAN = 'DB_PASSWORD = os.environ["DB_PASSWORD"]\n'
INJECT_FAIL_SAMPLE = 'cur.execute(f"SELECT * FROM u WHERE id={uid}")\n'
INJECT_WARN_SAMPLE = 'result = eval(user_expr)\n'
INJECT_CLEAN = 'cur.execute("SELECT * FROM u WHERE id=?", (uid,))\n'
XSS_SAMPLE = '<div v-html="raw"></div>\n'
XSS_CLEAN = "<div>{{ raw }}</div>\n"
RESIDUE_HEAVY_SAMPLE = "# TODO 下周把 生产 API 地址 https://prod.example.internal 换掉\n"
RESIDUE_LIGHT_SAMPLE = "print(debug_value)\n"
ABSPATH_SAMPLE = 'CONFIG = "E:\\\\Agent-work\\\\demo\\\\secret.pem"\n'
ABSPATH_CLEAN = 'CONFIG = "config/secret.pem"\n'


def _rule_hits(rule_id, text):
    return run_rule(rule_id, text)


def _fail_hits(rule_id, text):
    return [h for h in _rule_hits(rule_id, text) if h[2] == "fail"]


def _write(tmp, rel, content):
    p = Path(tmp) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _make_dirty_tree(tmp):
    _write(tmp, "app/settings.py", SECRET_SAMPLE + WEAK_SAMPLE + INJECT_FAIL_SAMPLE +
           INJECT_WARN_SAMPLE + RESIDUE_LIGHT_SAMPLE)
    _write(tmp, "web/view.vue", XSS_SAMPLE)
    _write(tmp, "notes.md", RESIDUE_HEAVY_SAMPLE + ABSPATH_SAMPLE)
    _write(tmp, "CHANGELOG.md", "旧路径证词：E:\\\\Agent-work\\\\old\\\\a.py（历史区不追改）\n")


def _make_clean_tree(tmp):
    _write(tmp, "app/settings.py", SECRET_CLEAN + WEAK_CLEAN + INJECT_CLEAN + "x = compute(1)\n")
    _write(tmp, "web/view.vue", XSS_CLEAN)
    _write(tmp, "README.md", "# demo\n相对路径 config/app.yaml，无绝对路径。\n")
    _write(tmp, "node_modules/pkg/evil.js", "API_KEY = \"sk-abcdef1234567890abcdef1234567890abcd\"\n")


def selftest(tmp_root=None):
    results = []
    ok_all = True

    def check(name, cond, detail=""):
        nonlocal ok_all
        results.append((name, bool(cond), detail))
        if not cond:
            ok_all = False

    # --- X01–X06 每规则「违规样例必被抓」（判据健全性：正则被改坏这里当场翻红＝变异探针常驻形态）
    check("X01 L1#1 密钥样例必抓", len(_fail_hits("L1#1", SECRET_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#1', SECRET_SAMPLE))}")
    check("X02 L1#2 弱默认样例必抓", len(_fail_hits("L1#2", WEAK_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#2', WEAK_SAMPLE))}")
    check("X03 L1#7 注入 fail 样例必抓", len(_fail_hits("L1#7", INJECT_FAIL_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#7', INJECT_FAIL_SAMPLE))}")
    check("X03b L1#7 eval 嫌疑可报(warn)", any(h[2] == "warn" for h in _rule_hits("L1#7", INJECT_WARN_SAMPLE)),
          f"命中={_rule_hits('L1#7', INJECT_WARN_SAMPLE)}")
    check("X04 L1#8 直插样例必抓", len(_fail_hits("L1#8", XSS_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#8', XSS_SAMPLE))}")
    check("X05 L1#12 重度残留必抓", len(_fail_hits("L1#12", RESIDUE_HEAVY_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#12', RESIDUE_HEAVY_SAMPLE))}")
    check("X06 L1#15 绝对路径样例必抓", len(_fail_hits("L1#15", ABSPATH_SAMPLE)) >= 1,
          f"命中={len(_fail_hits('L1#15', ABSPATH_SAMPLE))}")

    # --- X07 干净样例不误报（六规则各喂合法串）
    for rid, clean in [("L1#1", SECRET_CLEAN), ("L1#2", WEAK_CLEAN), ("L1#7", INJECT_CLEAN),
                       ("L1#8", XSS_CLEAN), ("L1#15", ABSPATH_CLEAN)]:
        check(f"X07 {rid} 合法样例零误报", len(_rule_hits(rid, clean)) == 0,
              f"误报={_rule_hits(rid, clean)}")

    # --- X08–X11 整树扫描行为
    tmp = Path(tempfile.mkdtemp(prefix="secscan-selftest-", dir=tmp_root))
    try:
        dirty = tmp / "dirty"
        clean = tmp / "clean"
        _make_dirty_tree(dirty)
        _make_clean_tree(clean)

        per, skipped, hist = scan_root(clean)
        check("X08 干净树零阻断", scan_exit_code(per) == 0 and all(not h for h in per.values()),
              f"命中={[h for h in per.values() if h]}")
        check("X09 排除目录生效（node_modules 不扫）", not any("node_modules" in h[0] for h in per["L1#1"]),
              f"L1#1 命中={per['L1#1']}")

        per, skipped, hist = scan_root(dirty)
        fail_rules = {rid for rid, _ in RULE_META if any(h[3] == "fail" for h in per[rid])}
        check("X10 脏树六规则全点亮", {"L1#1", "L1#2", "L1#7", "L1#8", "L1#12", "L1#15"} <= fail_rules,
              f"fail 规则={fail_rules}")
        check("X11 退出码 1 传导", scan_exit_code(per) == 1)

        # 历史区单列：CHANGELOG 里的旧路径点名但不阻断
        per2, _, hist2 = scan_root(dirty)
        changelog_hits = [h for h in hist2 if "CHANGELOG" in h[1]]
        blocked_files = {h[0] for h in per2["L1#15"]}
        check("X12 历史区单列不阻断（CHANGELOG 命中进 history、不在阻断集）",
              len(changelog_hits) >= 1 and not any("CHANGELOG" in f for f in blocked_files),
              f"history={hist2} 阻断文件={blocked_files}")

        # 追加区：一次 scan 写进报告，再跑出现第二期（时间序列可见）
        rep = tmp / "安检报告.md"
        date1, date2 = "2026-10-27", "2026-10-28"
        append_to_report(rep, build_append_block(per2, [], hist2, 1, date1))
        append_to_report(rep, build_append_block({rid: [] for rid, _ in RULE_META}, [], [], 0, date2))
        rep_text = rep.read_text(encoding="utf-8")
        check("X13 报告含历史判定追加区锚", APPEND_SECTION_ANCHOR in rep_text)
        check("X14 两期追加＝时间序列可见", f"### {date1}" in rep_text and f"### {date2}" in rep_text)

        # 签核机检五断言
        good_rep = tmp / "签核_合规.md"
        good_rep.write_text(
            "执行者：AI 窗口\n签核人：天天\n签核日期：2026-10-28\n异议：无\n", encoding="utf-8")
        code, _ = check_signoff(good_rep, last_eval="2026-10-27", strict_cooldown=True)
        check("X15 三栏齐＋隔天冷却 ⇒ 过", code == 0)

        bad_rep = tmp / "签核_缺人.md"
        bad_rep.write_text("执行者：AI 窗口\n签核人：待签\n签核日期：2026-10-27\n异议：\n", encoding="utf-8")
        code, rows = check_signoff(bad_rep, last_eval="2026-10-27", strict_cooldown=True)
        joined = "\n".join(rows)
        check("X16 占位签核人＋异议空白 ⇒ 红", code == 1 and "SO1" in joined and "SO3" in joined,
              f"rows={rows}")

        early_rep = tmp / "签核_早签.md"
        early_rep.write_text("签核人：天天\n签核日期：2026-10-26\n异议：无\n", encoding="utf-8")
        code, rows = check_signoff(early_rep, last_eval="2026-10-27")
        check("X17 签核日期早于最后评测 ⇒ 红（SO4）", code == 1 and any("SO4" in r for r in rows),
              f"rows={rows}")

        sameday_rep = tmp / "签核_同日.md"
        sameday_rep.write_text("签核人：天天\n签核日期：2026-10-27\n异议：无\n", encoding="utf-8")
        code_same, _ = check_signoff(sameday_rep, last_eval="2026-10-27")
        code_cool, rows_cool = check_signoff(sameday_rep, last_eval="2026-10-27", strict_cooldown=True)
        check("X18 同日签：普通过／冷却红（SO5）", code_same == 0 and code_cool == 1,
              f"冷却 rows={rows_cool}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 分母闸
    n = len(results)
    check(f"X99 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", n >= ASSERT_FLOOR, f"本轮 {n} 条")

    n_total = len(results)
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"｜{detail}" if detail and not ok else ""))
    print(f"断言 {n_total} 条：PASS={sum(1 for _, o, _ in results if o)} FAIL={sum(1 for _, o, _ in results if not o)}")
    return 0 if ok_all else 2


# ---------------------------------------------------------------- main


def _print_scan(per_rule, skipped, history, as_json=False):
    exit_code = scan_exit_code(per_rule)
    if as_json:
        print(json.dumps({
            "version": VERSION, "exit_code": exit_code,
            "rules": {rid: [{"file": f, "line": ln, "level": lv, "note": nt, "snippet": sn}
                            for f, ln, sn, lv, nt in hits]
                      for rid, hits in per_rule.items()},
            "history": [{"rule": r, "file": f, "line": ln} for r, f, ln in history],
            "skipped": [{"file": f, "reason": r} for f, r in skipped],
        }, ensure_ascii=False, indent=2))
        return exit_code
    for rid, name in RULE_META:
        hits = per_rule[rid]
        fails = [h for h in hits if h[3] == "fail"]
        warns = [h for h in hits if h[3] == "warn"]
        concl = "阻断" if fails else ("建议" if warns else "未命中")
        print(f"\n[{concl}] {rid} {name}（fail {len(fails)}／warn {len(warns)}）")
        for f, ln, sn, lv, nt in (fails + warns)[:8]:
            print(f"  {lv.upper():4} {f}:{ln}  {nt}  ｜ {sn}")
        if len(fails) + len(warns) > 8:
            print(f"  …另 {len(fails) + len(warns) - 8} 条")
    if skipped:
        print(f"\n跳过 {len(skipped)} 件：" + "；".join(f"{s[0]}（{s[1]}）" for s in skipped[:5]) +
              ("…" if len(skipped) > 5 else ""))
    if history:
        print(f"历史区命中 {len(history)} 条（点名不阻断，证词不追改）："
              + "；".join(f"{h[1]}:{h[2]}（{h[0]}）" for h in history[:5]) + ("…" if len(history) > 5 else ""))
    print(f"\n结论：{'有阻断项 → 切修复卡（人工判级照安全基线检查.md §二）' if exit_code else '六项无形态命中（人工面照旧：#3-#6/#9-#11/#13/#14/#16 与触发式加检不在工具面）'}")
    return exit_code


def main(argv=None):
    ap = argparse.ArgumentParser(description="安检常驻化扫描器（L1 六项工具化＋签核机检＋报告追加区）")
    sub = ap.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="对项目根跑 L1 六项规则")
    p_scan.add_argument("--root", required=True)
    p_scan.add_argument("--json", action="store_true", dest="as_json")
    p_scan.add_argument("--append", metavar="安检报告.md", help="把当次结果追加进报告 §7 历史判定追加区")
    p_scan.add_argument("--date", metavar="YYYY-MM-DD", help="追加区日期戳（默认今天）")

    p_so = sub.add_parser("check-signoff", help="安检报告签核三栏机检")
    p_so.add_argument("--report", required=True)
    p_so.add_argument("--last-eval", metavar="YYYY-MM-DD", default=None)
    p_so.add_argument("--strict-cooldown", action="store_true", help="隔天冷却：签核日期必须晚于最后评测日（compliance 高风险发布）")

    p_st = sub.add_parser("selftest", help="合成夹具自证（X 系断言，含变异探针常驻形态）")
    p_st.add_argument("--tmp-root", help=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    if args.cmd == "scan":
        if not Path(args.root).exists():
            print(f"根目录不存在：{args.root}")
            return 2
        per_rule, skipped, history = scan_root(args.root)
        code = _print_scan(per_rule, skipped, history, as_json=args.as_json)
        if args.append:
            import datetime
            stamp = args.date or datetime.date.today().isoformat()
            block = build_append_block(per_rule, skipped, history, code, stamp)
            append_to_report(args.append, block)
            print(f"已追加进 {args.append} §7 历史判定追加区（{stamp}）")
        return code
    if args.cmd == "check-signoff":
        code, rows = check_signoff(args.report, last_eval=args.last_eval, strict_cooldown=args.strict_cooldown)
        for r in rows:
            print(r)
        return code
    if args.cmd == "selftest":
        return selftest(tmp_root=args.tmp_root)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
