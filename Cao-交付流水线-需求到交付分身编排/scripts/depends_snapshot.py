#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""depends_snapshot.py — 依赖快照三件事：文件哈希 / 上游↔本地路径映射 / 依赖链接检查。

为什么要有这支脚本（外部审查点名的两条）：
  1) depends/ 里的副本是**快照**，不跟随上游。没有哈希就没人能回答
     「这份副本还是不是抓取那天那份」——手写的版本标注会腐烂，sha256 不会。
  2) 副本从上游搬到本库后，正文里的相对链接全部失效（上游的 code-reviewer.md
     在本库是 superpowers_assets/requesting-code-review/code-reviewer.md）。
     断链要能一条命令扫出来，而不是等人踩到。

铁律：本清单是**派生物**，由 make 生成；verify 只读对比；手改会在下一次
     make 时被覆盖，且 check 会打红（与 pipeline_state.py 的 AUTO-BEGIN 同口径）。

用法：
  py scripts/depends_snapshot.py make    [--apply]          # 生成/更新 快照清单.md
  py scripts/depends_snapshot.py verify                     # 哈希对账（漂移退 1）
  py scripts/depends_snapshot.py links   [--path a.md,b.md] # 全库断链扫描（有断链退 1）
  py scripts/depends_snapshot.py check   [--apply]          # make + verify + links 一条龙
  py scripts/depends_snapshot.py selftest                   # 合成夹具自证，不碰真实库

退出码：0 通过 / 1 检出问题 / 2 用法或参数错 / 3 清单缺失 / 4 清单非法或内容漂移 /
        8 同名文件无 AUTO 标记（人手写的，拒覆盖）
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime

TS_FMT = "%Y-%m-%d %H:%M:%S"
AUTO_MARK = "AUTO-BEGIN"
AUTO_END = "AUTO-END"
MANIFEST_REL = "references/depends/快照清单.md"
DEPDIR_REL = "references/depends"
SOURCE_RE = re.compile(r"^\s*>+\s*【自包含副本】来源[：:]\s*(.+?)\s*$")
EXTERNAL_DECL_RE = re.compile(r"【外指声明】(.+)")
SPLIT_RE = re.compile(r"[、，,；;\s]+")
SNAPVER_RE = re.compile(r"(?:快照版本|版本锚)[：:]\s*([^\s（(]+(?:\s*[^\s（(]+)?)")
COMMIT_RE = re.compile(r"main\s*@\s*([0-9a-f]{7,40})|@\s*([0-9a-f]{7,40})")
VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+|\d+\.\d+)")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# 正文里以反引号/裸文本提到的库内路径（与发布仓 library_check.py 的口径对齐）
PATH_RE = re.compile(r"(?:^|[\s`（(])(scripts|references|assets|tools)/([^\s`）)，。；、|\"'<>*]+?\.(?:py|sh|md|json|txt|ts))")
FENCE_MARK = "```"
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "ftp://", "#")
SCHEMA = "depends-snapshot/1"


def setup_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def die(code, msg):
    print(msg)
    raise SystemExit(code)


def now_str():
    return datetime.now().strftime(TS_FMT)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    try:
        return sha256_bytes(read_bytes(path))
    except OSError:
        return ""


def rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def list_depends(root):
    """references/depends/ 下全部快照件（清单自身除外——它不是快照，是派生物）。"""
    dep = os.path.join(root, DEPDIR_REL.replace("/", os.sep))
    if not os.path.isdir(dep):
        die(2, "[缺目录] 找不到 %s ⇒ 本库没有 depends 副本，无需快照" % DEPDIR_REL)
    out = []
    for dirpath, dirnames, filenames in os.walk(dep):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            r = rel(root, full)
            if r == MANIFEST_REL:
                continue
            out.append(r)
    return out


