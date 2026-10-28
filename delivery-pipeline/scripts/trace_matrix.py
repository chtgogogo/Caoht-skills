#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追踪矩阵生成器（trace_matrix）——把「条款↔符号↔测试↔走查↔卡」从人读面变成机读面。

    py scripts/trace_matrix.py gen   --root <项目根> [--apply]   # 默认只打印摘要，--apply 才写 trace/matrix.json
    py scripts/trace_matrix.py check --root <项目根>             # 只读校验：符号死指针 / 矩阵过期 / 测试未登记
    py scripts/trace_matrix.py selftest [--tmp-root <目录>]      # 红例自证，只写临时树

产物：`<项目根>/trace/matrix.json`（可机读、可 diff、可被 spec_check C17 复核）。
数据源全是现成件，不需要人工登记：
  · 需求块 `#### R-xx | 标题` ＋ `- 关键定位：` / `- 验收：` 两行（规格层本来就有）
  · 测试代码里反向标记 `@pytest.mark.req("R-xx")` / `# req: R-xx` / `// req: R-xx`（「代码即追踪」）
  · 卡片目录（卡号→文件）、PRD 走查清单（条号）

判据三件（check 用，spec_check C17 复用同一语义）：
  T01 活性：矩阵里每个 code 符号必须在其文件里真出现；tests 的 `文件::用例名` 必须真存在；卡文件必须真在盘
  T02 时效：specs/PRD/卡库任一文件的 md5 与矩阵指纹不符 ⇒ 矩阵过期（改了规格没重生成＝读的是旧账）
  T03 反标：测试里标了 R 号、但该需求验收行没写这条测试 ⇒ 记警（验收行是权威，测试标记只是并行证据源）

退出码：0 干净｜1 有红（--strict 时含警）｜2 根目录/用法不对。
零第三方依赖，只用标准库。
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
MATRIX_REL = Path("trace") / "matrix.json"

REQ_HEAD = re.compile(r"^#{2,5}\s+(R-[\w.\-]+)\s*(?:[|｜]\s*(.+?))?\s*$")
META_LINE = re.compile(r"^\s*[-*]?\s*(状态|验收|关联|来源|备注|优先级|负责人|关键定位|修订)\s*[：:]\s*(.*)$")
# 代码定位：路径（带扩展名）＋ 可选的「的 <片段>」
# 🔴 交替按先后命中：tsx 必须排在 ts 前、jsx 排在 js 前，否则 BookingForm.tsx 会被截成 .ts
LOC_ITEM = re.compile(
    r"(?P<path>[\w\-./\\]+\.(?:tsx|ts|jsx|js|vue|py|go|rs|java|sql))"
    r"(?:\s*的\s*(?P<tail>[^（(；;，,。\n]*))?"
)
# 测试定位：路径里的 test 文件 ＋ 可选的 `::用例名` 或 ` 的 用例名`
TEST_ITEM = re.compile(
    r"(?P<path>[\w\-./\\]*(?:tests?|__tests__)/[\w\-.]+\.(?:tsx|ts|jsx|js|mjs|py))"
    r"(?:::(?P<sym1>[A-Za-z_][\w]*))?"
    r"(?:\s*的\s*(?P<sym2>[^\s（(；;，,。\n]+))?"
)
WALK_ITEM = re.compile(r"走查(?:清单)?第\s*([0-9][0-9、/／,，和\s]*?)\s*条")
CARD_ITEM = re.compile(r"卡\s*#?\s*([A-Z]?\d+(?:\s*[/／]\s*[A-Z]?\d+)*)")
SYMBOL = re.compile(r"[A-Za-z_][\w]{2,}")
REQ_MARK = re.compile(r"(?:pytest\.mark\.req\(\s*[\"']|#\s*req:\s*|//\s*req:\s*|@req\s+)(R-[\w.\-]+)")

CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java", ".sql")


def read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def rel(root: Path, p: Path) -> str:
    try:
        return Path(p).relative_to(root).as_posix()
    except ValueError:
        return str(p).replace("\\", "/")


