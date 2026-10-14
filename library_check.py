#!/usr/bin/env python3
"""library_check — Caoht-skills 仓库级体检（零依赖可跑；装了 PyYAML 自动升级为真解析器）。

检查项分两档：
🔴 红（退出码 1）——装进去就跑不起来、或索引与本体不一致：
  · frontmatter 必须能被 YAML 解析：有 PyYAML 用真解析器并拒绝重复键，没有则用内置严格子集解析器并明示「降级模式」
  · 重复键 / 缺 --- 块 / name·description·version 缺失或类型不是字符串
  · SKILL.md 正文引用的 scripts|references|assets 相对路径文件不存在
  · README / docs 详解页 / SKILL.md 内的相对链接目标不存在
  · README 收录表 ↔ 实际 skill 目录双向不一致；README 版本列 ≠ frontmatter version
  · 发布仓专属：目录名 ≠ `name`（开放标准要求 name == 父目录名）；README「中文名」列 ≠ metadata.displayName；
    缺 metadata.displayName；docs/*.md 未被收录表任何一行指向（后一条为警告）
  · 传统四项：详解页·回链·锚点漂移、name 冲突、SKILL.md 逐字重复
🟡 警（不影响退出码）——要人来裁决的漂移与规范上限：
  · description 超 1024 字符、name 不合标准格式（权威库侧目录名 ≠ name 也只报警告，那边按中文目录名约定走）
  · frontmatter 版本 ↔ 本支 CHANGELOG 最新版本不一致，或没有 CHANGELOG
  · 跨 skill 的 `../` 链接失效（访客可能只装其中几支）；`tools/` 引用失效（本库约定那是使用者 store 部署件）
  · 指向本机绝对路径的链接（公开库里应换成占位符）；发布仓混进 __pycache__/*.pyc

用法：py library_check.py [仓库根目录]      （默认当前目录）
退出码：0=无红项  1=有红项
"""
import hashlib
import io
import re
import sys
import urllib.parse
from pathlib import Path

# 数字取自 Agent Skills 开放标准（agentskills.io/specification）：name 1-64 字符、description 1-1024 字符
NAME_MAX = 64
DESC_MAX = 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXT_LINK_RE = re.compile(r"(?i)^(https?|mailto|ftp|file):")
ABS_LINK_RE = re.compile(r"^([A-Za-z]:[\\/]|[/\\])")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEQ_ITEM_RE = re.compile(r"^-(?:\s+(.*))?$")
# 路径型引用必须正好收在扩展名上，否则会把后面的中文说明一起吞掉（误判成缺文件）
PATH_RE = re.compile(
    r"(?<![\w./-])((?:scripts|references|assets|tools)/[\w\-./%&\u4e00-\u9fff ]+?\.(?:py|md|json|txt|sh|ps1|yaml|yml|bat))(?![\w./])"
)
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
# CHANGELOG 版本号标题：允许 `## v1.2.0`、`## 1.2（...）`、`## 1. vX（...）`；纯日期标题（2026-09-28）不收
VER_HEAD_RE = re.compile(r"^#{1,4}\s*(?:\d+[.)]\s*)?v?(\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?)", re.M)

RED, WARN = [], []


# ---------------------------------------------------------------- 版本比较
def version_parts(text):
    """拆成可比较的 (数值段补齐4位, 后缀小写)。`v1.1` 与 `1.1.0` 视为同一版本。"""
    s = str(text).strip().lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+){0,3})(.*)$", s)
    if not m:
        return (None, s.lower())
    nums = [int(x) for x in m.group(1).split(".")]
    nums += [0] * (4 - len(nums))
    return (nums, m.group(2).strip("-").lower())


def same_version(a, b):
    return version_parts(a) == version_parts(b)


