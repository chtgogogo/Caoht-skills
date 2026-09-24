#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patrol_check.py · 记忆纪律体系巡检

巡检四件事（原先在 SKILL.md:120 承诺，但脚本一直不存在 → 双副本漂移 16 天无人发现）：
  1) 注册状态：记忆纪律 skill 的关键文件是否齐全
  2) 双副本一致性：全盘是否存在同名 skill 的过期副本
  3) 指针存活：CORE.md / global-behavior-rules.md 里引用的路径是否真实存在
  4) 容量红线：CORE.md / SKILL.md 是否超出约定行数

用法：
    python patrol_check.py            # 巡检，有 FAIL 时 exit 1
    python patrol_check.py --quiet    # 只输出结论行
退出码：0=全绿  1=有 FAIL  2=有 WARN 无 FAIL
"""
import os
import re
import sys
import hashlib
from pathlib import Path

# ---------------- 配置（改路径只改这里） ----------------
AUTH = Path(r"D:\Deepseek-ALL\skills\A-Cao-记忆纪律-跨会话存整召评分衰减升华")
SHARED_RULES = Path(r"D:\_ai_memory_sync\global-behavior-rules.md")
PITFALL_INDEX = Path(r"D:\Deepseek-ALL\踩坑日志\pitfall-log.md")
# 允许同时存在的其它副本（历史备份/归档/临时目录，不算漂移）
ALLOW_PATTERNS = (
    r"\\_archive\\", r"\\_old_skills_backup", r"\\_trash_",
    r"\\skills_archive\\", r"\\refs\\", r"\\depends\\",
    r"\\AppData\\Local\\Temp\\",          # 临时解包目录，非持久副本
    r"\\Temp\\",
)
REQUIRED_FILES = ["SKILL.md", "CORE.md", "CHANGELOG.md"]
LINE_LIMITS = {"CORE.md": 150, "SKILL.md": 200}

fails, warns, oks = [], [], []


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def check_registered():
    missing = [f for f in REQUIRED_FILES if not (AUTH / f).exists()]
    if missing:
        fails.append(f"[1] 注册状态：权威库缺文件 {missing}")
    else:
        oks.append(f"[1] 注册状态：{len(REQUIRED_FILES)} 个关键文件齐全")


def check_duplicates():
    """全盘找同名 skill 目录，排除已归档的。"""
    name = AUTH.name
    roots = [Path("D:\\"), Path("C:\\Users")]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            if dirpath.count(os.sep) > 6:          # 限制深度，防全盘慢扫
                dirnames[:] = []
                continue
            if name in dirnames:
                p = Path(dirpath) / name
                if any(re.search(pat, str(p), re.I) for pat in ALLOW_PATTERNS):
                    continue
                found.append(p)
    if len(found) <= 1:
        oks.append("[2] 双副本一致：未发现过期副本")
        return
    # 逐份比对 SKILL.md
    ref = AUTH / "SKILL.md"
    ref_h = sha(ref) if ref.exists() else "N/A"
    stale = []
    for p in found:
        if p == AUTH:
            continue
        f = p / "SKILL.md"
        h = sha(f) if f.exists() else "缺SKILL.md"
        if h != ref_h:
            stale.append(f"{p}  (sha={h})")
    if stale:
        fails.append("[2] 双副本一致：发现 %d 处过期副本\n        %s"
                     % (len(stale), "\n        ".join(stale)))
        warns.append("        处置：同步或移入 skills\\_archive\\，勿直接删")
    else:
        oks.append(f"[2] 双副本一致：{len(found)} 份内容相同")


def check_pointers():
    """抽取 CORE.md 与共享准则里的 D:\\ 路径，验证是否存在。"""
    targets = [AUTH / "CORE.md", SHARED_RULES]
    pat = re.compile(r"`(D:\\[^`\n]+?)`")
    dead = []
    total = 0
    for t in targets:
        if not t.exists():
            warns.append(f"[3] 指针存活：文件本身不存在 {t}")
            continue
        for m in pat.finditer(t.read_text(encoding="utf-8", errors="ignore")):
            raw = m.group(1).strip().rstrip("\\")
            if raw.endswith((".py", ".md")) or raw.count("\\") <= 1:
                pass
            total += 1
            if not Path(raw).exists():
                dead.append(f"{t.name} → {raw}")
    if dead:
        fails.append("[3] 指针存活：%d 处指向不存在\n        %s"
                     % (len(dead), "\n        ".join(dead)))
    else:
        oks.append(f"[3] 指针存活：{total} 处路径全部命中")


def check_limits():
    over = []
    for f, lim in LINE_LIMITS.items():
        p = AUTH / f
        if not p.exists():
            continue
        n = len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        if n > lim:
            over.append(f"{f} {n} 行 > 上限 {lim}")
    if over:
        warns.append("[4] 容量红线：" + "；".join(over))
    else:
        oks.append("[4] 容量红线：未超限")


def check_pitfall_activity():
    if not PITFALL_INDEX.exists():
        fails.append(f"[5] 踩坑日志：索引不存在 {PITFALL_INDEX}")
        return
    txt = PITFALL_INDEX.read_text(encoding="utf-8", errors="ignore")
    entries = len(re.findall(r"^\s*\|?\s*20\d\d-\d\d-\d\d", txt, re.M))
    if entries == 0:
        fails.append("[5] 踩坑日志：索引存在但 0 条目（疑似空壳副本）")
    else:
        oks.append(f"[5] 踩坑日志：{entries} 条目（{PITFALL_INDEX.parent.name}）")


def main():
    quiet = "--quiet" in sys.argv
    check_registered()
    check_duplicates()
    check_pointers()
    check_limits()
    check_pitfall_activity()

    if not quiet:
        for line in oks:
            print("  OK   " + line)
    for line in warns:
        print("  WARN " + line)
    for line in fails:
        print("  FAIL " + line)

    print("-" * 52)
    print(f"巡检结果：OK {len(oks)} / WARN {len(warns)} / FAIL {len(fails)}")
    if fails:
        return 1
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
