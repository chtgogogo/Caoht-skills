#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_skill_manifest — 从实际目录生成技能清单 `__技能清单.md`（零依赖可跑；装了 PyYAML 自动升级为真解析器）。

为什么要它：手抄的清单快照一定会过期（AGENTS.md 铁律 10 快照禁令）。本脚本每次现扫磁盘，
把 name / 展示名 / 入口 / 版本 / 依赖 / 适用场景 六列落到文件里，并在顶部写明「自动生成，禁止手改」。
清单里没有一条是人工转述的——读不到的东西就标 ⚠，不猜。

用法：
  py gen_skill_manifest.py --root <技能库根>                # 生成到 <技能库根>/__技能清单.md
  py gen_skill_manifest.py --root <根> --out <路径>          # 写到指定文件
  py gen_skill_manifest.py --root <根> --check               # 不写盘：与现文件比对，有漂移退出码 1
  py gen_skill_manifest.py --root <根> --prefix A-           # 只列目录名带该前缀的 skill（可重复给）

两条设计约束（都是本批踩过的坑，改动前先读）：
① `--root` 必填、不默认取当前目录，再加「分母为 0 直接报错」——否则在错误目录里跑会量到 0 支
   仍打「清单已生成」（本仓踩坑日志 pit-034 记的空集恒绿同一形状）。
② 落盘文本只写「解析器无关的规范化结论」，解析器的原始报错只打控制台——这样装了 PyYAML 的机器和
   没装的机器生成的清单逐字相同，不会因为你用哪个解释器跑而让文件来回重写。
③ 生成的内容必须是**不动点**：某支技能点名了清单文件自己时按「本次运行会写出它」判在场，不按磁盘
   现况判缺——否则全新库里第一次生成的表里写着「缺」，而写完它就在场了，`--check` 于是永远报过期。

