"""td_evidence.py · 测试纪律的确定性卸载件（capture + check）。

capture：跑命令 + 把七字段里机器能取的那几件当场写进运行记录，人只填判断部分。
check：对记录做机械校验，红项非零退出。两者共用同一份字段表，避免模板与判据各写一遍口径。
check --format freeform：粗检模式，给没按 capture 形状写的记录（旧记录／手写／别家模板）用。
"""

import argparse
import hashlib
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone

FIELDS = ["代码版本", "命令", "环境", "退出码", "时间", "数据集版本", "产物位置"]
FIELD_SYNONYMS = {
    "代码版本": ("代码版本", "提交号", "commit", "sha256", "哈希", "版本"),
    "命令": ("命令", "command", "跑的是什么", "执行命令"),
    "环境": ("环境", "解释器", "os", "python", "依赖"),
    "退出码": ("退出码", "exit"),
    "时间": ("时间", "起=", "开始", "墙钟", "耗时"),
    "数据集版本": ("数据集", "样本", "样本量", "n=", "评测集"),
    "产物位置": ("产物", "原始输出", "落盘", "路径", ".txt", ".log"),
}
STATE_WORDS = ("已证明", "现状快照", "未测")
SECRET_PATTERNS = [
    r"api[_-]?key",
    r"secret",
    r"password",
    r"passwd",
    r"private key",
    r"BEGIN [A-Z ]*PRIVATE KEY",
    r"Bearer\s+[A-Za-z0-9._\-]{12,}",
    r"gh[pousr]_[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"eyJ[A-Za-z0-9_\-]{12,}\.eyJ",
    r"[a-z][a-z0-9+.-]*://[^\s/]+:[^\s@]+@",
]
PLACEHOLDER = "【待填】"
EXIT_RE = re.compile(r"^F4\.退出码:[ \t]*(\d+)[ \t]*$", re.M)
FIELD_RE = re.compile(r"^F(\d)\.([^\s:]+):[ \t]*(.*)$", re.M)
PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?[^\s|、，。（）()]*?\.(?:txt|log|md|json|jsonl|csv|tsv|xml|html|png)"
)
RAW_EXT = (".txt", ".log", ".json", ".jsonl", ".csv", ".tsv")
N_RE = re.compile(r"\bn\s*[=＝:：]\s*\d+")
R_RE = re.compile(r"\br\s*[=＝:：]\s*\d+")
SPREAD_RE = re.compile(r"(波动|min|max|标准差|置信|range|区间)", re.I)
UNREC_RE = re.compile(r"未记录")
QUAL_RE = re.compile(
    r"n\s*[=＝:：]\s*\d|r\s*[=＝:：]\s*\d|波动|区间|标准差|置信|口径|分母|来源|样本|不适用|未测|未见稳定差异|不可比",
    re.I,
)
CLAIM_RE = re.compile(
    r"\d[\d.,]*\s*%|提升了|提高到|提升到|降低了|快了|省了|准确率|命中率|召回率|精确率|F1\s*[=:：]\s*[\d.]+",
    re.I,
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git_info(cwd):
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10
        )
        if head.returncode != 0:
            return None
        st = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=10
        )
        return head.stdout.strip(), ("dirty" if st.stdout.strip() else "clean")
    except Exception:
        return None


