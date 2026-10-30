#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""skill_selfcheck — 记忆纪律 skill 的自检器（零第三方依赖）。

用途：新 Agent 拿到本 skill 后先跑它，检查“规则本体是否可执行、是否已腐烂”。
它只读，不写盘；不依赖任何宿主路径。

用法：
  py tools/skill_selfcheck.py
  py tools/skill_selfcheck.py --manifest E:\path\to\resident-manifest.json
  py tools/skill_selfcheck.py --json

退出码：0=全绿；1=有红；2=只有黄；3=工具自身异常（报告不可采信）。
"""
import argparse
import json
import re
import sys
from pathlib import Path


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def add(results, level, code, msg):
    results.append((level, code, msg))


def parse_version(text):
    m = re.search(r"^\s*version:\s*(\S+)", text or "", re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


# 「沿革」指针：SKILL.md 头部指向包外单源的修订记录（如 ../修订记录/<资产名>.md）
POINTER_RE = re.compile(r"沿革[^\n]*?(\.\./[^\s。；，）】]+?\.md)")


def check_skill(skill_dir, results):
    skill = skill_dir / "SKILL.md"
    changelog = skill_dir / "CHANGELOG.md"
    if not skill.is_file():
        add(results, "红", "SKILL", f"SKILL.md 不存在：{skill}")
        return
    text = read(skill) or ""
    ver = parse_version(text)
    if not ver:
        add(results, "红", "VER", "frontmatter 缺 version 字段")
    else:
        probes = [("SKILL 头部", text[:4000])]
        no_narrative = ""
        if changelog.is_file():
            probes.append(("CHANGELOG 顶部", (read(changelog) or "")[:2000]))
        else:
            # CHANGELOG 已收编进库根修订记录（包外单源）：改读头部指针所指文件；指针断链降黄不静默跳过（防假绿）
            pm = POINTER_RE.search(text[:4000])
            narrative = read((skill_dir / pm.group(1)).resolve()) if pm else None
            if narrative is None:
                no_narrative = "头部无「沿革 ../修订记录/」指针" if not pm else f"指针所指文件读不到：{pm.group(1)}"
            else:
                probes.append(("修订记录 顶部", narrative[:2000]))
        missing = [label for label, probe in probes if ver not in probe]
        if missing:
            add(results, "红", "VER", f"版本 {ver} 未出现在 {'/'.join(missing)}——升版漏改")
        elif no_narrative:
            add(results, "黄", "VER", f"版本 {ver} 在 frontmatter/头部互证；版本叙事件未找到（{no_narrative}），无法三方互证")
        else:
            parties = "/".join(["frontmatter"] + [label for label, _ in probes[1:]])
            add(results, "绿", "VER", f"版本 {ver} 在 {parties} 三方互证")

    size = skill.stat().st_size  # 按磁盘字节算，避免 read_text 把 CRLF 归一成 LF 少算
    if size > 35840:
        add(results, "红", "SIZE", f"SKILL.md {size} B > 35 KB 冻结线——先结构迁移再新增")
    else:
        add(results, "绿", "SIZE", f"SKILL.md {size}/35840 B")

    heads = set(re.findall(r"^#{2,3}\s+(\d+(?:\.\d+)?)", text, re.M))
    refs = set(re.findall(r"§(\d+(?:\.\d+)?)", text))
    dangling = sorted(refs - heads, key=lambda x: [int(y) for y in x.split(".")])
    if not heads:
        add(results, "红", "REF", "未识别到任何编号章节标题")
    elif dangling:
        add(results, "红", "REF", "悬空 § 引用：" + "、".join("§" + x for x in dangling))
    else:
        add(results, "绿", "REF", f"§ 引用 {len(refs)} 个，全部有对应章节")

    required_tools = ["memory_recall.py", "memory_organize.py", "next_id.py"]
    for name in required_tools:
        p = skill_dir / "tools" / name
        if not p.is_file():
            add(results, "红", "TOOL", f"缺随包工具：tools/{name}")
            continue
        try:
            compile(read(p) or "", str(p), "exec")
            add(results, "绿", "TOOL", f"tools/{name} 存在且语法可编译")
        except SyntaxError as e:
            add(results, "红", "TOOL", f"tools/{name} 语法错误：{e}")

    for rel in ("references/宿主原生记忆接管审计.md", "references/包结构与防腐.md"):
        p = skill_dir / rel
        if p.is_file():
            add(results, "绿", "REFDOC", f"{rel} 存在")
        else:
            add(results, "红", "REFDOC", f"缺 {rel}（SKILL 的结构/冷启动依赖它）")

    local = skill_dir / "personal.local.md"
    if local.is_file():
        add(results, "绿", "LOCAL", "personal.local.md 存在（本机事实层）")
    else:
        add(results, "绿", "LOCAL", "personal.local.md 不存在（可选；按通用写法执行）")


def check_manifest(path, results):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        add(results, "红", "MANIFEST", f"manifest 读不了/JSON 无效：{type(e).__name__}: {e}")
        return
    required = ["schema", "host", "files", "skill_version_source", "card_source", "resident_pointer", "aliases", "moved"]
    missing = [k for k in required if k not in data]
    if missing:
        add(results, "红", "MANIFEST", "manifest 缺字段：" + "、".join(missing))
        return
    files = data.get("files")
    if not isinstance(files, list) or not files:
        add(results, "红", "MANIFEST", "manifest.files 为空")
        return
    bad = []
    for f in files:
        if not isinstance(f, dict) or not f.get("path"):
            bad.append("files 条目缺 path")
            continue
        if not isinstance(f.get("budget_bytes"), int) or f["budget_bytes"] <= 0:
            bad.append(f"预算非法：{f.get('path')}")
            continue
        p = Path(f["path"])
        if not p.is_file():
            bad.append(f"文件不存在：{p}")
            continue
        size = len(p.read_bytes())
        if size > f["budget_bytes"]:
            bad.append(f"{p.name} {size} B > {f['budget_bytes']} B")
    for key in ("skill_version_source", "card_source", "resident_pointer"):
        p = Path(str(data.get(key, "")))
        if not p.is_file():
            bad.append(f"{key} 不存在：{p}")
    aliases = data.get("aliases")
    if not isinstance(aliases, dict) or not aliases:
        bad.append("aliases 为空")
    else:
        for name, p in aliases.items():
            if not Path(str(p)).exists():
                bad.append(f"别名 {name} 路径不存在：{p}")
    moved = data.get("moved")
    resident_text = read(Path(str(data.get("resident_pointer", "")))) or ""
    if not isinstance(moved, list):
        bad.append("moved 不是列表")
    else:
        for entry in moved:
            if not isinstance(entry, dict):
                bad.append("moved 条目不是对象")
                continue
            p = Path(str(entry.get("path", "")))
            if not p.is_file():
                bad.append(f"移出项 {entry.get('id')} 路径不存在：{p}")
            trig = entry.get("trigger")
            if not trig:
                bad.append(f"移出项 {entry.get('id')} 缺 trigger")
            elif trig not in resident_text:
                bad.append(f"移出项 {entry.get('id')} 触发词「{trig}」不在 resident_pointer")
    if bad:
        add(results, "红", "MANIFEST", "；".join(bad))
    else:
        add(results, "绿", "MANIFEST", f"schema={data.get('schema')}，files={len(files)}，aliases={len(aliases)}，moved={len(moved)}")


def main():
    ap = argparse.ArgumentParser(description="记忆纪律 skill 自检器（只读）")
    ap.add_argument("--skill-dir", default=str(Path(__file__).resolve().parent.parent),
                    help="skill 根目录（默认按本文件位置推断）")
    ap.add_argument("--manifest", default="", help="可选：宿主 resident-manifest.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    results = []
    try:
        skill_dir = Path(args.skill_dir)
        check_skill(skill_dir, results)
        if args.manifest:
            check_manifest(Path(args.manifest), results)
        else:
            add(results, "黄", "MANIFEST", "未提供 --manifest：跳过宿主适配/预算校验（不算全绿）")
    except Exception as e:  # noqa: BLE001
        print(f"tool_error: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    reds = [r for r in results if r[0] == "红"]
    yellows = [r for r in results if r[0] == "黄"]
    greens = [r for r in results if r[0] == "绿"]

    if args.json:
        print(json.dumps({
            "status": "red" if reds else ("yellow" if yellows else "green"),
            "checks": [{"level": lv, "code": code, "message": msg} for lv, code, msg in results],
        }, ensure_ascii=False, indent=2))
    else:
        for level in ("红", "黄", "绿"):
            items = [r for r in results if r[0] == level]
            if not items:
                continue
            print(f"【{level}】{len(items)} 项")
            for _, code, msg in items:
                print(f"  [{code}] {msg}")
        print(f"结果：红 {len(reds)} / 黄 {len(yellows)} / 绿 {len(greens)}")
    return 1 if reds else (2 if yellows else 0)


if __name__ == "__main__":
    sys.exit(main())