# ---------------------------------------------------------------- 内置子集解析器
class SubsetYaml:
    """YAML 常用子集的严格解析器：顶格键、任意层嵌套映射、块序列（`- 项`）与流式序列（`[a, b]`）、
    `>`/`|` 块文本、单/双引号标量（可跨行）、无引号标量的缩进续行。解析不了的一律记红，不静默放过。"""

    def __init__(self, raw):
        self.lines = raw.split("\n")
        self.i = 0
        self.errors = []

    @staticmethod
    def _indent(line):
        return len(line) - len(line.lstrip(" "))

    @staticmethod
    def _typed(text):
        if text in ("", "~", "null", "Null", "NULL"):
            return None
        if re.fullmatch(r"(true|false|yes|no|on|off|True|False|Yes|No|On|Off)", text):
            return text.lower() in ("true", "yes", "on")
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
        if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?", text):
            return float(text)
        return text

    @staticmethod
    def _looks_like_key(text):
        return bool(re.match(r"""^['"][^'"]*['"]?\s*:(\s|$)|^[^\s:'"][^:]*\s*:(\s|$)""", text))

    def _fail(self, msg):
        self.errors.append(f"第{self.i}行：{msg}")

    def parse(self):
        data = self._mapping(0)
        left = [l for l in self.lines[self.i:] if l.strip() and not l.lstrip().startswith("#")]
        if left:
            self.errors.append(f"第{self.i + 1}行起有 {len(left)} 行没被解析（缩进或语法不合 YAML）")
        return data

    def _next_content_indent(self):
        return self._indent(self.lines[self._next_content_index()]) if self._next_content_index() < len(self.lines) else None

    def _next_content_index(self):
        j = self.i
        while j < len(self.lines):
            line = self.lines[j]
            if line.strip() and not line.lstrip().startswith("#"):
                return j
            j += 1
        return len(self.lines)

    def _sequence(self, indent):
        """块序列（`- 项`）：标量项、`- 键: 值` 单项映射、`-` 换行接子映射三种形状。"""
        items = []
        while True:
            j = self._next_content_index()
            if j != self.i:
                self.i = j
            if j >= len(self.lines) or self._indent(self.lines[j]) != indent:
                break
            stripped = self.lines[j].strip()
            m = SEQ_ITEM_RE.match(stripped)
            if not m:
                break
            self.i += 1
            item = (m.group(1) or "").strip()
            if not item:
                child = self._next_content_indent()
                items.append(self._mapping(child) if child is not None and child > indent else None)
            elif item[0] in "\"'":
                items.append(self._quoted(item, indent))
            elif item[0] == "[":
                items.append(self._flow_seq(item))
            elif self._looks_like_key(item):
                k, v = item.split(":", 1)
                items.append({k.strip().strip("\"'"): self._typed(v.strip())})
            else:
                items.append(self._typed(f"{item} {self._plain_cont(indent)}".strip()))
        return items

    def _flow_seq(self, text):
        """流式序列 `key: [a, b]`：按逗号切（引号内的逗号不算分隔）。"""
        if not text.endswith("]"):
            self._fail(f"流式序列 `{text[:30]}` 缺闭合的 `]`")
            return []
        parts, buf, quote = [], "", None
        for ch in text[1:-1]:
            if quote:
                buf += ch
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf += ch
            elif ch == ",":
                parts.append(buf)
                buf = ""
            else:
                buf += ch
        parts.append(buf)
        return [self._typed(p.strip().strip("\"'")) for p in parts if p.strip()]

    def _mapping(self, indent):
        out = {}
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip() or line.lstrip().startswith("#"):
                self.i += 1
                continue
            ind = self._indent(line)
            if ind < indent:
                break
            if "\t" in line[:ind + 1]:
                self._fail("用 Tab 缩进（YAML 只认空格）")
            self.i += 1
            if ind > indent:
                self._fail(f"缩进 {ind} 深于本层 {indent} 却不属任何键的值（YAML 缩进不一致）")
                continue
            stripped = line.strip()
            if ":" not in stripped:
                self._fail(f"`{stripped[:40]}` 不是 `键: 值` 结构")
                continue
            key, rest = stripped.split(":", 1)
            key, rest = key.strip().strip('"\''), rest.strip()
            if key in out:
                self._fail(f"重复键 `{key}`")
                continue
            if not rest:
                j = self._next_content_index()
                child = self._indent(self.lines[j]) if j < len(self.lines) else None
                head = self.lines[j].strip() if child is not None else ""
                if child is not None and child > ind:
                    out[key] = self._sequence(child) if SEQ_ITEM_RE.match(head) else self._mapping(child)
                elif child == ind and SEQ_ITEM_RE.match(head):
                    # YAML 允许键的块序列与本键同缩进（`python:` 紧跟同列的 `- 项`）
                    out[key] = self._sequence(ind)
                else:
                    out[key] = None
            elif rest[0] in "|>":
                out[key] = self._block(rest, ind)
            elif rest[0] in "\"'":
                out[key] = self._quoted(rest, ind)
            elif rest[0] == "[":
                out[key] = self._flow_seq(rest)
            else:
                tail = self._plain_cont(ind)
                value = f"{rest} {tail}".strip()
                if re.search(r":(\s|$)", value):
                    # 无引号值里出现「冒号+空格」时，标准解析器会把它当成新的键值对而中断
                    self._fail("值里有 `: `（冒号加空格）却没加引号——标准解析器会在此当成新键值，请加引号或改 `>` 块文本")
                out[key] = self._typed(value)
        return out

    def _plain_cont(self, indent):
        parts = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip() or self._indent(line) <= indent:
                break
            text = line.strip()
            if self._looks_like_key(text):
                # 无引号标量的续行里出现 `词: ` 时，标准解析器报 mapping values are not allowed here
                self._fail(f"续行里的 `{text[:24]}` 会被当成新键值（值含冒号要加引号或改块文本）")
            parts.append(text)
            self.i += 1
        return " ".join(parts)

    def _quoted(self, rest, indent):
        quote, buf, closed, tail = rest[0], rest[1:], False, ""
        while True:
            j = 0
            while j < len(buf):
                if quote == '"' and buf[j] == "\\":
                    j += 2
                    continue
                if buf[j] == quote:
                    if quote == "'" and buf[j + 1: j + 2] == "'":
                        buf = buf[:j] + "'" + buf[j + 2:]
                        j += 2
                        continue
                    closed, tail, buf = True, buf[j + 1:], buf[:j]
                    break
                j += 1
            if closed:
                break
            nxt = self.lines[self.i] if self.i < len(self.lines) else None
            if nxt is None or (nxt.strip() and self._indent(nxt) <= indent):
                break
            buf += " " + nxt.strip()
            self.i += 1
        if not closed:
            self._fail(f"引号未闭合——值里的双引号要么转义成 `\\\"`，要么改用 `description: >` 块文本")
            return buf
        if tail.strip():
            self._fail(f"引号标量结束处多出 `{tail.strip()[:30]}`（标准解析器在此中断）")
        if quote == '"':
            buf = re.sub(r"\\(u[0-9a-fA-F]{4}|.)",
                         lambda m: {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(m.group(1), m.group(1)), buf)
        return buf

    def _block(self, header, indent):
        style = header[0]
        chomp = "-" if "-" in header else ("+" if "+" in header else "")
        child = self._next_content_indent()
        if child is None or child <= indent:
            self._fail(f"块文本 `{header}` 后面没有任何内容")
            return ""
        body = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and self._indent(line) < child:
                break
            body.append(line[child:] if len(line) > child else "")
            self.i += 1
        while body and not body[-1].strip():
            body.pop()
        text = "\n".join(body)
        if style == ">":
            text = re.sub(r"(?<=\S)\n(?=\S)", " ", text)
        if chomp == "-":
            text = text.rstrip("\n")
        elif chomp == "":
            text = text.rstrip("\n") + "\n"
        return text


# ---------------------------------------------------------------- 解析入口
def parse_frontmatter(raw, use_yaml):
    """返回 (data, errors)；data=None 表示整块解析失败。"""
    if use_yaml:
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
        try:
            data = yaml.load(raw, Loader=DupLoader)
        except yaml.YAMLError as exc:
            return None, ["YAML 解析失败：" + " ".join(str(exc).split())[:220]]
        if not isinstance(data, dict):
            return None, ["frontmatter 顶层不是 `键: 值` 映射"]
        return data, []
    parser = SubsetYaml(raw)
    data = parser.parse()
    return data, [f"（降级模式）{e}" for e in parser.errors]


def read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def fm_field(data, key):
    """version 允许写在顶层或 `metadata` 下——CONTRIBUTING 第一节明写两种写法等价。"""
    val = data.get(key)
    if val is None and isinstance(data.get("metadata"), dict):
        val = data["metadata"].get(key)
    return val


def is_placeholder(target):
    return bool(re.search(r"[<>]|(?:x|X){3,}|\dX|\.\.\.|…", target))


def md_links(text):
    """正文里的 markdown 链接目标，跳过围栏代码块——模板里的示例链接不是真链接。"""
    out, fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fence = not fence
            continue
        if not fence:
            out += [m.group(1) for m in LINK_RE.finditer(line)]
    return out


def changelog_latest(path):
    versions = VER_HEAD_RE.findall(read(path))
    best, best_key = None, None
    for raw in versions:
        nums, suffix = version_parts(raw)
        if nums is None:
            continue
        key = (nums, suffix)
        if best_key is None or key > best_key:
            best_key, best = key, raw
    return best


def check_links(where, base, text, cross_tier):
    """cross_tier=None 表示本文件里的跨目录链接也算红（README/docs）；给函数则降级为该档。"""
    for target in md_links(text):
        if EXT_LINK_RE.match(target) or target.startswith("#") or is_placeholder(target):
            continue
        if ABS_LINK_RE.match(target):
            WARN.append(f"{where}：链接写的是本机绝对路径 `{target}`（公开库要换成占位符）")
            continue
        rel = urllib.parse.unquote(target.split("#")[0])
        if not rel or (base / rel).exists():
            continue
        msg = f"{where}：链接目标不存在 `{target}`"
        if rel.startswith("../") and cross_tier is not None:
            cross_tier(msg)
        else:
            RED.append(msg)


# ---------------------------------------------------------------- 逐支检查
def check_skill(d, mode):
    text = read(d / "SKILL.md")
    m = FM_RE.match(text)
    if not m:
        RED.append(f"{d.name}/SKILL.md：开头没有 `---` frontmatter 块")
        data, body, fm_errors = {}, text, ["无 frontmatter"]
    else:
        data, fm_errors = parse_frontmatter(m.group(1), mode["yaml"])
        body = text[m.end():]
        for e in fm_errors:
            RED.append(f"{d.name}/SKILL.md frontmatter：{e}")
        if data is None:
            data = {}

    # 整块 YAML 都没解析过去，字段级检查只会产出「缺 name / 缺 description」这类重复噪声
    ver = None
    if not fm_errors:
        for field in ("name", "description"):
            val = data.get(field)
            if val is None:
                RED.append(f"{d.name}：缺 `{field}` 字段")
            elif not isinstance(val, str):
                RED.append(f"{d.name}：`{field}` 不是字符串（解析成 {type(val).__name__}：{val!r}）")
        ver = fm_field(data, "version")
        if ver is None:
            RED.append(f"{d.name}：缺 `version`（顶层或 metadata 下都算）")
        elif not isinstance(ver, str):
            RED.append(f"{d.name}：`version` 不是字符串（解析成 {type(ver).__name__}：{ver!r}）——裸数字加引号或写成 `v1.0`")

    name = data.get("name") if isinstance(data.get("name"), str) else ""
    desc = data.get("description") if isinstance(data.get("description"), str) else ""
    if name and (len(name) > NAME_MAX or not NAME_RE.match(name)):
        WARN.append(f"{d.name}：`name`=`{name}` 不合开放标准（1-{NAME_MAX} 字符，小写字母数字加单个连字符）")
    if name and name != d.name:
        # 发布仓：开放标准明写 name 必须等于父目录名 ⇒ 改名（卡20）之后这条就是硬错，
        # 装在权威库侧（中文目录名，按 D-2 保留）只报警告，两条口径不能混。
        msg = (f"{d.name}：`name`=`{name}` 与目录名不一致（开放标准要求 name == 父目录名；"
               "中文展示名请写 metadata.displayName，见 CONTRIBUTING 第一节）")
        (RED if mode["release"] else WARN).append(msg)
    if len(desc) > DESC_MAX:
        WARN.append(f"{d.name}：`description` {len(desc)} 字符 > 上限 {DESC_MAX}")

    for ref in sorted(set(PATH_RE.findall(body))):
        if is_placeholder(ref) or (d / ref).exists():
            continue
        msg = f"{d.name}/SKILL.md：正文引用的 `{ref}` 不存在"
        (WARN if ref.startswith("tools/") else RED).append(msg)

    check_links(f"{d.name}/SKILL.md", d, body, WARN.append)

    chg = d / "CHANGELOG.md"
    if not chg.exists():
        WARN.append(f"{d.name}：没有 CHANGELOG.md（迭代留痕断链）")
    elif isinstance(ver, str) and ver.strip():
        latest = changelog_latest(chg)
        if latest is None:
            WARN.append(f"{d.name}：CHANGELOG 里找不到版本号标题（形如 `## v1.2.0`）")
        elif not same_version(latest, ver):
            WARN.append(f"{d.name}：版本漂移 frontmatter=`{ver}` ↔ CHANGELOG 最新=`{latest}`")
    if mode["release"]:
        junk = [p for p in d.rglob("*") if p.is_file() and p.suffix == ".pyc"]
        if junk:
            WARN.append(f"{d.name}：发布仓里混进 {len(junk)} 个 .pyc 编译缓存（如 {junk[0].name}），同步时应排除")
    return name, hashlib.sha256(text.encode()).hexdigest()[:16]


# README 收录表：卡20 起目录名＝frontmatter name（英文），详解页文件名保留中文（D-3 ③），
# 两者不再同名 ⇒ 「docs 文件名 == 目录名」这条老推论失效，映射关系只能由这张表自己承载。
# 列的位置一律按表头文字认，不按列序号——将来再加列也不用改正则。
TABLE_HEAD_RE = re.compile(r"^\|\s*Skill\s*\|")
TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
DOC_IN_CELL_RE = re.compile(r"\]\((docs/[^)]+)\)")
BACKTICK_RE = re.compile(r"^`(.+)`$")


def readme_rows(rtext):
    """解析收录表 → (rows, errs)。每行 {'dir','cn','ver','doc','line'}；表头缺列／行错位＝err。"""
    lines = rtext.splitlines()
    head_i = None
    for i, line in enumerate(lines):
        if TABLE_HEAD_RE.match(line):
            head_i = i
            break
    if head_i is None:
        return [], ["[收录表] 找不到 `| Skill | … |` 表头行"]
    cells = [c.strip() for c in lines[head_i].split("|")[1:-1]]

    def col(*keys):
        for j, c in enumerate(cells):
            if any(k in c for k in keys):
                return j
        return None

    idx = {"doc": col("Skill"), "dir": col("目录"), "cn": col("中文名"), "ver": col("版本")}
    miss = [k for k, v in idx.items() if v is None]
    if miss:
        return [], [f"[收录表] 表头缺列 {','.join(miss)}（现列：{cells}）"]
    rows, errs = [], []
    for i, line in enumerate(lines[head_i + 1:], head_i + 2):
        # 表格块以「第一行不是 | 开头」收尾（README 后面还有别的表，整篇扫会把它们当成收录行）
        if not line.startswith("|"):
            break
        if TABLE_SEP_RE.match(line):
            continue
        cs = [c.strip() for c in line.split("|")[1:-1]]
        if len(cs) != len(cells):
            errs.append(f"[收录表] 第 {i} 行 {len(cs)} 列 ≠ 表头 {len(cells)} 列")
            continue
        m = DOC_IN_CELL_RE.search(cs[idx["doc"]])
        if not m:
            errs.append(f"[收录表] 第 {i} 行 Skill 列没有 docs/ 链接")
            continue
        d = cs[idx["dir"]]
        bt = BACKTICK_RE.match(d)
        if bt:
            d = bt.group(1)
        rows.append({"dir": d, "cn": cs[idx["cn"]], "ver": cs[idx["ver"]],
                     "doc": urllib.parse.unquote(m.group(1)), "line": i})
    if not rows:
        errs.append("[收录表] 表头在但一行数据都没解析出来 ⇒ 分母为空，本次对账作废")
    dup = {r["dir"] for r in rows if [x["dir"] for x in rows].count(r["dir"]) > 1}
    for d in sorted(dup):
        errs.append(f"[收录表] 目录 `{d}` 出现多行")
    return rows, errs


# ---------------------------------------------------------------- 仓库级检查
def check_repo(root, mode):
    skill_dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists())
    if not mode["release"]:
        return skill_dirs
    readme = root / "README.md"
    if not readme.exists():
        RED.append("[缺README] 仓库根没有 README.md")
        return skill_dirs
    rtext = read(readme)
    if "skills-index" not in rtext:
        RED.append("[缺锚点] README 缺 #skills-index 跳转锚点")
    docs_dir = root / "docs"
    rows, tbl_errs = readme_rows(rtext)
    RED.extend(tbl_errs)
    if tbl_errs:
        return skill_dirs
    by_dir = {r["dir"]: r for r in rows}
    for d in skill_dirs:
        r = by_dir.get(d.name)
        if r is None:
            RED.append(f"[未收录] README 收录表缺目录 `{d.name}`")
            continue
        doc = root / r["doc"]
        if not doc.exists():
            RED.append(f"[缺详解页] {r['doc']}（README 目录 `{d.name}` 指向它）")
            continue
        if doc.parent != docs_dir:
            RED.append(f"[详解页跑偏] {r['doc']} 不在 docs/ 下")
        dtxt = read(doc)
        if "README.md#skills-index" not in dtxt:
            RED.append(f"[缺回链] {r['doc']} 无返回总目录链接")
        mm = FM_RE.match(read(d / "SKILL.md"))
        data, _ = parse_frontmatter(mm.group(1), mode["yaml"]) if mm else ({}, [])
        data = data or {}
        ver = fm_field(data, "version")
        if isinstance(ver, str) and not same_version(r["ver"], ver):
            RED.append(f"[版本漂移] README `{d.name}` 写 `{r['ver']}` ≠ frontmatter `{ver}`")
        disp = fm_field(data, "displayName")
        if not isinstance(disp, str) or not disp.strip():
            RED.append(f"[缺中文名] {d.name}：frontmatter 没有 metadata.displayName"
                       "（D-3 三处中文之一，README「中文名」列与它等值对账）")
        elif disp != r["cn"]:
            RED.append(f"[中文名不一致] {d.name}：README 写 `{r['cn']}` ≠ "
                       f"metadata.displayName `{disp}`")
        check_links(r["doc"], docs_dir, dtxt, None)
    real_dirs = {d.name for d in skill_dirs}
    referenced = {r["doc"] for r in rows}
    for r in rows:
        if r["dir"] not in real_dirs:
            RED.append(f"[收录幽灵行] README 列了目录 `{r['dir']}`，但没有同名 skill 目录")
        if not (root / r["doc"]).exists():
            RED.append(f"[docs死链] {r['doc']}")
    for p in sorted(docs_dir.glob("*.md")):
        if f"docs/{p.name}" not in referenced:
            WARN.append(f"[docs孤儿] {p.name} 没被 README 收录表任何一行指向")
    check_links("README.md", root, rtext, None)
    return skill_dirs


