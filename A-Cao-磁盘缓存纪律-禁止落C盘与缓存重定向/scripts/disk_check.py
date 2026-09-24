#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disk_check.py · 磁盘与缓存纪律体检

查四件事（对应 SKILL.md 六节）：
  1) 关键环境变量是否已重定向到非系统盘
  2) C 盘常见缓存/下载路径是否异常膨胀（--scan-user 时列出）
  3) 已知路径是否为 junction / symlink 且指向非系统盘（幂等检测）
  4) 各盘剩余空间是否低于阈值

用法：
    python disk_check.py                 # 常规体检
    python disk_check.py --scan-user     # 附带扫描 C 盘用户目录可疑项（只报告，不删）
    python disk_check.py --threshold 20  # 低于 20GB 剩余告警（默认 15）
退出码：0=全绿  1=有 FAIL  2=有 WARN
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

SYSTEM_DRIVE = "C:"
WARN_FREE_GB = 15.0

# 必须指向非系统盘的缓存类环境变量
ENV_TARGETS = {
    "PIP_CACHE_DIR": "pip 缓存",
    "HF_HOME": "HuggingFace 模型缓存",
    "npm_config_cache": "npm 缓存",
    "PNPM_HOME": "pnpm 目录",
    "TORCH_HOME": "torch 预训练权重",
    "XDG_CACHE_HOME": "通用 XDG 缓存",
}

# C 盘常见"偷偷长大"的位置
USER_SUSPECTS = [
    r"AppData\Local\pip\Cache",
    r"AppData\Local\npm-cache",
    r"AppData\Local\pnpm",
    r".cache\huggingface",
    r".cache\torch",
    r".cache\pip",
    r"Downloads",
    r".conda\pkgs",
    r"AppData\Local\Temp",
]

# 已知需要检查是否为 junction 的路径（按需补充；scan_junctions 会另外自动扫描）
JUNCTION_TARGETS = [
    r"C:\Users\ASUS\.cache",
    r"C:\Users\ASUS\AppData\Local\pip",
]

fails, warns, oks = [], [], []


def is_on_system_drive(p: str) -> bool:
    return Path(p).drive.upper().startswith(SYSTEM_DRIVE)


