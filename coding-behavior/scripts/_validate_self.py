#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_self — coding-behavior 自检尺（零依赖；改正文后必跑，跑不绿不交付）。

为什么存在：正文是给任何 AI 执行的规程，最怕「哪天只改一处、规矩偷偷跑偏」（CHANGELOG
v2.2 版本漂移、v2.4 尺子锯短未遂都是先例）。本脚本把「不能被动掉的东西」钉成
needle：改正文时先跑它看哪条翻红，**逐条申报改钉**，不许改松断言凑绿。

用法：
  py _validate_self.py                # 自检本技能目录（脚本所在目录）
  py _validate_self.py --root <目录>  # 自检指定副本（勿用正本做变异实验）

退出码：0=全 PASS；1=有 FAIL；2=用法/缺文件错误。

断言变更申报纪律（同 CHANGELOG v2.4 先例）：
  * needle 跟正文形状走，形状变了先「申报」再改钉，编号 KEEP；
  * 禁止把 == 放宽成 in / >= / 存在性检查来消红；
  * 该红的断言必须能用变异翻红（牙口），否则视为恒真断言＝没有。
"""
import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 断言定义

CONTRACT_A = "任何涉及代码、编程、技术实现、系统设计的任务都触发：写码、脚本、SQL、配置、改bug、调试、报错排查、修复、审查、重构、优化、架构与接口设计、解释代码、编程题、技术选型。"
CONTRACT_B = "只要沾代码就触发；拿不准时一律触发。"
CONTRACT_C = "纯闲聊、纯写作、纯翻译等零代码任务不触发。"

# 红N 断言钉成「红N · 专名」锚（指针句里的「红N→节」不会命中——防止红线整行被删后指针句冒充成立）
RED_LINES = [
    "红1 · 零写入",
    "红2 · 退路在场",
    "红3 · 缓解不是结案",
    "红4 · 写入要授权",
    "红5 · 待测带出口",
    "红6 · 凭据不落盘",
]
COMMON_ITEMS = [f"{i}. **" for i in range(1, 11)]

TABLE_SIX_ROW_EXPECT = 3      # S12：第六节反例表数据行数
TABLE_EIGHT_ROW_EXPECT = 6    # S13：第八节降级表数据行数
FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*", re.S)
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fail_name(idx):
    return idx


def section(text, title_pattern):
    """切出 `## <标题>` 段（到下一个 `## ` 为止）。"""
    m = re.search(title_pattern, text, re.M)
    if not m:
        return None
    start = m.start()
    nxt = re.search(r"\n## ", text[start + 1:])
    end = start + 1 + nxt.start() if nxt else len(text)
    return text[start:end]


def check(root: Path):
    """返回 [(标识, PASS/FAIL, 说明)，有失败则非空其中含 FAIL]。"""
    out = []

    def add(idx, ok, detail=""):
        out.append((idx, bool(ok), detail))

    skill_path = root / "SKILL.md"
    changelog_path = root / "CHANGELOG.md"
    if not skill_path.is_file():
        add("E01", False, f"拿不到 SKILL.md：{skill_path}")
        return out
    if not changelog_path.is_file():
        add("E02", False, f"拿不到 CHANGELOG.md：{changelog_path}")
        return out
    raw = skill_path.read_text(encoding="utf-8-sig")
    log_raw = changelog_path.read_text(encoding="utf-8-sig")

    # ---- frontmatter ---------------------------------------------------
    fm = FM_RE.search(raw)
    fm_text = fm.group(1) if fm else ""
    add("Fm01", bool(fm), "frontmatter 存在且可切分")
    m = re.search(r"^name:\s*(\S+)", fm_text, re.M)
    ok = bool(m) and len(m.group(1)) <= 64 and NAME_RE.match(m.group(1))
    add("Fm02", ok, f"name={m.group(1) if m else '（无）'}")
    m = re.search(r"^description:\s*(.+?)(?=^\w[\w-]*:|\Z)", fm_text, re.M | re.S)
    desc = m.group(1).strip() if m else ""
    add("Fm03", 1 <= len(desc) <= 1024, f"description 长度={len(desc)}")
    m = re.search(r"^metadata:", fm_text, re.M)
    add("Fm04", bool(m), "metadata 块存在")
    m = re.search(r"^\s*version:\s*(v[\w.]+)", fm_text, re.M)
    version = m.group(1).strip() if m else ""
    add("Fm05", bool(re.match(r"^v\d+(\.\d+)+$", version)), f"version={version or '（无）'}")

    # ---- 触发面契约（AGENTS.md 全队口径，逐字）--------------------------
    add("C01", CONTRACT_A in desc, "契约句A逐字在场")
    add("C02", CONTRACT_B in desc, "契约句B逐字在场")
    add("C03", CONTRACT_C in desc, "契约句C逐字在场")
    add("C04", "code review" in desc and "bug fix" in desc, "英文触发关键词在场")

    # ---- 正文结构 ------------------------------------------------------
    add("RD01", re.search(r"^## 〇、红线清单", raw, re.M) is not None, "〇节红线清单标题在场")
    red = section(raw, r"^## 〇、红线清单")
    if red:
        for i, tag in enumerate(RED_LINES, 1):
            add(f"RD{i + 1:02d}", tag in red, f"{tag} 在场")
    else:
        for i, tag in enumerate(RED_LINES, 1):
            add(f"RD{i + 1:02d}", False, "红线节缺失")
    add("RD08", raw.count("🔴") == 1, f"全文 🔴 数={raw.count('🔴')}（应只红线清单头一处）")
    add("WD01", "共通九条" not in raw and "九条" not in raw.replace("十条", "X"), "旧称呼「共通九条/九条」清除")

    tc = section(raw, r"^## 四、共通十条")
    if tc:
        body_items = re.findall(r"^(\d+)\. \*\*", tc, re.M)
        add("TD01", body_items == [str(i) for i in range(1, 11)], f"共通十条 1–10 齐藏：{body_items}")
        add("TD02", "随库走" in tc, "第 10 条「随库走」在场")
        low = tc.lower()
        add("TD03", "lint" in low and ("类型检查" in tc or "typecheck" in low), "第 5 条 lint/typecheck 兜底在场")
    else:
        add("TD01", False, "「共通十条」节缺失")
        add("TD02", False, "缺")
        add("TD03", False, "缺")

    # ---- 流程与防腐锚 --------------------------------------------------
    add("CF01", "未量波动带" in raw and "≥3 次" in raw, "C 流 波动带降级句＋两侧≥3 次对跑在场")
    dsec = section(raw, r"^### D\. 审查流")
    ok = bool(dsec) and all(k in dsec for k in ("安全与并发", "错误与边界", "复杂度与过度工程", "测试有效性", "兼容与影响面"))
    add("AF10", ok, "D 审查流标题＋五项 lens 在场")
    add("AF11", "审查既有代码／改动（要问题清单，不要补丁）" in raw, "第一节表含审查行")
    add("AF12", "D 审查流" in raw and "轻流程" in raw, "第一节表把审查行接到 D 流")

    af = section(raw, r"^## 九、防腐三锚")
    ok = bool(af) and all(k in af for k in ("交付信封", "动作点重锚", "退出闸门"))
    add("AF01", ok, "第九节 防腐三锚：信封/重锚/闸门在场")
    add("AF02", "档位＝X｜主流程＝Y｜未验证项＝Z" in (af or ""), "交付信封格式串逐字在场")
    gate_items = [
        "档位判对了？交付形状与第一节那格一致？",
        "第二节三问过了？该出的短计划出了？",
        "红1–红6 一条没碰？",
        "那句有运行证据？未验证项列了？",
        "有没有预埋、大扫除、顺手改任务外的东西？",
    ]
    add("AF03", all(g in (af or "") for g in gate_items), "退出闸门 5 问逐条在场")

    order = section(raw, r"^## 八、执行顺序与缺件降级")
    add("EX01", bool(order) and "第九节" in order, "执行顺序链提及第九节重锚")

    # ---- 形状快照（申报制改钉）------------------------------------------
    six = section(raw, r"^## 六、不应触发本 skill 的反例")
    if six:
        rows = [l for l in six.splitlines() if l.startswith("|")]
        add("S12", len(rows) - 2 == TABLE_SIX_ROW_EXPECT, f"第六节表数据行={len(rows) - 2}")
    else:
        add("S12", False, "第六节缺失")
    eight = section(raw, r"^## 八、执行顺序与缺件降级")
    if eight:
        rows = [l for l in eight.splitlines() if l.startswith("|")]
        add("S13", len(rows) - 2 == TABLE_EIGHT_ROW_EXPECT, f"第八节表数据行={len(rows) - 2}")
    else:
        add("S13", False, "第八节缺失")
    add("LC01", len(raw.splitlines()) <= 500, f"SKILL.md 行数={len(raw.splitlines())}（≤500）")

    # ---- 版本一致性 ----------------------------------------------------
    heads = re.findall(r"^## (v[\w.]+)", log_raw, re.M)
    add("RG01", bool(heads), "CHANGELOG 有版本节")
    if heads:
        add("RG02", heads[0] == version, f"CHANGELOG 最新节={heads[0]} ↔ frontmatter={version}")
    add("RG03", f"## {version}" in log_raw, f"CHANGELOG 含 {version} 节")
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    if not (root / "SKILL.md").is_file():
        print(f"USAGE-ERR：{root} 下没有 SKILL.md", file=sys.stderr)
        return 2
    results = check(root)
    if not results:
        print("NO-CHECKS：断言表为空——尺子坏了", file=sys.stderr)
        return 2
    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    for idx, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {idx}  {detail}")
    print(f"---- PASS={npass} FAIL={nfail} / {len(results)}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