def md5_of(p: Path) -> str:
    if not p.is_file():
        return ""
    return hashlib.md5(p.read_bytes()).hexdigest()


def norm_symbol(raw: str):
    """把 ` 的 XXX` 片段里的符号名抠出来；抠不出可信标识符就返回 None（只查文件存在）。"""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # `a~d` 展开式 → 取 `~` 前那段（真名就是它）
    if "~" in raw:
        raw = raw.split("~")[0]
    if "*" in raw:
        head = raw.split("*")[0]
        return head if len(head) >= 3 else None
    hits = SYMBOL.findall(raw)
    if not hits:
        return None
    return hits[-1]


def parse_spec_file(f: Path, root: Path):
    """→ [{id, title, status, code[], tests[], walkthrough[], cards[]}]"""
    out, cur = [], None
    for line in read_text(f).splitlines():
        m = REQ_HEAD.match(line)
        if m:
            if cur:
                out.append(cur)
            cur = {"id": m.group(1), "title": (m.group(2) or "").strip(),
                   "spec": rel(root, f), "specKey": Path(f).parent.name,
                   "status": "", "code": [], "tests": [], "walkthrough": [], "cards": []}
            continue
        if cur is None:
            continue
        mm = META_LINE.match(line)
        if not mm:
            continue
        key, body = mm.group(1), mm.group(2)
        if key == "状态":
            cur["status"] = re.split(r"[（(]", body, maxsplit=1)[0].strip()
        elif key == "关键定位":
            for it in LOC_ITEM.finditer(body):
                tail = norm_symbol(it.group("tail"))
                cur["code"].append({"file": it.group("path").replace("\\", "/"), "symbol": tail})
        elif key == "验收":
            cur["acceptance"] = body
            for it in TEST_ITEM.finditer(body):
                sym = it.group("sym1") or norm_symbol(it.group("sym2"))
                cur["tests"].append({"file": it.group("path").replace("\\", "/"), "symbol": sym})
            for it in WALK_ITEM.finditer(body):
                for n in re.split(r"[、/／,，\s]+", it.group(1)):
                    if n.isdigit():
                        cur["walkthrough"].append(int(n))
            for it in CARD_ITEM.finditer(body):
                for n in re.split(r"\s*[/／]\s*", it.group(1)):
                    if n:
                        cur["cards"].append(n)
    if cur:
        out.append(cur)
    for b in out:
        b["code"] = dedup(b["code"], ("file", "symbol"))
        b["tests"] = dedup(b["tests"], ("file", "symbol"))
        b["walkthrough"] = sorted(set(b["walkthrough"]))
        b["cards"] = sorted(set(b["cards"]))
    return out


def dedup(items, keys):
    seen, out = set(), []
    for it in items:
        k = tuple(it.get(x) for x in keys)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def parse_walkthrough_prd(root: Path) -> int:
    """PRD 走查清单的条目数（实测），用来给 C18 当分母。"""
    prd = root / "docs" / "pipeline" / "PRD.md"
    text = read_text(prd)
    if not text:
        return 0
    m = re.search(r"^#+\s*用户视角走查清单.*?$(.*)", text, re.M | re.S)
    if not m:
        return 0
    body = re.split(r"^#+\s", m.group(1), maxsplit=1, flags=re.M)[0]
    return len([l for l in body.splitlines() if re.match(r"^\s*\d+[.、)]\s*\S", l)])


def find_cards(root: Path):
    cards = []
    for sub in ("docs/pipeline/cards", "docs/pipeline/具体任务卡"):
        d = root / sub
        if d.is_dir():
            cards.extend(sorted(d.rglob("*.md")))
    return cards


def card_index(root: Path):
    """卡号 → 文件相对路径（一个卡号可能多文件，取集合）。"""
    idx = {}
    rx = re.compile(r"卡\s*#?\s*([A-Z]?\d+)")
    for f in find_cards(root):
        for m in rx.finditer(f.name):
            idx.setdefault(m.group(1), []).append(rel(root, f))
        # 文件名不带卡号时，退回扫正文首 40 行（施工图卡头通常有卡号）
        if not rx.search(f.name):
            for line in read_text(f).splitlines()[:40]:
                mm = rx.search(line)
                if mm:
                    idx.setdefault(mm.group(1), []).append(rel(root, f))
                    break
    return {k: sorted(set(v)) for k, v in idx.items()}


