#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disk_check.py · 磁盘与缓存纪律体检（默认只读 · 对文件系统零写入）

体检查五件事：
  [1] 关键环境变量是否已重定向到非系统盘
  [2] 系统盘用户目录是否异常膨胀（--scan-user 时逐目录量体积）
  [3] 候选缓存位置是否为 junction / symlink 且指向非系统盘（仅 Windows，其余平台输出「未检查」）
  [4] 每个在场盘**独立**判剩余空间：任一盘低于阈值即进 FAIL 并影响退出码；
      配置里列了、但本机没有的盘输出「未检查」，不静默跳过
  [5] pnpm 真实 store 位置：以 `pnpm store path` 实查为结论，PNPM_HOME 只作提示

清理与迁移是**独立显式入口**，默认体检不碰它们，它们也不在干跑阶段写一个字节：
  --clean     按目录「构成」定性（不按目录名判可删），非再生缓存类需白名单确认，--apply 才真删
  --migrate   占用核查 → 复制 → 逐文件校验 → 切换 → 回退，--apply 才动，--rollback 撤销

用法（本机解释器只用 py，python 是商店占位件）：
    py disk_check.py                                 # 常规体检（只读）
    py disk_check.py --scan-user                     # 附带扫描系统盘可疑大目录
    py disk_check.py --threshold 20                  # 临时改阈值
    py disk_check.py --data-drives D,E               # 指定要判阈的盘（不在场的报「未检查」）
    py disk_check.py --json                          # 机器可读输出
    py disk_check.py --clean --target "<绝对路径>"    # 只看定性计划（干跑）
    py disk_check.py --clean --target "<绝对路径>" --apply
    py disk_check.py --migrate --from "<系统盘路径>" --to "<数据盘路径>"
    py disk_check.py --migrate --rollback --journal "<登记文件>"

配置优先级：命令行 > 技能目录 personal.local.md > 内置默认值。
内置默认值对陌生机器安全：不预设任何数据盘存在，系统盘自动探测。
退出码：体检 0=全绿 1=有 FAIL 2=有 WARN｜动作 0=无阻塞 1=有拒绝项或失败
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT_VERSION = "v1.2"

DEFAULT_FREE_GB_THRESHOLD = 15.0
DEFAULT_DATA_DRIVES = "auto"          # auto = 本机在场的所有盘（含系统盘，逐盘独立判阈）
KNOWN_CONFIG_KEYS = ("system_drive", "data_drives", "free_gb_threshold",
                     "clean_roots", "user_suspects", "max_scan_entries")

# 必须指向非系统盘的缓存类环境变量（PNPM_HOME 不在此列，见 HINT_ONLY_ENV 与 [5]）
ENV_TARGETS = {
    "PIP_CACHE_DIR": "pip 缓存",
    "HF_HOME": "HuggingFace 模型缓存",
    "npm_config_cache": "npm 缓存",
    "TORCH_HOME": "torch 预训练权重",
    "XDG_CACHE_HOME": "通用 XDG 缓存",
}
# 只作提示、不作结论的开关：真正的 store 位置由 `pnpm store path` 实查
HINT_ONLY_ENV = {"PNPM_HOME": "pnpm 目录"}

# 系统盘用户目录里「会偷偷长大」的位置（相对 USERPROFILE，通用写法，不含任何具体账号）
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
# [3] 的候选位置：由环境变量派生，不写死账号目录（写死就只在天本机成立）
JUNCTION_CANDIDATES = ("~/.cache", "LOCALAPPDATA/pip", "LOCALAPPDATA/npm-cache")
CACHE_NAME_KEYS = ("cache", "pip", "npm", "pnpm", "huggingface", "torch", "conda",
                   "yarn", "uv", "runtimes", "chroma", "opencode")

# 构成特征表（SKILL.md §八.2 的代码化）。命中 STATEFUL / KEEP 即不得按名字清。
REGENERABLE_NAME_MARKERS = ("cache", "code cache", "gpucache", "dawn", "grshader",
                            "shadercache", "crashdumps", "crash dumps", "temp", "tmp",
                            "node-gyp", "-updater", "installer", ".old", "logs")
STATEFUL_MARKERS = ("service worker", "indexeddb", "local storage", "session storage",
                    "leveldb", "blob storage", "databases", "filesystem", "playwright",
                    "offline", "webstorage")
KEEP_MARKERS = ("screenshot", "download", "history", "projects", "vault", "keys",
                ".sqlite", ".sqlite3", ".db", ".mdb", ".accdb")
BINARY_MARKERS = (".exe", ".dll", ".onnx", ".gguf", ".safetensors", ".msi", ".iso")
# 这些路径永不允许被 --clean 删（大小写不敏感、按前缀判）
PROTECTED_ROOTS = ("windows", "windows.old", "program files", "program files (x86)",
                   "programdata", "$recycle.bin", "system volume information", "recovery",
                   "boot", "perflogs", "documents and settings")

fails, warns, oks, infos = [], [], [], []

# ------------------------------------------------------------------ 基础工具


def norm_case(p):
    return str(p).replace("/", "\\").rstrip("\\").rstrip("/").lower()