def extract_source(root, r):
    """来源与版本锚从**副本自己头部**提取，不再抄第二份表（抄了就必打架）。"""
    full = os.path.join(root, r.replace("/", os.sep))
    source = ""
    ver = ""
    try:
        text = read_bytes(full).decode("utf-8", "replace")
    except OSError:
        return "", ""
    head = text.splitlines()[:12]
    for line in head:
        m = SOURCE_RE.match(line)
        if m and not source:
            source = m.group(1).strip()
        cm = COMMIT_RE.search(line)
        if cm:
            ver = "main @ " + (cm.group(1) or cm.group(2))
    if not source:
        # assets 按原样存放、刻意不加头（提示词模板要能整篇复制），来源去同目录 README 取
        for cand in (os.path.join(os.path.dirname(full), "README.md"),
                     os.path.join(root, DEPDIR_REL.replace("/", os.sep), "superpowers_assets", "README.md"),
                     os.path.join(root, DEPDIR_REL.replace("/", os.sep), "README.md")):
            if os.path.isfile(cand):
                t = read_bytes(cand).decode("utf-8", "replace").splitlines()[:12]
                for line in t:
                    if "github.com/obra/superpowers" in line:
                        source = "github.com/obra/superpowers（MIT）· skills/ 附属文件"
                    cm = COMMIT_RE.search(line)
                    if cm:
                        ver = "main @ " + (cm.group(1) or cm.group(2))
                break
    if not ver:
        vm = VERSION_RE.search(source or "")
        if vm:
            ver = "v" + vm.group(1)
    return source, ver


def upstream_path(root, r):
    """上游路径 → 本地副本路径的映射（审查点名的「路径映射」）。"""
    base = os.path.basename(r)
    if r.startswith(DEPDIR_REL + "/superpowers_assets/"):
        if base == "README.md":
            # 收录清单是本库自建的登记件，上游仓库里没有对应文件
            return ""
        tail = r.split("superpowers_assets/", 1)[1]
        return "skills/" + tail
    if r.startswith(DEPDIR_REL + "/superpowers_") and base.endswith(".md"):
        return "skills/%s/SKILL.md" % base[len("superpowers_"):-len(".md")]
    if base.startswith("grill-me"):
        return "github.com/mattpocock/skills · grill-me/SKILL.md"
    return ""


def build_rows(root):
    rows = []
    for r in list_depends(root):
        src, ver = extract_source(root, r)
        full = os.path.join(root, r.replace("/", os.sep))
        rows.append({"path": r, "source": src, "upstream": upstream_path(root, r),
                     "version": ver, "sha256": sha256_file(full),
                     "bytes": os.path.getsize(full),
                     # 副本自己声明的外指件：清单摘录来源列时会把它们带进来，
                     # 所以清单要继承同一份声明，否则「摘录来的引用」被当成清单新造的断链
                     "declared": sorted(declared_externals(full))})
    return rows


def render_manifest(rows, generated_at):
    lines = [
        "# 依赖快照清单（生成件 · 禁手改）",
        "",
        "> 本文件由 `%s` 的 `make` 生成，是 %s 的**派生物**——手改会在下一次 make 被覆盖，"
        % ("scripts/depends_snapshot.py", MANIFEST_REL),
        "> 且 `verify` 会把「清单 ≠ 实际文件」当场打红。要改内容请改生成逻辑或副本本身。",
        ">",
        "> 覆盖三件事：**文件哈希**（sha256，判「副本还是不是抓取那天那份」）、"
        "**路径映射**（上游位置 ↔ 本地副本位置，判「搬迁后原文去哪了」）、",
        "> **来源锚**（每个副本自己头部的来源/commit 提取，不在别处再抄一份）。",
    ]
    # 继承声明：只列各副本自己【外指声明】过的件，清单不新增豁免口径
    inherited = sorted({d for x in rows for d in x.get("declared", [])})
    if inherited:
        lines += [">",
                  "> 【外指声明】%s ——" % "、".join("`%s`" % d for d in inherited),
                  "> 上面这些是**各副本自己声明**的上游件（本库刻意未收）。本清单的「来源」列"
                  "摘录副本头部原文时把它们带了进来，所以这里继承同一份声明，"
                  "不是清单自己新加的引用；条数在 `links` 报告里明写（不是静默豁免）。"]
    lines += [
        "",
        "<!-- " + AUTO_MARK + " · 以下整段由 depends_snapshot.py make 重写 -->",
        "",
        "- 生成时间：%s ｜ 件数：%d ｜ 口径：%s" % (generated_at, len(rows), SCHEMA),
        "",
        "| 本地副本路径 | 上游位置（路径映射） | 来源 | 版本锚 | sha256 | 字节 |",
        "|---|---|---|---|---|---|",
    ]
    for x in rows:
        lines.append("| `%s` | %s | %s | %s | `%s` | %d |" % (
            x["path"], ("`%s`" % x["upstream"]) if x["upstream"] else "（本机 skill 库，无仓库路径）",
            (x["source"] or "—").replace("|", "\\|"), x["version"] or "—",
            x["sha256"][:16], x["bytes"]))
    lines += ["", "<!-- " + AUTO_END + " -->", ""]
    return "\n".join(lines)