def scan_reverse(root: Path):
    """测试代码里的 R 号标记（代码即追踪）；返回 {R-xx: [文件::用例]}。"""
    rev = {}
    files = []
    for base in ("tests", "frontend/tests", "test", "src"):
        d = root / base
        if d.is_dir():
            files.extend(sorted(p for p in d.rglob("*") if p.suffix in CODE_EXT))
    for f in files:
        text = read_text(f)
        if not text or "req:" not in text and "mark.req(" not in text and "@req" not in text:
            continue
        lines = text.splitlines()
        def_line = [i for i, line in enumerate(lines)
                    if re.match(r"^\s*(?:async\s+)?def\s+test_|^\s*(?:test|it)\s*\(", line)]

        def name_after(idx):
            """标记行之后最近的用例定义名（装饰器在 def 之前的惯例；node:test 的字符串名整段取）。"""
            for i in def_line:
                if i >= idx:
                    m = re.search(r"(?:def\s+)([\w]+)|\(\s*[\"']([^\"']*)[\"']", lines[i])
                    if not m:
                        return ""
                    return (m.group(1) or m.group(2) or "").strip()
            return ""

        for i, line in enumerate(lines):
            for m in REQ_MARK.finditer(line):
                tname = name_after(i)
                key = f"{rel(root, f)}::{tname}" if tname else rel(root, f)
                rev.setdefault(m.group(1), [])
                if key not in rev[m.group(1)]:
                    rev[m.group(1)].append(key)
    return rev


def build_fingerprint(root: Path, spec_files, card_files):
    fp = {}
    for f in spec_files:
        fp[rel(root, f)] = md5_of(f)
    prd = root / "docs" / "pipeline" / "PRD.md"
    if prd.is_file():
        fp[rel(root, prd)] = md5_of(prd)
    fp["__cards__"] = md5_of_list([md5_of(f) for f in card_files])
    fp["__tests__"] = md5_of_list([md5_of(f) for f in (root / "tests").rglob("*.py")]) \
        if (root / "tests").is_dir() else ""
    return fp


def md5_of_list(items):
    return hashlib.md5("".join(sorted(items)).encode("utf-8")).hexdigest()


def gen(root: Path):
    specs_dir = root / "specs"
    if (root / "openspec" / "specs").is_dir():
        specs_dir = root / "openspec" / "specs"
    spec_files = sorted(specs_dir.rglob("*.md")) if specs_dir.is_dir() else []
    reqs = []
    for f in spec_files:
        reqs.extend(parse_spec_file(f, root))

    cards = card_index(root)
    walk_count = parse_walkthrough_prd(root)
    reverse = scan_reverse(root)

    requirements = {}
    walk_index = {}
    for b in reqs:
        entry = dict(b)
        entry["cardFiles"] = {c: cards.get(c, []) for c in b["cards"]}
        entry["reverseTests"] = reverse.get(b["id"], [])
        requirements[b["id"]] = entry
        for n in b["walkthrough"]:
            walk_index.setdefault(str(n), [])
            if b["id"] not in walk_index[str(n)]:
                walk_index[str(n)].append(b["id"])

    matrix = {
        "schema": 1,
        "generator": f"trace_matrix.py v{VERSION}",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "requirements": len(requirements),
            "with_code": sum(1 for r in requirements.values() if r["code"]),
            "with_tests": sum(1 for r in requirements.values() if r["tests"]),
            "with_walkthrough": sum(1 for r in requirements.values() if r["walkthrough"]),
            "with_cards": sum(1 for r in requirements.values() if r["cards"]),
            "walkthrough_items_in_prd": walk_count,
            "walkthrough_items_covered": len(walk_index),
            "cards_on_disk": len({p for v in cards.values() for p in v}),
        },
        "fingerprint": build_fingerprint(root, spec_files, find_cards(root)),
        "requirements": requirements,
        "walkthrough": walk_index,
        "reverse_tests": reverse,
    }
    return matrix