def setup_stdout():
    """输出编码兜底，两条规则：管道/文件 → UTF-8；真控制台 → 保留宿主码页但把编不出的字符换成 `?`。

    不能只靠 `isatty()`：Git Bash 把 `>/dev/null` 报成 tty，于是一个 emoji 就能在 GBK 码页上抛
    UnicodeEncodeError——体检脚本自己崩掉（退出码非 0、正文一个字没吐）比仓库有缺陷更难查。
    """
    enc = (getattr(sys.stdout, "encoding", "") or "").replace("-", "").lower()
    console_safe = enc in ("utf8", "cp65001")
    try:
        if sys.stdout.isatty():
            if not console_safe:
                sys.stdout.reconfigure(errors="replace")
        elif not console_safe:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def main():
    setup_stdout()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0] if args else ".").resolve()
    try:
        import yaml
        use_yaml, yaml_ver = True, getattr(yaml, "__version__", "?")
    except ImportError:
        use_yaml, yaml_ver = False, None
    # 发布仓判据要 README.md + docs/ 同时在场：权威库也有 README.md，但那是域说明文件、没有收录表与详解页目录，
    # 只看 README 会把权威库误判成发布仓，产出一堆「缺详解页」的假红项（2026-10-11 实测 150+ 条）。
    is_release = (root / "README.md").is_file() and (root / "docs").is_dir()
    mode = {"yaml": use_yaml, "release": is_release}
    if not is_release and (root / "README.md").is_file():
        mode["loose_readme"] = True

    print(f"体检目录：{root}")
    if use_yaml:
        print(f"解析器：PyYAML {yaml_ver}（真解析 + 拒绝重复键）")
    else:
        print("🔻 降级模式：本机没有 PyYAML，改用内置严格子集解析器（顶格键 / 任意层嵌套 / 块序列 `- 项` 与流式序列 `[a, b]` /")
        print("   引号标量 / 无引号续行）。它按常用 YAML 子集判红，覆盖面小于标准解析器；要完整判定请")
        print("   `pip install pyyaml` 后重跑。降级模式的红项会带「（降级模式）」前缀。")
    if not mode["release"]:
        why = "根目录有 README.md 但没有 docs/（判为权威库而非发布仓）" if mode.get("loose_readme") else "根目录没有 README.md"
        print(f"范围：{why} → 只查技能本体（frontmatter / 引用文件 / 链接 / name 冲突 / 逐字重复），")
        print("      跳过 README 收录表与 docs 详解页一致性。")

    skill_dirs = check_repo(root, mode)
    names, contents = {}, {}
    for d in skill_dirs:
        name, digest = check_skill(d, mode)
        if name:
            if name in names:
                RED.append(f"[name冲突] {name}：{names[name]} 与 {d.name}")
            names.setdefault(name, d.name)
        contents.setdefault(digest, []).append(d.name)
    for dirs in contents.values():
        if len(dirs) > 1:
            RED.append(f"[疑似重复] {', '.join(dirs)} 的 SKILL.md 逐字相同")

    print(f"skill 数：{len(skill_dirs)}")
    if WARN:
        print(f"\n🟡 警告 {len(WARN)} 条（不影响退出码）：")
        for w in WARN:
            print(" -", w)
    if RED:
        print(f"\n🔴 红项 {len(RED)} 条：")
        for p in RED:
            print(" -", p)
        print(f"\n❌ 体检未通过：{len(RED)} 个红项 / {len(WARN)} 个警告")
        sys.exit(1)
    print(f"\n✅ 无红项（{len(WARN)} 个警告见上）：frontmatter 可解析且字段为字符串、引用文件与相对链接齐、"
          f"README 收录与版本一致、无 name 冲突与逐字重复")


if __name__ == "__main__":
    main()