def parse_manifest(text):
    """读清单里 AUTO 段登记的行（verify 用）。"""
    rows = {}
    in_auto = False
    for line in text.splitlines():
        if AUTO_MARK in line:
            in_auto = True
            continue
        if AUTO_END in line:
            in_auto = False
            continue
        if not in_auto:
            continue
        if line.startswith("| `"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 6 or cells[0].startswith("本地副本"):
                continue
            path = cells[0].strip("`")
            rows[path] = {"path": path, "upstream": cells[1].strip("`"),
                          "sha256": cells[4].strip("`"), "bytes": cells[5]}
    return rows


def cmd_make(root, apply):
    rows = build_rows(root)
    target = os.path.join(root, MANIFEST_REL.replace("/", os.sep))
    text = render_manifest(rows, now_str())
    if os.path.lexists(target):
        cur = read_bytes(target).decode("utf-8", "replace")
        if AUTO_MARK not in cur:
            die(8, "[拒写] %s 里没有 %s 标记＝人手写的同名件 ⇒ 不覆盖。"
                          "确认它只是旧清单，请人工改名或删除后再跑 make"
                % (rel(root, target), AUTO_MARK))
    if not apply:
        print("[干跑] 将生成 %s（%d 件）⇒ 加 --apply 真写" % (MANIFEST_REL, len(rows)))
        return 0
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp-%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    back = read_bytes(target).decode("utf-8", "replace")
    if AUTO_MARK not in back:
        die(4, "[写后读回不符] %s 写坏 ⇒ 本次不算成功" % target)
    print("[已生成] %s｜件数=%d" % (MANIFEST_REL, len(rows)))
    return 0


def cmd_verify(root):
    target = os.path.join(root, MANIFEST_REL.replace("/", os.sep))
    if not os.path.isfile(target):
        die(3, "[缺清单] 没有 %s ⇒ 先跑 make --apply" % MANIFEST_REL)
    text = read_bytes(target).decode("utf-8", "replace")
    if AUTO_MARK not in text:
        die(4, "[清单非法] %s 无 %s 标记 ⇒ 不是生成件，拒绝据此对账"
               % (MANIFEST_REL, AUTO_MARK))
    registered = parse_manifest(text)
    actual = {x["path"]: x for x in build_rows(root)}
    problems = []
    for p in sorted(set(registered) - set(actual)):
        problems.append("清单登记了但盘上没有：%s ⇒ 副本被删/改名，快照链断" % p)
    for p in sorted(set(actual) - set(registered)):
        problems.append("盘上有但清单没登记：%s ⇒ 新副本没进快照（make --apply）" % p)
    for p in sorted(set(registered) & set(actual)):
        reg, act = registered[p], actual[p]
        if not reg["sha256"] or not act["sha256"].startswith(reg["sha256"]):
            problems.append("哈希不符：%s 清单=%s 现值=%s ⇒ 副本内容与抓取时不一致"
                            % (p, reg["sha256"], act["sha256"][:16]))
        # 清单对「无仓库路径」写的是说明句，比对前先归零，否则每条本机副本都误报漂移
        reg_up = "" if reg["upstream"].startswith("（") else reg["upstream"]
        if reg_up != act["upstream"]:
            problems.append("路径映射不符：%s 清单=%s 现值=%s" % (p, reg_up or "（无）", act["upstream"] or "（无）"))
    if problems:
        print("[依赖快照对账不通过] %d 条：" % len(problems))
        for x in problems:
            print("  ✗ %s" % x)
        return 1
    print("[依赖快照对账通过] %d 件哈希全部与清单一致｜路径映射齐 %d 条"
          % (len(actual), sum(1 for x in actual.values() if x["upstream"])))
    return 0


def md_files(root, only=None):
    if only:
        out = []
        for spec in only:
            for one in spec.split(","):
                one = one.strip().strip("`")
                if not one:
                    continue
                full = os.path.join(root, one.replace("/", os.sep))
                if not os.path.isfile(full):
                    die(2, "[待检文件不存在] %s" % one)
                out.append((one, full))
        return out
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__", "node_modules"))
        for fn in sorted(filenames):
            if fn.endswith(".md") and not fn.endswith(".local.md"):
                full = os.path.join(dirpath, fn)
                out.append((rel(root, full), full))
    return out


def declared_externals(full):
    """副本头部 `【外指声明】` 行列出的路径＝指向上游、本库刻意未收录的件。

    没有这个口子只有两种结局：把上游原件也搬进来（扩大收录范围），
    或者把断链判据放松（真断链也跟着漏）。声明是显式的、可反查的。
    """
    try:
        head = read_bytes(full).decode("utf-8", "replace").splitlines()[:15]
    except OSError:
        return set()
    out = set()
    for line in head:
        m = EXTERNAL_DECL_RE.search(line)
        if not m:
            continue
        for tok in SPLIT_RE.split(m.group(1).strip("` ").replace("`", " ")):
            tok = tok.strip("`（）()。:：")
            if tok and ("/" in tok or tok.endswith((".md", ".py", ".sh", ".json"))):
                out.add(tok.lstrip("./"))
    return out


def _norm_mention(s):
    return s.replace("\\", "/").lstrip("./").strip("`")


def scan_file(root, relpath, full):
    """返回 (断链/路径缺失清单, 绝对路径泄漏清单, 命中的外指声明清单)。围栏代码块内一律不检。"""
    bad, absolute, ext = [], [], []
    declared = declared_externals(full)
    seen = set()
    in_fence = False
    base = os.path.dirname(os.path.join(root, full.replace("/", os.sep))) if "/" in relpath else root
    try:
        lines = read_bytes(full).decode("utf-8", "replace").splitlines()
    except OSError:
        return ["(读不到文件)"], [], []
    for lineno, line in enumerate(lines, 1):
        if line.count(FENCE_MARK) % 2 == 1:
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        mentions = [(t, "链接") for t in LINK_RE.findall(line)]
        mentions += [(m.group(1) + "/" + m.group(2), "库内路径") for m in PATH_RE.finditer(line)]
        for raw, kind in mentions:
            if raw.startswith(EXTERNAL_SCHEMES):
                continue
            clean = raw.split("#")[0]
            if not clean:
                continue
            if re.match(r"^[A-Za-z]:[\\/]", clean) or clean.startswith("/"):
                absolute.append("%s:%s %s写成绝对路径 %s" % (relpath, lineno, kind, clean))
                continue
            if _norm_mention(clean) in declared:
                ext.append("%s:%s %s（外指声明已登记，未计入断链）" % (relpath, lineno, clean))
                continue
            # 链接按所在文件解析；正文里写的库内路径按库根解析（本库惯例）
            anchor = base if kind == "链接" else root
            resolved = os.path.normpath(os.path.join(anchor, clean.replace("/", os.sep)))
            key = (lineno, resolved)
            if key in seen:
                continue          # 同一行链接与路径两种写法指同一件时只报一次
            seen.add(key)
            if not os.path.exists(resolved):
                bad.append("%s:%s 断链/缺失 (%s)＝%s ⇒ 本库解析为 %s"
                           % (relpath, lineno, clean, kind, rel(root, resolved)))
    return bad, absolute, ext


def cmd_links(root, only):
    total_bad, total_abs, total_ext = [], [], []
    n = 0
    for relpath, full in md_files(root, only):
        n += 1
        bad, absolute, ext = scan_file(root, relpath, full)
        total_bad += bad
        total_abs += absolute
        total_ext += ext
    if total_bad or total_abs:
        print("[链接检查不通过] 扫了 %d 个 .md ｜ 断链或缺失 %d 条 ｜ 绝对路径泄漏 %d 条 ｜ 外指声明命中 %d 条："
              % (n, len(total_bad), len(total_abs), len(total_ext)))
        for x in total_bad:
            print("  ✗ %s" % x)
        for x in total_abs:
            print("  🔴 %s" % x)
        return 1
    print("[链接检查通过] %d 个 .md 内所有相对链接与库内路径都可达｜外指声明命中 %d 条（上游件，本库刻意未收）"
          % (n, len(total_ext)))
    return 0


def cmd_check(root, apply):
    rc_make = cmd_make(root, apply)
    if rc_make:
        return rc_make
    rc_verify = cmd_verify(root)
    rc_links = cmd_links(root, None)
    if rc_verify or rc_links:
        return 1
    print("[check 全绿] 快照哈希一致｜链接全可达")
    return 0


# ---------------------------------------------------------------- selftest
def chk(results, name, cond, extra=""):
    results.append((name, bool(cond), extra))


def _capture(fn, *a):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(*a)
    finally:
        sys.stdout = old
    return buf.getvalue()


def _exit_of(fn, *a):
    try:
        _capture(fn, *a)
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def _w(path, text):
    """夹具写盘：显式 newline=\n，否则 Windows 会写成 \\r\\n，
    「内容一字节未变」这类断言会因换行翻译假翻红。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _manifest_text(root):
    return read_bytes(os.path.join(root, MANIFEST_REL.replace("/", os.sep))).decode("utf-8")


def _outside_table(root):
    """把一行同样式的表格塞到 AUTO 段之外，验证解析器不吃段外内容。"""
    return _manifest_text(root) + "\n| `段外假件.md` | skills/x | 来源 | v1 | `deadbeef` | 1 |\n"


def build_fixture(tmp):
    """造一棵最小 skill 树：2 份副本 + 1 个 assets 附属件 + 1 条故意断的链。"""
    dep = os.path.join(tmp, "references", "depends")
    os.makedirs(os.path.join(dep, "superpowers_assets", "requesting-code-review"))
    os.makedirs(os.path.join(tmp, "scripts"))
    with io.open(os.path.join(dep, "superpowers_demo.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("> 【自包含副本】来源：obra/superpowers · skills/demo/SKILL.md（MIT，github.com/obra/superpowers）\n"
                "> 快照版本：main @ abc123456789（2026-09-22 抓取）\n\n"
                "正文引用 [评审提示词](superpowers_assets/requesting-code-review/code-reviewer.md)。\n")
    with io.open(os.path.join(dep, "本地副本.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("> 【自包含副本】来源：头脑风暴-先想清再写码 v1.2.3（用户本机 skill 库）\n\n无链接。\n")
    with io.open(os.path.join(dep, "superpowers_assets", "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# superpowers 附属文件快照\n\n"
                "> 来源：github.com/obra/superpowers（MIT）· 快照版本：main @ abc123456789\n")
    with io.open(os.path.join(dep, "superpowers_assets", "requesting-code-review", "code-reviewer.md"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write("# code reviewer prompt\n")
    with io.open(os.path.join(tmp, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# demo skill\n\n细则见 [阶段⑤](references/阶段⑤.md)，脚本 `scripts/exists.py`。\n"
                "另有一件只在正文提到：`references/只在正文提到.md`。\n")
    with io.open(os.path.join(tmp, "scripts", "exists.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("print('ok')\n")
    return tmp


def cmd_selftest(_args=None):
    results = []
    tmp = tempfile.mkdtemp(prefix="depends-snapshot-")
    try:
        root = build_fixture(tmp)
        rows = build_rows(root)
        paths = sorted(x["path"] for x in rows)
        chk(results, "D01 快照收录 4 件（清单自身除外，assets README 也算）", len(rows) == 4, str(paths))
        chk(results, "D02 副本来源从自己头部提取",
            any(x["path"].endswith("本地副本.md") and "头脑风暴" in x["source"] and x["version"] == "v1.2.3"
                for x in rows), str([x["source"] for x in rows]))
        chk(results, "D03 commit 锚从快照版本行提取",
            any(x["version"] == "main @ abc123456789" for x in rows))
        chk(results, "D04 路径映射：superpowers_ 副本 → skills/<name>/SKILL.md",
            any(x["path"].endswith("superpowers_demo.md") and x["upstream"] == "skills/demo/SKILL.md"
                for x in rows), str([x["upstream"] for x in rows]))
        chk(results, "D05 路径映射：assets 保持上游同构路径",
            any(x["upstream"] == "skills/requesting-code-review/code-reviewer.md" for x in rows))
        chk(results, "D06 assets 无头也能从同目录 README 取到来源",
            any("obra/superpowers" in x["source"]
                for x in rows if x["path"].endswith("code-reviewer.md")))
        chk(results, "D07 哈希是 64 位十六进制",
            all(re.match(r"^[0-9a-f]{64}$", x["sha256"]) for x in rows))
        chk(results, "D08 干跑不写盘",
            cmd_make(root, False) == 0 and not os.path.lexists(os.path.join(root, MANIFEST_REL.replace("/", os.sep))))
        chk(results, "D09 make --apply 生成清单", cmd_make(root, True) == 0
            and os.path.isfile(os.path.join(root, MANIFEST_REL.replace("/", os.sep))))
        text = read_bytes(os.path.join(root, MANIFEST_REL.replace("/", os.sep))).decode("utf-8")
        chk(results, "D10 清单带 AUTO-BEGIN 标记（可被 verify 认）", AUTO_MARK in text)
        chk(results, "D11 刚生成的清单 verify 必绿（不动点）", cmd_verify(root) == 0)
        p = os.path.join(root, "references", "depends", "本地副本.md")
        orig = read_bytes(p).decode("utf-8")
        _w(p, orig + "\n偷偷加一行\n")
        out_v = _capture(cmd_verify, root)
        chk(results, "D12 副本被改一个字节 ⇒ verify 翻红（哈希判据能红，不是恒绿）",
            out_v.count("哈希不符") == 1 and cmd_verify(root) == 1)
        chk(results, "D13 红只落在被改的那件，其余 3 件不误伤",
            "哈希不符：references/depends/本地副本.md" in out_v, out_v.strip().splitlines()[:3])
        _w(p, orig)
        chk(results, "D14 还原原文后复绿（判据不是恒红）", cmd_verify(root) == 0)
        os.remove(p)
        out_v2 = _capture(cmd_verify, root)
        chk(results, "D15 副本消失 ⇒ verify 报「清单登记了但盘上没有」",
            "清单登记了但盘上没有" in out_v2 and cmd_verify(root) == 1)
        _w(p, orig)
        _w(os.path.join(root, "references", "depends", "新副本.md"), "> 【自包含副本】来源：某 v9.9.9\n")
        out_v3 = _capture(cmd_verify, root)
        chk(results, "D16 新增副本未登记 ⇒ verify 报漏登并给出补齐命令",
            "没进快照" in out_v3 and cmd_verify(root) == 1)
        chk(results, "D17 重新 make 后复绿（清单与实际件重新对齐）",
            cmd_make(root, True) == 0 and cmd_verify(root) == 0)
        # 人手写的同名清单不许覆盖
        hand = os.path.join(root, "references", "depends", "旧版清单手写的.md")
        _w(hand, "# 手写件\n没有生成标记\n")
        chk(results, "D18 无 AUTO 标记的同名件 ⇒ make 拒写（退 8）", _exit_of(_make_at, root, hand) == 8)
        chk(results, "D19 拒写时手写件一字节未变",
            read_bytes(hand).decode("utf-8") == "# 手写件\n没有生成标记\n")
        os.remove(hand)
        # 链接检查
        out = _capture(cmd_links, root, None)
        chk(results, "D20 夹具里的真断链 ⇒ links 退 1", cmd_links(root, None) == 1)
        chk(results, "D21 断链定位精确到 文件:行号:目标",
            "SKILL.md:3 断链/缺失 (references/阶段⑤.md)" in out, out.strip().splitlines()[:5])
        chk(results, "D22 可达的相对链接不误报", "superpowers_demo.md" not in out)
        chk(results, "D23 正文提到的库内路径不存在也报（不只查 markdown 链接）",
            "references/只在正文提到.md" in out and "库内路径" in out, out.strip().splitlines()[:5])
        chk(results, "D23b 同一行两种写法（链接＋库内路径）指同一件时只报一条",
            len([l for l in out.splitlines() if "SKILL.md:3" in l]) == 1,
            [l for l in out.splitlines() if "SKILL.md:3" in l])
        chk(results, "D24 清单自己引用的脚本若不在盘上也报（宁严勿松）",
            "scripts/depends_snapshot.py" in out)
        # 外指声明：声明过的上游件不计断链，没声明的照样报
        decl = os.path.join(root, "references", "depends", "外指样本.md")
        _w(decl, "> 【自包含副本】来源：某上游 skill v1\n"
                 "> 【外指声明】`references/上游模板.md`、`scripts/上游脚本.py`\n\n"
                 "见 [上游模板](references/上游模板.md)；体检跑 `scripts/上游脚本.py`；"
                 "还有没声明的 [漏网](references/没声明的.md)。\n")
        out_d = _capture(cmd_links, root, None)
        chk(results, "D25 外指声明登记过的件不再算断链",
            "断链/缺失 (references/上游模板.md)" not in out_d
            and "断链/缺失 (scripts/上游脚本.py)" not in out_d, out_d.strip().splitlines()[:6])
        chk(results, "D26 没声明的件照样报红（声明口子不变成万能豁免）",
            "断链/缺失 (references/没声明的.md)" in out_d)
        # 🔴 判「条数是正数」而不是「有这句话」：只查字面在不在，把命中数抹成 0 也照样绿（半恒真判据）
        chk(results, "D27 报告里明写外指命中数（豁免是可见的，不是静默的）",
            re.search(r"外指声明命中 [1-9]\d* 条", out_d) is not None,
            out_d.strip().splitlines()[:3])
        os.remove(decl)
        # 围栏内的示例链接不得当真
        fence = os.path.join(root, "references", "depends", "围栏样本.md")
        _w(fence, "> 【自包含副本】来源：围栏 v1\n\n```md\n[假链接](不存在的.md)\n```\n\n真链接 [ok](superpowers_demo.md)\n")
        out2 = _capture(cmd_links, root, None)
        chk(results, "D28 代码围栏内的链接跳过（不误报模板示例）",
            "不存在的.md" not in out2, out2.strip().splitlines()[:4])
        os.remove(fence)
        # 绝对路径泄漏单独报警
        leak = os.path.join(root, "references", "depends", "泄漏样本.md")
        _w(leak, "> 【自包含副本】来源：泄漏 v1\n\n详见 [原件](E:/somewhere/else/x.md)。\n")
        out3 = _capture(cmd_links, root, None)
        chk(results, "D29 绝对路径链接判「泄漏」而不是普通断链",
            "绝对路径" in out3 and "泄漏样本.md" in out3, out3.strip().splitlines()[:4])
        os.remove(leak)
        # .local.md 不入检（个人适配层刻意不进仓）
        loc = os.path.join(root, "personal.local.md")
        _w(loc, "[坏链](根本没有这文件.md)\n")
        out4 = _capture(cmd_links, root, None)
        chk(results, "D30 *.local.md 不参与链接检查", "personal.local.md" not in out4)
        os.remove(loc)
        # 缺清单 / 非法清单 / 无 depends 目录
        empty = os.path.join(tmp, "空库")
        os.makedirs(os.path.join(empty, "references", "depends"))
        _w(os.path.join(empty, "references", "depends", "a.md"), "x\n")
        chk(results, "D31 无 depends 目录时报用法错（退 2）",
            _exit_of(list_depends, os.path.join(tmp, "根本没有库")) == 2)
        chk(results, "D32 缺清单时 verify 退 3（提示先 make）", _exit_of(cmd_verify, empty) == 3)
        _w(os.path.join(empty, MANIFEST_REL.replace("/", os.sep)), "# 没有生成标记的清单\n")
        chk(results, "D33 清单无 AUTO 标记 ⇒ verify 退 4（拒据此对账）", _exit_of(cmd_verify, empty) == 4)
        out5 = _capture(cmd_check, root, True)
        chk(results, "D34 check 一条龙：红必须来自链接而不是哈希（防因错误理由通过）",
            cmd_check(root, True) == 1 and "链接检查不通过" in out5 and "对账不通过" not in out5)
        n_in = len(parse_manifest(_manifest_text(root)))
        n_out = len(parse_manifest(_outside_table(root)))
        chk(results, "D35 解析器只吃 AUTO 段（段外同名表格行不登记）",
            n_in == len(build_rows(root)) == n_out and n_in > 0,
            "段内=%d 段外加一行后=%d 实际=%d" % (n_in, n_out, len(build_rows(root))))
        # 清单继承副本自己的外指声明（「来源」列摘录副本头部时，会把上游路径带进清单）
        ext_copy = os.path.join(root, "references", "depends", "外指来源副本.md")
        decl_line = "> 【外指声明】`references/上游附属.md` —— 上游件，本库刻意未收\n"
        _w(ext_copy, "> 【自包含副本】来源：某上游 skill v9（其 references/上游附属.md 未随副本复制）\n"
                     + decl_line + "\n正文无链接。\n")
        cmd_make(root, True)
        mtext = read_bytes(os.path.join(root, MANIFEST_REL.replace("/", os.sep))).decode("utf-8")
        chk(results, "D36 副本声明过的外指件 ⇒ 清单头部继承同一条声明",
            "【外指声明】" in mtext and "references/上游附属.md" in mtext,
            mtext.splitlines()[:12])
        out6 = _capture(cmd_links, root, None)
        # 只看这一件事：继承来的声明把「上游附属」豁免掉了。
        # 夹具里另有 SKILL.md 的故意断链与 scripts/depends_snapshot.py（夹具不复制工具本身，
        # 那正是 D24 要的效果），它们不在本条判据范围内 ⇒ 断言只盯被豁免的那个件，别拿全库零红当条件。
        chk(results, "D37 继承声明生效 ⇒ 清单「来源」列摘录来的上游件被记为外指、不算断链",
            "上游附属" not in out6 and "外指声明命中" in out6, out6.strip().splitlines()[:6])
        # 抹掉副本的声明 ⇒ 清单不再继承 ⇒ 同一处必须翻红（豁免是换来的，不是白给的）
        _w(ext_copy, "> 【自包含副本】来源：某上游 skill v9（其 references/上游附属.md 未随副本复制）\n"
                     "\n正文无链接。\n")
        cmd_make(root, True)
        out7 = _capture(cmd_links, root, None)
        hit = [l for l in out7.splitlines()
               if "快照清单.md" in l and "断链/缺失 (references/上游附属.md)" in l]
        chk(results, "D38 【必须红】副本没声明 ⇒ 清单不再豁免，摘录来的路径照报断链（红点落在清单自己那行）",
            len(hit) == 1, out7.strip().splitlines()[:8])
        rc = []
        _capture(lambda: rc.append(cmd_links(root, None)))
        chk(results, "D39 没声明时 links 返回 1 ⇒ main() 交给 sys.exit（红能传导到退出码，不只是打印）",
            rc == [1], str(rc))
        os.remove(ext_copy)
        cmd_make(root, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    npass = sum(1 for _, ok, _ in results if ok)
    for name, ok, extra in results:
        print("%s %s%s" % ("[PASS]" if ok else "[FAIL]", name,
                           ("  ← " + str(extra)) if (extra and not ok) else ""))
    print("断言 %d 条：PASS=%d FAIL=%d" % (len(results), npass, len(results) - npass))
    return 0 if npass == len(results) else 2


def _make_at(root, target_path):
    """把清单写到指定路径（自测用：验证「同名手写件拒覆盖」）。"""
    rows = build_rows(root)
    text = render_manifest(rows, now_str())
    if os.path.lexists(target_path) and AUTO_MARK not in read_bytes(target_path).decode("utf-8", "replace"):
        die(8, "[拒写] %s 无 %s 标记＝人手写" % (target_path, AUTO_MARK))
    io.open(target_path, "w", encoding="utf-8", newline="\n").write(text)
    return 0


def main(argv=None):
    setup_stdout()
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="依赖快照：文件哈希 / 路径映射 / 链接检查（零依赖）")
    ap.add_argument("verb", choices=["make", "verify", "links", "check", "selftest"])
    ap.add_argument("--root", default=default_root, help="skill 根（默认脚本的上一级）")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认干跑）")
    ap.add_argument("--path", action="append", help="只检这些文件（逗号分隔），默认全库 .md")
    args = ap.parse_args(argv)
    if args.verb == "selftest":
        return cmd_selftest(args)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        die(2, "[目录不存在] %s" % root)
    if args.verb == "make":
        return cmd_make(root, args.apply)
    if args.verb == "verify":
        return cmd_verify(root)
    if args.verb == "links":
        return cmd_links(root, args.path)
    return cmd_check(root, args.apply)


if __name__ == "__main__":
    sys.exit(main())