# ---------------------------------------------------------------- 校验（C17 复用这一套语义）

def symbol_hits(root: Path, f: str, sym):
    """文件在不在、符号在不在。返回 (ok, 原因)。"""
    p = root / f
    if not p.is_file():
        return False, "文件不存在"
    if not sym:
        return True, ""
    text = read_text(p)
    if sym in text:
        return True, ""
    if "*" in sym or "~" in sym:  # 模式式锚点：用前缀兜一层
        head = re.split(r"[~*]", sym)[0]
        if head and head in text:
            return True, ""
    return False, f"符号 {sym} 在文件里找不到（死指针）"


def lint_matrix(root: Path, strict=False):
    """→ (items, exited)  items=[(级别, 位置, 人话)]，级别 ∈ {"红","警"}"""
    items = []
    mp = root / MATRIX_REL
    if not mp.is_file():
        items.append(("警", rel(root, mp), "没有追踪矩阵（可选增强：跑 trace_matrix.py gen --root <项目根> --apply）"))
        return items
    try:
        m = json.loads(read_text(mp))
    except json.JSONDecodeError as e:
        items.append(("红", rel(root, mp), f"矩阵不是合法 JSON：{e}"))
        return items

    reqs = m.get("requirements") or {}
    if not reqs:
        items.append(("红", rel(root, mp), "矩阵里没有任何需求条目（空矩阵＝没追踪）"))
        return items

    # T02 时效：指纹对不上 ⇒ 读的是旧账
    specs_dir = root / "openspec" / "specs" if (root / "openspec" / "specs").is_dir() else root / "specs"
    spec_files = sorted(specs_dir.rglob("*.md")) if specs_dir.is_dir() else []
    now_fp = build_fingerprint(root, spec_files, find_cards(root))
    old_fp = m.get("fingerprint") or {}
    stale = sorted(k for k in now_fp if old_fp.get(k) != now_fp[k])
    if stale:
        items.append(("警", rel(root, mp),
                      f"矩阵已过期（{len(stale)} 个源文件变过，首条 {stale[0]}）——重跑 gen --apply"))

    # T01 活性
    for rid, e in reqs.items():
        for c in e.get("code") or []:
            ok, why = symbol_hits(root, c.get("file", ""), c.get("symbol"))
            if not ok:
                items.append(("红", rel(root, mp), f"{rid} 的 code 定位失效：{c.get('file')}——{why}"))
        for t in e.get("tests") or []:
            ok, why = symbol_hits(root, t.get("file", ""), t.get("symbol"))
            if not ok:
                items.append(("红", rel(root, mp), f"{rid} 的 tests 定位失效：{t.get('file')}——{why}"))
        for cid, files in (e.get("cardFiles") or {}).items():
            if not files:
                items.append(("红", rel(root, mp), f"{rid} 声称卡 {cid} 接住，但卡库里找不到卡 {cid}"))
        if not e.get("code") and not e.get("tests") and not e.get("walkthrough"):
            items.append(("警", rel(root, mp), f"{rid} 三面皆空（无符号/无测试/无走查）——纯文字条款？"))
        # T03 反标：测试标了 R 号但验收行没提这个测试文件
        # 判据按**文件级**（验收行已指向该测试文件就算登记；逐用例比对会把「文件里新加一条用例」
        # 也判成未登记，噪声大于信号）。
        # 🔴 目录/通配也算登记（外部评审指出的假阳面）：验收行常写「tests/api/ 全量回归」，
        #    反标却落在 tests/api/test_r36.py——父目录被验收行提到即视为已登记。
        rev = e.get("reverseTests") or []
        acc = e.get("acceptance") or ""
        declared_files = {t.get("file") for t in (e.get("tests") or []) if t.get("file")}

        def _registered(path):
            if path in declared_files:
                return True
            # 只认「至少两级」的目录前缀（tests/api）；单独一个 tests 几乎每行都出现，认它等于关掉 T03
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            return "/" in parent and parent in acc

        undeclared = [x for x in rev if not _registered(x.split("::")[0])]
        if undeclared:
            items.append(("警", rel(root, mp),
                          f"{rid} 有 {len(undeclared)} 处测试反标，验收行里没有这个测试文件（首条 {undeclared[0]}）"))
    return items


