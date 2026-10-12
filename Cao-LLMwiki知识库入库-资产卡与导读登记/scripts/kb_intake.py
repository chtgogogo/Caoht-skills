#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_intake.py — 知识库入库：三模式诊断 / 成组原子更新 / 中断恢复 / 单一写入点版本号。

只用 Python 标准库。全程不调任何外部命令：项目类型只从配置的 project_type_rules 判（不拿
「有没有 upstream／有没有提交」猜），版本号只从版本台账算（不读 git HEAD、不扫工作区）。

读写边界（正文 §7 三模式、§5 号位认领、§6 成组更新说的就是这里）：
  diagnose             默认＝只读环境诊断，一个字节都不写
  diagnose --report P  显式出报告＝只写 P 这一个文件
  diagnose --fix       显式修复＝只建「声明可建」的骨架文件，绝不改卡片／导读正文
  apply / resume       成组写盘：写前必读、每步先在日志里登记、写完必做一致性复核
  rollback             只认日志与日志登记的备份，按原字节还原

退出码：0 成功 / 1 检查或某一步未过 / 2 用法与配置错 / 3 成组不一致（要 resume 或 rollback）
        4 锁被活的写者占用或号位撞车 / 70 测试钩子模拟的中断（--crash-after 命中）
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

VERSION = "1.1.1"
CONFIG_NAME = "intake_config.json"
CRASH_EXIT = 70
REQUIRED_PATH_KEYS = ("card_dir", "guide", "changelog", "version_ledger")
VER_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
LEDGER_ROW_RE = re.compile(
    r"^\|\s*v?(\d+\.\d+(?:\.\d+)?)\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*?)\s*\|\s*(claimed|seeded|released)\s*\|")
LEDGER_HEADER = ("# 版本台账（版本号唯一写入点）\n"
                 "\n"
                 "> 号位只从本表算：认领时在锁内重读本表取最大号再进一位，认领即追加一行，不改写历史行。\n"
                 "> 本表不读 git HEAD、也不扫工作区，所以别人改了 CHANGELOG 还没提交也不会让号位撞车。\n"
                 "\n"
                 "| 版本 | 认领时间 | 宿主 | 事由 | 状态 |\n"
                 "|---|---|---|---|---|\n")
USAGE_HEADER = "# 格式: 日期|用到的卡(逗号分隔)|宿主|任务场景（浏览未用不记；同组只用其一，场景必写理由）\n"


class IntakeError(Exception):
    pass


class ConfigError(IntakeError):
    pass


class CrashSim(Exception):
    """测试钩子命中：模拟进程在两步之间被砍（不释放锁、不跑收尾）。"""


def setup_stdout():
    try:
        if sys.stdout.isatty():
            sys.stdout.reconfigure(errors="replace")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def die(msg, code=2):
    print("FAIL %s" % msg)
    return code


# ---------------------------------------------------------------- 文本读写（保住原换行形状）
def read_bytes(p):
    with open(str(p), "rb") as f:
        return f.read()


def decode_text(raw, where):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConfigError("%s 不是合法 UTF-8（%s）——先修编码再来，别用替换字符硬读" % (where, e))


def read_text(p):
    """返回 (原文, 该文件的主导换行)。文本模式会让 Python 偷偷翻译行尾，这里一律自己拼。"""
    text = decode_text(read_bytes(p), str(p))
    return text, newline_of(text)


def newline_of(text):
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return "\r\n" if crlf >= max(1, lf_only) else "\n"


def normalize_block(block, nl):
    tmp = block.replace("\r\n", "\n").replace("\r", "\n")
    return tmp.replace("\n", "\r\n") if nl == "\r\n" else tmp


def atomic_write(path, data_bytes):
    path = Path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(str(tmp), "wb") as f:
        f.write(data_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def write_text(path, text, nl):
    if "\r\r\n" in text:
        raise IntakeError("内部错误：待写内容里出现 \\r\\r\\n（行尾被拼了两遍），已拒绝落盘")
    atomic_write(path, text.encode("utf-8"))


def split_keepends(text):
    return text.splitlines(True)


def line_body(line):
    return line.rstrip("\r\n")


def trailing_of(line):
    for e in ("\r\n", "\n"):
        if line.endswith(e):
            return e
    return ""


def count_lines(text, exact):
    return sum(1 for ln in text.splitlines() if line_body(ln) == exact)


# ---------------------------------------------------------------- 配置
DEFAULT_CONFIG = {
    "paths": {
        "card_dir": "1-资产卡",
        "guide": "0-入口/AI导读.md",
        "card_template": "0-入口/模板/资产卡模板.md",
        "asset_map": "3-本机资产/AI工具地图.md",
        "project_archive": "3-本机资产/项目档案.md",
        "changelog": "CHANGELOG.md",
        "rules": "AGENTS.md",
        "usage_log": "6-outputs/usage.log",
        "version_ledger": "6-outputs/version_ledger.md",
    },
    "journal_dir": "6-outputs/_intake_journal",
    "guide_group_section": "触发词组清单",
    "functional_zones": ["学习区", "本机资产", "AI宿主地图", "决策", "采集"],
    "card_required_lines": ["group", "source", "local", "license", "last_checked", "status"],
    "card_required_values": ["group", "source", "license", "last_checked", "status"],
    "breadcrumb_token": "🏠",
    "date_field": "last_checked",
    "version_allocator": {"source": "ledger", "bump": "minor", "prefix": "v"},
    "project_type_default": "unknown",
    "project_type_rules": [],
    "landing_by_type": {"tool": "card_dir", "reference": "card_dir", "user_project": "project_archive"},
    "tool_paths": {"track": "", "check": ""},
}


def deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Vault(object):
    def __init__(self, root, config, config_path):
        self.root = Path(root).resolve()
        self.config = config
        self.config_path = config_path

    def p(self, key):
        rel = self.config["paths"].get(key)
        if not rel:
            raise ConfigError("配置 paths.%s 缺失或为空" % key)
        return self.abs(rel)

    def abs(self, rel):
        ap = Path(str(rel))
        return ap if ap.is_absolute() else (self.root / ap)

    def journal_dir(self, override=None):
        rel = override or self.config.get("journal_dir") or "6-outputs/_intake_journal"
        ap = Path(str(rel))
        return ap if ap.is_absolute() else (self.root / ap)

    def journal_rel(self, jdir):
        try:
            return Path(str(jdir)).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None


def load_vault(vault_root_arg, config_arg=None):
    if not vault_root_arg:
        raise ConfigError("必须显式给 --vault <库根>：本脚本不拿当前目录当库根——"
                          "在错目录里量到空集照样打「全绿」是最坏的一种假通过")
    root = Path(str(vault_root_arg)).resolve()
    if not root.is_dir():
        raise ConfigError("库根不存在：%s" % root)
    cfg_path = Path(str(config_arg)).resolve() if config_arg else (root / CONFIG_NAME)
    if not cfg_path.is_file():
        raise ConfigError("库根 %s 下没有 %s：项目类型与版本号都要靠它明确配置，缺了不许猜" % (root, CONFIG_NAME))
    cfg = deep_merge(DEFAULT_CONFIG, json.loads(decode_text(read_bytes(cfg_path), str(cfg_path))))
    for k in REQUIRED_PATH_KEYS:
        if not cfg["paths"].get(k):
            raise ConfigError("配置 paths.%s 必须有值" % k)
    return Vault(root, cfg, cfg_path)


# ---------------------------------------------------------------- frontmatter 与双链
def parse_frontmatter(text):
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", text, re.S)
    if not m:
        return None, text
    fields = {}
    for ln in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*)[ \t]*:[ \t]*(.*)$", ln)
        if mm:
            fields[mm.group(1)] = mm.group(2).strip()
    return fields, text[m.end():]


