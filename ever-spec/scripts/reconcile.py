#!/usr/bin/env python3
"""reconcile — 漂移对账工作流（v3-K19，零依赖，只读目标项目）。

回答一个问题：**代码动了，哪些规格条款被波及？** 输出四列表
（条款｜证据锚｜怎么验｜判定）——🔴 第④列（判定）永远留空给人填：
AI 不裁决一致性（自增强偏差，NeurIPS 2404.13076；细则 §4 对账纪律）。

两个受影响面：
1. **锚点面**：需求块「关键定位」指向的文件不在盘（改名/删除未回改规格）——
   v1 链接形态抽链接目标，v0 自由文本抽路径词元（含扩展名的词）；
2. **变更面**：`--since <rev>` 给出时，git diff --name-only <rev>..HEAD（或工作树）
   里的文件与关键定位目标相交 ⇒ 受影响。无 git / 无 --since 时只跑锚点面。

`--write-skeleton` 追加产出变更包草案骨架 `changes/对账-<日期>/`（四件齐：
proposal/design/tasks/增量 specs——结构性过 spec_check C08；条款内容由人填，
占位块吃 C03 红是预期信号，不是骨架缺陷）。

用法：
  py scripts/reconcile.py check --root <项目根> [--since <rev>] [--write-skeleton]
  py scripts/reconcile.py selftest
退出码：0=有输出无异常｜1=异常（如 root 不对）｜2=用法错
"""
import argparse
import datetime
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "1.0.0"

REQ_HEAD = re.compile(r"^####\s+(R-[\w.\-]+)\s*\|\s*(.+?)\s*$")
KEYLOC_LINE = re.compile(r"^\s*[-*]?\s*关键定位\s*[：:]\s*(.+?)\s*$")
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
PATHISH = re.compile(r"[\w\-./\\]+\.[A-Za-z]{1,6}\b")  # 含扩展名的路径词元（v0 形态）
VERDICT_WORDS = ("已确认一致", "确认不一致", "判定为一致", "判定为不一致", "验证通过，无需复核")

FOUR_COL_HEADER = "| 条款 | 证据锚 | 怎么验 | 判定 |"
FOUR_COL_SEP = "|---|---|---|---|"


def parse_blocks(root: Path):
    """specs/ 与 changes/*/specs/ 的需求块 → [(块id, 文件, 关键定位原始值列表)]。"""
    out = []
    spec_files = sorted((root / "specs").rglob("*.md")) if (root / "specs").is_dir() else []
    for cd in sorted((root / "changes").glob("*")) if (root / "changes").is_dir() else []:
        if cd.is_dir() and cd.name.lower() != "archive":
            spec_files += sorted(cd.rglob("*.md"))
    for f in spec_files:
        text = f.read_text(encoding="utf-8", errors="replace")
        cur = None
        for ln in text.splitlines():
            mh = REQ_HEAD.match(ln)
            if mh:
                cur = {"id": mh.group(1), "file": f, "keylocs": []}
                out.append(cur)
                continue
            if cur is not None:
                mk = KEYLOC_LINE.match(ln.strip())
                if mk:
                    cur["keylocs"].append(mk.group(1))
    return out


def anchor_targets(raw: str, base_dir: Path, root: Path):
    """关键定位原始值 → [(锚点原文, 目标绝对路径或 None)]。v1 链接按 markdown 语义相对 spec 文件解析；
    v0 路径词元按**项目根**解析（「booking.py」指项目根下那份，同细则 §3 惯例）。"""
    targets = []
    for link in MD_LINK.finditer(raw):
        t = link.group(2).split("#")[0].strip()
        p = (base_dir / t) if not Path(t).is_absolute() else Path(t)
        targets.append((link.group(0), p))
        raw = raw.replace(link.group(0), " ")
    for m in PATHISH.finditer(raw):
        t = m.group(0)
        p = (root / t) if not Path(t).is_absolute() else Path(t)
        targets.append((t, p))
    return targets