退出码：0=正常  1=--check 发现内容漂移 / 落盘读回不一致  2=用法或目录错误（含分母为 0）
"""
import argparse
import io
import os
import re
import sys
from datetime import date
from pathlib import Path

FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
BUNDLED_DIRS = ("scripts", "references", "assets", "tools")
# 库级引用：库根下的 `__tools/xxx.py` 或 `__xxx.md`，正文里正斜杠/反斜杠都算同一个东西
LIB_REF_RE = re.compile(r"__[\w.\-]*(?:[/\\][\w.\-]+)+|__[\w.\-]*\.(?:md|py|json|txt|ya?ml)")
KEY_LINE_RE = re.compile(r"^([ \t]*)([^#:][^:]*):(?:[ \t]|$)")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCENE_MAX = 80
NAME_MAX = 64

# 写进清单的结论只有这几种规范化说法（两边解析器共用，保证逐字一致）
WHY_NO_FM = "没有 `---` frontmatter 块"
WHY_PARSE_FAIL = "frontmatter 解析失败（本行字段不可信）"

def setup_stdout():
    """管道/文件 → UTF-8；真控制台 → 保留宿主码页，把编不出的字符换成 `?`。

    Git Bash 会把 `>/dev/null` 报成 tty，此时一个 emoji 就能在 GBK 码页上抛 UnicodeEncodeError——
    生成脚本自己崩在半路（已写了一半）比清单里少一行更难查。同 library_check.py 的教训。
    """
    enc = (getattr(sys.stdout, "encoding", "") or "").replace("-", "").lower()
    console_safe = enc in ("utf8", "cp65001")
    try:
        if sys.stdout.isatty():
            if not console_safe:
                sys.stdout.reconfigure(errors="replace")
        elif not console_safe:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError, OSError):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def die(msg, code=2):
    print(f"❌ {msg}")
    sys.exit(code)


# ---------------------------------------------------------------- frontmatter
def strip_quotes(text):
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        if text[0] == '"':
            body = re.sub(r"\\(u[0-9a-fA-F]{4}|.)",
                          lambda m: {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(m.group(1), m.group(1)), body)
        return body
    return text


def duplicate_keys(raw):
    """按缩进层分别找重复键；返回第一个撞车的键名（没有则 None）。

    这一层不交给解析器：PyYAML 的 SafeLoader 默认「后一个覆盖前一个」且零报警，
    子集解析器会报错——放解析器里判，两台机器的结论就不一样了。
    """
    seen = {}
    stack = []  # [(indent, {keys})]
    for line in raw.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = KEY_LINE_RE.match(line)
        if not m:
            continue
        indent, key = len(m.group(1)), m.group(2).strip().strip("\"'")
        while stack and stack[-1][0] > indent:
            stack.pop()
        if not stack or stack[-1][0] < indent:
            stack.append((indent, set()))
        if key in stack[-1][1]:
            return key
        stack[-1][1].add(key)
    return None


class MiniFm:
    """frontmatter 常用子集的严格读取器：顶格键、嵌套子映射、块文本 `>`/`|`、引号标量、无引号续行。

    认不了的一律记错并让整块判失败——静默把「读不懂」当成「没有」，正是本批审查里那类假绿结论的源头。
    """

    def __init__(self, raw):
        self.lines = raw.split("\n")
        self.i = 0
        self.errors = []
        self.data = {}

    @staticmethod
    def indent(line):
        return len(line) - len(line.lstrip(" "))

    def parse(self):
        out = self._mapping(0)
        left = [l for l in self.lines[self.i:] if l.strip() and not l.lstrip().startswith("#")]
        if left:
            self.errors.append(f"{len(left)} 行没被解析（缩进或语法不合 YAML）")
        if self.errors:
            return None
        return out

    def _next_child_indent(self):
        for j in range(self.i, len(self.lines)):
            if self.lines[j].strip() and not self.lines[j].lstrip().startswith("#"):
                return self.indent(self.lines[j]) or None
        return None

    def _next_content(self):
        """下一个非空非注释行的 (行号, 缩进)；没有则 (len, None)。"""
        for j in range(self.i, len(self.lines)):
            line = self.lines[j]
            if line.strip() and not line.lstrip().startswith("#"):
                return j, self.indent(line)
        return len(self.lines), None

    @staticmethod
    def _is_seq_item(text):
        return bool(re.match(r"^-(?:\s|$)", text))

    @staticmethod
    def _looks_like_key(text):
        return bool(re.match(r"""^['"][^'"]*['"]?\s*:(\s|$)|^[^\s:'"][^:]*\s*:(\s|$)""", text))

    def _sequence(self, indent):
        """块序列（`- 项`）。YAML 允许序列与本键同缩进（`tags:` 紧跟同列的 `- alpha`），
        也允许深缩进；两种都要收下——早先只认标量之外的形状，整块判死，把 4 支只带 tags 的
        skill 误标成「不可读」（2026-10-12 实跑 176 支时抓到，真解析器 PyYAML 本来就读得通）。"""
        items = []
        while True:
            j, ind = self._next_content()
            if ind is None or ind != indent:
                break
            stripped = self.lines[j].strip()
            if not self._is_seq_item(stripped):
                break
            self.i = j + 1
            item = stripped[1:].strip()
            if not item:
                child, cind = self._next_content()
                items.append(self._mapping(cind) if cind is not None and cind > indent else None)
            elif item[0] in "\"'":
                items.append(self._quoted(item, indent))
            elif self._looks_like_key(item):
                k, v = item.split(":", 1)
                one = {k.strip().strip("\"'"): v.strip()}
                child, cind = self._next_content()
                if cind is not None and cind > indent:
                    one.update(self._mapping(cind))
                items.append(one)
            else:
                items.append(strip_quotes(item))
        return items

    def _mapping(self, indent):
        out = {}
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip() or line.lstrip().startswith("#"):
                self.i += 1
                continue
            ind = self.indent(line)
            if ind < indent:
                break
            self.i += 1
            if "\t" in line[:ind + 1]:
                self.errors.append("用 Tab 缩进（YAML 只认空格）")
                continue
            if ind > indent:
                self.errors.append(f"第{self.i}行：缩进 {ind} 深于本层 {indent} 却不属任何键的值")
                continue
            stripped = line.strip()
            if ":" not in stripped:
                self.errors.append(f"第{self.i}行：`{stripped[:30]}` 不是 `键: 值` 结构")
                continue
            key, rest = stripped.split(":", 1)
            key, rest = key.strip().strip("\"'"), rest.strip()
            if key in out:
                self.errors.append(f"第{self.i}行：重复键 `{key}`")
                continue
            if not rest:
                j, child = self._next_content()
                head = self.lines[j].strip() if child is not None else ""
                if child is not None and child >= ind and self._is_seq_item(head):
                    out[key] = self._sequence(child)
                elif child is not None and child > ind:
                    out[key] = self._mapping(child)
                else:
                    out[key] = None
            elif rest[0] in "|>":
                out[key] = self._block(rest, self._next_child_indent() or 0, indent)
            elif rest[0] in "\"'":
                out[key] = self._quoted(rest, indent)
            else:
                val = " ".join([rest, self._plain(rest and indent)]).strip()
                out[key] = strip_quotes(val)
        return out

    def _plain(self, indent):
        parts = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip() or self.indent(line) <= indent:
                break
            parts.append(line.strip())
            self.i += 1
        return " ".join(parts)

    def _block(self, header, child, indent):
        if not child or child <= indent:
            self.errors.append(f"块文本 `{header}` 后面没有任何内容")
            return ""
        fold = header[0] == ">"
        body = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and self.indent(line) < child:
                break
            body.append(line[child:] if len(line) > child else "")
            self.i += 1
        while body and not body[-1].strip():
            body.pop()
        text = "\n".join(body)
        if fold:
            text = re.sub(r"(?<=\S)\n(?=\S)", " ", text)
        return text.strip()

    def _quoted(self, rest, indent):
        quote, buf = rest[0], rest[1:]
        while True:
            closed, tail = False, ""
            j = 0
            while j < len(buf):
                if quote == '"' and buf[j] == "\\":
                    j += 2
                    continue
                if buf[j] == quote:
                    if quote == "'" and buf[j + 1:j + 2] == "'":
                        buf = buf[:j] + "'" + buf[j + 2:]
                        j += 2
                        continue
                    tail, buf, closed = buf[j + 1:], buf[:j], True
                    break
                j += 1
            if closed:
                if tail.strip():
                    self.errors.append(f"引号标量结束处多出 `{tail.strip()[:30]}`")
                return strip_quotes(f'{quote}{buf}{quote}')
            if self.i >= len(self.lines):
                break
            nxt = self.lines[self.i]
            if nxt.strip() and self.indent(nxt) <= indent:
                break
            buf += " " + nxt.strip()
            self.i += 1
        self.errors.append("引号未闭合（值里的双引号要么转义，要么改用 `description: >` 块文本）")
        return buf


def load_yaml_raw(raw):
    """真解析器优先，并拒绝重复键（SafeLoader 默认后者覆盖前者且零报警）。"""
    import yaml

    class DupLoader(yaml.SafeLoader):
        pass

    def ctor(loader, node, deep=False):
        seen = set()
        for k_node, _ in node.value:
            k = loader.construct_object(k_node, deep=deep)
            if k in seen:
                raise yaml.error.MarkedYAMLError(f"重复键 `{k}`", k_node.start_mark)
            seen.add(k)
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    DupLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, ctor)
    data = yaml.load(raw, Loader=DupLoader)
    if not isinstance(data, dict):
        raise ValueError("frontmatter 顶层不是 `键: 值` 映射")
    return data