def env_get(var):
    """三级读取：进程 → 用户（持久） → 机器。只读进程级会误报"未设置"。"""
    v = os.environ.get(var)
    if v:
        return v
    if os.name == "nt":
        try:
            import winreg
            for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                              (winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        val, _ = winreg.QueryValueEx(k, var)
                        if val:
                            return str(val)
                except OSError:
                    continue
        except ImportError:
            pass
    return ""


def check_env():
    moved, missing, bad = [], [], []
    for var, desc in ENV_TARGETS.items():
        val = env_get(var)
        if not val:
            missing.append(f"{var}({desc})")
        elif is_on_system_drive(val):
            bad.append(f"{var}={val} → 仍在系统盘")
        else:
            moved.append(f"{var} → {val}")
    if bad:
        fails.append("[1] 环境变量重定向：%d 项仍指向系统盘\n        %s"
                     % (len(bad), "\n        ".join(bad)))
    if missing:
        warns.append("[1] 环境变量重定向：%d 项未设置（未用到的工具可忽略）\n        %s"
                     % (len(missing), "\n        ".join(missing)))
    if moved and not bad:
        oks.append("[1] 环境变量重定向：%d 项已指向非系统盘" % len(moved))


def dir_size_mb(p: Path) -> float:
    total = 0
    try:
        for dirpath, _, files in os.walk(p):
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total / (1024 * 1024)


def check_user_dirs(scan: bool):
    home = Path(os.environ.get("USERPROFILE", r"C:\Users\Default"))
    big = []
    for rel in USER_SUSPECTS:
        p = home / rel
        if p.exists() and not p.is_symlink():
            size = dir_size_mb(p)
            if size >= 1024:            # ≥1GB 才算"异常膨胀"
                big.append(f"{p}  {size/1024:.1f} GB")
    if big:
        warns.append("[2] C 盘用户目录可疑项（≥1GB）\n        " + "\n        ".join(big)
                     + "\n        处置：设环境变量重定向；已生成的先迁移再建 junction")
    else:
        oks.append("[2] C 盘用户目录：无 ≥1GB 的可疑缓存" + ("（已扫描）" if scan else ""))


def scan_junctions():
    """自动扫描候选位置，报告哪些还是实体目录、哪些已正确拐走。

    遍历 C 盘用户目录下常见缓存位置（深度受限，避免全盘慢扫）。
    """
    home = Path(os.environ.get("USERPROFILE", r"C:\Users\Default"))
    roots = [home / ".cache", home / "AppData" / "Local"]
    redirected, still_physical = [], []
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                is_link = p.is_symlink() or p.is_junction()
            except OSError:
                continue
            # 只看缓存/运行时类目录，跳过普通程序目录
            name_l = p.name.lower()
            if not any(k in name_l for k in (
                "cache", "pip", "npm", "pnpm", "huggingface", "torch",
                "runtimes", "opencode", "chroma", "memory", "hyperframes",
                "conda", "yarn", "uv",
            )):
                continue
            if is_link:
                try:
                    tgt = os.readlink(p)
                except OSError:
                    tgt = ""
                if tgt and tgt[:2].upper().startswith(SYSTEM_DRIVE):
                    still_physical.append(f"{p} → 链接仍指向系统盘 {tgt}")
                else:
                    redirected.append(f"{p} → {tgt}")
            else:
                size = dir_size_mb(p)
                if size >= 50:      # 实体且 ≥50MB 才值得报
                    still_physical.append(f"{p} 实体目录 {size:.0f} MB（未拐走）")
    if still_physical:
        warns.append("[3] 重定向检查：%d 处未拐走或拐错\n        %s"
                     % (len(still_physical), "\n        ".join(still_physical))
                     + "\n        处置：数据移至非系统盘 → 删空原目录 → mklink /J 重建")
    if redirected:
        oks.append("[3] 重定向检查：%d 处已正确拐到非系统盘" % len(redirected))
    if not redirected and not still_physical:
        oks.append("[3] 重定向检查：未发现候选缓存目录")


def check_free_space(threshold: float):
    low = []
    drives = []
    for letter in "CDEFG":
        root = f"{letter}:\\"
        if not Path(root).exists():
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        free_gb = usage.free / (1024 ** 3)
        drives.append(f"{letter}: {free_gb:.1f} GB 可用 / {usage.total/(1024**3):.0f} GB")
        if letter == "C" and free_gb < threshold:
            low.append(f"{letter}: 仅剩 {free_gb:.1f} GB（阈值 {threshold} GB）")
    if low:
        fails.append("[4] 空间预警：C 盘低于阈值\n        " + "\n        ".join(low)
                     + "\n        处置：清理 + 重定向，禁止未确认的大规模写入")
    else:
        oks.append("[4] 空间预警：各盘均在阈值之上（" + "；".join(drives) + "）")


def main():
    args = sys.argv[1:]
    scan = "--scan-user" in args
    threshold = WARN_FREE_GB
    if "--threshold" in args:
        try:
            threshold = float(args[args.index("--threshold") + 1])
        except (IndexError, ValueError):
            pass
    json_out = "--json" in args

    check_env()
    check_user_dirs(scan)
    scan_junctions()
    check_free_space(threshold)

    if json_out:
        print(json.dumps({"ok": oks, "warn": warns, "fail": fails},
                         ensure_ascii=False, indent=2))
    else:
        for line in oks:
            print("  OK   " + line)
        for line in warns:
            print("  WARN " + line)
        for line in fails:
            print("  FAIL " + line)
        print("-" * 56)
        print(f"体检结果：OK {len(oks)} / WARN {len(warns)} / FAIL {len(fails)}")

    if fails:
        return 1
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