def wikilinks(text):
    return [t.strip() for t in re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", text) if t.strip()]


SKIP_DIRS = {"模板", ".git", ".obsidian", "_Repos", "__pycache__", ".mimosa"}


def md_stems(vault):
    out = set()
    for p in vault.root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.add(p.stem)
    return out


# ---------------------------------------------------------------- 版本号：单一写入点
def parse_ver(s):
    m = VER_RE.search(s or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def fmt_ver(t, prefix="v"):
    return "%s%d.%d.%d" % (prefix, t[0], t[1], t[2])


def bump_ver(t, kind):
    a, b, c = t
    if kind == "major":
        return (a + 1, 0, 0)
    if kind == "minor":
        return (a, b + 1, 0)
    return (a, b, c + 1)


def ledger_rows(path):
    if not Path(str(path)).is_file():
        return []
    text, _ = read_text(Path(str(path)))
    out = []
    for ln in text.splitlines():
        m = LEDGER_ROW_RE.match(ln.strip())
        if m:
            out.append((parse_ver(m.group(1)), m.group(5), line_body(ln)))
    return out


def ledger_state(path, vid):
    n = parse_ver(vid)
    claimed = released = 0
    for t, state, _ln in ledger_rows(path):
        if t == n:
            if state == "claimed":
                claimed += 1
            elif state == "released":
                released += 1
    return claimed, released


def next_version(vault, kind):
    alloc = vault.config["version_allocator"]
    led = vault.p("version_ledger")
    vs = [t for t, _s, _l in ledger_rows(led)]
    if not vs:
        raise IntakeError("版本台账 %s 里没有可用行 ⇒ 不入库。先跑 seed-ledger（从库的 CHANGELOG 现有最大号认领一次），"
                          "本脚本不读 git HEAD 也不扫工作区补号" % led.name)
    return fmt_ver(bump_ver(max(vs), kind), alloc.get("prefix", "v"))


def append_ledger_row(vault, row):
    led = vault.p("version_ledger")
    if not led.is_file():
        raise IntakeError("版本台账不存在：%s（先跑 seed-ledger 建表）" % led)
    text, nl = read_text(led)
    if not text.endswith(("\n", "\r\n")):
        text += nl
    write_text(led, text + row + nl, nl)
    back, _ = read_text(led)
    if row not in back.splitlines():
        raise IntakeError("台账追加后读回不认这一行：%s" % row)
    return row


def claim_version(vault, vid, host, reason):
    """认领：号位已由锁内 next_version 算出；这里追加一行并读回确认该号只被认领一次。"""
    led = vault.p("version_ledger")
    claimed, _ = ledger_state(led, vid)
    if claimed:
        raise IntakeError("号位 %s 在台账里已被认领 %d 次 ⇒ 不重复认领（并发入库撞号，停手）" % (vid, claimed))
    row = "| %s | %s | %s | %s | claimed |" % (vid, now_stamp(), host, reason or "")
    append_ledger_row(vault, row)
    c2, _ = ledger_state(led, vid)
    if c2 != 1:
        raise IntakeError("号位 %s 认领后台账里出现 %d 行 claimed（应为 1）⇒ 判并发，停手" % (vid, c2))
    return row


def release_version(vault, vid, host):
    return append_ledger_row(vault, "| %s | %s | %s | 回滚释放 | released |" % (vid, now_stamp(), host))


# ---------------------------------------------------------------- 锁
class Lock(object):
    def __init__(self, path, stale_seconds=120):
        self.path = Path(str(path))
        self.stale = stale_seconds
        self.held = False
        self.took_over = False

    def acquire(self, owner):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "owner": owner,
                              "ts": datetime.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False)
        for attempt in (0, 1):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                age, info = self._age()
                if age is None:
                    raise IntakeError("锁文件 %s 读不出时间戳 ⇒ 不接管（人工确认没人在写之后删掉它重试）" % self.path)
                if age <= self.stale:
                    raise IntakeError("锁被占用：%s 秒前由 %s 建立（陈旧线 %d 秒）%s"
                                      % (int(age), info.get("owner", "?"), self.stale, self.path))
                self.took_over = True
                print("   [接管] 残留锁已超 %d 秒陈旧线（原持有者 %s，%d 秒前）⇒ 判定写者已死，摘锁重写"
                      % (self.stale, info.get("owner", "?"), int(age)))
                os.remove(str(self.path))
                continue
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(payload + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.held = True
            return self
        raise IntakeError("抢锁失败：%s" % self.path)

    def _age(self):
        try:
            info = json.loads(decode_text(read_bytes(self.path), str(self.path)))
            ts = datetime.datetime.fromisoformat(info["ts"])
            return (datetime.datetime.now() - ts).total_seconds(), info
        except Exception:
            return None, {}

    def release(self):
        if self.held:
            try:
                os.remove(str(self.path))
            except OSError:
                pass
            self.held = False


def make_lock(vault, args, owner):
    jdir = vault.journal_dir(getattr(args, "journal_dir", None))
    lk = Lock(jdir / ".intake.lock", stale_seconds=getattr(args, "lock_stale", 120))
    lk.acquire(owner)
    return lk


def now_stamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def slug(s):
    return re.sub(r"[^\w.\-]+", "__", str(s)).strip("_")


# ---------------------------------------------------------------- journal（成组日志）
class Journal(object):
    def __init__(self, jpath, data):
        self.path = Path(str(jpath))
        self.data = data

    @classmethod
    def create(cls, jdir, vid, plan, vault_root, host, steps, plan_sha, plan_path):
        Path(str(jdir)).mkdir(parents=True, exist_ok=True)
        data = {"vid": vid, "created": now_stamp(), "host": host, "vault": str(vault_root),
                "plan_sha256": plan_sha, "plan_path": str(plan_path), "plan_title": plan.get("title", ""),
                "state": "claimed", "steps": steps, "backups": []}
        jp = Path(str(jdir)) / ("%s.json" % slug(vid))
        atomic_write(jp, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return cls(jp, data)

    @classmethod
    def load(cls, jpath):
        return cls(jpath, json.loads(decode_text(read_bytes(jpath), str(jpath))))

    @classmethod
    def scan(cls, jdir):
        out = []
        if not Path(str(jdir)).is_dir():
            return out
        for p in sorted(Path(str(jdir)).glob("*.json")):
            try:
                out.append(cls.load(p))
            except Exception as e:
                print("   [warn] 日志读不出：%s（%s）" % (p.name, e))
        return out

    def save(self):
        self.data["updated"] = now_stamp()
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=2).encode("utf-8"))

    def unfinished(self):
        return self.data.get("state") in ("claimed", "applying", "failed", "inconsistent")


# ---------------------------------------------------------------- 步骤
def subst(value, mapping):
    if isinstance(value, str):
        for k, v in mapping.items():
            value = value.replace(k, v)
        return value
    if isinstance(value, dict):
        return dict((k, subst(v, mapping)) for k, v in value.items())
    if isinstance(value, list):
        return [subst(v, mapping) for v in value]
    return value


def mapping_for(vid, plan, host):
    header = plan.get("changelog_header", "## %s" % vid)
    return {"@VERSION@": vid, "@DATE@": datetime.date.today().isoformat(),
            "@TITLE@": plan.get("title", ""), "@HOST@": host or plan.get("host", "-"),
            "@HEADER@": subst(header, {"@VERSION@": vid, "@DATE@": datetime.date.today().isoformat(),
                                       "@TITLE@": plan.get("title", "")})}


class Stepper(object):
    """一次 run 内的备份去重：同一个文件被多步改，只按第一次的原样备份一份。"""

    def __init__(self, vault, jdir, jdata):
        self.v = vault
        self.jdir = jdir
        self.jd = jdata
        self.backed = set()

    def backup(self, rel):
        if rel in self.backed:
            return
        src = self.v.abs(rel)
        if not src.is_file():
            self.backed.add(rel)
            return
        bdir = Path(str(self.jdir)) / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        raw = read_bytes(src)
        dst = bdir / (slug(rel) + ".bak")
        atomic_write(dst, raw)
        self.jd.setdefault("backups", []).append(
            {"rel": rel, "bak": str(dst), "sha256": hashlib.sha256(raw).hexdigest()})
        self.backed.add(rel)

    def run(self, step):
        action = step["action"]
        handler = {"create": self.do_create, "replace_line": self.do_replace_line,
                   "insert_before": self.do_insert_before, "append": self.do_append}.get(action)
        if handler is None:
            return "fail", "未知步骤类型：%s" % action
        return handler(step)

    def do_create(self, s):
        rel = s["path"]
        content = normalize_block(s["content"], "\n")
        target = self.v.abs(rel)
        if target.is_file():
            if read_bytes(target) == content.encode("utf-8"):
                return "skipped_exists", "文件已在且逐字节相同（不重写）：%s" % rel
            if not s.get("overwrite"):
                return "fail", "目标已存在且内容不同 ⇒ 不覆盖（真要覆盖给这一步加 overwrite:true）：%s" % rel
            self.backup(rel)
        atomic_write(target, content.encode("utf-8"))
        if not target.is_file():
            return "fail", "写完读不回来：%s" % rel
        return "done", "新建 %s（%d 字）" % (rel, len(content))

    def do_replace_line(self, s):
        rel = s["path"]
        target = self.v.abs(rel)
        if not target.is_file():
            return "fail", "文件不存在：%s" % rel
        text, nl = read_text(target)
        before, after = line_body(s["before"]), line_body(s["after"])
        n_before, n_after = count_lines(text, before), count_lines(text, after)
        if n_before == 0 and n_after == 1:
            return "skipped_exists", "新行已在（不重复替换）：%s" % rel
        if n_before == 0:
            return "fail", "锚点行没找到（0 次）：%s ⇒ 说明这行已被别人改过，重读文件再出 plan" % rel
        if n_before > 1:
            return "fail", "锚点行出现 %d 次 ⇒ 不猜改哪一条：%s" % (n_before, rel)
        if n_after:
            return "fail", "新行已在 %d 次而锚点也还在 ⇒ 状态可疑，停手：%s" % (n_after, rel)
        self.backup(rel)
        n_lines = len(text.splitlines())
        out = "".join(after + trailing_of(ln) if line_body(ln) == before else ln
                      for ln in split_keepends(text))
        write_text(target, out, nl)
        back, _ = read_text(target)
        if count_lines(back, before) or count_lines(back, after) != 1:
            return "fail", "写后读回不认（锚点没消失或新行不唯一）：%s" % rel
        if len(back.splitlines()) != n_lines:
            return "fail", "行数从 %d 变成 %d ⇒ 本步只该换一行：%s" % (n_lines, len(back.splitlines()), rel)
        return "done", "换 1 行 %s（行数 %d 未变）" % (rel, n_lines)

    def do_insert_before(self, s):
        rel = s["path"]
        target = self.v.abs(rel)
        if not target.is_file():
            return "fail", "文件不存在：%s" % rel
        text, nl = read_text(target)
        anchor = line_body(s["anchor"])
        marker = line_body(s["marker"]) if s.get("marker") else None
        if marker is not None and count_lines(text, marker) > 1:
            return "fail", "标记行已在 %d 处 ⇒ 判重复，停手：%s" % (count_lines(text, marker), rel)
        if marker is not None and count_lines(text, marker) == 1:
            return "skipped_exists", "标记行已在（不重复插）：%s" % rel
        lines = split_keepends(text)
        hits = [i for i, ln in enumerate(lines) if line_body(ln) == anchor]
        if len(hits) != 1:
            return "fail", "插入锚点出现 %d 次（应为 1 次）：%s" % (len(hits), rel)
        self.backup(rel)
        n_lines = len(lines)
        block = normalize_block(s["block"], nl)
        if not block.endswith(("\n", "\r\n")):
            block += nl
        added = len(block.splitlines())
        i = hits[0]
        write_text(target, "".join(lines[:i]) + block + "".join(lines[i:]), nl)
        back, _ = read_text(target)
        if marker is not None and count_lines(back, marker) != 1:
            return "fail", "写后读回标记行不是恰好一次：%s" % rel
        expect = n_lines + added
        if len(back.splitlines()) != expect:
            return "fail", "行数应为 %d 实为 %d：%s" % (expect, len(back.splitlines()), rel)
        return "done", "在锚点前插 %d 行 %s" % (added, rel)

    def do_append(self, s):
        rel = s["path"]
        target = self.v.abs(rel)
        nl, text = "\n", ""
        if target.is_file():
            text, nl = read_text(target)
        line = normalize_block(s["line"], nl).rstrip("\r\n")
        if line and count_lines(text, line):
            return "skipped_exists", "该行已在（不重复记）：%s" % rel
        self.backup(rel)
        if text and not text.endswith(("\n", "\r\n")):
            text += nl
        write_text(target, text + line + nl, nl)
        back, _ = read_text(target)
        if count_lines(back, line) != 1:
            return "fail", "写后读回该行不是恰好一次：%s" % rel
        return "done", "追加 1 行 %s" % rel


# ---------------------------------------------------------------- 一致性复核
def verify_group(vault, plan, vid, host=None):
    """卡片 / 导读组行 / 组清单 / CHANGELOG / 台账 五处必须互相对得上。返回 [(ok, 说明)]"""
    cfg = vault.config
    out = []
    card_rel = plan.get("card_path")
    if not card_rel:
        return [(False, "plan 里没写 card_path ⇒ 无法复核卡片")]
    card = vault.abs(card_rel)
    if not card.is_file():
        return [(False, "卡片不存在：%s" % card_rel)]
    text, _ = read_text(card)
    fm, body = parse_frontmatter(text)
    if fm is None:
        return [(False, "卡片 frontmatter 不成形（开头没有成对的 --- 块）")]
    absent = [k for k in cfg["card_required_lines"] if k not in fm]
    if absent:
        out.append((False, "卡片 frontmatter 缺字段行：%s（体检按行存在性判红，少一行就红）" % "/".join(absent)))
    noval = [k for k in cfg["card_required_values"] if not fm.get(k)]
    if noval:
        out.append((False, "卡片 frontmatter 字段无值：%s" % "/".join(noval)))
    df = cfg.get("date_field") or "last_checked"
    if fm.get(df):
        try:
            datetime.date.fromisoformat(fm[df][:10])
        except ValueError:
            out.append((False, "卡片 %s 不是 YYYY-MM-DD：%s" % (df, fm[df])))
    crumb = cfg.get("breadcrumb_token") or ""
    if crumb and crumb not in body[:400]:
        out.append((False, "卡片正文开头 400 字内没有返回导航标记 %s" % crumb))
    stems = md_stems(vault)
    dead = [t for t in wikilinks(text) if t not in stems]
    if dead:
        out.append((False, "卡片双链指向不存在的页：%s" % "、".join(dead[:5])))

    guide_rel = cfg["paths"]["guide"]
    guide, _ = read_text(vault.abs(guide_rel))
    stem = Path(str(card_rel)).stem
    if stem not in guide:
        out.append((False, "导读里没有卡片名 %s ⇒ 孤儿卡（存了但检索不到）" % stem))
    group = plan.get("group") or fm.get("group")
    in_table = group and any(line_body(ln).startswith("| %s |" % group) for ln in guide.splitlines())
    if group and not in_table and group not in (cfg.get("functional_zones") or []):
        out.append((False, "导读总索引表里没有组行：%s" % group))
    if plan.get("new_group"):
        section = guide.split(cfg.get("guide_group_section", "触发词组清单"))[-1]
        if group not in set(re.findall(r"`([^`]+)`", section)):
            out.append((False, "声明新建组 %s，但「%s」节里没有它的反引号条目"
                        % (group, cfg.get("guide_group_section", "触发词组清单"))))
    header = subst(plan.get("changelog_header", "## %s" % vid), mapping_for(vid, plan, host))
    ch, _ = read_text(vault.p("changelog"))
    n = sum(1 for ln in ch.splitlines() if line_body(ln).startswith(header))
    if n != 1:
        out.append((False, "CHANGELOG 里本节标题出现 %d 次（应为 1 次）：%s" % (n, header)))
    claimed, released = ledger_state(vault.p("version_ledger"), vid)
    if claimed != 1:
        out.append((False, "版本台账里 %s 的 claimed 行有 %d 条（应为 1 条）" % (vid, claimed)))
    if released > claimed:
        out.append((False, "版本 %s 的释放次数多于认领次数" % vid))
    if not out:
        out.append((True, "五处齐：卡片 %s ｜ 导读收卡 ｜ 组「%s」在表 ｜ CHANGELOG 恰好 1 节 ｜ 台账认领 1 次"
                    % (stem, group or "-")))
    return out


# ---------------------------------------------------------------- 命令：diagnose
def dir_snapshot(root, exclude=("_intake_journal",)):
    snap = {}
    root = Path(str(root))
    if not root.is_dir():
        return snap
    for p in sorted(root.rglob("*")):
        if any(part in exclude for part in p.parts) or not p.is_file():
            continue
        try:
            snap[p.relative_to(root).as_posix()] = (p.stat().st_size, hashlib.sha256(read_bytes(p)).hexdigest())
        except OSError:
            pass
    return snap


def table_groups(text, section_marker, functional):
    out = set()
    for ln in text.split(section_marker)[0].splitlines():
        m = re.match(r"^\|\s*([^|\s][^|]*?)\s*\|", ln)
        if not m:
            continue
        g = m.group(1).strip()
        if g in ("组", "---", "—", "") or set(g) <= set("-: "):
            continue
        if re.match(r"^[A-Za-z一-鿿][\w与]*$".replace("鿿", "龥"), g) and g not in functional:
            out.add(g)
    return out


def cmd_diagnose(a):
    v = load_vault(a.vault, a.config)
    cfg = v.config
    red, yel, grn = [], [], []
    mode = "修复（显式写盘）" if a.fix else ("出报告（只写报告文件）" if a.report else "只读")

    found = 0
    for key in REQUIRED_PATH_KEYS:
        p = v.p(key)
        hit = p.exists()
        found += 1 if hit else 0
        (grn if hit else red).append("paths.%s %s：%s" % (key, "在场" if hit else "缺失",
                                                         p.relative_to(v.root) if p.is_absolute() is False else p))
    if found == 0:
        print("🔴 分母为空：%s 里一个必需路径都没找到 ⇒ 判据作废，别说「一切正常」" % cfg.get("guide"))
        for m in red:
            print("   - %s" % m)
        return 1

    for key, label in (("card_template", "资产卡模板"), ("rules", "库规矩文件"), ("asset_map", "本机资产地图"),
                       ("project_archive", "项目档案")):
        rel = cfg["paths"].get(key)
        if not rel:
            continue
        p = v.abs(rel)
        msg = "%s %s：%s" % (label, "在场" if p.is_file() else "缺失", rel)
        if label in ("资产卡模板", "库规矩文件"):
            (grn if p.is_file() else yel).append(msg + ("" if p.is_file() else "（写卡没有形状参照／规矩只在本 skill 里）"))
        else:
            (grn if p.is_file() else yel).append(msg)

    # 留痕流水：库该有、脚本能容忍 ⇒ 缺就报黄（references/必需文件与工具清单.md 第二节承诺的那一档）
    ul_rel = cfg["paths"].get("usage_log")
    if ul_rel and not v.abs(ul_rel).is_file():
        yel.append("留痕流水 缺失：%s ⇒ 用卡痕迹无处可记（同组只用其一时要写清取舍理由）；"
                   "diagnose --fix 可建骨架，正文内容由人自己补" % ul_rel)

    guide_p = v.p("guide")
    if guide_p.is_file():
        text, _ = read_text(guide_p)
        sec = cfg.get("guide_group_section", "触发词组清单")
        (grn if sec in text else red).append("导读有「%s」节：%s" % (sec, "是" if sec in text else "否"))
        tg = table_groups(text, sec, cfg.get("functional_zones") or [])
        listed = set(re.findall(r"`([^`]+)`", text.split(sec)[-1])) if sec in text else set()
        unreg, ghost = sorted(tg - listed), sorted(listed - tg)
        (grn if not (unreg or ghost) else yel).append(
            "导读组清单同步：总索引 %d 组｜表有清单无=%s｜清单有表无=%s" % (len(tg), unreg or "无", ghost or "无"))
        card_dir = v.p("card_dir")
        cards = [p for p in card_dir.rglob("*.md")] if card_dir.is_dir() else []
        orphans = sorted(p.stem for p in cards if p.stem not in text and "模板" not in p.parts)
        (grn if not orphans else yel).append("孤儿卡 %d 张%s" % (len(orphans), ("：%s" % "、".join(orphans[:5])) if orphans else ""))
        bad_fm = []
        for p in cards:
            fm, body = parse_frontmatter(read_text(p)[0])
            if fm is None:
                bad_fm.append(p.stem + "（无 frontmatter）")
                continue
            miss = [k for k in cfg["card_required_lines"] if k not in fm]
            if miss:
                bad_fm.append("%s（缺 %s）" % (p.stem, "/".join(miss)))
            noval = [k for k in (cfg.get("card_required_values") or []) if not fm.get(k)]
            if noval:
                bad_fm.append("%s（字段有行但空值：%s）" % (p.stem, "/".join(noval)))
            dfield = cfg.get("date_field") or "last_checked"
            if fm.get(dfield):
                try:
                    datetime.date.fromisoformat(fm[dfield][:10])
                except ValueError:
                    bad_fm.append("%s（%s 不是 YYYY-MM-DD：%s）" % (p.stem, dfield, fm[dfield]))
            crumb = cfg.get("breadcrumb_token") or ""
            if crumb and crumb not in body[:400]:
                bad_fm.append("%s（正文开头 400 字内没有导航标记 %s）" % (p.stem, crumb))
        (grn if not bad_fm else red).append("卡片 frontmatter 六字段行 %d 张里 %d 张不齐%s"
                                            % (len(cards), len(bad_fm), ("：%s" % "、".join(bad_fm[:5])) if bad_fm else ""))

    stems = md_stems(v)
    dead = []
    for rel in ("guide", "card_dir"):
        base = v.p("guide") if rel == "guide" else v.p("card_dir")
        files = [base] if (rel == "guide" and base.is_file()) else (list(base.rglob("*.md")) if base.is_dir() else [])
        for p in files:
            if "模板" in p.parts:
                continue
            for t in wikilinks(read_text(p)[0]):
                if t not in stems:
                    dead.append("%s → 链接目标「%s」库内无同名页" % (p.name, t))
    (grn if not dead else red).append("双链死链：%d 条%s" % (len(dead), ("：%s" % "；".join(dead[:5])) if dead else ""))

    jdir = v.journal_dir(a.journal_dir)
    pending = [j for j in Journal.scan(jdir) if j.unfinished()]
    (grn if not pending else red).append("未完成的成组日志 %d 个%s" % (
        len(pending), ("（%s）⇒ 先 resume 或 rollback，别提交" % "、".join(j.data["vid"] for j in pending)) if pending else ""))
    led = v.p("version_ledger")
    ch_max = None
    if v.p("changelog").is_file():
        ctext, _ = read_text(v.p("changelog"))
        cands = [parse_ver(m.group(0)) for ln in ctext.splitlines() if ln.startswith("#") for m in VER_RE.finditer(ln)]
        cands = [c for c in cands if c]
        ch_max = max(cands) if cands else None
    if led.is_file():
        rows = ledger_rows(led)
        dups = sorted({fmt_ver(t) for t, _s, _l in rows if [x for x, _a, _b in rows].count(t) > 1})
        (grn if not dups else red).append("版本台账 %d 行；同号多次=%s" % (len(rows), dups or "无"))
        if ch_max and rows and ch_max > max([t for t, _s, _l in rows]):
            yel.append("CHANGELOG 最大号 %s 高于台账最大号 %s ⇒ 台账漏记，先跑 seed-ledger 补认领"
                       % (fmt_ver(ch_max), fmt_ver(max([t for t, _s, _l in rows]))))
        if not rows:
            yel.append("版本台账里没有可解析的表行 ⇒ apply 会拒绝入库，先跑 seed-ledger")
    else:
        yel.append("版本台账缺失（%s）⇒ apply 会拒绝；跑 seed-ledger 建表" % cfg["paths"]["version_ledger"])
    for label, key in (("留痕工具", "track"), ("体检工具", "check")):
        tp = (cfg.get("tool_paths") or {}).get(key) or ""
        if not tp:
            grn.append("%s：未配置 ⇒ 走纯手工降级流程（references/必需文件与工具清单.md）" % label)
        elif Path(str(tp)).is_file():
            grn.append("%s：在场 %s" % (label, tp))
        else:
            yel.append("%s：配置指向 %s 但不存在 ⇒ 走纯手工降级流程" % (label, tp))
    if (v.root / ".git").exists():
        gi = v.root / ".gitignore"
        gt = decode_text(read_bytes(gi), str(gi)) if gi.is_file() else ""
        rel = v.journal_rel(jdir)
        if rel is None:
            grn.append("成组日志目录在库外（%s）⇒ 不脏库的 git 状态，无需 .gitignore" % jdir)
        elif any(line_body(ln).strip().startswith(rel.rstrip("/")) for ln in gt.splitlines()):
            grn.append("成组日志目录已被 .gitignore 盖住：%s" % rel)
        else:
            yel.append("成组日志目录 %s 在库里且未被 .gitignore 盖住 ⇒ 库体检会报「git 有未提交内容」；"
                       "加一行 `%s/`，或 --journal-dir 指到库外" % (rel, rel.rstrip("/")))
    miss_land = [t for t, k in (cfg.get("landing_by_type") or {}).items() if not cfg["paths"].get(k)]
    (grn if not miss_land else red).append("项目类型落点配置%s" % ("齐全" if not miss_land else "缺：%s" % miss_land))
    if not cfg.get("project_type_rules"):
        yel.append("project_type_rules 为空 ⇒ classify 一律判不出，需要人工先按自己的库填规则（别拿「有没有上游」猜）")

    level = "🔴 红" if red else ("🟡 黄" if yel else "🟢 绿")
    lines = ["# 入库环境诊断（%s）" % now_stamp(),
             "库根：%s" % v.root,
             "模式：%s｜结论：%s（红 %d／黄 %d／通过 %d）" % (mode, level, len(red), len(yel), len(grn)), ""]
    for tag, arr in (("红", red), ("黄", yel), ("通过", grn)):
        lines.append("【%s】%d 条" % (tag, len(arr)))
        lines += ["  - %s" % m for m in arr]
        lines.append("")
    report_text = "\n".join(lines)
    # 双链语法落进库里就成了体检的又一条死链 ⇒ 渲染时抹成全角。
    # 🔴 这里绝不「整份不输出」：文案里出现双链语法，恰恰是库已经坏了的时刻，
    # 旧形状退 1 只印一行「内部错误」＝最需要看诊时一条诊断都看不到（2026-10-13 现采，T63）。
    n_wiki = report_text.count("[[") + report_text.count("]]")
    if n_wiki:
        report_text = report_text.replace("[[", "［［").replace("]]", "］］")
    print(report_text)
    if n_wiki:
        print("【报告渲染】文案里 %d 处双链语法已抹平为全角 ⇒ 落盘不会给体检添死链；诊断内容一条没少" % n_wiki)
    if a.fix:
        wrote = []
        for key, body in (("version_ledger", LEDGER_HEADER), ("usage_log", USAGE_HEADER)):
            p = v.p(key)
            if not p.is_file():
                atomic_write(p, normalize_block(body, "\n").encode("utf-8"))
                wrote.append(p.name)
        jdir.mkdir(parents=True, exist_ok=True)
        print("\n【--fix】本次只建骨架：%s" % ("、".join(wrote) if wrote else "没有需要补的"))
        print("         卡片正文／导读正文／CHANGELOG 一律不自动改（改了就是替人做内容决定）；"
              "上面所有红黄项仍需人或 agent 自己修")
    elif a.report:
        atomic_write(a.report, normalize_block(report_text + "\n", "\n").encode("utf-8"))
        print("【--report】已写 1 个文件：%s" % a.report)
    return 1 if red else 0


# ---------------------------------------------------------------- 命令：classify
def cmd_classify(a):
    v = load_vault(a.vault, a.config)
    rules = v.config.get("project_type_rules") or []
    tried = [("name", a.name), ("prefix", a.path), ("url_prefix", a.url)]
    for i, r in enumerate(rules):
        for key, val in tried:
            if key == "prefix":
                hit = r.get(key) and a.path and a.path.replace("\\", "/").startswith(r[key].replace("\\", "/"))
            elif key == "url_prefix":
                hit = r.get(key) and a.url and a.url.startswith(r[key])
            else:
                hit = r.get(key) and a.name and r[key] == a.name
            if hit:
                return report_type(v, r, i, "%s 命中 %s" % (key, r[key]))
    d = v.config.get("project_type_default", "unknown")
    if d in ("", "unknown", None):
        print("FAIL 没有配置规则命中：name=%s／path=%s／url=%s" % (a.name, a.path or "-", a.url or "-"))
        print("     类型只从 %s 的 project_type_rules 来；本脚本不跑 git，所以「无上游／零提交」这类猜测不会发生。"
              % CONFIG_NAME)
        print("     下一步：问用户这是「自有工程」还是「工具／资料」，然后往 project_type_rules 里加一条明确规则。")
        return 2
    return report_type(v, {"type": d, "why": "配置里的 project_type_default 兜底"}, len(rules), "兜底默认值")


def report_type(v, rule, idx, matched):
    t = rule.get("type", "unknown")
    land_key = (v.config.get("landing_by_type") or {}).get(t)
    land = v.p(land_key).relative_to(v.root) if land_key else "(未配置落点)"
    print("项目类型＝%s｜依据＝%s 的 project_type_rules 第 %d 条｜命中方式＝%s｜落点＝%s"
          % (t, CONFIG_NAME, idx, matched, land))
    print("说明：为什么不是工具卡而是项目档案（或反之）由这条规则的 why 字段承担：%s" % rule.get("why", "（未写，建议补）"))
    return 0


# ---------------------------------------------------------------- 命令：seed-ledger
def cmd_seed_ledger(a):
    v = load_vault(a.vault, a.config)
    led = v.p("version_ledger")
    rows = ledger_rows(led)
    ch = v.p("changelog")
    if not ch.is_file():
        return die("库里没有 %s，取不到现有最大号" % ch)
    text, _ = read_text(ch)
    cands = [parse_ver(m.group(0)) for ln in text.splitlines() if ln.startswith("#") for m in VER_RE.finditer(ln)]
    cands = [c for c in cands if c]
    if not cands:
        return die("%s 里找不到形如 `## v1.2.3` 的版本标题，无从认领" % ch.name)
    top = max(cands)
    vid = fmt_ver(top, v.config["version_allocator"].get("prefix", "v"))
    if rows and ledger_state(led, vid)[0] + ledger_state(led, vid)[1] and not a.force:
        print("SKIP 台账已有 %s 的认领记录（%d 行）；要再补一行加 --force" % (vid, len(rows)))
        return 0
    lk = make_lock(v, a, "seed-ledger")
    try:
        if not led.is_file():
            atomic_write(led, normalize_block(LEDGER_HEADER, "\n").encode("utf-8"))
        row = "| %s | %s | %s | 从 CHANGELOG 现有最大号认领（迁移播种） | seeded |" % (vid, now_stamp(), a.host)
        append_ledger_row(v, row)
    finally:
        lk.release()
    print("OK 已播种最大号 %s；此后 apply 只从台账往上进位（%s）" % (vid, led))
    return 0


# ---------------------------------------------------------------- 命令：apply / resume / rollback / status
def read_plan(path):
    p = Path(str(path))
    if not p.is_file():
        raise ConfigError("plan 文件不存在：%s" % p)
    plan = json.loads(decode_text(read_bytes(p), str(p)))
    if not plan.get("steps"):
        raise ConfigError("plan 里没有任何 steps")
    return plan, p


def cmd_apply(a):
    v = load_vault(a.vault, a.config)
    plan, ppath = read_plan(a.plan)
    psha = hashlib.sha256(read_bytes(ppath)).hexdigest()
    jdir = v.journal_dir(a.journal_dir)
    for j in Journal.scan(jdir):
        if j.data.get("plan_sha256") == psha and j.data.get("state") == "done":
            print("ALREADY_DONE 这份 plan 已在 %s 入库（版本 %s，完成于 %s）；"
                  "要改内容请出新 plan，别重复入库占号位" % (Path(str(j.path)).name, j.data["vid"],
                                                        j.data.get("finished", "-")))
            return 0
    if a.dry_run:
        try:
            vid = plan.get("vid") or next_version(
                v, plan.get("vid_kind") or v.config["version_allocator"].get("bump", "patch"))
        except IntakeError as e:
            vid = "(拒绝入库：%s)" % e
        print("【dry-run】一个字节都不写。将分配 %s｜将动文件：%s" % (vid, "、".join(
            sorted(set(s["path"] for s in plan["steps"])))))
        print("           号位只在锁内认领，dry-run 不认领，所以真跑时仍以台账现值为准")
        return 0
    lk = make_lock(v, a, "apply")
    try:
        kind = plan.get("vid_kind") or v.config["version_allocator"].get("bump", "patch")
        pinned = plan.get("vid")
        if pinned:
            if parse_ver(pinned) is None:
                raise IntakeError("plan 里写了 vid=%r 但不是可解析的号位（形如 v1.2.3）⇒ 停手" % pinned)
            vid = pinned
        else:
            vid = next_version(v, kind)
        claim_version(v, vid, a.host, plan.get("title", ""))
        mapping = mapping_for(vid, plan, a.host)
        steps = []
        for s in plan["steps"]:
            s2 = subst(s, mapping)
            steps.append({"id": s2.get("id", s2["action"]), "action": s2["action"], "path": s2["path"],
                          "params": dict((k, vv) for k, vv in s2.items() if k not in ("id", "action", "path")),
                          "status": "pending", "note": ""})
        j = Journal.create(jdir, vid, plan, v.root, a.host, steps, psha, ppath)
        print("OK 已认领号位 %s（锁内追加台账一行；日志：%s，步骤 %d 个）" % (vid, Path(str(j.path)).name, len(steps)))
        return run_steps(v, j, jdir, mapping, plan, a)
    except CrashSim:
        lk.held = False       # 模拟进程被砍：锁留在盘上，由下一轮 resume 判陈旧后接管
        print("（测试钩子：--crash-after 命中 ⇒ 进程在此被砍，不释放锁、不跑收尾）")
        return CRASH_EXIT
    finally:
        lk.release()


def run_steps(v, j, jdir, mapping, plan, a):
    stepper = Stepper(v, jdir, j.data)
    crash_at = getattr(a, "crash_after", None)
    completed = 0
    for idx, st in enumerate(j.data["steps"]):
        if st["status"] in ("done", "skipped_exists"):
            continue
        step = subst(st["params"], mapping)
        step.update({"action": st["action"], "path": st["path"], "id": st["id"]})
        try:
            status, note = stepper.run(step)
        except (IntakeError, ConfigError) as e:
            status, note = "fail", str(e)
        st["status"] = {"done": "done", "skipped_exists": "skipped_exists", "fail": "failed"}[status]
        st["note"] = note
        j.data["state"] = "failed" if status == "fail" else "applying"
        j.save()
        print("  步 %-14s %-14s %s" % (st["id"], st["status"], note))
        if status == "fail":
            print("🔴 第 %d 步失败 ⇒ 停在这里：已写的不撤（有备份可 rollback），后面的步骤一个都不动。" % (idx + 1))
            return 1
        completed += 1
        if crash_at is not None and completed >= crash_at:
            raise CrashSim()
    return finish(v, j, plan, mapping, a)


def finish(v, j, plan, mapping, a):
    checks = verify_group(v, plan, j.data["vid"], j.data.get("host"))
    ok = all(c for c, _m in checks)
    for c, m in checks:
        print("  %s %s" % ("[一致]" if c else "[不一致]", m))
    j.data["state"] = "done" if ok else "inconsistent"
    if ok:
        j.data["finished"] = now_stamp()
    j.save()
    if not ok:
        print("🔴 成组不一致 ⇒ 先别提交。修完跑 resume（--journal %s），或 rollback --journal %s 还原。"
              % (j.data["vid"], j.data["vid"]))
        return 3
    print("OK 成组入库完成：%s" % j.data["vid"])
    return 0


def cmd_resume(a):
    v = load_vault(a.vault, a.config)
    jdir = v.journal_dir(a.journal_dir)
    cands = [j for j in Journal.scan(jdir) if j.unfinished()]
    if a.journal:
        cands = [j for j in cands if j.data["vid"] == a.journal]
        if not cands:
            jp = Path(str(jdir)) / ("%s.json" % slug(a.journal))
            if jp.is_file():
                j = Journal.load(jp)
                cands = [] if j.data.get("state") == "done" else [j]
    if not cands:
        print("没有待恢复的成组日志（%s）。要做首次入库请跑 apply。" % jdir)
        return 0
    rc = 0
    lk = make_lock(v, a, "resume")
    try:
        for j in cands:
            plan = plan_for(j, a)
            if plan is None:
                print("FAIL 日志 %s 找不到原始 plan ⇒ resume 不靠记忆重编内容；"
                      "把当时的 plan 用 --plan 传进来，或人工按正文 §6 补齐后跑 diagnose 复核" % Path(str(j.path)).name)
                rc = max(rc, 2)
                continue
            mapping = mapping_for(j.data["vid"], plan, j.data.get("host"))
            print("resume %s（state=%s，步骤 %d 个）" % (j.data["vid"], j.data["state"], len(j.data["steps"])))
            rc = max(rc, run_steps(v, j, jdir, mapping, plan, a))
        return rc
    finally:
        lk.release()


def plan_for(j, a):
    p = getattr(a, "plan", None) or j.data.get("plan_path")
    if not p or not Path(str(p)).is_file():
        return None
    plan, ppath = read_plan(p)
    sha = hashlib.sha256(read_bytes(ppath)).hexdigest()
    if sha != j.data.get("plan_sha256"):
        print("   [warn] plan 内容与首次入库时指纹不同（%s… ↔ %s…）⇒ 以盘上现件续跑，请人工确认这是你要的"
              % (j.data.get("plan_sha256", "")[:8], sha[:8]))
    j.data["plan_path"] = str(ppath)
    j.save()
    return plan


def cmd_rollback(a):
    v = load_vault(a.vault, a.config)
    jdir = v.journal_dir(a.journal_dir)
    jp = Path(str(jdir)) / ("%s.json" % slug(a.journal))
    if not jp.is_file():
        return die("日志不存在：%s（rollback 只认日志，不猜要撤哪一组）" % jp)
    j = Journal.load(jp)
    if j.data.get("state") == "rolled_back" and not a.force:
        print("该组已回滚过（%s）；要再走一遍加 --force" % a.journal)
        return 0
    lk = make_lock(v, a, "rollback")
    try:
        for st in j.data["steps"]:
            if st["action"] == "create" and st["status"] == "done":
                p = v.abs(st["path"])
                if p.is_file():
                    p.unlink()
                    print("  删回本次新建 %s" % st["path"])
        for b in reversed(j.data.get("backups", [])):
            src, dst = Path(str(b["bak"])), v.abs(b["rel"])
            if not src.is_file():
                return die("备份缺失：%s ⇒ 不敢还原，请人工核对" % src)
            raw = read_bytes(src)
            if hashlib.sha256(raw).hexdigest() != b["sha256"]:
                return die("备份指纹与登记时不符：%s ⇒ 不敢覆盖，请人工核对" % src)
            atomic_write(dst, raw)
            print("  还原 %s" % b["rel"])
        release_version(v, j.data["vid"], a.host)
        j.data["state"] = "rolled_back"
        j.data["rolled_back_at"] = now_stamp()
        j.save()
        print("OK 已回滚 %s；台账追加一行 released（保留原认领行，不改写历史）" % j.data["vid"])
        return 0
    finally:
        lk.release()


def cmd_status(a):
    v = load_vault(a.vault, a.config)
    jdir = v.journal_dir(a.journal_dir)
    js = Journal.scan(jdir)
    mark = {"pending": "-", "done": "+", "skipped_exists": "=", "failed": "X"}
    print("成组日志目录：%s（%d 份）" % (jdir, len(js)))
    bad = 0
    for j in js:
        if j.unfinished():
            bad += 1
        print("  %-13s %-12s %s" % (j.data["vid"], j.data["state"],
                                    " ".join("%s%s" % (mark.get(s["status"], "?"), s["id"]) for s in j.data["steps"])))
    led = v.p("version_ledger")
    rows = ledger_rows(led)
    print("版本台账：%s（%d 行；最大号 %s）" % (cfg_or(led), len(rows),
                                          fmt_ver(max([t for t, _s, _l in rows])) if rows else "-"))
    return 1 if bad else 0


def cfg_or(p):
    return str(p)


# ---------------------------------------------------------------- selftest（合成夹具，绝不碰真库）
def external_calls(src):
    """抓「偷偷去调外部命令」的三形状。字面量在这里被拆开写，所以本文件自己不会命中。"""
    pats = [re.compile("su" + "bprocess"), re.compile("os" + r"\.\s*sys" + "tem"), re.compile("Po" + "pen")]
    return [p.pattern for p in pats if p.search(src)]


def cmd_selftest(a):
    here = Path(__file__).resolve().parent.parent
    tmpl = here / "templates" / "最小可运行库"
    results = []
    made_tmp = not a.tmp
    keep = Path(str(a.tmp)).resolve() if a.tmp else Path(tempfile.mkdtemp(prefix="kb_intake_self_"))
    if a.tmp and keep.exists():
        # 同一个 --tmp 重跑必须整个重建：plan 文本是确定的（号位日期都写占位符），
        # 留着上一轮的成组日志，这一轮 apply 会被判 ALREADY_DONE，读数就不是那回事了。
        shutil.rmtree(str(keep))
    keep.mkdir(parents=True, exist_ok=True)

    def T(code, cond, note):
        results.append((bool(cond), note))
        print("  [%s] %s：%s" % ("PASS" if cond else "FAIL", code, note))

    print("自测夹具根：%s（全部合成，不碰任何真库）" % keep)
    try:
        T("T01", tmpl.is_dir(), "随包的空白示例库在场（templates/最小可运行库）")
        if not tmpl.is_dir():
            return 1
        fx = fresh(tmpl, keep / "fixture")
        jd = keep / "journal"
        base = ["--vault", str(fx), "--journal-dir", str(jd), "--host", "selftest"]

        snap0 = dir_snapshot(fx)
        T("T02", _call(["diagnose"] + base) == 0, "模板库默认 diagnose 退出码 0（红 0）")
        T("T03", dir_snapshot(fx) == snap0, "默认 diagnose 全库快照逐字节相同（只读边界）")
        T("T04", not (jd / ".intake.lock").exists(), "diagnose 不留锁文件")
        rep = keep / "report.md"
        T("T05", _call(["diagnose"] + base + ["--report", str(rep)]) == 0 and rep.is_file(),
          "--report 显式才落文件（只落这一个）")
        rtext = decode_text(read_bytes(rep), str(rep))
        T("T06", "[[" not in rtext, "报告正文零双链（落进库里不会给体检添死链）")
        T("T07", dir_snapshot(fx) == snap0, "带 --report 时库内一个文件都没多／没改")

        T("T08", _call(["diagnose", "--vault", str(keep / "not_a_vault")]) == 2, "库根不存在 ⇒ 退 2")
        (keep / "empty_vault").mkdir(exist_ok=True)
        T("T09", _call(["diagnose", "--vault", str(keep / "empty_vault")]) == 2,
          "空目录里没有 %s ⇒ 退 2（不默认当前目录）" % CONFIG_NAME)

        noled = fresh(tmpl, keep / "no_ledger")
        (noled / "6-outputs" / "version_ledger.md").unlink()
        pl = sample_plan(keep, "1-资产卡/示例组-空台账探针.md", vault_root=noled)
        snap_n = dir_snapshot(noled)
        T("T10", _call(["apply", "--vault", str(noled), "--journal-dir", str(jd), "--plan", pl]) == 2
            and dir_snapshot(noled) == snap_n, "台账缺失 ⇒ apply 拒绝且不落任何卡片")
        rc, out = _cap(["apply", "--vault", str(fx), "--journal-dir", str(jd), "--plan", pl, "--dry-run"])
        T("T11", rc == 0 and "dry-run" in out and dir_snapshot(fx) == snap0, "--dry-run 快照不变并打印将要分配的号")

        fake = keep / "fake_repo"
        (fake / ".git").mkdir(parents=True, exist_ok=True)
        atomic_write(fake / ".git" / "config",
                     b'[remote "origin"]\n\tdurl = https://github.com/x/y.git\n')
        atomic_write(fake / "LICENSE", b"Apache-2.0\n")
        rc, out = _cap(["classify"] + base + ["--name", "示例资产", "--path", str(fake)])
        T("T12", rc == 0 and "tool" in out, "目录里有 upstream 线索时仍按配置判 tool（类型不靠猜）")
        rc, out = _cap(["classify"] + base + ["--name", "配置里没登记的对象"])
        T("T13", rc == 2 and "问用户" in out, "规则没命中且默认 unknown ⇒ 退 2 要人补配置")
        src = decode_text(read_bytes(Path(__file__)), __file__)
        hits = external_calls(src)
        synth = "import su" + "bprocess\nx = os.sy" + "stem('ls')\nP = Po" + "pen([])\n"
        T("T14", not hits, "脚本源码零外部命令调用（不跑 git ⇒ 既不看 upstream 也不读 HEAD），命中：%s"
          % ("、".join(hits) if hits else "无"))
        T("T14b", len(external_calls(synth)) == 3,
          "这把尺子能红：把三种调用写成样串必须全被抓到（否则 T14 是恒绿的废尺）")

        led = fx / "6-outputs" / "version_ledger.md"
        rc, out = _cap(["seed-ledger"] + base)
        T("T15", rc == 0 and "seeded" in decode_text(read_bytes(led), str(led)),
          "seed-ledger 从 CHANGELOG 现有最大号播种台账")

        pl1 = sample_plan(keep, "1-资产卡/示例组-自测资产.md", title="自测资产", vault_root=fx)
        rc, out = _cap(["apply"] + base + ["--plan", pl1])
        card = fx / "1-资产卡" / "示例组-自测资产.md"
        gtext = decode_text(read_bytes(fx / "0-入口" / "AI导读.md"), "g")
        ctext = decode_text(read_bytes(fx / "CHANGELOG.md"), "c")
        ltext = decode_text(read_bytes(led), str(led))
        T("T16", rc == 0, "完整成组入库退出码 0")
        T("T17", card.is_file(), "卡片已建")
        T("T18", gtext.count("示例组-自测资产") == 1, "导读里这张卡恰好出现一次（入卡即入表，非孤儿）")
        T("T19", ctext.count("## v") == 3 and re.search(r"^## v[\d.]+（[\d-]+）自测资产$", ctext, re.M) is not None,
          "CHANGELOG 恰好新增一节且标题形状正确")
        T("T20", "## v1.0.0（2026-10-11）空白示例库首版" in ctext and "## v0.9.0（2026-10-11）模板骨架" in ctext,
          "邻居版本节原文还在（新节插在上方，不圈进替换范围）")
        T("T21", ltext.count("| claimed |") == 1, "台账恰好一行 claimed（单一写入点认领）")

        pl2 = sample_plan(keep, "1-资产卡/示例组-自测资产二.md", title="自测资产二",
                          guide_before=guide_row_of(fx), vault_root=fx)
        rc2, out2 = _cap(["apply"] + base + ["--plan", pl2])
        ltext2 = decode_text(read_bytes(led), str(led))
        v1 = re.search(r"已认领号位 (v[\d.]+)", out).group(1)
        v2 = re.search(r"已认领号位 (v[\d.]+)", out2).group(1)
        T("T22", rc2 == 0 and parse_ver(v2) > parse_ver(v1), "第二次入库领到更大且不同的号位（%s→%s）" % (v1, v2))
        before = ltext2.count("| claimed |")
        rc3, out3 = _cap(["apply"] + base + ["--plan", pl2])
        T("T23", rc3 == 0 and "ALREADY_DONE" in out3
            and decode_text(read_bytes(led), str(led)).count("| claimed |") == before,
          "同一份 plan 重跑 ⇒ 报 ALREADY_DONE 且不重复占号位")

        fxc = fresh(tmpl, keep / "crash")
        jdc = keep / "journal_crash"
        basec = ["--vault", str(fxc), "--journal-dir", str(jdc), "--host", "selftest"]
        pl3 = sample_plan(keep, "1-资产卡/示例组-中断资产.md", title="中断资产", vault_root=fxc)
        rc4, out4 = _cap(["apply"] + basec + ["--plan", pl3, "--crash-after", "1"])
        T("T24", rc4 == CRASH_EXIT, "中断注入：做完第 1 步就把进程砍掉（退出码 70）")
        g3 = decode_text(read_bytes(fxc / "0-入口" / "AI导读.md"), "g3")
        T("T25", (fxc / "1-资产卡" / "示例组-中断资产.md").is_file() and "示例组-中断资产" not in g3,
          "中断后确实是互相矛盾的半成品（卡片在、导读没登记）")
        T("T26", (jdc / ".intake.lock").is_file(), "中断把锁留在盘上（真实断电就是这样）")
        rc5, out5 = _cap(["resume", "--vault", str(fxc), "--journal-dir", str(jdc), "--lock-stale", "0"])
        T("T27", rc5 == 0 and "接管" in out5, "resume 接管残留锁并续跑成功（不重编内容，用日志里的 plan 路径）")
        g4 = decode_text(read_bytes(fxc / "0-入口" / "AI导读.md"), "g4")
        T("T28", g4.count("示例组-中断资产") == 1, "续跑后导读只出现一次（幂等：没有重复插行）")
        T("T29", _call(["diagnose", "--vault", str(fxc), "--journal-dir", str(jdc)]) == 0, "恢复后 diagnose 全绿")
        T("T30", not [j for j in Journal.scan(jdc) if j.unfinished()], "没有遗留未完成的成组日志")

        lockp = jd / ".intake.lock"
        jd.mkdir(parents=True, exist_ok=True)
        atomic_write(lockp, json.dumps({"pid": 999999, "owner": "别人", "ts": now_stamp()}).encode("utf-8"))
        snapL = dir_snapshot(fx)
        rc6, out6 = _cap(["apply", "--vault", str(fx), "--journal-dir", str(jd), "--plan",
                          sample_plan(keep, "1-资产卡/示例组-锁探针.md",
                                      guide_before=guide_row_of(fx), vault_root=fx), "--host", "selftest"])
        T("T31", rc6 == 4 and dir_snapshot(fx) == snapL, "活锁占用 ⇒ 退 4 且快照不变（不硬闯）")
        stale = (datetime.datetime.now() - datetime.timedelta(seconds=400)).isoformat(timespec="seconds")
        atomic_write(lockp, json.dumps({"pid": 999999, "owner": "别人", "ts": stale}).encode("utf-8"))
        rc7, out7 = _cap(["apply", "--vault", str(fx), "--journal-dir", str(jd), "--plan",
                          sample_plan(keep, "1-资产卡/示例组-陈旧锁探针.md",
                                      guide_before=guide_row_of(fx), vault_root=fx), "--host", "selftest",
                          "--lock-stale", "60"])
        T("T32", rc7 == 0 and "接管" in out7, "超陈旧线的残留锁被接管并跑完（不卡死）")
        try:
            lockp.unlink()
        except OSError:
            pass

        fxa = fresh(tmpl, keep / "anchor")
        jda = keep / "journal_anchor"
        bpla = sample_plan(keep, "1-资产卡/示例组-锚点坏.md", vault_root=fxa)
        d = json.loads(decode_text(read_bytes(bpla), bpla))
        d["steps"][1]["before"] = "| 这个组行根本不在文件里 |"
        atomic_write(bpla, json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8"))
        rc8, out8 = _cap(["apply", "--vault", str(fxa), "--journal-dir", str(jda), "--plan", bpla, "--host", "selftest"])
        T("T33", rc8 == 1 and "锚点行没找到" in out8, "锚点行找不到 ⇒ 该步失败并停住（退 1，不猜）")
        vid8 = re.search(r"已认领号位 (v[\d.]+)", out8).group(1)
        rc9, out9 = _cap(["rollback", "--vault", str(fxa), "--journal-dir", str(jda), "--journal", vid8,
                          "--host", "selftest"])
        T("T40", rc9 == 0 and not (fxa / "1-资产卡" / "示例组-锚点坏.md").exists(), "rollback 删掉本次新建的卡片")
        T("T41", "示例组-锚点坏" not in decode_text(read_bytes(fxa / "0-入口" / "AI导读.md"), "a")
            and "锚点坏" not in decode_text(read_bytes(fxa / "CHANGELOG.md"), "b"), "rollback 后导读与 CHANGELOG 无残留")
        led8 = decode_text(read_bytes(fxa / "6-outputs" / "version_ledger.md"), "l8")
        T("T42", "| released |" in led8 and "| claimed |" in led8, "rollback 追加 released（保留认领行，不改写历史）")

        fxg = fresh(tmpl, keep / "group")
        jdg = keep / "journal_group"
        plg = sample_plan(keep, "1-资产卡/新探针组-探针.md", title="探针资产", group="新探针组", new_group=True,
                          guide_new_row=True, vault_root=fxg)
        rcG, outG = _cap(["apply", "--vault", str(fxg), "--journal-dir", str(jdg), "--plan", plg, "--host", "selftest"])
        T("T43", rcG == 3 and "组清单" in outG, "新建组只写了总索引表、没登记组清单 ⇒ 复核判不一致（退 3）")
        T("T44", (fxg / "1-资产卡" / "新探针组-探针.md").is_file(), "不一致时已写的文件不回滚（留给 resume 补，不假装干净）")

        fxd = fresh(tmpl, keep / "deadlink")
        jdd = keep / "journal_dead"
        pld = sample_plan(keep, "1-资产卡/示例组-死链卡.md", title="死链卡", extra_link="不存在的页",
                          vault_root=fxd)
        rcD, outD = _cap(["apply", "--vault", str(fxd), "--journal-dir", str(jdd), "--plan", pld, "--host", "selftest"])
        T("T45", rcD == 3 and "双链" in outD, "卡片双链指向不存在的页 ⇒ 复核判不一致（退 3）")

        fxb = fresh(tmpl, keep / "badfm")
        jdb = keep / "journal_badfm"
        plb = sample_plan(keep, "1-资产卡/示例组-坏字段.md", title="坏字段", vault_root=fxb)
        db = json.loads(decode_text(read_bytes(plb), plb))
        db["steps"][0]["content"] = db["steps"][0]["content"].replace("license:", "licen_se:")
        atomic_write(plb, json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8"))
        rcB, outB = _cap(["apply", "--vault", str(fxb), "--journal-dir", str(jdb), "--plan", plb, "--host", "selftest"])
        T("T46", rcB == 3 and "缺字段行" in outB, "卡片少一个字段行 ⇒ 复核判不一致（退 3）")

        fxf = fresh(tmpl, keep / "fixmode")
        (fxf / "6-outputs" / "version_ledger.md").unlink()
        (fxf / "6-outputs" / "usage.log").unlink()
        bad_card = fxf / "1-资产卡" / "示例组-示例资产.md"
        bc0 = decode_text(read_bytes(bad_card), "bc")
        atomic_write(bad_card, bc0.replace("license: MIT", "license: ").encode("utf-8"))
        snap6 = dir_snapshot(fxf)
        rcC, outC = _cap(["diagnose", "--vault", str(fxf), "--journal-dir", str(keep / "journal_fix"), "--fix"])
        T("T47", (fxf / "6-outputs" / "version_ledger.md").is_file()
            and (fxf / "6-outputs" / "usage.log").is_file(), "--fix 只补缺失的骨架文件（台账＋留痕流水）")
        T("T48", decode_text(read_bytes(bad_card), "bc2") == bc0.replace("license: MIT", "license: "),
          "--fix 不自动改卡片正文（内容决定不替人做）")
        T("T49", sorted(k for k in dir_snapshot(fxf) if k not in snap6) ==
            ["6-outputs/usage.log", "6-outputs/version_ledger.md"], "--fix 除这两个骨架文件外没多写任何一个")
        T("T50", rcC == 1 and "字段有行但空值：license" in outC,
          "卡片字段「有行但值为空」时 diagnose 点名到字段并判红（退 1）——--fix 不会把它盖成绿")

        # 号位只认台账：把 CHANGELOG 顶部改成更大的号，分配结果不该变
        fxh = fresh(tmpl, keep / "head")
        jdh = keep / "journal_head"
        baseh = ["--vault", str(fxh), "--journal-dir", str(jdh), "--host", "selftest"]
        _call(["seed-ledger"] + baseh)
        chp = fxh / "CHANGELOG.md"
        chtext, chnl = read_text(chp)
        atomic_write(chp, chtext.replace("## v1.0.0（2026-10-11）空白示例库首版",
                                         "## v99.9.9（2026-10-12）别人改了还没提交").encode("utf-8"))
        rcH1, outH1 = _cap(["diagnose"] + baseh)
        plh = sample_plan(keep, "1-资产卡/示例组-号位探针.md", title="号位探针", vault_root=fxh)
        rcH, outH = _cap(["apply"] + baseh + ["--plan", plh])
        vidH = re.search(r"已认领号位 (v[\d.]+)", outH).group(1)
        T("T51", parse_ver(vidH) == (1, 1, 0), "CHANGELOG 顶部被改成 v99.9.9 后仍按台账进位（%s），不读工作区猜号" % vidH)
        T("T52", "高于台账" in outH1, "diagnose 黄报「CHANGELOG 最大号高于台账」并指路 seed-ledger")

        fxdup = fresh(tmpl, keep / "dupclaim")
        jdup = keep / "journal_dup"
        ledp = fxdup / "6-outputs" / "version_ledger.md"
        lt, ln2 = read_text(ledp)
        atomic_write(ledp, (lt + "| v1.0.5 | x | a | 手工 | claimed |\n| v1.0.5 | y | b | 手工 | claimed |\n").encode("utf-8"))
        rcDD, outDD = _cap(["diagnose", "--vault", str(fxdup), "--journal-dir", str(jdup)])
        T("T53", rcDD == 1 and "同号多次" in outDD and "v1.0.5" in outDD,
          "台账里同号被认领两次 ⇒ diagnose 判红（并发撞号的探测器本身能用）")
        rcAP, outAP = _cap(["apply", "--vault", str(fxdup), "--journal-dir", str(jdup), "--plan",
                            sample_plan(keep, "1-资产卡/示例组-撞号探针.md", vid="v1.0.5",
                                        vault_root=fxdup), "--host", "selftest"])
        T("T54", rcAP == 4 and "不重复认领" in outAP,
          "plan 钉了台账里已被认领的号 ⇒ 停手不重复占号（退 4，一个文件都不写）")
        T("T54b", not (fxdup / "1-资产卡" / "示例组-撞号探针.md").exists(),
          "撞号停手时卡片确实没落盘（拒绝发生在写之前，不是写完再后悔）")

        fxp = fresh(tmpl, keep / "partial")
        jdp = keep / "journal_partial"
        basep = ["--vault", str(fxp), "--journal-dir", str(jdp), "--host", "selftest"]
        plp = sample_plan(keep, "1-资产卡/示例组-半程资产.md", title="半程资产", vault_root=fxp)
        rcP1, outP1 = _cap(["apply"] + basep + ["--plan", plp, "--crash-after", "2"])
        T("T55", rcP1 == CRASH_EXIT, "中断注入：做完第 2 步被砍（两步之后的形状）")
        rcP2, outP2 = _cap(["resume", "--vault", str(fxp), "--journal-dir", str(jdp), "--lock-stale", "0"])
        g5 = decode_text(read_bytes(fxp / "0-入口" / "AI导读.md"), "g5")
        c5 = decode_text(read_bytes(fxp / "CHANGELOG.md"), "c5")
        T("T56", rcP2 == 0 and g5.count("示例组-半程资产") == 1 and c5.count("半程资产") >= 1,
          "第二步失败后重跑：卡片／导读／索引三者最终一致且不重复")
        T("T57", len(re.findall(r"^## v", c5, re.M)) == 3, "恢复后 CHANGELOG 只有 3 节（没有因为重跑多插一节）")

        # ↓ 三条由变异探针（evidence/卡10/_mutation_probe.py）逼出来的断言：前一批里
        #   「幂等标记」「卡片空值」「分母闸」的旧断言都是恒绿的——摘掉防线它们照样全绿，等于没守。
        bare = keep / "bare_vault"
        bare.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(tmpl / "intake_config.json"), str(bare / "intake_config.json"))
        rcBare, outBare = _cap(["diagnose", "--vault", str(bare),
                                "--journal-dir", str(keep / "journal_bare")])
        T("T58", rcBare == 1 and "分母为空" in outBare and "结论：🟢 绿" not in outBare,
          "必需路径一个都没找到 ⇒ 明说「分母为空、判据作废」并退 1，不靠一堆「缺失」凑成看似通过的报告")

        fxr = fresh(tmpl, keep / "replay")
        jdr = keep / "journal_replay"
        baser = ["--vault", str(fxr), "--journal-dir", str(jdr), "--host", "selftest"]
        plr = sample_plan(keep, "1-资产卡/示例组-重放卡.md", title="重放卡", vault_root=fxr)
        rcR1, outR1 = _cap(["apply"] + baser + ["--plan", plr])
        vidr = re.search(r"已认领号位 (v[\d.]+)", outR1).group(1)
        jpr = jdr / ("%s.json" % slug(vidr))
        jraw = json.loads(decode_text(read_bytes(jpr), jpr))
        for _st in jraw["steps"]:
            if _st["id"] == "changelog":
                _st["status"] = "pending"
        jraw["state"] = "failed"
        atomic_write(jpr, json.dumps(jraw, ensure_ascii=False, indent=2).encode("utf-8"))
        rcR2, outR2 = _cap(["resume", "--vault", str(fxr), "--journal-dir", str(jdr), "--lock-stale", "0"])
        c6 = decode_text(read_bytes(fxr / "CHANGELOG.md"), "c6")
        T("T59", rcR1 == 0 and rcR2 == 0 and len(re.findall(r"^## v", c6, re.M)) == 3
            and "标记行已在（不重复插）" in outR2,
          "磁盘已写好但日志没标 done 的重放窗口：幂等标记认出「这节已经在」⇒ 再跑一次不多插一节（仍 3 节）")

        fxs = fresh(tmpl, keep / "sameafter")
        jds = keep / "journal_sameafter"
        pls = sample_plan(keep, "1-资产卡/示例组-可疑卡.md", title="可疑卡", vault_root=fxs)
        dsw = json.loads(decode_text(read_bytes(pls), pls))
        dsw["steps"][1]["after"] = dsw["steps"][1]["before"]
        atomic_write(pls, json.dumps(dsw, ensure_ascii=False, indent=2).encode("utf-8"))
        gp_s = fxs / "0-入口" / "AI导读.md"
        gpre = read_bytes(gp_s)
        rcS, outS = _cap(["apply", "--vault", str(fxs), "--journal-dir", str(jds), "--plan", pls,
                          "--host", "selftest"])
        T("T60", rcS == 1 and "状态可疑" in outS and read_bytes(gp_s) == gpre,
          "新行已在而锚点也还在 ⇒ 预写判「状态可疑」当场停手，导读一个字节都没被改过")

        # 顶部倒序：连着入两枚卡，后一枚必须压在前一枚上面（锚点写死模板行会立刻变成正序）
        fxo = fresh(tmpl, keep / "order")
        jdo = keep / "journal_order"
        baseo = ["--vault", str(fxo), "--journal-dir", str(jdo), "--host", "selftest"]
        for _nm in ("一号", "二号"):
            _call(["apply"] + baseo + ["--plan", sample_plan(keep, "1-资产卡/示例组-序%s.md" % _nm,
                                                             title="序%s" % _nm, vault_root=fxo)])
        heads = re.findall(r"^## (v[\d.]+)", decode_text(read_bytes(fxo / "CHANGELOG.md"), "c8"), re.M)
        T("T61", heads == sorted(heads, reverse=True) and heads[:2] == ["v1.2.0", "v1.1.0"],
          "连入两枚卡后版本史保持顶部倒序（%s）⇒ 锚点取自现状，不是写死模板第一行" % "→".join(heads))

        vrel = load_vault(fx, None)
        jrel = str(vrel.journal_dir("6-outputs/rel_demo")).replace("\\", "/")
        T("T62", jrel.endswith("fixture/6-outputs/rel_demo"),
          "相对的 --journal-dir 按**库内相对路径**解析（不是当前工作目录）⇒ 拿相对路径跑夹具会静默换位置")

        # ── 档位对账两条（2026-10-13：拿 references/必需文件与工具清单.md 第二节逐行「抽掉这一件会怎样」撞出来的）
        # ① 缺首页：卡片面包屑指空 ⇒ 死链该判红。旧实现把死链目标用双链语法原样写进诊断文案，
        #    撞上「报告零双链」守卫 ⇒ 整份报告被吞成一行「内部错误」——最需要看诊的形状恰好看不到。
        fx_nohome = fresh(tmpl, keep / "no_home")
        (fx_nohome / "0-入口" / "首页.md").unlink()
        rc_nh, out_nh = _cap(["diagnose", "--vault", str(fx_nohome), "--journal-dir", str(jd), "--host", "selftest"])
        T("T63", "内部错误" not in out_nh and "双链死链" in out_nh and "首页" in out_nh,
          "缺首页 ⇒ 诊断报告照常打印并点名死链目标（旧形状＝整份被守卫吞成「内部错误」，一条都看不到）")
        T("T63b", rc_nh == 1 and "🔴 红" in out_nh,
          "同一例判红退 1（死链是真信号，不降成黄）：退码=%d" % rc_nh)

        # ② 守卫不能变成死代码：链接目标本身混进双链语法时，报告仍要出得来、落盘文件仍要零双链
        fx_smug = fresh(tmpl, keep / "smuggled_link")
        gp_sm = fx_smug / "0-入口" / "AI导读.md"
        atomic_write(gp_sm, normalize_block(decode_text(read_bytes(gp_sm), "gsm") +
                                            "\n参考 [[前[[后]] 这一页。\n", "\n").encode("utf-8"))
        smug_rep = keep / "report_smuggled.md"
        rc_sm, out_sm = _cap(["diagnose", "--vault", str(fx_smug), "--journal-dir", str(jd), "--host", "selftest",
                              "--report", str(smug_rep)])
        smug_txt = decode_text(read_bytes(smug_rep), "srep") if smug_rep.is_file() else ""
        T("T63c", rc_sm == 1 and "内部错误" not in out_sm and "抹平" in out_sm and "[[" not in smug_txt,
          "诊断文案里混进双链语法 ⇒ 抹成全角后照常输出并留告示，落盘报告仍零双链（守卫不再吞掉整份报告）")

        # ③ 缺留痕流水：references 第二节承诺「报黄」，脚本得真报这一档
        fx_nousage = fresh(tmpl, keep / "no_usage")
        (fx_nousage / "6-outputs" / "usage.log").unlink()
        rc_ul, out_ul = _cap(["diagnose", "--vault", str(fx_nousage), "--journal-dir", str(jd), "--host", "selftest"])
        T("T64", rc_ul == 0 and "🟡 黄" in out_ul and "usage.log" in out_ul,
          "缺 usage.log ⇒ 退 0 报黄并点名文件（与文档承诺同档）：退码=%d" % rc_ul)
        rc_ulf, out_ulf = _cap(["diagnose", "--fix", "--vault", str(fx_nousage), "--journal-dir", str(jd),
                                "--host", "selftest"])
        rc_ul2, out_ul2 = _cap(["diagnose", "--vault", str(fx_nousage), "--journal-dir", str(jd), "--host", "selftest"])
        T("T64b", (fx_nousage / "6-outputs" / "usage.log").is_file() and "usage.log" in out_ulf and "🟢 绿" in out_ul2,
          "--fix 建出 usage.log 骨架并点名它，复跑转绿（黄项被真修掉，不是被静音）")

        # ④ references 第二节剩下三行「报黄」档位：文档写了哪一档，脚本就得真给哪一档
        for _code, _rel, _label in (("T65a", "0-入口/模板/资产卡模板.md", "资产卡模板"),
                                    ("T65b", "AGENTS.md", "库规矩文件"),
                                    ("T65c", "3-本机资产/AI工具地图.md", "本机资产地图")):
            fx_opt = fresh(tmpl, keep / ("opt_" + _label))
            (fx_opt / _rel).unlink()
            rc_opt, out_opt = _cap(["diagnose", "--vault", str(fx_opt), "--journal-dir", str(jd), "--host", "selftest"])
            T(_code, rc_opt == 0 and "🟡 黄" in out_opt and _label in out_opt and _rel in out_opt,
              "缺 %s ⇒ 退 0 报黄并点名（文档第二节承诺的那一档，不是沉默通过）" % _rel)

        # ⑤ 文档第二节「报红」那行的真条件＝配置声明了落点键却没给路径
        fx_land = fresh(tmpl, keep / "no_landing")
        cfg_p = fx_land / "intake_config.json"
        cfg_d = json.loads(decode_text(read_bytes(cfg_p), "cfgp"))
        cfg_d["paths"]["project_archive"] = ""
        atomic_write(cfg_p, json.dumps(cfg_d, ensure_ascii=False, indent=2).encode("utf-8"))
        rc_l, out_l = _cap(["diagnose", "--vault", str(fx_land), "--journal-dir", str(jd), "--host", "selftest"])
        T("T66", rc_l == 1 and "落点" in out_l and "user_project" in out_l,
          "paths.project_archive 被声明为空 ⇒ 判红并点名类型 user_project（退码=%d）" % rc_l)

        n_pass = sum(1 for ok, _n in results if ok)
        n_fail = len(results) - n_pass
        print("\n自测结论：断言 %d 条：PASS=%d FAIL=%d" % (len(results), n_pass, n_fail))
        if n_fail:
            print("未通过：")
            for ok, note in results:
                if not ok:
                    print("   - %s" % note)
        return 1 if n_fail else 0
    finally:
        if made_tmp and not a.keep:
            shutil.rmtree(str(keep), ignore_errors=True)
            print("（临时夹具已清理：%s）" % keep)


def guide_row_of(vault_root, group="示例组"):
    """出 plan 前读「这一行现在的原文」——真实入库必须这样，plan 的锚点不能凭记忆抄。"""
    text, _ = read_text(Path(str(vault_root)) / "0-入口" / "AI导读.md")
    for ln in text.splitlines():
        if line_body(ln).startswith("| %s |" % group):
            return line_body(ln)
    raise ConfigError("导读里没有组「%s」的行 ⇒ 文件已被别人改过或组名写错，重读再出 plan" % group)


def changelog_anchor(vault_root):
    """版本史锚点＝文件里**当前第一节**的原文。写死某一行就是「凭记忆抄锚点」：
    第二次入库会把新节插到上一次那节的**下面**，顶部倒序当场被打乱（CLI 演练实测到过）。"""
    text, _ = read_text(Path(str(vault_root)) / "CHANGELOG.md")
    for ln in text.splitlines():
        if re.match(r"^## v", line_body(ln)):
            return line_body(ln)
    raise ConfigError("版本史里没有以 `## v` 开头的节标题 ⇒ 锚点只能现读，先去 CHANGELOG 看现状再出 plan")


def fresh(tmpl, dst):
    if dst.exists():
        shutil.rmtree(str(dst))
    shutil.copytree(str(tmpl), str(dst))
    return dst


GUIDE_ROW = ("| 示例组 | 示例 / 演示 / 模板 / 空白库 | 1-资产卡/示例组-示例资产.md ＋ "
             "0-入口/模板/资产卡模板.md | 空白示例库自带的演示组 |")


def row_with_card(row, card_rel):
    """把新卡并进导读组行的「目标」列（第 3 格），同组并列、不覆盖旧条目。"""
    cells = row.split("|")
    tgt = cells[3].strip()
    cells[3] = " %s ＋ %s " % (card_rel, tgt) if tgt and tgt != "-" else " %s " % card_rel
    return "|".join(cells)


def sample_plan(keep, card_rel, title="自测资产", group="示例组", new_group=False,
                guide_new_row=False, extra_link="", guide_before=None, vid=None,
                vault_root=None):
    """合成 plan：卡片正文、导读行、CHANGELOG 节都在这儿造，全程不落真库。

    guide_before＝这一行的**当前原文**（第一次入库是模板自带行，第二次就得传上一次改完之后的形状），
    锚点按现状出才是诚实做法——拿旧文本当锚点去改已经变过的行，正是本卡要根除的失效模式。
    vault_root 必填：导读行与版本史锚点都由它现读；版本史锚点写死会让第二次入库插到第一次下面（倒序被打乱）。
    """
    if vault_root is None:
        raise IntakeError("sample_plan 必须给 vault_root ⇒ 两处锚点都要从现状读，不许凭记忆抄")
    stem = Path(card_rel).stem
    card = ("---\n"
            "type: asset-card\n"
            "group: %s\n"
            "source: 仅在线\n"
            "local:\n"
            "license: MIT\n"
            "status: active\n"
            "superseded_by:\n"
            "last_checked: @DATE@\n"
            "tags: [资产卡, %s]\n"
            "---\n\n"
            "> 🏠 [[首页]] ｜ 📖 [[AI导读]] ｜ 📁 1-资产卡\n\n"
            "# %s\n\n"
            "一句话：这是 selftest 造的合成卡片，只用来验成组更新、复核与中断恢复。\n"
            "%s" % (group, group, title, ("\n双链探针：[[%s]]\n" % extra_link) if extra_link else ""))
    before = guide_before or guide_row_of(vault_root)
    after = row_with_card(before, card_rel)
    cl_anchor = changelog_anchor(vault_root)
    steps = [{"id": "card", "action": "create", "path": card_rel, "content": card},
             {"id": "changelog", "action": "insert_before", "path": "CHANGELOG.md",
              "anchor": cl_anchor, "marker": "@HEADER@",
              "block": "@HEADER@\n\n- 优化了什么：无（合成演练）\n- 增加了什么：卡片《%s》\n"
                       "- 解决了什么问题：无\n\n" % title}]
    if guide_new_row:
        steps.insert(1, {"id": "guide_row", "action": "insert_before", "path": "0-入口/AI导读.md",
                         "anchor": GUIDE_ROW,
                         "marker": "| %s | 探针 / 演练 | %s | 自测新造组 |" % (group, card_rel),
                         "block": "| %s | 探针 / 演练 | %s | 自测新造组 |\n" % (group, card_rel)})
    else:
        steps.insert(1, {"id": "guide_row", "action": "replace_line", "path": "0-入口/AI导读.md",
                         "before": before, "after": after})
    plan = {"title": title, "group": group, "card_path": card_rel, "new_group": new_group,
            "changelog_header": "## @VERSION@（@DATE@）@TITLE@", "steps": steps}
    if vid:
        plan["vid"] = vid
    d = keep / ("plans_%s" % slug(stem))
    d.mkdir(parents=True, exist_ok=True)
    p = d / "plan.json"
    atomic_write(p, json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8"))
    return str(p)


class Capture(object):
    def __init__(self):
        self._b = io.StringIO()

    def write(self, s):
        self._b.write(s)

    def flush(self):
        pass

    def isatty(self):
        return False

    def value(self):
        return self._b.getvalue()


def _cap(argv):
    buf = Capture()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = main(argv)
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        sys.stdout = old
    return rc, buf.value()


def _call(argv):
    return _cap(argv)[0]


# ---------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(prog="kb_intake.py",
                                 description="知识库入库：三模式诊断 / 成组原子更新 / 中断恢复 / 单一写入点版本号")
    ap.add_argument("--version", action="version", version="kb_intake %s" % VERSION)
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="库根（必填，不拿当前目录当库根）")
    common.add_argument("--config", help="入库配置文件（默认 <库根>/%s）" % CONFIG_NAME)
    common.add_argument("--journal-dir", dest="journal_dir", help="成组日志目录（默认取配置 journal_dir）")
    common.add_argument("--host", default="-", help="宿主名，写进台账与日志")
    common.add_argument("--lock-stale", dest="lock_stale", type=int, default=120, help="残留锁抢占线（秒，默认 120）")

    d = sub.add_parser("diagnose", parents=[common], help="默认只读诊断；--report 才出文件；--fix 才写盘")
    d.add_argument("--report", help="显式把报告写到这个路径")
    d.add_argument("--fix", action="store_true", help="显式修复：只建声明可建的骨架文件")
    c = sub.add_parser("classify", parents=[common], help="按配置判项目类型（不跑 git）")
    c.add_argument("--name", help="对象名／卡名")
    c.add_argument("--path", help="本地路径")
    c.add_argument("--url", help="上游地址")
    s = sub.add_parser("seed-ledger", parents=[common], help="从 CHANGELOG 现有最大号播种版本台账（老库迁移用）")
    s.add_argument("--force", action="store_true", help="已有台账时仍再补一行 seeded")
    a = sub.add_parser("apply", parents=[common], help="成组入库：锁内认领号位 → 记日志 → 逐步写 → 一致性复核")
    a.add_argument("--plan", required=True, help="plan 文件（JSON）")
    a.add_argument("--dry-run", dest="dry_run", action="store_true", help="只说将要做什么，一个字节都不写")
    a.add_argument("--crash-after", dest="crash_after", type=int,
                   help="测试钩子：做完 N 步就按进程被砍处理（退出码 %d）" % CRASH_EXIT)
    r = sub.add_parser("resume", parents=[common], help="续跑未完成或已判不一致的组（幂等）")
    r.add_argument("--journal", help="只处理这个版本号的组")
    r.add_argument("--plan", help="原始 plan 路径（默认取日志里登记的）")
    sub.add_parser("status", parents=[common], help="列出成组日志与台账现状")
    rb = sub.add_parser("rollback", parents=[common], help="按日志登记的备份逐字节还原（只认日志）")
    rb.add_argument("--journal", required=True, help="要回滚的版本号")
    rb.add_argument("--force", action="store_true", help="已回滚过的再走一遍")
    tf = sub.add_parser("selftest", help="用随包的空白示例库自证整套机制可用（全是合成夹具，不碰任何真库）")
    tf.add_argument("--tmp", help="夹具根（默认系统临时目录）")
    tf.add_argument("--keep", action="store_true", help="跑完不清理夹具（留证据）")
    return ap


HANDLERS = {"diagnose": cmd_diagnose, "classify": cmd_classify, "seed-ledger": cmd_seed_ledger,
            "apply": cmd_apply, "resume": cmd_resume, "status": cmd_status, "rollback": cmd_rollback}


def main(argv=None):
    setup_stdout()
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        if a.cmd == "selftest":
            return cmd_selftest(a)
        return HANDLERS[a.cmd](a)
    except (ConfigError, IntakeError) as e:
        msg = str(e)
        print("FAIL %s" % msg)
        if "锁被占用" in msg or "已被认领" in msg or "判并发" in msg:
            return 4
        return 2


if __name__ == "__main__":
    sys.exit(main())