def read_frontmatter(text):
    """返回 (data|None, 规范化结论, 解析器原始报错)。

    规范化结论进清单（两台机器逐字一致），原始报错只打控制台——PyYAML 与子集解析器对同一个坏文件的
    说法不一样，把说法写进文件就会因为「你用哪个解释器跑」而让清单来回重写。
    """
    m = FM_RE.match(text)
    if not m:
        return None, WHY_NO_FM, ""
    raw = m.group(1)
    dup = duplicate_keys(raw)
    if dup:
        return None, f"重复键 `{dup}`（后一个键覆盖前一个，标准解析器在此中断）", ""
    try:
        import yaml  # noqa: F401
    except ImportError:
        fm = MiniFm(raw)
        data = fm.parse()
        return data, "" if data is not None else WHY_PARSE_FAIL, "；".join(fm.errors)
    try:
        return load_yaml_raw(raw), "", ""
    except Exception as exc:
        return None, WHY_PARSE_FAIL, "标准解析器拒绝：" + " ".join(str(exc).split())[:160]


def pick(data, key):
    """version 允许写在顶层或 metadata 下（与 library_check.py 同口径）。"""
    if not isinstance(data, dict):
        return None
    val = data.get(key)
    if val is None and isinstance(data.get("metadata"), dict):
        val = data["metadata"].get(key)
    if isinstance(val, (int, float)):
        return str(val)
    return val if isinstance(val, str) else None


# ---------------------------------------------------------------- 单支扫描
def bundled_files(skill_dir):
    out = []
    for sub in BUNDLED_DIRS:
        p = skill_dir / sub
        if not p.is_dir() or p.is_symlink():
            continue
        for f in sorted(os.scandir(p), key=lambda e: e.name):
            if f.is_dir(follow_symlinks=False) or f.name.startswith((".", "__")):
                continue
            if f.name.endswith((".pyc", ".bak")):
                continue
            out.append(f"{sub}/{f.name}")
    return out


