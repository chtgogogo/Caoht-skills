#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""活路径活性检查（check_dead_paths）——「打不开的路径一律不留字符串」的机检。

    py scripts/check_dead_paths.py check --root <项目根或手册目录> [--json <落盘>]
    py scripts/check_dead_paths.py selftest [--tmp-root <目录>]

为什么要有这把尺（2026-10-18 实测触发）：
  文档里留一条「已删除的旧路径」（哪怕加了删除线、后面补一句"作废"）**不算留证，算留雷**——
  ① 任何检索/字符串匹配（grep、glob、AI 的关键词扫描）按**字面**命中，删除线对机器不存在；
  ② 模型读到「带盘符的具体路径」时，它是上下文里最像"可直接用"的锚点，否定语义（"已作废"）
     常在片段截断时被丢掉；
  ③ 死路径**不会报错**：文档照常打开，只在你真去用它那一刻才失败（RT-014 同一根因）。
  ⇒ 铁律：**能打开的路径才许写做人读面的指针；打不开的，连字符串一起删**。
  历史区（CHANGELOG／changes/archive／evidence／归档台账／回看报告）里的旧路径是**证词**，
  不追改，但必须在报告里被点名计数——不许静默豁免。

判据四件（selftest 逐条钉死）：
  D1 死路径（盘符或 UNC，`Path.exists()` 为 False）⇒ **红**，非历史区一律不许留；
  D2 **删除线包着的路径照样算死**（`~~X~~`、`<del>X</del>`、`（作废）X` 都是同一个雷）——不留豁免；
  D3 脱敏省略写法（`E:\Acht…pem\`、`<你的体系根目录>\...`）不是可用指针、也不是死链 ⇒ 跳过（否则真红被假红淹没）；
  D4 历史区死路径点名计数，退出码不受它影响（证词不追改）。

退出码：0 无死路径｜1 有死路径｜2 路径/用法不对。
零第三方依赖。
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

VERSION = "1.0.0"

# 盘符路径（<盘符>:\ 或 <盘符>:/）与 UNC（\\server\share）
# 🔴 盘符前不许是字母/数字：否则 `http://` 里的 `p:` 会被当成盘符（首版实测踩过）
PATH_RX = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/][^\s\"'`<>|*?）)\]】」』，。；：、]+)"
    r"|(?:\\\\[\w.\-]+\\[^\s\"'`<>|*?）)\]】」』，。；：、]+)"
)
EXTS = {".md", ".txt", ".json", ".ps1", ".py", ".sh", ".yaml", ".yml"}
# 非文档区：第三方库与构建产物里的示例路径不是本项目的指针（首版实测：扫 .venv 出 84 条假红）
EXCL_DIRS = {".venv", "node_modules", ".git", "__pycache__", "dist", ".pytest_cache",
             ".mimosa", ".idea", ".pip-cache", "data", ".ruff_cache", ".mypy_cache"}
# 历史区（证词）：不追改，但点数照报
HIST = ("CHANGELOG.md", "changes/archive/", "evidence/", "回看报告",
        "台账_归档_", "交付记录清单", "docs/流水账本", "00-目录说明.md")
# D2：删除线／作废标记——它们只对人类渲染有效，对字符串匹配无效
DEL_MARK = re.compile(r"~~|<del>|</del>|（作废）|\(作废\)|【作废】|作废[:：]")


def excluded(rel: str) -> bool:
    return any(seg in EXCL_DIRS for seg in rel.split("/")[:-1])


def is_hist(rel: str) -> bool:
    return any(k in rel for k in HIST)


V_TAIL = "已作废："


def form_issues(line: str):
    """D5：作废说明的**形态**（判据源＝共享层 global-behavior-rules.md §11）。"""
    out = []
    if V_TAIL in line:
        if line.strip().startswith(V_TAIL):
            out.append("「已作废：」出现在行首——现状必须在前")
        if line.count(V_TAIL) > 1:
            out.append("同一行出现多个「已作废：」（同条至多一处）")
        if "~~" in line:
            out.append("删除线与「已作废：」并用（黑名单形态，二选一）")
    elif "~~" in line and PATH_RX.search(line):
        out.append("删除线包着路径、却没写「已作废：」——纯值应整串删掉，不靠划掉")
    return out


def scan(root: Path):
    alive, dead, hist, badform = [], [], [], []
    files = 0
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in EXTS:
            continue
        rel = f.relative_to(root).as_posix()
        if excluded(rel):
            continue
        files += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in PATH_RX.finditer(line):
                raw = m.group(0).rstrip(".,;:)")
                if "…" in raw or "..." in raw:      # D3
                    continue
                item = {"file": rel, "line": i, "path": raw}
                if Path(raw).exists():
                    item["struck"] = bool(DEL_MARK.search(line))   # 活路径被划掉＝可疑，单列
                    alive.append(item)
                elif is_hist(rel):
                    hist.append(item)
                else:
                    dead.append(item)
            if not is_hist(rel):
                for msg in form_issues(line):
                    badform.append({"file": rel, "line": i, "msg": msg})
    return files, alive, dead, hist, badform


