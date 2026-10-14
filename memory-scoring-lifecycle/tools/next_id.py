#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
next_id — 记忆库取号器（R-004 防撞车根治工具）

背景：多宿主并行写同一个 store 曾多次 ID 撞车（正文被覆盖、一号两用、号位被占）。
铁律 R-004：写入前必须取「当前已用最大号 + 1」，且以**文件系统**为准——
index.md 可能是精选索引（落后于实际文件），只看 index 会撞车。

用法：
  py next_id.py <store根> <库名>            # 只读查询：报下一个号，不落任何文件
  py next_id.py <store根> --all             # 一次报所有库（只读，不需要库名；
                                            #   写成 <store根> <库名> --all 也认，库名被忽略）
  py next_id.py <store根> <库名> --claim    # 原子占号：先把号位登记进 <store根>/.id_claims/ 再报号

输出：
  next: work-063       ← 下一个可用 ID（零填充宽度跟着本库现有正文走）
  claimed: work-063    ← --claim 成功：这个号已登记，别的窗取不到同一个
  [提醒] / [FAIL] / [UNASSESSED] ← 风险 / 撞号或断链 / 尺子没认出一个 ID

退出码：0=取号安全  1=有风险（同前缀重号 / index 登记了不存在的正文）
        2=参数错误（缺参数 / 没给库名也没给 --all / 目录不存在 / --claim 配 --all 或无库名 / 未知参数）
        3=尺子作废（库里有正文却一个 ID 都没解析出来 → 报出的号不可信；
                    或无法导入同目录 memory_recall.py = 索引解析器唯一来源缺失）
        4=占号写不进（store 只读 / 权限）⇒ 不返回可用号
        5=连续顺延 50 次仍撞车 ⇒ 有窗在批量占号，先人工看
退出码非 0 时不要直接用报出的号落盘；退 4/5 时「凭记忆猜一个号」等于把撞车重新引进来。

写入流程（配套）：
  1. py next_id.py <store根> <库名> --claim 拿号（非 0 先处理）
  2. 落盘正文 <id>.md → 3. 往该库 index.md 追加一行 → 4. 复跑只读查询，确认 next 已前进