def lib_refs(body):
    """正文里点名的库根级文件（`__tools/…`、`__xxx.md`），归一化分隔符后去重排序。"""
    seen = set()
    for tok in LIB_REF_RE.findall(body):
        seen.add(tok.replace("\\", "/"))
    return sorted(seen)


def scene_from(description):
    if not description:
        return "⚠ 缺 description"
    flat = " ".join(description.split())
    return flat if len(flat) <= SCENE_MAX else flat[:SCENE_MAX].rstrip() + "…"


def cell(text):
    """表格单元：转义竖线、吃掉换行，别让一行数据撑破表格。"""
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "—"


def scan(root, prefixes, out_path=None):
    rows, problems, details = [], [], []

    def present(rel):
        p = root / rel
        if p.exists():
            return True
        return out_path is not None and p.resolve() == out_path

    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False) or entry.name.startswith((".", "__")):
            continue
        if prefixes and not any(entry.name.startswith(p) for p in prefixes):
            continue
        d = Path(entry.path)
        sk = d / "SKILL.md"
        if not sk.is_file():
            continue
        text = sk.read_text(encoding="utf-8", errors="replace")
        data, reason, detail = read_frontmatter(text)
        m = FM_RE.match(text)
        body = text[m.end():] if (data is not None and m) else text
        name = pick(data, "name")
        ver = pick(data, "version")
        desc = pick(data, "description")
        if detail:
            details.append(f"{entry.name}：{detail}")
        mine = []
        if data is None:
            problems.append(f"{entry.name}：{reason}")
        else:
            for field, val in (("name", name), ("description", desc)):
                if val is None:
                    mine.append(f"缺 `{field}`")
            if ver is None:
                mine.append("缺 `version`")
            if name and (len(name) > NAME_MAX or not NAME_RE.match(name)):
                mine.append(f"`name` 不合开放标准（`{name}`）")
        if mine:
            problems.append(f"{entry.name}：" + "、".join(mine))
        h1 = re.search(r"^#[ \t]+(.+)$", body, re.M)
        bundled = bundled_files(d)
        refs = lib_refs(body)
        dep = "、".join(bundled[:4]) + (f"…（另 {len(bundled) - 4} 个）" if len(bundled) > 4 else "") if bundled else "无"
        if refs:
            # 清单自己点名自己时按「在场」判：本次运行就会把它写出来。按磁盘现况判「缺」的话，
            # 首次生成的内容立刻与生成后的磁盘矛盾 ⇒ --check 永远报过期（全新安装第一跑就中招）。
            dep += " ｜ 库级：" + "；".join(
                "{}（{}）".format(r, "在场" if present(r) else "缺") for r in refs)
        rows.append({
            "dir": entry.name,
            "name": name,
            "display": (h1.group(1).strip() if h1 else entry.name),
            "entry": f"{entry.name}/SKILL.md",
            "version": ver,
            "dep": dep,
            "scene": scene_from(desc),
            "bad": data is None,
        })
    return rows, problems, details


