"""td_selftest.py · 证明 td_evidence.py 那把尺子能红（防恒真／防假红）。

为什么随包：check 的判据只要有一条恒真，证据包就会在没人察觉的情况下一路放行。
这条自检不靠人记得住，靠退出码：任何一条恒真漏抓、或正向对照假红，都退 1。

用法（产物落当前目录 .td_selftest，要放非系统盘就显式 --out）：
    py scripts/td_selftest.py
逐条原始回显落 <out>/selftest_raw.txt。
"""

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "td_evidence.py")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FREE_TEXT = """# 运行记录（手写自由格式）· selftest 改前

- 代码版本：提交号 {HEAD}，工作区 clean
- 命令：{PY} -c 打印四行汇总
- 环境：Windows，解释器 {PY}，无第三方依赖
- 退出码 1
- 时间：见改前那次运行，墙钟不到 1 秒
- 数据集：合成样例（本自检自带 4 条断言），无外部真实数据
- 产物：原始输出落在 {RAW}
- 结论三态：现状快照（改前基线，提升与否待改后对照）
"""

FILL = [
    ("依赖=【待填】", "依赖=无第三方依赖（纯 stdlib）"),
    (r"F6\.数据集版本: .*", "F6.数据集版本: 合成样例，非真实数据；本自检自带断言集，样本量以用例数计 n=4"),
    (r"^\|[^\n]*0\.这是什么改动[^\n]*$", "| 0.这是什么改动（任务/一句话/类别①②③/本次角色） | selftest；给金额取分改动配一组正确性检查；类别①；本条＝改前（红） |"),
    (r"^\|[^\n]*结果（逐字摘[^\n]*$", "| 结果（逐字摘原始输出里的汇总行） | `Ran 4 tests in 0.001s` / `FAILED (failures=1)`，逐字在同目录 raw 文件里 |"),
    (r"^\|[^\n]*结论三态[^\n]*$", "| 结论三态（已证明／现状快照／未测） | 现状快照（本条为改前基线，提升与否待改后对照） |"),
    (r"^\|[^\n]*断言变更[^\n]*$", "| 断言变更（A 口径重算逐条／B 断言弱化单独列＋理由／无） | 无 |"),
    (r"^\|[^\n]*跳过项[^\n]*$", "| 跳过项与原因（无写「无」） | 无 |"),
]

CASES = []