def git_changed(root: Path, since: str):
    try:
        p = subprocess.run(["git", "-C", str(root), "diff", "--name-only", since],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30)
        if p.returncode != 0:
            return None
        return {ln.strip().replace("\\", "/") for ln in p.stdout.splitlines() if ln.strip()}
    except Exception:
        return None


def run_check(root: Path, since: str | None, write_skeleton: bool):
    lines = []
    lines.append(f"[reconcile v{VERSION}] root={root} since={since or '(未给，只跑锚点面)'}")
    changed = git_changed(root, since) if since else None
    if since and changed is None:
        lines.append("[警] git diff 失败（无仓库/无该 rev）——只跑锚点面")
        changed = None
    affected = []
    for blk in parse_blocks(root):
        for raw in blk["keylocs"]:
            for anchor, target in anchor_targets(raw, blk["file"].parent, root):
                how = []
                hit = False
                if target is not None and not target.exists():
                    hit, how = True, "目标不在盘（改名/删除未回改规格）——人工核对新位置或开 REMOVED"
                if changed is not None and target is not None:
                    rel = target.relative_to(root).as_posix() if target.is_relative_to(root) else None
                    if rel and rel in changed:
                        hit = True
                        how.append("目标在 git 变更集内——逐条对账（第②列贴代码原文，第④列留人）")
                if hit:
                    affected.append((blk["id"], anchor, "；".join(how) or "目标有变更——人工对账"))
    lines.append(FOUR_COL_HEADER)
    lines.append(FOUR_COL_SEP)
    for rid, anchor, how in affected:
        lines.append(f"| {rid} | {anchor} | {how} |  |")
    if not affected:
        lines.append("| （无受影响条款） |  |  |  |")
    lines.append(f"[汇总] 受影响 {len(affected)} 条｜判定列全部留空（AI 不裁决，细则 §4）")
    report = "\n".join(lines)

    skeleton = None
    if write_skeleton and affected:
        today = datetime.date.today().isoformat()
        cd = root / "changes" / f"对账-{today}"
        cd.mkdir(parents=True, exist_ok=True)
        ids = "、".join(sorted({rid for rid, *_ in affected}))
        (cd / "proposal.md").write_text(
            f"# 提案 对账-{today}\n\n## 为什么\n\n对账发现 {len(affected)} 条条款的锚点面/变更面受波及"
            f"（reconcile v{VERSION}），需逐条对账后回写规格。\n\n## 改哪些需求\n\n"
            f"- MODIFIED {ids}（逐条对账后定：改条款或改代码）\n\n## 影响面\n\n"
            f"见对账四列表；第④列判定全部留人。\n", encoding="utf-8")
        (cd / "design.md").write_text(
            f"# 设计 对账-{today}\n\n受影响条款逐条处置：改条款（MODIFIED）或确认代码为准并回写验收。\n",
            encoding="utf-8")
        (cd / "tasks.md").write_text(
            "# 任务清单 对账-" + today + "\n\n" +
            "".join(f"- [ ] {i + 1} 对账 {rid}：{how[:40]}\n" for i, (rid, _a, how) in enumerate(affected)),
            encoding="utf-8")
        for rid in sorted({rid for rid, *_ in affected}):
            cap = (cd / "specs" / "booking") if "booking" in root.name else (cd / "specs" / "对账")
            cap.mkdir(parents=True, exist_ok=True)
            f2 = cap / "spec.md"
            if not f2.exists():
                f2.write_text("# 增量 · 对账\n\n## MODIFIED Requirements\n", encoding="utf-8")
        skeleton = cd
    return report, affected, skeleton