# ---------------------------------------------------------------- 渲染
def render(rows, problems, root_label, date_str):
    """输出里不写任何盘符绝对路径（发布仓侧要过泄漏扫描），入口一律相对库根。"""
    no_ver = [r for r in rows if not r["version"]]
    bad = [r for r in rows if r["bad"]]
    by_name = {}
    for r in rows:
        if r["name"]:
            by_name.setdefault(r["name"], []).append(r["dir"])
    dup = {k: v for k, v in by_name.items() if len(v) > 1}

    out = [
        "# Skills 清单（自动生成 · 禁止手改）",
        "",
        "> 由 `gen_skill_manifest.py` 现扫磁盘生成；手抄的快照一定会过期。要改内容请改各支 frontmatter，再重跑本脚本。",
        "> 重生成：`py <本脚本所在目录>/gen_skill_manifest.py --root <技能库根>`　自检：同命令加 `--check`（有漂移退出码 1）",
        f"> 生成日期：{date_str}｜库根标记：`{root_label}`（表内路径一律相对库根）｜分母：{len(rows)} 支",
        "> 列口径：展示名＝正文一级标题（无则目录名）；入口＝相对库根的 `SKILL.md`；版本＝frontmatter `version`（顶层或 `metadata` 下）；"
        "依赖＝包内 `scripts|references|assets|tools` 文件 ＋ 正文点名的库根级文件（括号标在场/缺）；"
        "适用场景＝`description` 前 80 字符截断，完整触发描述见各支 frontmatter。跨支跳转链接（`../`）不计入依赖。",
        "",
        "| name（调用键） | 展示名 | 入口 | 版本 | 依赖 | 适用场景 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append("| {} | {} | {} | {} | {} | {} |".format(
            cell(r["name"] or "⚠ 缺 name"),
            cell(r["display"]),
            cell(r["entry"]),
            cell(r["version"] or ("⚠ 无版本" if not r["bad"] else "⚠ 不可读")),
            cell(r["dep"]),
            cell(r["scene"]),
        ))
    out += ["", "## 生成时发现的问题", ""]
    out.append(f"- 分母 {len(rows)} 支｜frontmatter 不可读 {len(bad)} 支｜缺 `version` {len(no_ver)} 支｜`name` 重复 {len(dup)} 组")
    for msg in problems:
        out.append(f"- ⚠ {cell(msg)}")
    for k, v in sorted(dup.items()):
        out.append(f"- 🔴 `name: {k}` 被 {len(v)} 个目录同时使用：{', '.join(v)}"
                   "（按 `name` 寻址会撞车，调度器 State 1 无法唯一命中）")
    if not problems and not dup:
        out.append("- 无")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(prog="gen_skill_manifest.py", add_help=True,
                                 description=__doc__.splitlines()[0],
                                 epilog="详见模块 docstring 的用法与退出码")
    ap.add_argument("--root", required=True, help="技能库根目录（必填：默认取当前目录会造出空集恒绿）")
    ap.add_argument("--out", help="输出文件（默认 <root>/__技能清单.md）")
    ap.add_argument("--check", action="store_true", help="不写盘，与现文件比对；内容漂移退出码 1")
    ap.add_argument("--prefix", action="append", default=[], help="只列目录名以此前缀开头的 skill（可重复）")
    a = ap.parse_args()

    setup_stdout()
    root = Path(a.root).resolve()
    if not root.is_dir():
        die(f"库根不存在或不是目录：请用 --root 指到技能库根（当前给的解析为 {root}）")
    out_path = Path(a.out).resolve() if a.out else (root / "__技能清单.md").resolve()
    rows, problems, details = scan(root, a.prefix, out_path)
    if not rows:
        # 分母闸：一支没扫到就是尺子贴错了地方，禁止打「清单已生成」
        die(f"分母为 0：{root} 下没找到任何含 SKILL.md 的目录"
            f"——要么 --root 给错，要么前缀过滤过窄（当前前缀：{a.prefix or '不过滤'}）")

    try:
        import yaml
        parser = f"PyYAML {getattr(yaml, '__version__', '?')}（真解析 + 拒绝重复键）"
    except ImportError:
        parser = "🔻 降级模式：本机没有 PyYAML，用内置子集解析器"
    body = render(rows, problems, root.name, date.today().isoformat())

    cur = out_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n") if out_path.is_file() else None
    print(f"库根：{root}")
    print(f"解析器：{parser}")
    print(f"分母：{len(rows)} 支｜问题条目 {len(problems)} 条｜解析器原始报错（不进清单）{len(details)} 条")
    print(f"输出：{out_path}")
    for x in details:
        print(f"   · {x}")

    if a.check:
        if cur is None:
            print("❌ --check：清单文件不存在，无从比对（先不带 --check 生成一次）")
            sys.exit(1)
        cut = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("> 生成日期："))
        if cut(cur) == cut(body):
            print("✅ --check：清单与实际目录一致" + ("" if cur == body else "（仅生成日期一行不同）"))
            sys.exit(0)
        print("❌ --check：清单已过期或与磁盘不一致——重跑本脚本（不带 --check）覆盖它，别手改")
        sys.exit(1)

    if cur == body:
        print("✅ 内容与现文件逐字相同，未写盘（幂等）")
        return
    out_path.write_text(body, encoding="utf-8", newline="\n")
    back = out_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    if back != body:
        die("落盘后读回比对不一致，清单可能只写了一半，请检查磁盘/权限", 1)
    print(f"✅ 已生成 {len(rows)} 支清单（{len(body.splitlines())} 行）；写后读回逐字一致")


if __name__ == "__main__":
    main()