def read(path):
    """读文本并归一行尾：capture 写的是 CRLF（Windows 记事本直接可看），
    不归一的话 `^…$` 型判据会把行尾那个 `\\r` 当成值的一部分，把自己生成的记录判成假红。"""
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def cmd_capture(a):
    project = os.path.abspath(a.project or os.getcwd())
    outdir = os.path.abspath(a.out)
    os.makedirs(outdir, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    stem = "run_%s_%s_%s" % (day, a.task, a.stage)
    raw_path = os.path.join(outdir, stem + ".txt")
    rec_path = os.path.join(outdir, stem + ".md")
    if os.path.exists(raw_path) and not a.force:
        sys.stderr.write("红：目标已存在，拒绝覆盖（要重跑加 --force）：%s\n" % raw_path)
        return 2

    argv = a.command
    t0 = datetime.now(timezone.utc).astimezone()
    p = subprocess.run(argv, cwd=project, capture_output=True)
    t1 = datetime.now(timezone.utc).astimezone()
    blob = p.stdout + (b"\n--- STDERR ---\n" + p.stderr if p.stderr else b"")
    with open(raw_path, "wb") as f:
        f.write(blob)

    g = git_info(project)
    if g:
        code_ver = "%s (%s)" % g
    elif a.input:
        parts = []
        for rel in a.input:
            fp = os.path.join(project, rel)
            if not os.path.exists(fp):
                parts.append("%s=路径不存在" % rel)
            else:
                parts.append("%s=%s" % (rel.replace(os.sep, "/"), sha256(fp)[:12]))
        code_ver = "无版本库；被测文件 sha256 前 12 位：" + "、".join(parts)
    else:
        code_ver = "未记录：不是 git 仓库，且没用 --input 给出被测文件"

    ver = "%s.%s.%s" % sys.version_info[:3]
    lines = [
        "# 最小运行记录 · %s（%s）" % (a.task, a.stage),
        "",
        "> 第 1～5 项字段由 `td_evidence.py capture` 当场生成，不要手改；",
        "> 第 6 项与下面的人写段落必须你自己填，填不满写「未记录＋原因」。",
        "",
        "## A. 机器字段（capture 生成）",
        "",
        "F1.代码版本: %s" % code_ver,
        "F2.命令: %s" % " ".join(argv),
        "F3.环境: os=%s | 解释器=%s (%s) | 依赖=%s"
        % (platform.platform(), sys.executable, ver, a.deps or PLACEHOLDER),
        "F4.退出码: %d" % p.returncode,
        "F5.时间: 起=%s 止=%s 墙钟=%.2fs" % (t0.isoformat(timespec="seconds"), t1.isoformat(timespec="seconds"), (t1 - t0).total_seconds()),
        "F6.数据集版本: %s" % (a.dataset or PLACEHOLDER),
        "F7.产物位置: raw=%s | 记录=%s" % (raw_path.replace(os.sep, "/"), rec_path.replace(os.sep, "/")),
        "",
        "```",
        "原始输出字节数：%d，sha256：%s" % (len(blob), sha256(raw_path)),
        "```",
        "",
        "## B. 人写字段（必填，缺项写「未记录＋原因」）", "",
    ]
    if a.raw_tail:
        tail = blob.decode("utf-8", "replace").splitlines()[-a.raw_tail :]
        lines += ["```", *tail, "```", ""]
    lines += [
        "| 项 | 内容 |",
        "|---|---|",
        "| 0.这是什么改动（任务/一句话/类别①②③/本次角色） | %s |" % PLACEHOLDER,
        "| 结果（逐字摘原始输出里的汇总行） | %s |" % PLACEHOLDER,
        "| 结论三态（已证明／现状快照／未测） | %s |" % PLACEHOLDER,
        "| 断言变更（A 口径重算逐条／B 断言弱化单独列＋理由／无） | %s |" % PLACEHOLDER,
        "| 跳过项与原因（无写「无」） | %s |" % PLACEHOLDER,
        "",
        "## C. 基线不可用（仅取不到改前那份时填，三件缺一不合法）",
        "",
        "```",
        "原因：",
        "结论降级为：",
        "补齐条件：",
        "```",
        "",
    ]
    with open(rec_path, "wb") as f:
        f.write("\r\n".join(lines).encode("utf-8"))
    print("生成：%s" % rec_path.replace(os.sep, "/"))
    print("生成：%s（退出码 %d）" % (raw_path.replace(os.sep, "/"), p.returncode))
    return 0


PRODUCT_HINTS = ("产物", "原始输出", "raw=", "另见", "落在", "见 ", "evidence/", "结果文件")


def collect_cited(text, base, fields=None):
    """返回（在场引用件绝对路径, 红项, 黄项）。strict 与 freeform 共用同一把路径尺子。

    判"这个路径 token 是不是被引用的产物"看三件事：在 F7 行里／带目录分隔符／同行有产物类提示词。
    三者都不沾的裸文件名（例：说明文字里的 `pip freeze > env_freeze.txt`）只给黄，不判红——
    假红会把人推去绕闸门，比漏一条更糟。
    """
    reds = []
    warns = []
    cited = []
    parent = os.path.dirname(base)
    f7 = (fields or {}).get(7, "")
    for line in text.splitlines():
        line_is_f7 = line.lstrip().startswith("F7.")
        for raw_tok in PATH_RE.findall(line):
            p = raw_tok.strip("`").lstrip("`")
            is_raw = p.lower().endswith(RAW_EXT)
            in_f7 = line_is_f7 or (bool(f7) and p in f7)
            has_sep = ("/" in p) or ("\\" in p) or (len(p) > 1 and p[1] == ":")
            hinted = any(h in line for h in PRODUCT_HINTS)
            if not (is_raw or in_f7):
                continue
            must_exist = in_f7 or has_sep or hinted
            bn = os.path.basename(p)
            cands = [os.path.join(base, bn), p, os.path.join(base, p), os.path.join(parent, p)]
            hit = next((c for c in cands if c and os.path.exists(c)), None)
            if hit:
                cited.append(os.path.normpath(hit))
            elif must_exist:
                reds.append(("C1 路径不在场", "记录引用 %s，在同目录与上级目录都找不到该文件" % p))
            else:
                warns.append(("C1 提及未落盘", "记录里提到 %s 但盘上没有；若它是产物请补路径，若只是举例可忽略" % p))
    return cited, reds, warns


def scan_blobs(cited, rec):
    """凭据／空文件／复制改数：两种格式共用。"""
    reds = []
    for p in set(cited):
        if os.path.getsize(p) == 0:
            reds.append(("C1 空文件", "%s 是 0 字节" % p))
        if os.path.basename(p) == os.path.basename(rec):
            continue
        blob = read(p)
        for pat in SECRET_PATTERNS:
            hit = re.search(pat, blob, re.I)
            if hit:
                reds.append(("C8 凭据", "%s 命中密钥模式 %r（示例：%s）" % (os.path.basename(p), pat, hit.group(0)[:40])))
    dup = {}
    for p in set(cited):
        dup.setdefault(sha256(p), []).append(os.path.basename(p))
    for _h, names in dup.items():
        if len(names) > 1:
            reds.append(("C6 复制改数", "这些被引用文件字节完全相同：%s" % names))
    return reds


def scan_claims(text):
    reds = []
    bad = []
    for ln in text.splitlines():
        if CLAIM_RE.search(ln) and not QUAL_RE.search(ln):
            bad.append(ln.strip()[:70])
    if bad:
        reds.append(("C9 提升无口径", "%d 行报了数字/提升却没带口径（n／r／波动／分母／来源／未测之一）：%s" % (len(bad), " ‖ ".join(bad))))
    return reds


def scan_state_and_control(text, cited):
    """三态归位 ＋ 声称已证明必须有 before/after 两份对照。"""
    reds = []
    m = re.search(r"^\|\s*结论三态[^|]*\|\s*([^|\n]+?)\s*\|\s*$", text, re.M)
    if m is None:
        m = re.search(r"结论三态[^:：\n]*[:：]\s*(.+)", text)
    cell = m.group(1).strip() if m else None
    if cell is None:
        reds.append(("C10 三态缺失", "找不到「结论三态」那一行（自由格式请写「结论三态：已证明／现状快照／未测」）"))
    else:
        if not any(w in cell for w in STATE_WORDS):
            reds.append(("C10 三态未归位", "「结论三态」的值里三态之一都没有：%r" % cell[:40]))
    want = cell if cell is not None else text
    if "已证明" in want:
        have = {k for k in ("before", "after") if any(k in os.path.basename(c) for c in cited)}
        miss = [k for k in ("before", "after") if k not in have]
        if miss:
            reds.append(("C11 已证明缺对照", "声称已证明，但被引用产物里缺 %s 那一份" % "/".join(miss)))
    return reds


BOILER_UNREC_RE = re.compile(r"未记录[＋+]原因")


def scan_unrecorded(text_of_field_values):
    """未记录必须带原因；两种格式共用（传值列表）。模板提示语「未记录＋原因」不计。"""
    reds = []
    vals = [BOILER_UNREC_RE.sub("", v) for v in text_of_field_values]
    hits = [v for v in vals if UNREC_RE.search(v)]
    for v in hits:
        reason = v.split("未记录", 1)[1].lstrip("：:，, ")
        if len(reason) < 4:
            reds.append(("C2 未记录无原因", "「未记录」后面没给原因：%r" % v[:60]))
    if len(hits) >= 3:
        reds.append(("C3 逃生舱超载", "%d 处写「未记录」⇒ 本记录按未测处理，不许出现在「已证明」栏" % len(hits)))
    return reds


def cmd_check_strict(text, rec, base):
    reds = []
    fields = {int(n): val.strip() for n, _name, val in FIELD_RE.findall(text)}
    for i, name in enumerate(FIELDS, 1):
        if i not in fields:
            reds.append(("C2 缺字段", "F%d.%s 整行不在记录里" % (i, name)))
            continue
        v = fields[i]
        if not v:
            reds.append(("C2 空值", "F%d.%s 值为空（要写「未记录＋原因」）" % (i, name)))
        elif "【" in v:
            reds.append(("C2 待填", "F%d.%s 里还有模板括号未替换成实际值：%s" % (i, name, v[:40])))

    reds += scan_unrecorded(list(fields.values()))

    m = EXIT_RE.search(text)
    if not m:
        reds.append(("C4 退出码", "F4 行不是一条纯数字退出码"))
    else:
        cmdline = fields.get(2, "")
        if "|" in cmdline and "EXIT=" not in text and "set -o pipefail" not in cmdline:
            reds.append(("C4 管道吃码", "命令里有管道，但记录里没有管道之外另取的退出码"))

    if "依赖=%s" % PLACEHOLDER in text:
        reds.append(("C2 待填", "F3 环境里的依赖栏是占位符"))

    cited, r, w = collect_cited(text, base, fields)
    reds += r
    soft = list(w)
    if not cited:
        reds.append(("C1 无在场产物", "记录里没有任何真实存在的原始输出文件"))
    reds += scan_blobs(cited, rec)

    for p in set(cited):
        ts = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc)
        mm = re.search(r"起=(\S+)", text)
        if mm:
            try:
                st = datetime.fromisoformat(mm.group(1))
            except ValueError:
                reds.append(("C7 时间格式", "F5 里的「起」不是 ISO8601"))
                continue
            drift = abs((st - ts).total_seconds())
            if drift > 6 * 3600:
                reds.append(("C7 补写嫌疑", "%s 的落盘时间与记录起始时间差 %.1f 小时" % (os.path.basename(p), drift / 3600)))

    body = text.split("## B.", 1)[1] if "## B." in text else text
    reds += scan_state_and_control(text, cited)
    if "【待填" in body:
        reds.append(("C2 待填", "人写段落里还有 %d 处占位符未替换" % body.count("【待填")))
    reds += scan_claims(text)
    return reds, soft