def case(name, fmt, target, expect, fn):
    CASES.append((name, fmt, target, expect, fn))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, t):
    """变异写回一律用 CRLF：capture 真实落盘就是 CRLF，自检若在 LF 记录上跑，
    等于用一把和现场不同的尺子量自己（行尾漏判就是这么藏住的）。"""
    with open(p, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(t)


def sub(pattern, repl, t, flags=0):
    new, n = re.subn(pattern, repl, t, count=1, flags=flags)
    assert n == 1, "变异锚点没命中：%s" % pattern
    return new


def app(text):
    return lambda e: write(e["md"], read(e["md"]) + text)


# ---------- strict 组 ----------
case("s01_空值", "strict", "md", "C2 空值", lambda e: write(e["md"], sub(r"(F6\.数据集版本: ).*", r"\g<1>", read(e["md"]))))
case("s02_未记录无原因", "strict", "md", "C2 未记录无原因", lambda e: write(e["md"], sub(r"F6\.数据集版本: .*", "F6.数据集版本: 未记录", read(e["md"]))))
def three_unrec(e):
    t = read(e["md"])
    t = sub(r"F1\.代码版本: .*", "F1.代码版本: 未记录：目录不在版本库管辖内", t)
    t = sub(r"F2\.命令: .*", "F2.命令: 未记录：当时没记下来", t)
    t = sub(r"F3\.环境: .*", "F3.环境: 未记录：采集脚本没跑", t)
    write(e["md"], t)


case("s03_逃生舱超载", "strict", "md", "C3 逃生舱超载", three_unrec)
case("s04_占位符残留", "strict", "md", "C2 待填", lambda e: write(e["md"], sub(r"(\| 结果（逐字摘原始输出里的汇总行） \| ).*( \|)", r"\g<1>【待填】\g<2>", read(e["md"]))))
case("s05_路径不在场", "strict", "md", "C1 路径不在场", lambda e: write(e["md"], read(e["md"]).replace(os.path.basename(e["raw"]), "selftest_beforX.txt")))
case("s06_管道吃码", "strict", "md", "C4 管道吃码", lambda e: write(e["md"], sub(r"(F2\.命令: .*)", r"\g<1> | head -50", read(e["md"]))))
case("s07_复制改数", "strict", "md", "C6 复制改数", lambda e: (shutil.copy2(e["raw"], e["dup"]), write(e["md"], read(e["md"]) + "\n另见 %s\n" % os.path.basename(e["dup"]))))
case("s08_凭据入日志", "strict", "raw", "C8 凭据", lambda e: write(e["raw"], read(e["raw"]) + "\nAuthorization: Bearer abcdefghijklmnop1234567890\napi_key: sk-1234567890abcdef\n"))
case("s09_提升无口径", "strict", "md", "C9 提升无口径", app("\n把银行家舍入改成四舍五入后，解析准确率从 88% 提升到 95%。\n"))
case("s10_三态没归位", "strict", "md", "C10 三态未归位", lambda e: write(e["md"], sub(r"(\| 结论三态[^|]*\| )[^|]*( \|)", r"\g<1>这次跑得挺顺的\g<2>", read(e["md"]))))
case("s11_已证明缺对照", "strict", "md", "C11 已证明缺对照", lambda e: write(e["md"], sub(r"(\| 结论三态[^|]*\| )[^|]*( \|)", r"\g<1>已证明：修好了\g<2>", read(e["md"]))))
case("s12_删整行字段", "strict", "md", "C2 缺字段", lambda e: write(e["md"], sub(r"F3\.环境: .*\r?\n", "", read(e["md"]))))
case("s13_补写嫌疑", "strict", "raw", "C7 补写嫌疑", lambda e: os.utime(e["raw"], (e["old"], e["old"])))
case("s14_正向对照_不该红", "strict", "md", None, lambda e: None)
case("s17_机器字段留着模板括号", "strict", "md", "C2 待填", lambda e: write(e["md"], sub(r"F5\.时间: .*", "F5.时间: 起=【YYYY-MM-DDTHH:MM:SS+08:00】 止=【同上】 墙钟=【秒】", read(e["md"]))))
case("s15_改后正向对照_不该红", "strict", "md_after", None, lambda e: None)


def crlf(p):
    """把记录换回 capture 真实写出的 CRLF 行尾。"""
    with open(p, "rb") as f:
        b = f.read()
    with open(p, "wb") as f:
        f.write(b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


case("s18_正向CRLF行尾_不该假红", "strict", "md_after", None, lambda e: crlf(e["md_after"]))


def prove(e):
    return write(e["md"], sub(r"(\| 结论三态[^|]*\| )[^|]*( \|)", r"\g<1>已证明：n=4 r=1 波动未测\g<2>", read(e["md"])) + "\n改后那份见 %s\n" % os.path.basename(e["raw_after"]))


case("s16_已证明且两份对照齐_不该红", "strict", "md", None, prove)

# ---------- freeform 组（11 红 + 1 正向） ----------
case("f01_凭据入正文", "freeform", "free", "C8 凭据", lambda e: write(e["free"], read(e["free"]) + "\n配置里 api_key: sk-1234567890abcdef 已填好\n"))
case("f02_路径不在场", "freeform", "free", "C1 路径不在场", lambda e: write(e["free"], read(e["free"]).replace(os.path.basename(e["raw"]), "selftest_beforX.txt")))
case("f03_无在场产物", "freeform", "free", "C1 无在场产物", lambda e: write(e["free"], sub(r"- 产物：.*\n", "", read(e["free"]))))
case("f04_提升无口径", "freeform", "free", "C9 提升无口径", lambda e: write(e["free"], read(e["free"]) + "\n改成四舍五入后解析准确率提升到 95%。\n"))
case("f05_三态没归位", "freeform", "free", "C10 三态未归位", lambda e: write(e["free"], sub(r"结论三态：.*", "结论三态：这次跑得挺顺", read(e["free"]))))
case("f06_已证明缺对照", "freeform", "free", "C11 已证明缺对照", lambda e: write(e["free"], sub(r"结论三态：.*", "结论三态：已证明，修好了", read(e["free"]))))
case("f07_未记录无原因", "freeform", "free", "C2 未记录无原因", lambda e: write(e["free"], sub(r"- 数据集：.*", "- 数据集：未记录", read(e["free"]))))
case("f08_记录过薄", "freeform", "free", "C2 记录过薄", lambda e: write(e["free"], "# 结论\n\n改完了，结论三态：现状快照\n"))
case("f09_退出码没数字", "freeform", "free", "C4 退出码", lambda e: write(e["free"], sub(r"- 退出码 1", "- 退出码：见原始输出", read(e["free"]))))
case("f10_复制改数", "freeform", "free", "C6 复制改数", lambda e: (shutil.copy2(e["raw"], e["dup"]), write(e["free"], read(e["free"]) + "\n改后那份见 %s\n" % os.path.basename(e["dup"]))))
case("f11_空文件", "freeform", "raw", "C1 空文件", lambda e: write(e["raw"], ""))
case("f12_正向对照_不该红", "freeform", "free", None, lambda e: None)
case("f13_举例文件名不该判红", "freeform", "free", None, lambda e: write(e["free"], read(e["free"]) + "\n另取依赖清单参考 env_freeze.txt 核对版本\n"))


def run(argv):
    p = subprocess.run(argv, capture_output=True)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def capture(d, stage, rc):
    code = ("print('Ran 4 tests in 0.001s');print('FAILED (failures=1)')" if rc == 1
            else "print('Ran 4 tests in 0.001s');print('OK')")
    argv = [sys.executable, CHECKER, "capture", "--task", "selftest", "--stage", stage,
            "--out", d, "--project", d, "--deps", "无第三方依赖（纯 stdlib）",
            sys.executable, "-c", code]
    r, out = run(argv)
    if r != 0:
        raise SystemExit("capture 自身没跑通（退出码 %s）：\n%s" % (r, out))
    md = re.search(r"生成：(\S+\.md)", out).group(1)
    raw = re.search(r"生成：(\S+\.txt)", out).group(1)
    return md, raw


def fill(md):
    t = read(md)
    for pat, repl in FILL:
        if pat.startswith("依赖="):
            t = t.replace(pat, repl)
        elif pat.startswith(r"^\|"):
            t = sub(pat, repl, t, re.M)
        else:
            t = sub(pat, repl, t)
    assert "【待填】" not in t, "填充没覆盖全部占位符：%s" % md
    write(md, t)


def env(d, name):
    w = os.path.join(d, name)
    if os.path.exists(w):
        shutil.rmtree(w)
    os.makedirs(w)
    md_b, raw_b = capture(w, "before", 1)
    md_a, raw_a = capture(w, "after", 0)
    fill(md_b)
    fill(md_a)
    free = os.path.join(w, "freeform_record.md")
    write(free, FREE_TEXT.format(HEAD="0" * 40, PY=sys.executable.replace(os.sep, "/"), RAW=raw_b.replace(os.sep, "/")))
    return {"md": md_b, "raw": raw_b, "md_after": md_a, "raw_after": raw_a, "free": free,
            "dup": os.path.join(w, "selftest_dup.txt"),
            "old": dt.datetime.now().timestamp() - 10 * 86400, "now": dt.datetime.now().timestamp()}


def check(rec, fmt):
    rc, out = run([sys.executable, CHECKER, "check", rec, "--format", fmt])
    return rc, out, re.findall(r"红 \[([^\]]+)\]", out)


def main():
    ap = argparse.ArgumentParser(description="测试纪律判据自检（证明尺子能红）")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), ".td_selftest"))
    a = ap.parse_args()
    d = os.path.abspath(a.out)
    pkg_root = os.path.dirname(HERE)
    if d == pkg_root or d.startswith(pkg_root + os.sep):
        sys.stderr.write("红：自检产物不许落在技能目录内（会跟着进发布仓）。请用 --out 指到项目证据目录。\n")
        return 2
    os.makedirs(d, exist_ok=True)

    lines, caught, total, false_red = [], {"strict": 0, "freeform": 0}, {"strict": 0, "freeform": 0}, 0
    for name, fmt, target, expect, fn in CASES:
        e = env(d, name)
        fn(e)
        rec = e["free"] if fmt == "freeform" else (e["md_after"] if target == "md_after" else e["md"])
        rc, out, tags = check(rec, fmt)
        if expect is None:
            ok = rc == 0
            verdict = "绿（符合预期）" if ok else "假红：尺子过紧，会逼人绕闸门"
            false_red += 0 if ok else 1
        else:
            total[fmt] += 1
            ok = rc != 0 and any(expect in t for t in tags)
            caught[fmt] += 1 if ok else 0
            verdict = ("翻红（由 %s 抓到）" % expect) if ok else ("恒真漏抓：改了还判通过 ⇒ 这把尺子坏了" if rc == 0 else "红了但没按预期抓：%s" % tags)
        lines.append("== %s [%s] | 期望：%s | 退出码=%s | 抓到：%s\n%s\n-->%s\n" % (name, fmt, expect, rc, tags, out.strip(), verdict))

    e = env(d, "r01_回滚mtime")
    os.utime(e["raw"], (e["old"], e["old"]))
    c1, _o1, _t1 = check(e["md"], "strict")
    os.utime(e["raw"], (e["now"], e["now"]))
    c2, o2, _t2 = check(e["md"], "strict")
    lines.append("回滚验证 strict：改 mtime 退出码=%s，还原后 退出码=%s（应 1 -> 0）\n%s\n" % (c1, c2, o2.strip()))

    e = env(d, "r02_回滚密钥行")
    write(e["free"], read(e["free"]) + "\n配置里 api_key: sk-1234567890abcdef 已填好\n")
    c3, _o3, _t3 = check(e["free"], "freeform")
    write(e["free"], FREE_TEXT.format(HEAD="0" * 40, PY=sys.executable.replace(os.sep, "/"), RAW=e["raw"].replace(os.sep, "/")))
    c4, o4, _t4 = check(e["free"], "freeform")
    lines.append("回滚验证 freeform：注入密钥 退出码=%s，还原后 退出码=%s（应 1 -> 0）\n%s\n" % (c3, c4, o4.strip()))
    rollback_ok = (c1 == 1 and c2 == 0 and c3 == 1 and c4 == 0)

    txt = "\n".join(lines)
    rawlog = os.path.join(d, "selftest_raw.txt")
    with open(rawlog, "wb") as f:
        f.write(txt.replace("\n", "\r\n").encode("utf-8"))
    print(txt)
    done = all(caught[k] == total[k] and total[k] > 0 for k in caught) and rollback_ok and false_red == 0
    print("\n自检汇总：strict %d/%d，freeform %d/%d，正向假红 %d，回滚 %s；原始回显：%s" % (
        caught["strict"], total["strict"], caught["freeform"], total["freeform"], false_red,
        "可逆" if rollback_ok else "不可逆", rawlog.replace(os.sep, "/")))
    print("结论：%s" % ("尺子有效" if done else "尺子有坏判据（见上面标红的那几组）"))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
