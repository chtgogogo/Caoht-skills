#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""check_encoding.py — 落盘文本的编码体检（零依赖，Python 3.8+）。

它取代旧判据「中文字符数 > 0 且 问号数 = 0」。旧判据两个面都会错：

* 漏判：真正的损坏常常**不长成问号**。UTF-8 字节被按 cp1252/latin-1 读坏后写回文件，
  得到的是「â€™ / Ã©」这类合法拉丁字符；GBK 链式损坏得到的是「锟斤拷」这类合法汉字。   # encoding-check:ignore
  这些文本问号数为 0、中文字符数 > 0，旧判据全绿，内容却是废的。
* 误判：正常文本里本来就可以有 `?`（英文疑问句、三元运算符、正则量词）；纯英文产物
  中文字符数天然为 0。按旧判据它们都算「编码失败」，于是人会开始忽略这条检查。

本脚本的判据（命中即判红）：
  E1 严格解码失败      bytes.decode("utf-8") 抛 UnicodeDecodeError
  E2 替换字符          U+FFFD（解码时用 errors="replace"/"ignore" 留下的疤）
  E3 控制字符          C0（\t \n \r 除外）与 C1（U+0080–U+009F），含 NUL
  E4 隐形字符          零宽系列与双向格式符（Trojan Source 一类，肉眼看不见）
  E5 乱码特征          ① 命中已知 mojibake 特征串；② 拉丁连续段能反向 encode→decode
                       成更短的序列（UTF-8 多字节被当单字节读坏后的**塌缩**形状）
  E6 base64 未解码     整段像一个 base64 大块（GitHub contents 接口的原始返回没解码就落盘）

只打印、不判红的辅助信号：中文字符数、ASCII 问号数、非 ASCII 占比、行数、字节数、被豁免命中数。

逐行豁免（唯一的降级出口）：某一行行尾写了 `encoding-check:ignore`（纯 ASCII 标记），这一行的命中
不计入判红，但**每次运行都会打印「豁免 N 处」**——它是给「文件本身就在讲乱码长什么样」的教学正文、
判据表、反例样例用的，不是给真损坏用的；真损坏的正确修法是把内容换掉，不是加标记。
本脚本自身的特征表因此一律用 `\u` 转义写（写字面量就会被自己打红，实测 9 处假红）。

🔴 已知盲区（如实写明）：把 UTF-8 字节按 GBK 读，会产出**合法但语义错乱**的汉字
（例："中文" → "涓腑"）。这类损坏结构上完全合法，机检抓不到——只能与来源字节数
（API 的 `size` 字段）对账，或抽一段与原文逐字比。

用法：
    py scripts/check_encoding.py <文件或目录>...           # 体检
    py scripts/check_encoding.py <路径>... --json          # 机器可读输出
    py scripts/check_encoding.py --selftest                # 自带合成样例自检（不落一个字节）
    py scripts/check_encoding.py --print-codes             # 打印判据编号表