def cmd_gen(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    m = gen(root)
    s = m["summary"]
    print(f"[trace_matrix v{VERSION}] root={root}")
    print(f"[摘要] 需求 {s['requirements']}｜有符号 {s['with_code']}｜有测试 {s['with_tests']}"
          f"｜有走查 {s['with_walkthrough']}｜有卡 {s['with_cards']}"
          f"｜PRD 走查 {s['walkthrough_items_in_prd']} 条/覆盖 {s['walkthrough_items_covered']}"
          f"｜卡文件 {s['cards_on_disk']}")
    if not args.apply:
        print(f"[干跑] 未写盘。加 --apply 写到 {MATRIX_REL.as_posix()}")
        return 0
    out = root / MATRIX_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"[已写] {rel(root, out)}（{out.stat().st_size} 字节）")
    return 0


def cmd_check(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[根目录不对] {root} 不存在或不是目录 ⇒ 退 2")
        return 2
    items = lint_matrix(root, strict=args.strict)
    print(f"[trace_matrix check v{VERSION}] root={root}")
    for level, where, msg in items:
        print(f"[{level}] T01/T02/T03｜{where}｜{msg}")
    if not items:
        print("[全绿] 矩阵活性、时效、反标三件零红零警")
    reds = sum(1 for i in items if i[0] == "红")
    warns = len(items) - reds
    print(f"[汇总] 红 {reds}｜警 {warns}")
    return 1 if reds or (args.strict and warns) else 0


# ---------------------------------------------------------------- 夹具

GOOD_SPEC = """# 主规格 · 预约

> 唯一真源。

#### R-1 | 营业窗
系统应在时间落在 08:00 至 20:00 之外时拒绝该次预约。
- 状态：生效
- 验收：tests/test_rules.py::test_window＋走查清单第 3、4 条
- 关键定位：app/rules.py 的 validate_window（实况定位）

#### R-2 | 取消
当用户取消自己的预约时，系统应把状态置为已取消。
- 状态：生效
- 验收：tests/test_cancel.py 的 test_cancel_a~b
- 关键定位：app/cancel.py 的 cancel_booking
"""

GOOD_PRD = """# PRD

### 项目宪章

1. 端口固定 8000。
2. 禁止 GPL 依赖。

## 用户视角走查清单

1. 打开首页看到三张卡。
2. 创建一场成功。
3. 超窗创建被拒。
"""

GOOD_TEST_RULES = """import pytest


@pytest.mark.req("R-1")
def test_window():
    assert True
"""

GOOD_TEST_CANCEL = """def test_cancel_a():
    assert True


def test_cancel_b():
    assert True
"""


_SEQ = [0]


def mk_tree(tmp_root: Path):
    _SEQ[0] += 1
    root = tmp_root / f"p{_SEQ[0]}"
    (root / "specs" / "booking").mkdir(parents=True)
    (root / "docs" / "pipeline" / "cards").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "app").mkdir(parents=True)
    (root / "specs" / "booking" / "spec.md").write_text(GOOD_SPEC, encoding="utf-8")
    (root / "docs" / "pipeline" / "PRD.md").write_text(GOOD_PRD, encoding="utf-8")
    (root / "docs" / "pipeline" / "cards" / "卡B1_预约校验.md").write_text("# 卡B1\n\n覆盖 R-1 R-2\n", encoding="utf-8")
    (root / "tests" / "test_rules.py").write_text(GOOD_TEST_RULES, encoding="utf-8")
    (root / "tests" / "test_cancel.py").write_text(GOOD_TEST_CANCEL, encoding="utf-8")
    (root / "app" / "rules.py").write_text("def validate_window():\n    return True\n", encoding="utf-8")
    (root / "app" / "cancel.py").write_text("def cancel_booking():\n    return True\n", encoding="utf-8")
    return root