def cmd_check_freeform(text, rec, base):
    """粗检：不要求 F1.…F7. 行首格式，只查能被机械判定的四类硬伤＋七字段覆盖率。"""
    reds = []
    warns = [("格式", "freeform 粗检：不校验字段行格式与填充顺序，只查硬伤。要逐格判据请用 --format strict。")]

    cited, r, w = collect_cited(text, base)
    reds += r
    warns += w
    if not cited:
        reds.append(("C1 无在场产物", "记录里没有任何真实存在的原始输出文件（.txt/.log/.json/.csv 等）"))
    reds += scan_blobs(cited, rec)

    own = read(rec)
    for pat in SECRET_PATTERNS:
        hit = re.search(pat, own, re.I)
        if hit:
            reds.append(("C8 凭据", "记录正文自身命中密钥模式 %r（示例：%s）" % (pat, hit.group(0)[:40])))

    reds += scan_unrecorded([ln for ln in text.splitlines()])
    reds += scan_claims(text)
    reds += scan_state_and_control(text, cited)

    missing = []
    low = text.lower()
    for name, syns in FIELD_SYNONYMS.items():
        if not any(s.lower() in low for s in syns):
            missing.append(name)
    for name in missing:
        warns.append(("C2 字段覆盖", "没在记录里看到「%s」的任何同义词 ⇒ 视为未记录，请补写或写明未记录＋原因" % name))
    if len(missing) >= 5:
        reds.append(("C2 记录过薄", "七字段里 %d 项（%s）都找不到 ⇒ 这不是一份运行记录，只是一段结论" % (len(missing), "、".join(missing))))

    if not re.search(r"退出码|exit\s*code", low):
        warns.append(("C4 退出码", "记录里没提退出码。接了管道时那个 0 可能不是被测命令的 0"))
    else:
        # 收紧口径：数字必须紧跟退出码，否则「退出码见原始输出，共 4 条」这种句子会蒙混过关
        ln = re.search(r"(?:退出码|exit\s*code)[ \t]*[:：=]?[ \t]*(\d+)\b", text, re.I)
        if not ln:
            reds.append(("C4 退出码", "提到退出码但后面没有紧跟一条纯数字（管道吃码风险，请在管道之外另取一次）"))
    return reds, warns