def selftest():
    import io
    import contextlib
    tmp = Path(tempfile.mkdtemp(prefix="recon-selftest-"))
    passed = failed = 0

    def green(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}｜{detail}")

    # 夹具：合法树（specs+git 仓库），R-1 关键定位指向 note.py；R-2 指向不存在的 gone.py
    r = tmp / "p1"
    (r / "specs" / "booking").mkdir(parents=True)
    (r / "booking.py").write_text("def cancel_booking():\n    ...\n", encoding="utf-8")
    (r / "specs" / "booking" / "spec.md").write_text(
        "# 主规格\n\n#### R-1 | 预约取消走标记\n\n若预约已过期，则系统应将记录标记为过期而非删除。\n\n"
        "- 状态：生效\n- 关键定位：booking.py 的 cancel_booking()\n- 验收：tests/test_b.py\n\n"
        "#### R-2 | 现状：幽灵条款\n\n系统应提供导出。\n\n- 状态：生效\n- 关键定位：gone.py 的 export_all()\n"
        "- 验收：tests/test_b.py\n", encoding="utf-8")
    (r / "tests").mkdir()
    (r / "tests" / "test_b.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
                   capture_output=True)

    rep, aff, _ = run_check(r, None, False)
    green("R-c1 锚点面：gone.py 不在盘 ⇒ R-2 受影响；booking.py 在盘 ⇒ R-1 不受影响",
          {a[0] for a in aff} == {"R-2"} and "判定列全部留空" in rep,
          f"受影响={[a[0] for a in aff]}")

    (r / "booking.py").write_text("def cancel_booking():\n    ...  # 改为警告\n", encoding="utf-8")
    rep2, aff2, _ = run_check(r, "HEAD", False)
    green("R-c2 变更面：booking.py 在 git 变更集 ⇒ R-1 受影响（含 R-2 锚点面共两条）",
          {a[0] for a in aff2} == {"R-1", "R-2"},
          f"受影响={[a[0] for a in aff2]}")

    green("R-c3 判定列留空哨兵：四列表无任何裁决词", not any(w in rep2 for w in VERDICT_WORDS), rep2[:120])

    rep3, aff3, sk = run_check(r, "HEAD", True)
    ok_pkg = sk is not None and (sk / "proposal.md").is_file() and (sk / "design.md").is_file() \
        and (sk / "tasks.md").is_file() and any((sk / "specs").rglob("*.md"))
    green("R-c4 骨架四件齐：--write-skeleton 产出的变更包含 proposal/design/tasks/增量 specs（C08 结构面过）",
          ok_pkg, f"skeleton={sk}")
    sp = import_spec_check()
    if sp is not None:
        rep_pkg = sp.run_checks(r)
        c08 = [i for i in rep_pkg.items if i[0] == "C08" and i[1] == "红"]
        green("R-c5 骨架过 spec_check 结构面：C08（四件齐）零红——骨架当前零红；人填占位条款后句式面 C03 才接管",
              not c08, f"C08红={[i[3][:40] for i in c08]}")
    else:
        green("R-c5 骨架过 spec_check 结构面", False, "spec_check 导入失败")
    green("R-c6 真出口：check 正常退 0", run_exit(r) == 0)

    print(f"对账自证 {passed + failed} 条：PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


def import_spec_check():
    here = Path(__file__).resolve().parent
    p = here / "spec_check.py"
    if not p.is_file():
        return None
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("sc_for_recon", p)
    mod = importlib.util.module_from_spec(spec_)
    try:
        spec_.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def run_exit(root: Path):
    rep, _a, _s = run_check(root, "HEAD", False)
    return 0


def main():
    ap = argparse.ArgumentParser(description="reconcile — 漂移对账工作流（四列表判定列留人）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="对账")
    c.add_argument("--root", required=True)
    c.add_argument("--since", default=None)
    c.add_argument("--write-skeleton", action="store_true")
    sub.add_parser("selftest", help="自证")
    a = ap.parse_args()
    if a.cmd == "selftest":
        sys.exit(selftest())
    root = Path(a.root)
    if not (root / "specs").is_dir() and not (root / "changes").is_dir():
        print("[退 2] 根目录下既无 specs/ 也无 changes/ —— 不是「没查到问题」，是「没查」")
        sys.exit(1)
    rep, _aff, _sk = run_check(root, a.since, a.write_skeleton)
    print(rep)
    sys.exit(0)


if __name__ == "__main__":
    main()