def env_get(var):
    """三级读取：进程 → 用户（持久） → 机器。只读进程级会误报「未设置」。"""
    v = os.environ.get(var)
    if v:
        return v
    if IS_WINDOWS:
        try:
            import winreg
            for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                              (winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        val, _type = winreg.QueryValueEx(k, var)
                        if val:
                            return str(val)
                except OSError:
                    continue
        except ImportError:
            pass
    return ""


def detect_system_drive():
    if not IS_WINDOWS:
        return "/"
    return (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper()


def is_on_system_drive(p, cfg):
    sd = cfg["system_drive"]
    if not IS_WINDOWS:
        return str(p) == sd or str(p).startswith("/home") or str(p).startswith("/root")
    drive = Path(str(p)).drive.upper()
    return bool(drive) and drive == sd


def is_reparse_point(p) -> bool:
    """判「是不是链接（junction / symlink）」。

    🔴 os.path.islink 对 junction 恒 False（本机血案判据：顺着 junction 删会把真实数据端掉），
    所以改用重解析点属性——不挑 Python 版本、不挑是否管理员。
    """
    if not IS_WINDOWS:
        return os.path.islink(str(p))
    try:
        import ctypes
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        INVALID = 0xFFFFFFFF
        attr = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        if attr in (-1, INVALID) or attr < 0:
            return False
        return bool(attr & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        try:
            return bool(os.path.islink(str(p)) or Path(str(p)).is_junction())
        except (AttributeError, OSError):
            return False


def link_target(p):
    """链接指向。🔴 os.readlink 对 junction 回的是设备命名空间形式 `\\?\\E:\\...`
    （2026-10-11 实测），不剥掉前缀，「已是指向正确目标的链接」永远判不出来。"""
    try:
        t = os.readlink(str(p))
        if t:
            if t.startswith("\\\\?\\") or t.startswith("\\\\.\\"):
                t = t[4:]
            return t
    except OSError:
        pass
    try:
        return str(Path(str(p)).resolve())
    except OSError:
        return ""


def dir_size_mb(p):
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total / (1024 * 1024)


# ------------------------------------------------------------------ 配置层


def load_personal_config(skill_dir, enabled=True):
    """读技能目录的 personal.local.md（个人适配层）。缺文件不是错误，返回空字典。

    解析口径：整份文件逐行找「ASCII 键: 值」，围栏代码块内的键**照样读**
    （卡02 血案：解析器跳过围栏导致规则数静默归零）。中文冒号「：」不当分隔符，
    所以正文里的散文行不会被误读成配置。
    """
    out = {}
    if not enabled:
        return out
    f = Path(skill_dir) / "personal.local.md"
    if not f.is_file():
        return out
    key_re = re.compile(r"^[ \t]*(?:[-*][ \t]*)?`?([a-z][a-z0-9_]*)`?[ \t]*:[ \t]*(\S.*)$")
    for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = key_re.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key not in KNOWN_CONFIG_KEYS:
            warns.append("[0] 配置：%s 里的键 `%s` 不认识（拼错了？已忽略）" % (f.name, key))
            continue
        if key in out:
            warns.append("[0] 配置：%s 里 `%s` 出现多次，取第一次" % (f.name, key))
            continue
        out[key] = val
    return out


def build_config(args):
    cfg = {
        "system_drive": detect_system_drive(),
        "data_drives": DEFAULT_DATA_DRIVES,
        "free_gb_threshold": DEFAULT_FREE_GB_THRESHOLD,
        "clean_roots": "",
        "user_suspects": "",
        "max_scan_entries": 4000,
    }
    personal = load_personal_config(SKILL_DIR, enabled=not args.no_personal_config)
    if personal:
        infos.append("[0] 配置：已读 personal.local.md（%d 项：%s）"
                     % (len(personal), "、".join(sorted(personal))))
    for key, val in personal.items():
        cfg[key] = val
    if args.system_drive:
        cfg["system_drive"] = args.system_drive
    if args.data_drives:
        cfg["data_drives"] = args.data_drives
    if args.threshold is not None:
        cfg["free_gb_threshold"] = args.threshold
    if args.clean_root:
        cfg["clean_roots"] = ";".join(args.clean_root)
    if isinstance(cfg["free_gb_threshold"], str):
        try:
            cfg["free_gb_threshold"] = float(cfg["free_gb_threshold"])
        except ValueError:
            cfg["free_gb_threshold"] = DEFAULT_FREE_GB_THRESHOLD
            warns.append("[0] 配置：free_gb_threshold 不是数字，回落到默认 %s GB"
                         % DEFAULT_FREE_GB_THRESHOLD)
    try:
        cfg["max_scan_entries"] = int(cfg["max_scan_entries"])
    except (TypeError, ValueError):
        cfg["max_scan_entries"] = 4000
        warns.append("[0] 配置：max_scan_entries 不是整数，回落到默认 4000")
    if IS_WINDOWS:
        cfg["system_drive"] = str(cfg["system_drive"]).rstrip("\\").upper()
    return cfg


def split_list(text):
    return [x.strip() for x in re.split(r"[;,\n]", str(text or "")) if x.strip()]


# ------------------------------------------------------------------ [4] 空间判阈


def drive_present(letter):
    """盘符是否在场。单独成函数＝合成盘图可以只替换这一层，让上面的判阈逻辑照常真跑。"""
    return Path(f"{letter}:\\").exists()


def list_mounts(cfg):
    """返回 [(标签, 挂载点, 是否在场)]。

    auto = 本机在场的所有盘；显式列了盘符时，不在场的盘也要出现在结果里（在场位=False），
    调用方据此输出「未检查」——不静默跳过是这张卡的主修项。
    """
    want = split_list(cfg["data_drives"])
    if not IS_WINDOWS:
        pts = [cfg["system_drive"]]
        for p in want:
            if p.lower() != "auto" and p not in pts:
                pts.append(p)
        return [(p, p, os.path.isdir(p)) for p in pts] if want and want[0].lower() != "auto" \
            else [(p, p, True) for p in dict.fromkeys(pts)]
    present = [l for l in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if drive_present(l)]
    if not want or want[0].lower() == "auto":
        return [(f"{l}:", f"{l}:\\", True) for l in present]
    out = []
    for item in want:
        letter = item.strip().rstrip(":").upper()
        if len(letter) != 1 or not ("A" <= letter <= "Z"):
            warns.append("[4] 空间预警：`%s` 不是盘符，这一项未检查" % item)
            continue
        out.append((f"{letter}:", f"{letter}:\\", drive_present(letter)))
    return out


def check_free_space(cfg):
    thr = float(cfg["free_gb_threshold"])
    low, detail, unchecked = [], [], []
    for label, mount, present in list_mounts(cfg):
        if not present:
            unchecked.append(f"{label} 未检查（配置里列了它，本机没有这个盘）")
            continue
        try:
            usage = shutil.disk_usage(mount)
        except OSError as exc:
            unchecked.append(f"{label} 未检查（读取失败 {exc}）")
            continue
        free_gb = usage.free / (1024 ** 3)
        state = "低于阈值" if free_gb < thr else "达标"
        detail.append(f"{label} {free_gb:.1f} GB 可用 / {usage.total / (1024 ** 3):.0f} GB（{state}）")
        if free_gb < thr:
            low.append(f"{label} 仅剩 {free_gb:.1f} GB（阈值 {thr:g} GB）")
    if low:
        fails.append("[4] 空间预警：%d 个盘低于阈值（逐盘独立判定，含数据盘）\n        %s\n"
                     "        处置：这些盘一律禁止未确认的大规模写入；先清理或改落盘目标"
                     % (len(low), "\n        ".join(low)))
    elif detail:
        oks.append("[4] 空间预警：在场 %d 个盘逐一判阈均达标（阈值 %g GB｜%s）"
                   % (len(detail), thr, "；".join(detail)))
    else:
        warns.append("[4] 空间预警：没有任何盘被检查到（盘符全部不在场）——这不算健康")
    if unchecked:
        infos.append("[4] 空间预警：" + "；".join(unchecked))


# ------------------------------------------------------------------ [1] 环境变量


def check_env(cfg):
    moved, missing, bad, hints = [], [], [], []
    for var, desc in ENV_TARGETS.items():
        val = env_get(var)
        if not val:
            missing.append(f"{var}({desc})")
        elif is_on_system_drive(val, cfg):
            bad.append(f"{var}={val} → 仍在系统盘")
        else:
            moved.append(f"{var} → {val}")
    for var, desc in HINT_ONLY_ENV.items():
        val = env_get(var)
        if not val:
            hints.append(f"{var} 未设置（{desc}，结论见 [5]）")
        elif is_on_system_drive(val, cfg):
            warns.append(f"[1] {var}={val} 指向系统盘（只作提示，store 真身以 [5] `pnpm store path` 为准）")
        else:
            hints.append(f"{var}={val}（提示值，结论见 [5]）")
    if bad:
        fails.append("[1] 环境变量重定向：%d 项仍指向系统盘\n        %s"
                     % (len(bad), "\n        ".join(bad)))
    if missing:
        warns.append("[1] 环境变量重定向：%d 项未设置（未用到的工具可忽略）\n        %s"
                     % (len(missing), "\n        ".join(missing)))
    if moved and not bad:
        oks.append("[1] 环境变量重定向：%d 项已指向非系统盘" % len(moved))
    if hints:
        infos.append("[1] 提示型开关：" + "；".join(hints))


# ------------------------------------------------------------------ [2] 系统盘用户目录


def check_user_dirs(cfg, scan):
    if not scan:
        return
    home = Path(os.environ.get("USERPROFILE" if IS_WINDOWS else "HOME", str(Path.home())))
    suspects = split_list(cfg["user_suspects"]) or USER_SUSPECTS
    big = []
    for rel in suspects:
        p = Path(rel) if os.path.isabs(rel) else home / rel
        if not p.exists():
            continue
        if is_reparse_point(p):
            infos.append("[2] %s 是链接，体积记在未检查（真身在被指向的盘）" % p)
            continue
        size = dir_size_mb(p)
        if size >= 1024:
            big.append(f"{p}  {size / 1024:.1f} GB")
    if big:
        warns.append("[2] 系统盘用户目录可疑项（≥1GB）\n        %s\n"
                     "        处置：先设环境变量重定向；已生成的用 --migrate 先迁后校再切"
                     % "\n        ".join(big))
    else:
        oks.append("[2] 系统盘用户目录：无 ≥1GB 的可疑缓存（已扫描 %d 项）" % len(suspects))


# ------------------------------------------------------------------ [3] 链接指向


def junction_candidates():
    home = Path(os.environ.get("USERPROFILE" if IS_WINDOWS else "HOME", str(Path.home())))
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    roots = [home / ".cache", local / "pip", local / "npm-cache", local]
    seen, out = set(), []
    for r in roots:
        key = norm_case(r)
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def scan_junctions(cfg):
    if not IS_WINDOWS:
        infos.append("[3] 重定向检查：未检查（目录联接是 Windows 机制，本平台没有）")
        return
    redirected, problem = [], []
    for root in junction_candidates():
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            name_l = p.name.lower()
            if not any(k in name_l for k in CACHE_NAME_KEYS):
                continue
            if is_reparse_point(p):
                tgt = link_target(p)
                if tgt and is_on_system_drive(tgt, cfg):
                    problem.append(f"{p} → 链接仍指向系统盘 {tgt}")
                else:
                    redirected.append(f"{p} → {tgt}")
            elif p.is_dir():
                size = dir_size_mb(p)
                if size >= 50:
                    problem.append(f"{p} 实体目录 {size:.0f} MB（未拐走）")
    if problem:
        warns.append("[3] 重定向检查：%d 处未拐走或拐错\n        %s\n"
                     "        处置：用 --migrate（占用核查→复制→校验→切换→回退）"
                     % (len(problem), "\n        ".join(problem)))
    if redirected:
        oks.append("[3] 重定向检查：%d 处已正确拐到非系统盘" % len(redirected))
    if not redirected and not problem:
        oks.append("[3] 重定向检查：未发现候选缓存目录")


# ------------------------------------------------------------------ [5] pnpm 实查


def run_cmd(argv, timeout=30):
    """返回 (退出码, stdout)；找不到可执行文件返回 (None, 原因)。

    .cmd / .bat 在 Windows 上走 cmd /c 更稳（CreateProcess 对批处理的兼容面窄）。
    """
    try:
        if IS_WINDOWS and str(argv[0]).lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c"] + [str(a) for a in argv]
        else:
            cmd = [str(a) for a in argv]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def check_pnpm_store():
    exe = shutil.which("pnpm")
    if not exe:
        infos.append("[5] pnpm store：未检查（PATH 里没有 pnpm，装了再说）")
        return
    code, out = run_cmd([exe, "store", "path"])
    if code is None:
        warns.append("[5] pnpm store：未检查（无法调用 %s：%s）" % (exe, out))
        return
    if code != 0:
        warns.append("[5] pnpm store：未检查（`pnpm store path` 退出码 %s，没给出位置）" % code)
        return
    line = [l.strip() for l in out.splitlines() if l.strip()]
    store = line[-1] if line else ""
    if not store:
        warns.append("[5] pnpm store：未检查（命令回显为空）")
        return
    home = env_get("PNPM_HOME")
    note = "；PNPM_HOME=%s 只是提示" % home if home else ""
    if is_on_system_drive(store, {"system_drive": detect_system_drive()}):
        fails.append("[5] pnpm 实际 store 在系统盘：%s（结论来自 `pnpm store path`%s）\n"
                     "        处置：`pnpm config set store-dir <非系统盘>\\pnpm-store`" % (store, note))
    else:
        oks.append("[5] pnpm 实际 store：%s（`pnpm store path` 实查%s）" % (store, note))


# ------------------------------------------------------------------ 定性（不按目录名下结论）


def clean_roots(cfg):
    home = Path(os.environ.get("USERPROFILE" if IS_WINDOWS else "HOME", str(Path.home())))
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    roaming = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    out = [local, roaming, Path(os.environ.get("TEMP", str(local / "Temp"))), home / ".cache"]
    for extra in split_list(cfg.get("clean_roots", "")):
        out.append(Path(extra))
    return out


def inside_roots(path, roots):
    np = norm_case(path)   # norm_case 已把 / 换成 \，只需比反斜杠前缀
    for r in roots:
        nr = norm_case(r)
        if np == nr or np.startswith(nr + "\\"):
            return r
    return None


def scan_tree(target, limit=4000, depth=4):
    """返回 (看过多少项, 命中的构成特征, 是否看完, 树内链接清单)。"""
    seen, hits, links = 0, [], []
    complete = True
    for root, dirs, files in os.walk(str(target)):
        rel_parts = [p for p in os.path.relpath(root, str(target)).split(os.sep) if p != "."]
        if len(rel_parts) >= depth:
            dirs[:] = []
        for name in list(dirs) + list(files):
            fp = Path(root) / name
            low = name.lower()
            if is_reparse_point(fp):
                links.append(str(fp))
            rel = os.path.relpath(fp, str(target)).replace("\\", "/")
            for marker in STATEFUL_MARKERS:
                if marker in low:
                    hits.append("状态件 %s（特征 `%s`）" % (rel, marker))
                    break
            else:
                for marker in KEEP_MARKERS:
                    if marker in low:
                        hits.append("数据件 %s（特征 `%s`）" % (rel, marker))
                        break
                else:
                    if any(low.endswith(m) for m in BINARY_MARKERS):
                        hits.append("二进制/权重 %s" % rel)
            seen += 1
            if seen >= limit:
                complete = False
                break
        if not complete:
            break
    return seen, hits, complete, links


def classify_target(target, cfg):
    """→ (verdict, evidence list)，verdict ∈ protected / needs_confirm / regenerable。

    判据顺序：先硬守卫（在不在允许区、是不是链接、有没有撞系统目录），
    再按**树里的构成**定性；目录名只能给出「候选族」，不能单独判可删。
    """
    p = Path(str(target))
    ev = []
    if not p.exists():
        return "protected", ["路径不存在，未检查"]
    if not p.is_dir():
        return "protected", ["不是目录（本工具只处理目录级清理）"]
    if is_reparse_point(p):
        return "protected", ["本身是链接（→ %s）：删链接只准 `cmd /c rmdir`，本工具不代删链接内容"
                             % (link_target(p) or "未知")]
    drive = Path(str(p)).drive.upper() if IS_WINDOWS else ""
    if drive and norm_case(p) in (drive.lower(), drive.lower() + "\\"):
        return "protected", ["是盘根，禁止清理"]
    # 系统根锚点：环境变量优先；探不到时用「已探测/已配置的 system_drive + \Windows」推导，
    # 不在源码里写死任何具体盘符（发布到陌生机器才不会指错，也不会被脱敏扫描当成本机痕迹）。
    sysroot = os.environ.get("SystemRoot") or (str(cfg.get("system_drive") or "C:").rstrip("\\") + "\\Windows")
    for anchor in (Path(sysroot).parent if IS_WINDOWS else Path("/"),
                   Path(os.environ.get("ProgramFiles", "Program Files")),
                   Path(os.environ.get("ProgramFiles(x86)", "Program Files (x86)"))):
        try:
            if norm_case(p) == norm_case(anchor):
                return "protected", ["是系统根目录 %s，禁止清理" % anchor]
        except OSError:
            pass
    rel_top = p.name.lower()
    if rel_top in PROTECTED_ROOTS:
        return "protected", ["命中系统目录名 `%s`，禁止清理" % rel_top]
    roots = clean_roots(cfg)
    hit_root = inside_roots(p, roots)
    if hit_root is None:
        return "protected", ["不在允许清理的根目录下（允许根：%s）；要放行请用 --clean-root 或把它写进 personal.local.md"
                             % "、".join(str(r) for r in roots)]
    seen, hits, complete, links = scan_tree(p, limit=int(cfg.get("max_scan_entries", 4000)))
    if links:
        return "protected", ["树内含 %d 个链接（例：%s），删除会伤到被指向的真实数据" % (len(links), links[0])]
    if hits:
        return "needs_confirm", ["按目录名叫 `%s`，但树里查出的构成不允许按名字删：%s（要删请写进白名单）"
                                 % (p.name, "；".join(hits[:6]))]
    if not complete:
        return "needs_confirm", ["构成扫描在项目上限处截断，没看完就不能判可删"]
    if any(m in rel_top for m in REGENERABLE_NAME_MARKERS):
        return "regenerable", ["目录名属再生缓存族（`%s`）且树内 %d 项未见状态件/数据件/二进制" % (rel_top, seen)]
    return "needs_confirm", ["目录名 `%s` 不属再生缓存族，树内 %d 项也无状态特征——定不了性就按需确认处理"
                             % (rel_top, seen)]


def load_clean_allowlist():
    f = SKILL_DIR / "clean_allowlist.local.md"
    if not f.is_file():
        return None, []
    entries = []
    for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line.split("#")[0].strip())
    return f, [e for e in entries if e]


def allowlisted(path, entries):
    np = norm_case(path)
    for e in entries:
        ne = norm_case(e)
        if np == ne or np.startswith(ne + "\\") or np.startswith(ne + "/"):
            return e
    return None


def safe_delete_tree(target):
    """自建递归删除：先整树确认没有重解析点，再自底向上删。

    🔴 不用 shutil.rmtree——它会把 junction 当普通目录钻进去，顺着链接删到真实数据。
    """
    for root, dirs, files in os.walk(str(target)):
        for name in list(dirs) + list(files):
            fp = Path(root) / name
            if is_reparse_point(fp):
                raise RuntimeError("树内发现链接 %s，已中止（不顺链接删）" % fp)
    freed = 0
    for root, dirs, files in os.walk(str(target), topdown=False):
        for name in files:
            fp = Path(root) / name
            try:
                freed += fp.stat().st_size
                fp.unlink()
            except PermissionError:
                os.chmod(str(fp), 0o777)
                fp.unlink()
            except FileNotFoundError:
                pass
        for name in dirs:
            dp = Path(root) / name
            try:
                dp.rmdir()
            except (FileNotFoundError, OSError):
                pass
    Path(str(target)).rmdir()
    return freed


def run_clean(cfg, targets, apply_):
    if not targets:
        fails.append("[clean] 没有 --target：清理只认显式点名的目标，不做「扫到什么删什么」")
        return 1
    list_file, entries = load_clean_allowlist()
    head = ("[clean] 白名单：%s（%d 条）" % (list_file, len(entries))) if list_file else \
           ("[clean] 白名单文件不在场（%s）：非再生缓存类目录一律需确认、不动一个字节"
            % (SKILL_DIR / "clean_allowlist.local.md"))
    infos.append(head)
    blocked, deleted, freed_total = 0, 0, 0
    for t in targets:
        verdict, ev = classify_target(t, cfg)
        body = "\n        · " + "\n        · ".join(ev)
        if verdict == "protected":
            blocked += 1
            fails.append("[clean] [拒绝] %s%s" % (t, body))
            continue
        approved = allowlisted(t, entries) if verdict == "needs_confirm" else None
        if verdict == "needs_confirm" and not approved:
            blocked += 1
            warns.append("[clean] [需确认] %s%s" % (t, body))
            continue
        if not apply_:
            infos.append("[clean] [计划·干跑] %s｜定性=%s%s｜未写一个字节，加 --apply 才执行"
                         % (t, verdict, ("（白名单放行：%s）" % approved) if approved else ""))
            continue
        try:
            freed = safe_delete_tree(Path(str(t)))
        except (OSError, RuntimeError) as exc:
            blocked += 1
            fails.append("[clean] [失败] %s：%s（已删的不可回退，剩余内容请人工检查）" % (t, exc))
            continue
        deleted += 1
        freed_total += freed
        oks.append("[clean] [已删] %s｜释放 %.1f MB" % (t, freed / (1024 * 1024)))
    infos.append("[clean] 小结：目标 %d｜已删 %d｜需确认或被拒 %d｜释放 %.1f MB"
                 % (len(targets), deleted, blocked, freed_total / (1024 * 1024)))
    return 1 if blocked else 0


# ------------------------------------------------------------------ 迁移（占用→复制→校验→切换→回退）


def read_journal(path):
    try:
        return json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_journal(path, data):
    Path(str(path)).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def probe_writable_dir(path):
    """占用核查：改名再改回来。Windows 上目录内有未共享删除的句柄时改名必失败，
    失败即判「被占用」，不动一个字节地知道有没有活的读者。
    """
    if not IS_WINDOWS:
        return True, "非 Windows：跳过重命名探针（该平台占用语义不同）"
    sentinel = str(path) + ".__occupancy_probe__"
    try:
        os.rename(str(path), sentinel)
    except OSError as exc:
        return False, "改名被拒（多半有程序正在用）：%s" % exc
    try:
        os.rename(sentinel, str(path))
    except OSError as exc:
        return False, "严重：探针改名后未能还原 %s → %s（请手工还原）" % (sentinel, path)
    return True, "未被占用"


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tree_manifest(root, with_hash=True):
    """相对路径 → (size, sha256 或 None)；遇链接即报错（不跟着链接走）。"""
    out = {}
    for dirpath, dirs, files in os.walk(str(root)):
        for name in files:
            fp = Path(dirpath) / name
            if is_reparse_point(fp):
                raise RuntimeError("树内含链接 %s，迁移会丢内容，已中止" % fp)
            rel = os.path.relpath(fp, str(root)).replace("\\", "/")
            try:
                st = fp.stat()
            except OSError as exc:
                raise RuntimeError("读取 %s 失败：%s" % (fp, exc))
            out[rel] = (st.st_size, sha256_of(fp) if with_hash else None)
        for name in dirs:
            dp = Path(dirpath) / name
            if is_reparse_point(dp):
                raise RuntimeError("树内含链接 %s，迁移会丢内容，已中止" % dp)
    return out


def copy_tree(src, dst, reporter=None):
    src, dst = Path(str(src)), Path(str(dst))
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for dirpath, dirs, files in os.walk(str(src)):
        rel = os.path.relpath(dirpath, str(src))
        target_dir = dst if rel == "." else dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            fp = target_dir / name
            shutil.copy2(str(Path(dirpath) / name), str(fp))
            n += 1
            if reporter:
                reporter(n)
    return n


def create_junction(link, target):
    if not IS_WINDOWS:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True, "symlink"
    code, out = run_cmd(["cmd", "/c", "mklink", "/J", str(link), str(target)])
    return code == 0, (out or "")


def remove_junction(link):
    """摘链只准 `cmd /c rmdir`（不带 /S）——带 /S 或 rm -rf 会顺链接删到真实数据。"""
    if not IS_WINDOWS:
        os.unlink(str(link))
        return True, ""
    code, out = run_cmd(["cmd", "/c", "rmdir", str(link)])
    return code == 0, (out or "")


def migrate_status(src, dst):
    """幂等检测三态（SKILL.md §三）：已是正确链接就停止，绝不再挪一次。"""
    s = Path(str(src))
    if is_reparse_point(s):
        tgt = link_target(s)
        if norm_case(tgt) == norm_case(dst):
            return "already", tgt
        return "wrong-link", tgt
    return "physical", ""


def run_migrate(cfg, src, dst, apply_, rollback, journal_arg):
    if rollback:
        # 回退只认登记文件：登记里带着 src/dst/stage，比让人重新敲一遍 --from/--to 可靠，
        # 也避免了「记错目标 → 回退删错目录」这种二次事故。
        if journal_arg:
            jpath = Path(str(journal_arg))
        elif src and dst:
            jpath = Path(str(dst) + ".disk_check_migrate.json")
        else:
            fails.append("[migrate] 回退需要 --journal <登记文件>（或同时给 --from 与 --to 按默认登记名找）——不猜路径")
            return 1
        if not IS_WINDOWS:
            infos.append("[migrate] 本平台不是 Windows：回退依赖的链接机制不存在，未检查、不执行")
            return 1
        return do_rollback(jpath)
    if not src or not dst:
        fails.append("[migrate] 需要 --from 与 --to（迁移不猜路径）")
        return 1
    if not IS_WINDOWS:
        infos.append("[migrate] 本平台不是 Windows：junction 机制不存在，未检查、不执行")
        return 1
    jpath = Path(str(journal_arg)) if journal_arg else Path(str(dst) + ".disk_check_migrate.json")
    if jpath.exists():
        fails.append("[migrate] 已有未完成登记：%s（上次中断或正在做）——先跑 --migrate --rollback --journal 那个文件"
                     % jpath)
        return 1
    state, tgt = migrate_status(src, dst)
    if state == "already":
        oks.append("[migrate] %s 已是链接且指向 %s：无需迁移，已停止（幂等检测生效）" % (src, tgt))
        return 0
    if state == "wrong-link":
        fails.append("[migrate] %s 是链接但指向 %s：请先 `cmd /c rmdir \"%s\"` 摘链再重跑（本工具不代摘链）"
                     % (src, tgt, src))
        return 1
    s = Path(str(src))
    if not s.is_dir():
        fails.append("[migrate] --from %s 不是实体目录（不存在或不是目录），未检查" % src)
        return 1
    d = Path(str(dst))
    if d.exists() and any(d.iterdir()):
        fails.append("[migrate] --to %s 已存在且非空：拒绝往别人的目录里倒东西" % dst)
        return 1
    if is_on_system_drive(dst, cfg):
        fails.append("[migrate] --to %s 仍在系统盘：迁移就没有意义了（--data-drives 或换目标）" % dst)
        return 1
    dst_drive = Path(str(d)).drive
    try:
        usage = shutil.disk_usage(dst_drive + "\\" if dst_drive else str(d))
    except OSError as exc:
        fails.append("[migrate] 目标盘容量读不到：%s，未检查" % exc)
        return 1
    size_mb = dir_size_mb(s)
    need_mb = size_mb * 1.05 + 1
    if usage.free / (1024 ** 2) < need_mb:
        fails.append("[migrate] 目标盘剩余 %.1f GB 不足以放 %s（需 ≥%.1f MB，含 5%% 余量）"
                     % (usage.free / (1024 ** 3), "%.1f MB" % size_mb, need_mb))
        return 1
    plan = ["① 占用核查（重命名探针）", "② 复制 %s → %s（%.1f MB）" % (src, dst, size_mb),
            "③ 逐文件 size+sha256 校验", "④ 改名让位（可还原）", "⑤ mklink /J 建链",
            "⑥ 跨链接写测试", "⑦ 删除原目录实体（仅在①~⑥全过后）"]
    if not apply_:
        infos.append("[migrate] [计划·干跑] " + "｜".join(plan)
                     + "｜未执行占用探针（它会短暂改名），加 --apply 才动手")
        return 0
    ok, why = probe_writable_dir(s)
    if not ok:
        fails.append("[migrate] [中止·①占用核查] %s：%s（原目录一个字节没动）" % (src, why))
        return 1
    infos.append("[migrate] ①占用核查通过：%s" % why)
    write_journal(jpath, {"stage": "copying", "src": str(s), "dst": str(d),
                          "started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps_done": []})
    try:
        n = copy_tree(s, d)
    except (OSError, RuntimeError) as exc:
        fails.append("[migrate] [失败·②复制] %s（源目录未动；用 --rollback 清掉半成品）：%s" % (jpath, exc))
        return 1
    infos.append("[migrate] ②复制完成：%d 个文件" % n)
    try:
        a, b = tree_manifest(s), tree_manifest(d)
    except RuntimeError as exc:
        remove_tree_quiet(d)
        jpath.unlink(missing_ok=True)
        fails.append("[migrate] [中止·③校验] %s（源目录未动，目标已收回）" % exc)
        return 1
    if a != b:
        only_src = sorted(set(a) - set(b))[:5]
        only_dst = sorted(set(b) - set(a))[:5]
        diff = [k for k in set(a) & set(b) if a[k] != b[k]][:5]
        write_journal(jpath, {"stage": "verify_failed", "src": str(s), "dst": str(d),
                              "steps_done": ["occupancy", "copy"],
                              "detail": {"只在源": only_src, "只在目标": only_dst, "内容不同": diff}})
        fails.append("[migrate] [中止·③校验] 复制结果与源不一致（源目录未动）。"
                     "只在源 %s｜只在目标 %s｜不同 %s → 用 --rollback 撤销"
                     % (only_src, only_dst, diff))
        return 1
    oks.append("[migrate] ③校验通过：%d 个文件 size+sha256 全部一致" % len(a))
    old = str(s) + ".__migrating_old__"
    try:
        os.rename(str(s), old)
    except OSError as exc:
        fails.append("[migrate] [中止·④让位] 改名失败：%s（源目录未动；--rollback 收目标）" % exc)
        return 1
    mark_journal(jpath, "source_renamed", old_path=old)
    ok, out = create_junction(str(s), str(d))
    if not ok:
        try:
            os.rename(old, str(s))
        except OSError as exc:
            fails.append("[migrate] [严重·⑤建链失败后还原也失败] %s → %s：%s" % (old, s, exc))
            return 1
        jpath.unlink(missing_ok=True)
        fails.append("[migrate] [中止·⑤建链] mklink 失败：%s（源目录已还原，登记已清）" % out)
        return 1
    mark_journal(jpath, "linked")
    probe = Path(str(s)) / ".disk_check_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        back = probe.read_text(encoding="utf-8")
        if back != "ok":
            raise IOError("读回内容不符")
        probe.unlink()
    except OSError as exc:
        remove_junction(str(s))
        try:
            os.rename(old, str(s))
        except OSError:
            pass
        jpath.unlink(missing_ok=True)
        fails.append("[migrate] [中止·⑥写测试] 跨链接写失败：%s（已回退）" % exc)
        return 1
    infos.append("[migrate] ⑤建链 + ⑥跨链接写测试通过")
    try:
        freed = safe_delete_tree(Path(old))
    except (OSError, RuntimeError) as exc:
        mark_journal(jpath, "old_not_deleted", old_path=old)
        warns.append("[migrate] [注意·⑦] 链接已生效，但原目录 %s 没能删掉：%s——请人工确认后再删（数据双份，不影响读写）"
                     % (old, exc))
        return 0
    mark_journal(jpath, "done")
    jpath.unlink(missing_ok=True)
    oks.append("[migrate] 完成：%s → 链接指向 %s，原实体已删（释放 %.1f MB），登记文件已清"
               % (src, dst, freed / (1024 * 1024)))
    return 0


def mark_journal(path, stage, **extra):
    try:
        data = read_journal(path) or {}
    except OSError:
        data = {}
    data["stage"] = stage
    data.setdefault("steps_done", []).append(stage)
    data.update(extra)
    try:
        write_journal(path, data)
    except OSError:
        pass


def remove_tree_quiet(p):
    try:
        safe_delete_tree(Path(str(p)))
    except (OSError, RuntimeError):
        pass


def do_rollback(jpath):
    """按登记文件把迁移倒回去。原则：源侧数据优先还原，目标侧只在「源还在场」时才回收。"""
    data = read_journal(jpath)
    if not data:
        fails.append("[migrate] [回退] 读不到登记文件 %s，不做任何猜测式删除" % jpath)
        return 1
    stage = data.get("stage", "")
    src, dst, old = data.get("src", ""), data.get("dst", ""), data.get("old_path", "")
    steps = []

    if stage in ("linked", "old_not_deleted") and is_reparse_point(src):
        ok, out = remove_junction(src)
        if not ok:
            fails.append("[migrate] [回退受阻] 摘链失败：%s（请手工 `cmd /c rmdir \"%s\"` 后重试）"
                         % (out, src))
            return 1
        steps.append("摘掉链接 %s" % src)
    if old and Path(str(old)).is_dir() and not Path(str(src)).exists():
        try:
            os.rename(str(old), str(src))
            steps.append("原目录复位 %s" % src)
        except OSError as exc:
            fails.append("[migrate] [回退受阻] 原目录复位失败：%s（%s 仍在，请手工改名）" % (exc, old))
            return 1
    if stage in ("copying", "verify_failed") and Path(str(dst)).exists():
        if Path(str(src)).exists():
            remove_tree_quiet(dst)
            gone = not Path(str(dst)).exists()
            steps.append("收回复制阶段的目标目录 %s（%s）" % (dst, "已清空" if gone else "未能清空"))
            if not gone:
                fails.append("[migrate] [回退受阻] 目标目录 %s 没清干净，登记文件保留" % dst)
                return 1
        else:
            steps.append("目标目录 %s 未动（源目录不在场，不敢删目标）" % dst)

    for s in steps:
        oks.append("[migrate] [回退] %s" % s)
    src_back = Path(str(src)).exists()
    if stage in ("linked", "old_not_deleted") and src_back and Path(str(dst)).exists():
        oks.append("[migrate] [回退] 数据仍是双份（%s 与 %s）——源侧已复位，目标侧请人工核对后再删" % (src, dst))
    else:
        jpath.unlink(missing_ok=True)
        oks.append("[migrate] [回退] 完成到阶段 %s｜源目录%s｜登记文件已清"
                   % (stage, "在场" if src_back else "不在场"))
    return 0


# ------------------------------------------------------------------ 输出与入口


def emit(as_json):
    if as_json:
        print(json.dumps({"version": SCRIPT_VERSION, "ok": oks, "warn": warns,
                          "fail": fails, "info": infos}, ensure_ascii=False, indent=2))
        return
    for line in oks:
        print("  OK   " + line)
    for line in warns:
        print("  WARN " + line)
    for line in fails:
        print("  FAIL " + line)
    for line in infos:
        print("  INFO " + line)
    print("-" * 56)
    print("体检结果：OK %d / WARN %d / FAIL %d / INFO %d" % (len(oks), len(warns), len(fails), len(infos)))


def build_parser():
    import argparse
    ap = argparse.ArgumentParser(add_help=True, prog="disk_check.py",
                                 description="磁盘与缓存纪律体检（默认只读）+ 显式清理/迁移入口")
    ap.add_argument("--scan-user", action="store_true", help="扫描系统盘用户目录大缓存")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--threshold", type=float, default=None, help="剩余空间阈值 GB")
    ap.add_argument("--system-drive", default=None, help="系统盘盘符（默认自动探测）")
    ap.add_argument("--data-drives", default=None, help="要判阈的盘，逗号分隔；auto=所有在场盘")
    ap.add_argument("--clean-root", action="append", default=[], help="追加允许清理的根目录（可重复）")
    ap.add_argument("--no-personal-config", action="store_true", help="不读 personal.local.md")
    ap.add_argument("--clean", action="store_true", help="清理模式（默认干跑）")
    ap.add_argument("--migrate", action="store_true", help="迁移模式（默认干跑）")
    ap.add_argument("--target", action="append", default=[], help="清理目标（可重复）")
    ap.add_argument("--from", dest="from_", default=None, help="迁移源（系统盘上的实体目录）")
    ap.add_argument("--to", dest="to_", default=None, help="迁移目标（非系统盘）")
    ap.add_argument("--apply", action="store_true", help="真写盘（不加只出计划）")
    ap.add_argument("--rollback", action="store_true", help="按登记文件回退迁移")
    ap.add_argument("--journal", default=None, help="迁移登记文件路径")
    ap.add_argument("--version", action="store_true", help="打印脚本版本")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if args.version:
        print("disk_check.py %s" % SCRIPT_VERSION)
        return 0
    cfg = build_config(args)
    if args.clean:
        code = run_clean(cfg, args.target, args.apply)
        emit(args.json)
        return code
    if args.migrate:
        code = run_migrate(cfg, args.from_, args.to_, args.apply, args.rollback, args.journal)
        emit(args.json)
        return code
    check_env(cfg)
    check_user_dirs(cfg, args.scan_user)
    scan_junctions(cfg)
    check_free_space(cfg)
    check_pnpm_store()
    emit(args.json)
    if fails:
        return 1
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