def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-trace-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    results = []

    def chk(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    root = mk_tree(tmp_root)
    m = gen(root)
    reqs = m["requirements"]
    chk("S01 gen 抽到 2 条需求", len(reqs) == 2, f"实际={list(reqs)}")
    chk("S02 R-1 的 code 抽到 app/rules.py::validate_window",
        {"file": "app/rules.py", "symbol": "validate_window"} in reqs["R-1"]["code"],
        f"实际={reqs['R-1']['code']}")
    chk("S03 R-1 的 tests 抽到 test_window（::形式）",
        {"file": "tests/test_rules.py", "symbol": "test_window"} in reqs["R-1"]["tests"],
        f"实际={reqs['R-1']['tests']}")
    chk("S04 R-2 的 tests 抽到 test_cancel_a（`的 X~Y` 形式取前半段）",
        {"file": "tests/test_cancel.py", "symbol": "test_cancel_a"} in reqs["R-2"]["tests"],
        f"实际={reqs['R-2']['tests']}")
    chk("S05 R-1 的走查抽到 [3,4]", reqs["R-1"]["walkthrough"] == [3, 4],
        f"实际={reqs['R-1']['walkthrough']}")
    chk("S06 反向扫描抓到 @pytest.mark.req('R-1')",
        any("test_window" in x for x in m["reverse_tests"].get("R-1", [])),
        f"实际={m['reverse_tests']}")
    chk("S07 PRD 走查条目数实测 3", m["summary"]["walkthrough_items_in_prd"] == 3,
        f"实际={m['summary']['walkthrough_items_in_prd']}")

    # —— 干净树：活性能绿
    out = root / MATRIX_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    items0 = lint_matrix(root)
    greens_only = [i for i in items0 if i[0] == "红"]
    chk("S08 新鲜矩阵零红（T03 允许警）", not greens_only, f"红={greens_only}")

    # —— 符号死指针
    (root / "app" / "rules.py").write_text("def validate_other():\n    return True\n", encoding="utf-8")
    red1 = [i for i in lint_matrix(root) if i[0] == "红"]
    chk("S09 符号被改名 ⇒ T01 红且点名 R-1", any("R-1" in i[2] for i in red1), f"红={red1}")

    # —— 测试文件消失
    root2 = mk_tree(tmp_root)
    m2 = gen(root2)
    (root2 / MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    (root2 / MATRIX_REL).write_text(json.dumps(m2, ensure_ascii=False), encoding="utf-8")
    (root2 / "tests" / "test_rules.py").unlink()
    red2 = [i for i in lint_matrix(root2) if i[0] == "红"]
    chk("S10 测试文件被删 ⇒ T01 红且点名 R-1", any("R-1" in i[2] for i in red2), f"红={red2}")

    # —— 卡声称存在但卡库没有
    root3 = mk_tree(tmp_root)
    m3 = gen(root3)
    m3["requirements"]["R-2"]["cards"] = ["Z9"]
    m3["requirements"]["R-2"]["cardFiles"] = {"Z9": []}
    (root3 / MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    (root3 / MATRIX_REL).write_text(json.dumps(m3, ensure_ascii=False), encoding="utf-8")
    red3 = [i for i in lint_matrix(root3) if i[0] == "红"]
    chk("S11 卡号查无此卡 ⇒ T01 红", any("Z9" in i[2] for i in red3), f"红={red3}")

    # —— 矩阵过期
    root4 = mk_tree(tmp_root)
    m4 = gen(root4)
    (root4 / MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    (root4 / MATRIX_REL).write_text(json.dumps(m4, ensure_ascii=False), encoding="utf-8")
    (root4 / "specs" / "booking" / "spec.md").write_text(GOOD_SPEC + "\n#### R-3 | 新条款\n系统应做点什么。\n", encoding="utf-8")
    warns4 = [i for i in lint_matrix(root4) if i[0] == "警"]
    chk("S12 改了规格没重生成 ⇒ T02 记警（不记红）",
        any("过期" in i[2] for i in warns4) and not [i for i in lint_matrix(root4) if i[0] == "红"],
        f"警={warns4}")

    # —— 缺矩阵记警不记红（可选增强，不许炸老项目）
    root5 = mk_tree(tmp_root)
    items5 = lint_matrix(root5)
    chk("S13 没有矩阵 ⇒ 记警不记红", [i[0] for i in items5] == ["警"], f"实际={items5}")

    # —— 反标未登记
    root6 = mk_tree(tmp_root)
    (root6 / "tests" / "test_rules.py").write_text(
        'import pytest\n\n@pytest.mark.req("R-2")\ndef test_extra():\n    assert True\n', encoding="utf-8")
    m6 = gen(root6)
    (root6 / MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    (root6 / MATRIX_REL).write_text(json.dumps(m6, ensure_ascii=False), encoding="utf-8")
    warns6 = [i for i in lint_matrix(root6) if i[0] == "警"]
    chk("S14 测试标了 R-2 但验收行没写 ⇒ T03 记警", any("R-2" in i[2] for i in warns6), f"警={warns6}")

    # —— 外部评审回流（2026-10-18）：验收行常写「tests/api/ 全量回归」这种目录形式，
    # 反标却落在具体文件里 ⇒ 不认目录就会满屏假警（判据必须先过"不误伤"这一关）
    root7 = mk_tree(tmp_root)
    (root7 / "tests" / "api").mkdir(parents=True)
    (root7 / "tests" / "api" / "test_x.py").write_text(
        'import pytest\n\n@pytest.mark.req("R-2")\ndef test_dir_case():\n    assert True\n', encoding="utf-8")
    sp7 = root7 / "specs" / "booking" / "spec.md"
    sp7.write_text(read_text(sp7).replace("- 验收：tests/test_cancel.py 的 test_cancel_a~b",
                                         "- 验收：tests/api/ 全量回归"), encoding="utf-8")
    m7 = gen(root7)
    (root7 / MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    (root7 / MATRIX_REL).write_text(json.dumps(m7, ensure_ascii=False), encoding="utf-8")
    warns7 = [i for i in lint_matrix(root7) if i[0] == "警"]
    chk("S19 目录形式验收（tests/api/ 全量回归）不被误报为「反标未登记」",
        not any("反标" in i[2] for i in warns7), f"警={warns7}")

    # —— 真出口
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_ok = cmd_check(argparse.Namespace(root=str(root4), strict=False))
        rc_missing = cmd_check(argparse.Namespace(root=str(tmp_root / "不存在"), strict=False))
        rc_dry = cmd_gen(argparse.Namespace(root=str(root4), apply=False))
    chk("S15 真出口：有警非 strict 退 0", rc_ok == 0, f"退码={rc_ok}")
    chk("S16 真出口：根目录不存在退 2", rc_missing == 2, f"退码={rc_missing}")
    chk("S17 gen 干跑不写盘退 0", rc_dry == 0 and "[干跑]" in buf.getvalue(), f"退码={rc_dry}")

    # —— 零写入证据：临时树之外不许动
    chk("S18 临时树落在 tmp-root 内", tmp_root.is_dir() and home not in tmp_root.parents, f"tmp_root={tmp_root}")

    shutil.rmtree(tmp_root, ignore_errors=True)
    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[trace_matrix selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="追踪矩阵生成器（条款↔符号↔测试↔走查↔卡）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="生成 trace/matrix.json（默认干跑）")
    g.add_argument("--root", required=True)
    g.add_argument("--apply", action="store_true", help="真写盘")
    g.set_defaults(func=cmd_gen)
    c = sub.add_parser("check", help="只读校验矩阵活性/时效/反标")
    c.add_argument("--root", required=True)
    c.add_argument("--strict", action="store_true", help="把警当红")
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("selftest", help="红例自证（只写临时树）")
    s.add_argument("--tmp-root", default=None)
    s.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