为什么不删占号文件：它代表「这个号已经发出去了」。
后来正文没写，代价只是一个号被跳过（无害）；删掉它，代价是下一次撞车（有害）。
多窗并发的正确姿势是先占号再写正文；不带 --claim 时本脚本一个字节都不写。
"""
import os
import re
import socket
import sys
from datetime import datetime

try:  # GBK 控制台防护：输出中文，Windows 旧码页下会乱码甚至崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:  # 单尺子：index 里的号用 memory_recall 的解析器认，不再自带第二套正则
    import memory_recall as R
except Exception as _e:
    print("[next_id] state=tool_error｜无法导入同目录的 memory_recall.py（索引解析器唯一来源）：%r\n"
          "  两个脚本必须同目录一起部署。降级流程：按 SKILL.md §5 手工「列目录取最大号 +1」并复核，"
          "不要拿本工具的零输出判「查过了没问题」。" % (_e,), file=sys.stderr)
    sys.exit(3)

# 库目录名 → ID 规范前缀（含历史别名；未列出的库用目录名小写）
PREFIX_MAP = {
    "WORK": "work", "PITFALL": "pit", "DETAIL": "detail",
    "PROFILE": "profile", "DECISION": "decision",
    "REJECTED": "rejected", "FORGOTTEN": "forgotten",
}
ID_RE = re.compile(r"^([a-z]+?)-?0*(\d+)\.md\Z")
ID_ANY_RE = re.compile(r"^([a-z][a-z0-9]*?)-?0*(\d+)\Z")
CLAIM_DIRNAME = ".id_claims"
CLAIM_RETRY = 50


def to_id(text):
    """把 `pit-003` / `pit-003.md` / `PIT-0003.claim` 统一翻成 (前缀小写, 整数号)。"""
    if not text:
        return None
    s = os.path.basename(str(text).strip()).lower()
    for tail in (".md", ".claim"):
        if s.endswith(tail):
            s = s[:-len(tail)]
    m = ID_ANY_RE.match(s)
    return (m.group(1), int(m.group(2))) if m else None


def pad(prefix, num, width):
    return "%s-%0*d" % (prefix, max(width, 1), num)


def scan_ids(lib_dir):
    """目录下所有 *.md 的 (前缀, 号) → ({前缀: [号…]}, {前缀: 零填充宽度})。"""
    by_prefix, width = {}, {}
    for fn in os.listdir(lib_dir):
        m = ID_RE.match(fn)
        if not m:
            continue
        p, n = m.group(1), int(m.group(2))
        by_prefix.setdefault(p, []).append(n)
        digits = re.search(r"(\d+)\.md\Z", fn).group(1)
        width[p] = max(width.get(p, 0), len(digits))
    return by_prefix, width


def index_ids(lib_dir, store):
    """index.md 登记过的号；ID 表格体与链接体都走同一个解析器。

    旧版这里自带一套只认表格体的正则，在链接体库上恒返回空 ⇒
    「index 落后于文件系统」这条告警一次都没响过（2026-10-03 实测死代码）。
    永不翻红的判据就是假绿，所以改成复用 memory_recall.parse_index。
    """
    idx = os.path.join(lib_dir, "index.md")
    if not os.path.isfile(idx):
        return None
    try:
        with open(idx, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    ids = set()
    for r in R.parse_index(text, idx, store):
        for cand in (r.get("path"), r.get("id")):
            t = to_id(cand)
            if t:
                ids.add(t)
    return ids


def claim_ids(store):
    """已发出的号。只读：目录不存在就返回空集（只读查询不得创建任何东西）。"""
    d = os.path.join(store, CLAIM_DIRNAME)
    out = set()
    if not os.path.isdir(d):
        return out
    try:
        for fn in os.listdir(d):
            t = to_id(fn)
            if t:
                out.add(t)
    except OSError:
        pass
    return out


def try_claim(store, lib, id_str, basis):
    """O_CREAT|O_EXCL 独占创建占号文件。返回 (路径|None, 说明)；写坏就回收，不留半截。"""
    d = os.path.join(store, CLAIM_DIRNAME)
    path = os.path.join(d, id_str + ".claim")
    holder = "lib=%s host=%s pid=%d at=%s basis=%s" % (
        lib, socket.gethostname(), os.getpid(),
        datetime.now().isoformat(timespec="seconds"), basis)
    for _ in range(2):  # 目录可能刚被别的窗建出来，创建失败再试一次
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileNotFoundError:
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                return None, "占号目录建不出来：%s（%s）" % (d, e.strerror or e)
            continue
        except FileExistsError:
            return None, "occupied"
        except OSError as e:
            return None, (e.strerror or str(e))
        try:
            os.write(fd, ("id=%s\n%s\n" % (id_str, holder)).encode("utf-8", errors="replace"))
        except OSError as e:
            os.close(fd)
            try:
                os.remove(path)  # 半截占号文件会让后面的窗以为号已发出，必须回收
            except OSError:
                pass
            return None, "占号内容写入失败：%s" % (e.strerror or e)
        os.close(fd)
        try:  # 读回验收：登记里写的号要和给出去的号一致（第一行是 `id=<号>`）
            back = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            return None, "占号文件读回失败：%s" % (e.strerror or e)
        first = (back.splitlines() or [""])[0].strip()
        got = to_id(first[3:].strip()) if first.startswith("id=") else None
        if got != to_id(id_str):
            # 不回删：号已经发出去了，删登记等于把撞车放回来
            return None, "占号文件读回不符（第一行=%r，期望 id=%s；登记留在原处不回删）" % (first, id_str)
        return path, None
    return None, "占号目录不可用：%s" % d


def report_lib(store, name, lib_dir, claim_mode, claims):
    """检查一个库并直接打印；返回统计 dict。"""
    st = {"red": 0, "yellow": 0, "void": 0, "claimed": None, "claim_error": False, "retry": False}
    print("\n== [%s] %s" % (claim_mode and "占号" or "只读", lib_dir))
    want = PREFIX_MAP.get(name.upper(), name.lower())
    by_prefix, width = scan_ids(lib_dir)
    nums = by_prefix.get(want, [])
    aliases = {p: max(v) for p, v in by_prefix.items() if p != want}
    n_md = len([f for f in os.listdir(lib_dir) if f.endswith(".md") and f != "index.md"])
    w = width.get(want, 3)

    idx = index_ids(lib_dir, store)
    occupied = set(nums) | ({n for (p, n) in idx if p == want} if idx is not None else set())
    occupied |= {n for (p, n) in claims if p == want}
    nxt = (max(occupied) + 1) if occupied else 1

    if n_md and not by_prefix:
        st["void"] += 1
        print("  [UNASSESSED] %s 有 %d 个正文，ID_RE 全没认出来 → 报 %s 可能只是解析失败，不是冷启动"
              % (name, n_md, pad(want, nxt, w)))
    if len(nums) != len(set(nums)):
        st["red"] += 1
        dup = sorted({n for n in nums if nums.count(n) > 1})
        print("  [FAIL] %s 前缀 %s 重号 %s → 别按 next 直接写，先人工分号" % (name, want, dup))
    if aliases:
        st["yellow"] += 1
        print("  [提醒] %s 目录混有别名前缀 %s（历史命名，新条目统一用 %s-）" % (name, aliases, want))
    if idx is None:
        st["yellow"] += 1
        print("  [提醒] %s 没有 index.md（七库规范每库一个），本次只对账了文件系统" % name)
    else:
        fs_ids = {(p, n) for p, v in by_prefix.items() for n in v}
        dead = sorted(idx - fs_ids)
        unreg = sorted(fs_ids - idx)
        if dead:
            st["red"] += 1
            print("  [FAIL] index 登记了但目录里没正文（断链）：%s → 跑 memory_organize.py" % dead)
        if unreg:
            st["yellow"] += 1
            print("  [提醒] 有正文却没登记进 index：%s（取号以文件系统为准，但请补登记）"
                  % ["%s-%d" % (p, n) for p, n in unreg[:12]])

    if not claim_mode:
        print("  next: %s" % pad(want, nxt, w))
        print("    依据：前缀 %s 已用最大号 %s ＋ 占号登记（本 store 共 %d 个）；零填充宽度 %d"
              % (want, (max(occupied) if occupied else "无"), len(claims), w))
        return st

    pushed = 0
    while True:
        id_str = pad(want, nxt, w)
        if os.path.exists(os.path.join(lib_dir, id_str + ".md")):
            nxt += 1
            pushed += 1
            continue
        path, why = try_claim(store, name, id_str,
                              "max=%s" % (max(occupied) if occupied else "none"))
        if path:
            claims.add((want, nxt))
            st["claimed"] = id_str
            print("  next: %s" % id_str)
            tail = " （前面 %d 个号已被别的窗占走，已顺延）" % pushed if pushed else ""
            print("  claimed: %s%s" % (id_str, tail))
            print("    登记位：%s/%s.claim｜写正文失败也别删这条（删了就是把撞车放回来）"
                  % (CLAIM_DIRNAME, id_str))
            return st
        if why == "occupied":
            nxt += 1
            pushed += 1
            if pushed > CLAIM_RETRY:
                st["retry"] = True
                print("  [FAIL] 连续顺延 %d 次仍撞车 → 未返回可用号，先人工看谁在批量占号" % CLAIM_RETRY)
                return st
            continue
        st["red"] += 1
        st["claim_error"] = True
        print("  [FAIL] 占号失败：%s → 未返回可用号（宁可不给号，也不给可能撞车的号）" % why)
        return st


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    store = os.path.abspath(argv[1])
    # 第二个位置参数既可能是库名，也可能是开关。`--all` 报的是所有库，此时库名没有意义；
    # 旧版硬要 argv[2] 当库名 ⇒ `next_id.py <store根> --all` 会把 "--all" 当成库目录去查，
    # 报「库目录不存在 …\--all」退 2（2026-10-11 卡03 归口到部署店时实跑撞出，
    # 当时 §8.1 命令块写的正是这条 ⇒ 正文承诺了一条跑不通的命令）。
    if argv[2].startswith("--"):
        lib, flags = None, argv[2:]
    else:
        lib, flags = argv[2], argv[3:]
    unknown = [f for f in flags if f not in ("--all", "--claim")]
    if unknown:
        # 认不出的参数一律拒绝：静默丢弃时 `--cliam` 拼错一个字母就变成「只读查询」的假占号。
        print("[错误] 未知参数：%s（只接受 --all / --claim）" % " ".join(unknown), file=sys.stderr)
        return 2
    if lib is None and "--claim" in flags:
        print("[错误] --claim 必须点名库名：占号是「一个库的一个号」，"
              "无库名的批量占号会拿不到可对账的号位。", file=sys.stderr)
        return 2
    if lib is None and "--all" not in flags:
        print("[错误] 没给库名，也没给 --all：取号必须点名是哪个库（WORK / PITFALL / …），"
              "--all 则一次报所有库。", file=sys.stderr)
        return 2
    if "--claim" in flags and "--all" in flags:
        print("[错误] --claim 不能配 --all：一次只占一个库的一个号，"
              "批量占号会让「拿到的号」和「要写的库」对不上。", file=sys.stderr)
        return 2
    if not os.path.isdir(store):
        print("[错误] store 根不存在：%s" % store, file=sys.stderr)
        return 2
    if lib is not None:
        lib_dir = os.path.join(store, lib)
        if not os.path.isdir(lib_dir):
            print("[错误] 库目录不存在：%s\n（取号要在已有库上进行；新库请先建目录再取号，"
                  "否则「目录不存在」会被当成冷启动从 1 开始撞别人）" % lib_dir, file=sys.stderr)
            return 2

    claim_mode = "--claim" in flags
    claims = claim_ids(store)
    targets = list(PREFIX_MAP) if "--all" in flags else [lib]
    red = yellow = void = 0
    claim_error = retry = False
    n_claimed = 0
    for name in targets:
        d = os.path.join(store, name)
        if not os.path.isdir(d):
            yellow += 1
            print("\n== [跳过] %s（库目录不存在，没查 ≠ 查过没问题）" % d)
            continue
        st = report_lib(store, name, d, claim_mode, claims)
        red += st["red"]
        yellow += st["yellow"]
        void += st["void"]
        claim_error = claim_error or st["claim_error"]
        retry = retry or st["retry"]
        n_claimed += 1 if st["claimed"] else 0

    print("\n[判定] 红 %d · 黄 %d · UNASSESSED %d%s" % (red, yellow, void,
          " · 已占号 %d 个" % n_claimed if claim_mode else ""))
    if void:
        print("[判定] state=ruler_void｜尺子作废：报出的号不可信")
        return 3
    if claim_error:
        print("[判定] state=claim_failed｜未返回可用号")
        return 4
    if retry:
        print("[判定] state=claim_contention｜顺延超限")
        return 5
    print("[判定] state=%s" % ("risk" if red else "ok"))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