退出码：0 全部通过｜1 有判红｜2 用法错误或读文件失败
"""

import argparse
import base64
import binascii
import json
import re
import sys
from pathlib import Path

CODES = {
    "E1": "UTF-8 严格解码失败",
    "E2": "含 U+FFFD 替换字符",
    "E3": "含控制字符（\t \n \r 之外）",
    "E4": "含零宽／双向隐形字符",
    "E5": "疑似乱码（mojibake）",
    "E6": "疑似 base64 原文未解码就落盘",
}
SIGNALS = ("chinese_chars", "ascii_question_marks", "non_ascii_ratio_pct", "lines", "bytes",
           "suppressed_by_marker")

# 允许出现的 C0 控制字符；\x1b(ESC) 不放行——终端色码不该进分析文档
ALLOWED_C0 = "\t\n\r"
INVISIBLE = {
    "\u200b": "零宽空格 ZWSP",
    "\u200c": "零宽不连字 ZWNJ",
    "\u200d": "零宽连字 ZWJ",
    "\u2060": "不看式字符 WORD JOINER",
    "\ufeff": "零宽不换行空格／BOM",
    "\u202a": "方向嵌入 LRE",
    "\u202b": "方向嵌入 RLE",
    "\u202c": "方向终止 PDF",
    "\u202d": "方向覆盖 LRO",
    "\u202e": "方向覆盖 RLO",
    "\u2066": "方向隔离 LRI",
    "\u2067": "方向隔离 RLI",
    "\u2068": "方向隔离 FSI",
    "\u2069": "方向隔离 PDI",
}
MOJIBAKE_SIGNATURES = [
    # 🔴 这一整张表一律用 \u 转义写，不写字面乱码：本脚本自己就是「乱码样本的集合」，
    #    写了字面量就会被自己的判据打成红（实测：字面版自查报 9 处 E5，全是特征表自己）。
    #    转义后运行时值完全相同——S7 这条断言就是它的回归网，写错一个码位 SELFTEST 立刻翻红。
    "\u951f\u65a4\u62f7",  # 「锟斤拷」——GBK 链式替换的经典形状（本行是举例，故带豁免标记）encoding-check:ignore
    "\ufeff?",  # "锘" —— UTF-8 BOM 被按 GBK 读
    "\u00e2\u20ac",  # "â€" —— UTF-8 被按 cp1252 读的开头（é＝U+00E2，€＝U+20AC）encoding-check:ignore
    "\u00c3\u00a9", "\u00c3\u00a8", "\u00c3\u00a4", "\u00c3\u00b6", "\u00c3\u00bc",
    "\u00c3\u00a1", "\u00c3\u00b3",
]
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# cp1252 码页里 0x80–0xFF 这 128 个字符＝UTF-8 多字节被按单字节读坏后「必然长成的样子」
# （bytes(range) 直接映射，未定义位 0x81/0x8D/0x8F/0x90/0x9D 由 errors="ignore" 丢掉）
SUSPECT_CHARS = frozenset(bytes(range(0x80, 0x100)).decode("cp1252", errors="ignore"))
ASCII_QUESTION = "?"
# 逐行豁免标记（纯 ASCII，任何编码事故都还在）：写在这一行末尾即豁免本行命中，见 _apply_line_markers
IGNORE_TOKEN = "encoding-check:ignore"
# 整段 base64：长、无空格、字符集干净、长度是 4 的倍数
B64_BLOCK_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")

BIN_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar", ".xz", ".bz2",
    ".exe", ".dll", ".so", ".dylib", ".pyd", ".pyc", ".bin", ".iso", ".msi",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".pdb", ".obj", ".lib", ".class", ".jar", ".wheel",
    # git bundle 是打包后的二进制（发布仓根就有一个备份件）：按文本检会得到 E1/E3/E4/E5 一整套假红
    ".bundle",
}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist", "build"}


def _pos(text, idx):
    """把字符下标换成「第几行第几列」，方便人一眼定位。"""
    line = text.count("\n", 0, idx) + 1
    col = idx - (text.rfind("\n", 0, idx) + 1) + 1
    return line, col


def _findings_add(out, code, text, idx, detail):
    line, col = _pos(text, idx) if idx >= 0 and text else (0, 0)
    out.append({"code": code, "line": line, "col": col, "detail": detail})


def _check_mojibake(text, out):
    """两条独立线索：① 已知特征串（覆盖拉丁族读坏）② 单字节段反解成含汉字的更短序列（覆盖中文族读坏）。"""
    for sig in MOJIBAKE_SIGNATURES:
        start = 0
        while True:
            # 逐处报，不只报第一处：只报第一处的话，前一处被豁免就把后一处的真损坏一起盖掉了
            # （实测踩过：docs 详解页 1102 行带标记、1114 行没带，旧写法整页报绿）
            idx = text.find(sig, start)
            if idx < 0:
                break
            _findings_add(out, "E5", text, idx, "命中乱码特征串 %r" % sig)
            start = idx + len(sig)
    i, n = 0, len(text)
    while i < n:
        if text[i] not in SUSPECT_CHARS:
            i += 1
            continue
        hit = None
        for size in range(min(12, n - i), 1, -1):
            seg = text[i:i + size]
            if any(c not in SUSPECT_CHARS for c in seg):
                continue
            for enc in ("cp1252", "latin-1"):
                try:
                    fixed = seg.encode(enc).decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
                # 必须「变短」且「反解出汉字」才判——只变短会把法文 Ã© 这类误判成乱码（那族交给特征表）encoding-check:ignore
                if CJK_RE.search(fixed) and len(fixed) < len(seg):
                    hit = (size, seg, enc, fixed)
                    break
            if hit:
                break
        if hit:
            size, seg, enc, fixed = hit
            _findings_add(out, "E5", text, i,
                          "连续 %d 个单字节字符按 %s 反解塌缩成汉字 %r（%r 是 UTF-8 字节被读坏后的形状）"
                          % (size, enc, fixed, seg[:16]))
            i += size
        else:
            i += 1


def _check_base64(text, out):
    for m in B64_BLOCK_RE.finditer(text):
        chunk = m.group(0)
        if len(chunk) % 4 == 0:
            try:
                base64.b64decode(chunk, validate=True)
            except (binascii.Error, ValueError):
                continue
            _findings_add(out, "E6", text, m.start(),
                          "长度 %d 的整段可被 base64 解码，疑似接口原文未解码就落盘" % len(chunk))


def _apply_line_markers(text, findings):
    """正文里显式写了 `encoding-check:ignore` 的那一行，其命中降级为「豁免」，不判红。

    只服务一种场景：**这份文件本身就在讲「乱码长什么样」**（教学正文、判据表、反例样例），
    检测器撞上了自己被教导要找的形状。豁免是逐行的、写在明面上的、可 grep 的，
    且每次运行都会把豁免条数打出来——不许静默吞掉。
    真损坏的正确处理方式是修文件，不是往行尾加这句 token；加了也不会被悄悄算过，输出里会留一行。
    """
    lines = text.split("\n")
    kept, dropped = [], 0
    for f in findings:
        ln = f["line"]
        seg = lines[ln - 1] if 0 < ln <= len(lines) else ""
        if IGNORE_TOKEN in seg:
            dropped += 1
            continue
        kept.append(f)
    return kept, dropped


def check_bytes(data):
    """返回 (findings, signals)。findings 非空即判红。"""
    findings = []
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        bad = data[exc.start:min(exc.end, len(data))]
        _findings_add(findings, "E1", "", -1,
                      "第 %d 字节起不是合法 UTF-8（%s，坏字节 %s）——写入端用了别的编码或解码时吞了字节"
                      % (exc.start, exc.reason, bad.hex()))
        text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]  # 开头 BOM 单独判，见下
    for ch, name in INVISIBLE.items():
        idx = text.find(ch)
        if idx >= 0:
            _findings_add(findings, "E4", text, idx, "含 %s（U+%04X）" % (name, ord(ch)))
    for idx, ch in enumerate(text):
        o = ord(ch)
        if ch in ALLOWED_C0:
            continue
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F:
            _findings_add(findings, "E3", text, idx, "含控制字符 U+%04X" % o)
            break
    idx = text.find("\ufffd")
    if idx >= 0 and not any(f["code"] == "E1" for f in findings):
        # E1 已判红时不再报 E2：替换字符是本次为看后半段而 errors="replace" 造出来的，不是文件里的疤
        n = text.count("\ufffd")
        _findings_add(findings, "E2", text, idx, "含 U+FFFD 替换字符 %d 处（解码时 errors 不是 strict）" % n)
    _check_mojibake(text, findings)
    _check_base64(text, findings)
    findings, suppressed = _apply_line_markers(text, findings)
    signals = {
        "chinese_chars": len(CJK_RE.findall(text)),
        "ascii_question_marks": text.count(ASCII_QUESTION),
        "non_ascii_ratio_pct": round(100.0 * sum(1 for c in text if ord(c) > 0x7F) / max(len(text), 1), 2),
        "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        "bytes": len(data),
        "suppressed_by_marker": suppressed,
    }
    return findings, signals


def check_path(p):
    data = Path(p).read_bytes()
    return check_bytes(data)


def iter_targets(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if any(part in SKIP_DIRS for part in f.parts):
                    continue
                if f.suffix.lower() in BIN_EXT:
                    continue
                yield f
        elif p.is_file():
            yield p
        else:
            raise FileNotFoundError(str(p))


SELFTEST = [
    ("S1 正常中文报告含标点（正例哨兵）",
     "本报告价值 ★★★★（核心参考）。\n天问：「这一步能不能交给企业真实业务使用？」——能，理由见 §4。\n"
     "含《架构总览》、100％ 覆盖、－5 ℃、……与 — 破折号；示例码：`x = a if b else c` 有 0 个问号。\n".encode("utf-8"), []),
    ("S2 GBK 字节冒充 UTF-8", "中文".encode("gbk") + "\n".encode("utf-8"), ["E1"]),
    ("S3 errors=replace 留下的疤", ("前段正常\ufffd后段也正常\n").encode("utf-8"), ["E2"]),
    ("S4 合法文本里就有问号（旧判据会误判）", "Is this repo useful? Yes? Maybe??\n".encode("utf-8"), []),
    ("S5 纯英文产物，0 个汉字（旧判据会误判）", "# Guide\nNothing Chinese here.\n".encode("utf-8"), []),
    ("S6 UTF-8 被按 cp1252 读坏的塌缩", "\u00e9\u00a1\u00b9\u00e7\u203a\u00ae\n".encode("utf-8"), ["E5"]),
    ("S7 GBK 链式经典形状", ("这段是 \u951f\u65a4\u62f7\u62f7\u62f7\n").encode("utf-8"), ["E5"]),
    ("S8 NUL 与控制字符", ("正常\x00异常\n").encode("utf-8"), ["E3"]),
    ("S9 隐藏的双向覆盖符（肉眼不可见）", ("正常文字\u202e\n").encode("utf-8"), ["E4"]),
    ("S10 base64 原文没解码就落盘", base64.b64encode(b"def main():\n    pass\n" * 20) + b"\n", ["E6"]),
    # S11 / S12 是同一份内容的**红绿一对**：只有行尾多了那句 ASCII 标记，判红就应当变豁免。
    # 少了这一对，「豁免」这条通道是死的——标记失效也没人报警。
    # 样例内容一律用 \u 转义拼（本文件里不写真乱码，理由见 MOJIBAKE_SIGNATURES 上方注释）。
    ("S11 乱码样例＋行尾标记（应降级为豁免，不判红）",
     ("\u00e2\u20ac\u2122 \u00c3\u00a9 \u951f\u65a4\u62f7 # encoding-check:ignore\n"
      "正常中文结尾。\n").encode("utf-8"), [], {"suppressed_by_marker": 3}),
    ("S12 同一内容去掉标记（必须照旧判红）",
     ("\u00e2\u20ac\u2122 \u00c3\u00a9 \u951f\u65a4\u62f7\n"
      "正常中文结尾。\n").encode("utf-8"), ["E5"], {"suppressed_by_marker": 0}),
    # S13 ＝「只报第一处」这个洞的回归网：前一处被豁免，后一处的真损坏必须照报。
    # 旧实现用 text.find(sig) 只取第一次命中 ⇒ 这一条会整页报绿（docs 详解页实测踩过）。
    ("S13 同名乱码出现两处，第一处带标记、第二处不带（必须仍判红）",
     ("\u951f\u65a4\u62f7 这行在讲乱码长什么样 # encoding-check:ignore\n"
      "\u951f\u65a4\u62f7 这行是真损坏没加标记\n").encode("utf-8"), ["E5"],
     {"suppressed_by_marker": 1}),
]


def selftest():
    ok = 0
    bad = 0
    for case in SELFTEST:
        name, data, expect = case[0], case[1], case[2]
        # 第 4 段可选＝对辅助信号的断言（判据代码抓不到的通道，如「豁免是否真的生效」）
        extra = case[3] if len(case) > 3 else {}
        findings, signals = check_bytes(data if isinstance(data, bytes) else bytes(data))
        got = sorted({f["code"] for f in findings})
        want = sorted(set(expect))
        misses = ["信号 %s 期望 %r 实得 %r" % (k, v, signals.get(k))
                  for k, v in extra.items() if signals.get(k) != v]
        if got == want and not misses:
            print("[PASS] %-42s 判据=%s 中文=%d 问号=%d 豁免=%d" % (
                name, got or "无", signals["chinese_chars"], signals["ascii_question_marks"],
                signals["suppressed_by_marker"]))
            ok += 1
        else:
            print("[FAIL] %-42s 期望=%s 实得=%s%s" % (
                name, want, got, ("｜" + "；".join(misses)) if misses else ""))
            for f in findings:
                print("        %s @%d:%d %s" % (f["code"], f["line"], f["col"], f["detail"]))
            bad += 1
    print("-" * 72)
    print("SELFTEST 断言 %d 条：PASS=%d FAIL=%d" % (len(SELFTEST), ok, bad))
    return 0 if bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description="落盘文本编码体检（判据见文件头 docstring）")
    ap.add_argument("paths", nargs="*", help="要检查的文件或目录")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--selftest", action="store_true", help="跑内置合成样例（不读写任何文件）")
    ap.add_argument("--print-codes", action="store_true", help="打印判据编号表")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.print_codes:
        for k in sorted(CODES):
            print("%s %s" % (k, CODES[k]))
        print("辅助信号（只打印不判红）：%s" % ", ".join(SIGNALS))
        return 0
    if not args.paths:
        print("用法：py check_encoding.py <文件或目录>... [--json]｜--selftest｜--print-codes", file=sys.stderr)
        return 2

    results = []
    red = 0
    try:
        targets = list(iter_targets(args.paths))
    except FileNotFoundError as exc:
        print("路径不存在：%s" % exc, file=sys.stderr)
        return 2
    if not targets:
        print("没有可检文件（0 个）——分母为空不构成「通过」", file=sys.stderr)
        return 2
    for f in targets:
        try:
            findings, signals = check_path(f)
        except OSError as exc:
            print("读不了：%s（%s）" % (f, exc), file=sys.stderr)
            return 2
        if findings:
            red += 1
        results.append({"path": str(f), "pass": not findings, "findings": findings, "signals": signals})

    if args.json:
        print(json.dumps({"checked": len(results), "failed": red, "results": results}, ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = "OK  " if r["pass"] else "FAIL"
            print("%s %s｜中文 %d｜问号 %d｜非 ASCII %.1f%%｜%d 行｜豁免 %d" % (
                mark, r["path"], r["signals"]["chinese_chars"], r["signals"]["ascii_question_marks"],
                r["signals"]["non_ascii_ratio_pct"], r["signals"]["lines"],
                r["signals"]["suppressed_by_marker"]))
            for fnd in r["findings"]:
                print("      %s @%d:%d %s" % (fnd["code"], fnd["line"], fnd["col"], fnd["detail"]))
        total_sup = sum(r["signals"]["suppressed_by_marker"] for r in results)
        print("-" * 72)
        print("共 %d 个文件，判红 %d 个（判据：E1–E6；问号数与中文数只是辅助信号，不参与判定）" % (len(results), red))
        if total_sup:
            print("🔴 本次有 %d 处命中被行尾标记 `encoding-check:ignore` 豁免——豁免是降级出口不是修法，"
                  "请逐条确认那些行真是在「讲乱码长什么样」而不是真坏了" % total_sup)
    return 1 if red else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
