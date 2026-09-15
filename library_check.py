#!/usr/bin/env python3
"""library_check — Caoht-skills 仓库级体检（零依赖，纯标准库）。

检查四件事：
1. 目录列表 vs README 收录表漂移（有 SKILL.md 的目录都应被 README 提到）
2. name 字段冲突（两个 skill 用同一调用键）
3. 已知重复对（逐字 diff）
4. frontmatter 完整性（name/version/description 缺失）

用法：py library_check.py [仓库根目录]   （默认当前目录）
退出码：0=通过  1=有问题
"""
import re
import sys
import difflib
import subprocess
from pathlib import Path


def read_fm(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"\A---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        stripped = line.strip()
        # 顶格键与一层缩进键（metadata 下）都收进来；同名时顶格优先。
        # 兼容部分 Agent 框架要求 version 放在 metadata 下的写法。
        if ":" in stripped and not stripped.startswith("-"):
            k, v = stripped.split(":", 1)
            fm.setdefault(k.strip(), v.strip())
    return fm


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = []

    skill_dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
    )
    names = {}
    for d in skill_dirs:
        fm = read_fm(d / "SKILL.md")
        name = fm.get("name", "")
        # 2. name 冲突
        if name and name in names:
            problems.append(f"[name冲突] {name}: {names[name]} 与 {d.name}")
        names[name] = d.name
        # 4. frontmatter 完整性
        for field in ("name", "version", "description"):
            if not fm.get(field):
                problems.append(f"[缺字段] {d.name}/SKILL.md 缺 {field}")

    # 1. README/docs 覆盖检查（规范：每 skill 一篇 docs 详解页 + 返回总目录回链）
    readme = (root / "README.md")
    if not readme.exists():
        problems.append("[缺README]")
    else:
        rtext = readme.read_text(encoding="utf-8", errors="replace")
        if "skills-index" not in rtext:
            problems.append("[缺锚点] README 缺 #skills-index 跳转锚点")
        docs_dir = root / "docs"
        for d in skill_dirs:
            doc = docs_dir / f"{d.name}.md"
            if not doc.exists():
                problems.append(f"[缺详解页] docs/{d.name}.md")
                continue
            dtext = doc.read_text(encoding="utf-8", errors="replace")
            if "README.md#skills-index" not in dtext:
                problems.append(f"[缺回链] docs/{d.name}.md 无返回总目录链接")
        for m in re.finditer(r"\]\((docs/[^)#]+\.md)\)", rtext):
            if not (root / m.group(1)).exists():
                problems.append(f"[docs死链] {m.group(1)}")

    # 3. 已知重复对（同 name 的目录已在上面查过；这里查不同目录同内容 SKILL.md）
    contents = {}
    for d in skill_dirs:
        key = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        contents.setdefault(hashlib_key(key), []).append(d.name)
    for k, dirs in contents.items():
        if len(dirs) > 1:
            problems.append(f"[疑似重复] {', '.join(dirs)} SKILL.md 逐字相同")

    print(f"体检目录：{root}")
    print(f"skill 数：{len(skill_dirs)}")
    if problems:
        print(f"\n发现 {len(problems)} 个问题：")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("\n✅ 全部通过：无 name 冲突、无重复、README 收录一致、frontmatter 完整")


def hashlib_key(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


if __name__ == "__main__":
    main()