def report(root: Path, as_json: str = ""):
    files, alive, dead, hist, badform = scan(root)
    struck = [x for x in alive if x.get("struck")]
    print(f"[scan v{VERSION}] root={root}")
    print(f"[扫描] {files} 个文本件｜路径引用 {len(alive) + len(dead) + len(hist)} 处"
          f"（活 {len(alive)}／死 {len(dead)}／历史区死 {len(hist)}）")
    for it in dead:
        print(f"[死] {it['file']}:{it['line']}｜{it['path']}")
    for it in struck:
        print(f"[警] {it['file']}:{it['line']}｜路径是活的却被删除线/作废标记包住：{it['path']}"
              f"（要么删标记，要么连路径一起删）")
    for it in badform:
        print(f"[警] D5 {it['file']}:{it['line']}｜{it['msg']}")
    if hist:
        by = {}
        for it in hist:
            by[it["file"]] = by.get(it["file"], 0) + 1
        print("[证词区·不追改但点名] " + "／".join(f"{k} {v} 处" for k, v in sorted(by.items())))
    if as_json:
        Path(as_json).write_text(json.dumps(
            {"alive": alive, "dead": dead, "hist": hist, "struck": struck, "badform": badform},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[已写] {as_json}")
    print(f"[汇总] 死 {len(dead)} 处｜活路径被划掉 {len(struck)} 处｜形态告警 {len(badform)} 处"
          f"｜历史区死 {len(hist)} 处（不计红）")
    return 1 if dead else 0


def cmd_check(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[路径不对] {root} 不存在 ⇒ 退 2")
        return 2
    return report(root, args.json)


def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-deadpath-selftest"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    root = tmp / "p1"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "pipeline" / "evidence").mkdir(parents=True)
    alive_file = tmp / "真的存在.txt"
    alive_file.write_text("x", encoding="utf-8")

    results = []

    def chk(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    (root / "GUIDE.md").write_text(
        "# 指南\n\n"
        f"活：`{alive_file}`\n"
        "死：`C:\\不存在的目录\\a.md`\n"
        "删线：~~`D:\\已删除\\旧说明.docx`~~（作废）\n"
        "脱敏：`E:\\Acht…pem\\` 与 `E:\\Agent\\...`\n"
        "URL：`http://127.0.0.1:8000/health`\n"
        "相对：`docs/pipeline/README.md`\n"
        "已作废：旧值甲（这行现状不在前，形态错）\n"
        "正确形态：现状乙 → 已作废：旧值乙\n", encoding="utf-8")
    (root / "docs" / "pipeline" / "evidence" / "读数.txt").write_text(
        "旧读数里的路径：`C:\\曾经在\\这里.txt`\n已作废：历史区这么写不算形态错（证词不追改）\n",
        encoding="utf-8")

    files, alive, dead, hist, badform = scan(root)
    chk("S01 扫到 2 个文本件", files == 2, f"实际={files}")
    dead_paths = [d["path"] for d in dead]
    chk("S02 死路径被判红（C:\\不存在的目录\\a.md）",
        any("不存在的目录" in p for p in dead_paths), f"死={dead_paths}")
    chk("S03 删除线包着的死路径**照样**判红（不留豁免——这正是雷区）",
        any("已删除" in p for p in dead_paths), f"死={dead_paths}")
    chk("S04 脱敏省略写法不当死链（D3）",
        not any("Acht" in p or "Agent" in p for p in dead_paths), f"死={dead_paths}")
    chk("S05 URL 里的 p: 不被当盘符（D4 前置）",
        not any("127.0.0.1" in p for p in dead_paths), f"死={dead_paths}")
    chk("S06 活路径计入 alive", any("真的存在" in a["path"] for a in alive), f"活={[a['path'] for a in alive]}")
    chk("S07 历史区死路径单列、不混进红", len(hist) == 1 and "曾经在" in hist[0]["path"],
        f"历史={[h['path'] for h in hist]}")
    chk("S12 D5 抓到「已作废：」在行首（现状没在前）",
        any("行首" in b["msg"] for b in badform), f"形态={[b['msg'] for b in badform]}")
    chk("S13 D5 不误伤正确形态（现状 → 已作废：旧值）",
        not any("GUIDE.md:8" in f"{b['file']}:{b['line']}" for b in badform),
        f"形态={[(b['file'], b['line'], b['msg']) for b in badform]}")
    chk("S14 历史区形态不报（证词不追改）",
        not any("读数.txt" in b["file"] for b in badform),
        f"形态={[(b['file'], b['line']) for b in badform]}")

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(root)
        rc_json = report(root, str(tmp / "out.json"))
        rc_bad = cmd_check(argparse.Namespace(root=str(tmp / "不存在"), json=""))
    chk("S08 真出口：有死路径退 1", rc == 1, f"退码={rc}")
    chk("S09 真出口：JSON 落盘也退 1 且文件在盘",
        rc_json == 1 and (tmp / "out.json").is_file(), f"退码={rc_json}")
    chk("S10 真出口：路径不存在退 2（不许当全绿）", rc_bad == 2, f"退码={rc_bad}")
    chk("S11 输出面：红行点名「文件:行」", re.search(r"\[死\] GUIDE\.md:4", buf.getvalue()) is not None,
        buf.getvalue()[:160])

    shutil.rmtree(tmp, ignore_errors=True)
    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[check_dead_paths selftest v{VERSION}] 临时树={tmp}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="活路径活性检查（打不开的路径一律不留字符串）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--root", required=True)
    c.add_argument("--json", default="")
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest")
    s.add_argument("--tmp-root", default=None)
    s.set_defaults(func=cmd_selftest)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