def cmd_check(a):
    rec = os.path.abspath(a.record)
    base = os.path.dirname(rec)
    text = read(rec)
    if a.fmt == "freeform":
        reds, warns = cmd_check_freeform(text, rec, base)
    else:
        reds, warns = cmd_check_strict(text, rec, base)
    print("校验对象：%s（格式 %s）" % (rec.replace(os.sep, "/"), a.fmt))
    for tag, msg in reds:
        print("红 [%s] %s" % (tag, msg))
    for tag, msg in warns:
        print("黄 [%s] %s" % (tag, msg))
    print("结论：%s（红 %d / 黄 %d）" % ("不通过" if reds else "通过", len(reds), len(warns)))
    return 1 if reds else 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="测试纪律确定性卸载件")
    sub = ap.add_subparsers(dest="op", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--task", required=True)
    c.add_argument("--stage", required=True, choices=["before", "after", "single"])
    c.add_argument("--out", required=True)
    c.add_argument("--project", default=None)
    c.add_argument("--deps", default=None)
    c.add_argument("--input", action="append", default=None)
    c.add_argument("--dataset", default=None)
    c.add_argument("--raw-tail", type=int, default=0)
    c.add_argument("--force", action="store_true")
    c.add_argument("command", nargs=argparse.REMAINDER)
    k = sub.add_parser("check")
    k.add_argument("record")
    k.add_argument("--format", dest="fmt", choices=["strict", "freeform"], default="strict")
    a = ap.parse_args(argv)
    if a.op == "capture":
        if a.command and a.command[0] == "--":
            a.command = a.command[1:]
        return cmd_capture(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
