#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edit_lock — 纯文件系统的真实编辑锁（写入代次校验 + 单写队列），零依赖、跨平台。

为什么需要它：治理契约里的 `EDIT_LOCK: ...` 只是一段文字，谁都能写、谁都能声称自己持有，
两个 agent 可能同时认为自己拿到了锁；超时接管后旧写者醒来还能继续写，把接管者的成果盖掉。
本脚本把锁变成**文件系统层面的独占创建**（O_CREAT|O_EXCL，NTFS/ext4 都保证原子），
并给每把锁一个**代次（generation）**：接管必然让代次 +1，旧写者手里那个代次当场作废，
它再想写、再想续心跳、再想释放，都会被拒（退出码 6）。

只用标准库，不读任何本机特有工具，别人从 GitHub 下下来即可用。

用法（`--root` 是项目根，锁存放在 `<root>/.治理锁/`）：
  py scripts/edit_lock.py acquire   --root R --target 设计主文档.md --holder 技术美术 [--lease 3600] [--note ...]
  py scripts/edit_lock.py heartbeat --root R --target 设计主文档.md --holder 技术美术 --gen 1
  py scripts/edit_lock.py guard     --root R --target 设计主文档.md --holder 技术美术 --gen 1   # 动手写之前必跑
  py scripts/edit_lock.py takeover  --root R --target 设计主文档.md --holder 脚本工程师   # 仅租约过期时允许
  py scripts/edit_lock.py release   --root R --target 设计主文档.md --holder 技术美术 --gen 1
  py scripts/edit_lock.py status    --root R
  py scripts/edit_lock.py spool     --root R --target 设计主文档.md --holder 技术美术 --file 我的草稿.md
  py scripts/edit_lock.py queue     --root R [--target 设计主文档.md] [--done <件名>]   # 集成者取件/销件

写共享文件的标准姿势（三选一，别混用）：
  A. 串行写：acquire → guard → 改文件 → heartbeat（长任务每 ≤12h）→ release
  B. 单写队列：不碰共享文件，只 spool 自己的草稿；由唯一的集成者 queue 取件、合并、--done 销件
     ——多窗并行改同一份文档时用 B（对应「独立工作区 + 单一集成者合并」）
  C. 只是给文档留人类可读痕迹：文字锁 EDIT_LOCK（不互斥，只作提示）

退出码：0 成功 ｜ 1 锁被他人有效持有（正常互斥，不是错误）｜ 2 参数/路径错误
        3 该目标没有锁记录 ｜ 5 租约已过期（需显式 takeover）｜ 6 代次失效（旧写者被拒）
        7 接管被他人抢先（接管标记已在场）｜ 8 锁记录损坏（JSON 解析不了，互斥已失效）
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime

STORE_DIRS = (".治理锁", ".governance-locks")
TS_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEASE = 3600
CREATE_GRACE = 5.0


def setup_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def now_str():
    return datetime.now().strftime(TS_FMT)


def parse_ts(s):
    try:
        return datetime.strptime(str(s).strip(), TS_FMT)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(s).strip(), fmt)
            except ValueError:
                continue
    return None


def store_root(root, create=False):
    for name in STORE_DIRS:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            return p
    if create:
        p = os.path.join(root, STORE_DIRS[0])
        os.makedirs(os.path.join(p, "locks"), exist_ok=True)
        os.makedirs(os.path.join(p, "queue"), exist_ok=True)
        return p
    return None


def slug_for(target):
    norm = target.replace("\\", "/").strip().lower()
    base = os.path.basename(norm.rstrip("/")) or norm
    safe = re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", base)[:40] or "target"
    return "%s-%s" % (safe, hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10])


def lock_path(root, target, create=False):
    store = store_root(root, create=create)
    if store is None:
        return None
    return os.path.join(store, "locks", slug_for(target) + ".json")


def atomic_write(path, text):
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_lock(path):
    """返回 (记录 dict | None, 错误码 | None)。"""
    if not os.path.lexists(path):
        return None, 3
    raw = ""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except (IOError, OSError):
        return None, 8
    if not raw.strip():
        # O_EXCL 占位与填内容之间有极短窗口；新建 5 秒内的空文件判「正在建立」
        if time.time() - os.path.getmtime(path) < CREATE_GRACE:
            return None, 1
        return None, 8
    try:
        return json.loads(raw), None
    except ValueError:
        return None, 8


def lease_state(rec, now=None):
    now = now or datetime.now()
    hb = parse_ts(rec.get("heartbeat_at") or rec.get("acquired_at") or "")
    if hb is None:
        return "无心跳", 10 ** 9
    lease = int(rec.get("lease_seconds", DEFAULT_LEASE))
    age = (now - hb).total_seconds()
    return ("expired" if age > lease else "valid"), age


def describe(target, rec, age):
    return "%s holder=%s gen=%s 心跳=%s 租约=%ss 已过 %ss" % (
        target, rec.get("holder"), rec.get("generation"), rec.get("heartbeat_at"),
        rec.get("lease_seconds", DEFAULT_LEASE), int(age))


def ensure_target(args):
    if not args.target:
        print("[用法] --target 必须给出（要保护的相对路径或逻辑名）")
        return False
    return True


def cmd_acquire(args, root):
    path = lock_path(root, args.target, create=True)
    payload = {
        "target": args.target.replace("\\", "/"),
        "holder": args.holder,
        "generation": 1,
        "acquired_at": now_str(),
        "heartbeat_at": now_str(),
        "lease_seconds": args.lease,
        "note": args.note or "",
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        rec, err = read_lock(path)
        if err is not None:
            print("[拒绝] 锁记录读不到（码 %s）：%s —— 先跑 status 看清状态，别硬写" % (err, path))
            return err
        state, age = lease_state(rec)
        if rec.get("holder") == args.holder:
            rec["heartbeat_at"] = now_str()
            atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
            print("[复用] %s 已在你手里（同持有者续心跳）GEN=%s" % (args.target, rec["generation"]))
            print("TOKEN=%s@gen%s" % (args.holder, rec["generation"]))
            return 0
        if state == "expired":
            print("[过期] %s\n锁已超时（%ds > 租约 %ss），但**不自动接手**：请显式跑 takeover，接管会推进代次，"
                  "旧写者再写必被拒。" % (describe(args.target, rec, age), int(age), rec.get("lease_seconds", DEFAULT_LEASE)))
            return 5
        print("[占用] %s\n该锁仍在有效租约内（已 %ds），按互斥规则不得并行改。" % (describe(args.target, rec, age), int(age)))
        return 1
    os.close(fd)  # 先独占占位，再原子填内容
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    print("[拿到] %s\nholder=%s GEN=1 LEASE=%ss" % (args.target, args.holder, args.lease))
    print("TOKEN=%s@gen1" % args.holder)
    print("[下一步] 写文件前先跑 guard；长任务每 ≤12h 跑 heartbeat；改完跑 release")
    return 0


def _check_ownership(args, root, need_gen=True):
    """返回 (path, rec, exit_code_or_None)。exit_code 非 None 表示已被拒。"""
    path = lock_path(root, args.target)
    if path is None:
        print("[未配置] %s 下没有锁存储（.治理锁/）——本目标从未用真实锁保护过" % root)
        return None, None, 3
    rec, err = read_lock(path)
    if err is not None:
        print("[拒绝] 读不到 %s 的锁记录（码 %s）：%s" % (args.target, err, path))
        return None, None, err
    if args.gen is not None and int(args.gen) != int(rec.get("generation", 1)):
        print("[代次失效] %s 你的代次=%s，当前代次=%s（holder=%s）⇒ **拒绝写入**："
              "这把锁已被接管或重置，继续写会把别人的成果盖掉。"
              % (args.target, args.gen, rec.get("generation"), rec.get("holder")))
        print("          要接着干就重新 acquire/takeover 拿新代次，或改用单写队列 spool 让集成者合并。")
        return None, None, 6
    if args.holder and rec.get("holder") != args.holder:
        print("[他人持有] %s 当前 holder=%s（你报的是 %s）⇒ 拒绝" % (args.target, rec.get("holder"), args.holder))
        return None, None, 1
    if need_gen and args.gen is None:
        print("[用法] heartbeat/guard/release 必须带 --gen（写入代次校验的凭据就是它）")
        return None, None, 2
    return path, rec, None


def cmd_heartbeat(args, root):
    path, rec, code = _check_ownership(args, root)
    if code is not None:
        return code
    rec["heartbeat_at"] = now_str()
    if args.lease_given:
        rec["lease_seconds"] = args.lease
    atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
    print("[心跳] %s holder=%s GEN=%s → %s" % (args.target, rec["holder"], rec["generation"], rec["heartbeat_at"]))
    return 0


def cmd_guard(args, root):
    path, rec, code = _check_ownership(args, root)
    if code is not None:
        return code
    state, age = lease_state(rec)
    if state == "expired":
        print("[租约过期] %s 已 %ds 无心跳（租约 %ss）⇒ 你现在写，别人随时可合法接管并推代代次，"
              "写完的成果会被判作废。先 heartbeat 续租再动笔。"
              % (args.target, int(age), rec.get("lease_seconds", DEFAULT_LEASE)))
        return 5
    print("[放行] %s holder=%s GEN=%s（可以动笔；改完记得 release）" % (args.target, rec["holder"], rec["generation"]))
    return 0


def cmd_takeover(args, root):
    path = lock_path(root, args.target)
    if path is None or not os.path.lexists(path):
        print("[无锁可接] %s 没有锁记录；直接 acquire 即可" % args.target)
        return 3
    marker = path + ".takeover"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("[被抢先] 另一个角色正在接管 %s（接管标记已在场）⇒ 本次不动，等对方完成后再看状态。" % args.target)
        return 7
    try:
        os.close(fd)
        rec, err = read_lock(path)
        if err is not None:
            print("[拒绝] 读不到锁记录（码 %s）：%s" % (err, path))
            return err
        state, age = lease_state(rec)
        if state != "expired" and not args.force:
            print("[禁止接管] %s 仍在有效租约内（%ds < %ss）。超时接管只针对**过期锁**；"
                  "要强行接管请显式加 --force（并把原因写进 --note）。"
                  % (args.target, int(age), rec.get("lease_seconds", DEFAULT_LEASE)))
            return 1
        old = int(rec.get("generation", 1))
        prev_holder = rec.get("holder")
        prev_hb = rec.get("heartbeat_at")
        rec["generation"] = old + 1
        rec["holder"] = args.holder
        rec["heartbeat_at"] = now_str()
        rec["acquired_at"] = now_str()
        if args.lease_given:
            rec["lease_seconds"] = args.lease
        rec["taken_over_from"] = prev_holder
        rec["note"] = (args.note or "") + ("｜接管自 %s（原心跳 %s）" % (prev_holder, prev_hb))
        atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=2))
        new_gen = int(rec.get("generation", old))
        print("[接管成功] %s 代次 %d → %d，新 holder=%s\n"
              "旧写者手里的 gen=%d 从现在起作废：它再跑 heartbeat/guard/release 都会退出码 6 被拒。"
              % (args.target, old, new_gen, args.holder, old))
        print("GEN=%d" % new_gen)
        return 0
    finally:
        try:
            os.remove(marker)
        except OSError:
            pass


def cmd_release(args, root):
    path, rec, code = _check_ownership(args, root)
    if code is not None:
        return code
    try:
        os.remove(path)
    except OSError as e:
        print("[失败] 锁文件删不掉：%s（%s）" % (path, e))
        return 8
    print("[释放] %s holder=%s GEN=%s ⇒ 现在别人可以 acquire" % (args.target, rec["holder"], rec["generation"]))
    return 0


def cmd_status(args, root):
    store = store_root(root)
    print("=== 真实锁状态 ===")
    print("项目根: %s" % os.path.abspath(root))
    if store is None:
        print("未配置：根目录下没有 .治理锁/ ——协作只靠文字锁 EDIT_LOCK，不互斥。")
        print("要启用：py scripts/edit_lock.py acquire --root <项目根> --target <文件> --holder <角色>")
        return 0
    locks_dir = os.path.join(store, "locks")
    rows, expired, broken = [], 0, 0
    if os.path.isdir(locks_dir):
        for fn in sorted(os.listdir(locks_dir)):
            full = os.path.join(locks_dir, fn)
            if fn.endswith(".takeover"):
                age = time.time() - os.path.getmtime(full)
                rows.append("· 接管标记在场 %s（已 %ds）⇒ 有窗正在接管或中途退出" % (fn, int(age)))
                continue
            if not fn.endswith(".json"):
                continue
            rec, err = read_lock(full)
            if err is not None:
                broken += 1
                rows.append("· 损坏/占位 %s（码 %s）⇒ 该目标互斥已失效" % (fn, err))
                continue
            state, age = lease_state(rec)
            if state == "expired":
                expired += 1
            rows.append("· %s | holder=%s gen=%s | 心跳=%s | 状态=%s（%ds / 租约 %ss）"
                        % (rec.get("target"), rec.get("holder"), rec.get("generation"),
                           rec.get("heartbeat_at"), "过期可接管" if state == "expired" else "有效",
                           int(age), rec.get("lease_seconds", DEFAULT_LEASE)))
    qdir = os.path.join(store, "queue")
    queued = {}
    if os.path.isdir(qdir):
        for slug in os.listdir(qdir):
            sp = os.path.join(qdir, slug)
            if os.path.isdir(sp):
                items = [f for f in os.listdir(sp) if not f.endswith(".tmp")]
                if items:
                    queued[slug] = len(items)
    print("\n锁 %d 把（其中过期 %d、损坏 %d）；单写队列待合并 %d 个目标 / %d 件"
          % (len([r for r in rows if r.startswith("· ") and "holder=" in r]), expired, broken,
             len(queued), sum(queued.values())))
    for r in rows:
        print(r)
    if queued:
        print("\n待合并件（按目标）：")
        for slug, n in sorted(queued.items()):
            print("  %s: %d 件" % (slug, n))
    print("\n提示：本命令只报现状、不下健康结论；过期锁一律 takeover 显式接管（代次会 +1）。")
    return 0


def cmd_spool(args, root):
    """单写队列：把草稿原子投递到队列，绝不直接改共享文件。"""
    if not os.path.isfile(args.file):
        print("[用法] --file 指向的草稿文件不存在：%s" % args.file)
        return 2
    store = store_root(root, create=True)
    qdir = os.path.join(store, "queue", slug_for(args.target))
    os.makedirs(qdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "%s-%s.md" % (stamp, re.sub(r"[^\w\u4e00-\u9fff-]+", "_", args.holder)[:24])
    full = os.path.join(qdir, name)
    seq = 0
    while True:
        try:
            fd = os.open(full, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            seq += 1
            if seq > 50:
                print("[失败] 同秒同名超过 50 次，队列命名撞死")
                return 8
            full = os.path.join(qdir, "%s-%s.%d.md" % (stamp, re.sub(r"[^\w\u4e00-\u9fff-]+", "_", args.holder)[:24], seq))
    os.close(fd)  # 名字已独占占位，内容随后一次写完
    name = os.path.basename(full)
    with io.open(args.file, "r", encoding="utf-8", errors="replace") as f:
        body = f.read()
    header = "\n".join([
        "<!-- SPOOL 待合并 -->",
        "目标：%s" % args.target.replace("\\", "/"),
        "投稿人：%s" % args.holder,
        "投递时间：%s" % now_str(),
        "原文如下：", "",
    ])
    with io.open(full, "w", encoding="utf-8", newline="\n") as out:
        out.write(header + body)
    print("[已投递] %s → 队列件 %s" % (args.target, name))
    print("[说明] 共享文件一个字节都没动；请由**唯一的集成者**跑 queue 取件合并，避免多窗同时写。")
    return 0


def cmd_queue(args, root):
    store = store_root(root)
    if store is None:
        print("未配置：没有 .治理锁/，队列里自然没有待合并件。")
        return 0
    qdir = os.path.join(store, "queue")
    if args.done:
        if not args.target:
            print("[用法] --done 需和 --target 一起用")
            return 2
        full = os.path.join(qdir, slug_for(args.target), args.done)
        if not os.path.isfile(full):
            print("[失败] 队列件不存在：%s" % full)
            return 3
        os.remove(full)
        print("[已销件] %s（集成者已把它合并进目标文件）" % args.done)
        return 0
    total = 0
    if os.path.isdir(qdir):
        slugs = [args.target and slug_for(args.target) or None] if args.target else sorted(os.listdir(qdir))
        for slug in slugs:
            if not slug:
                continue
            sp = os.path.join(qdir, slug)
            if not os.path.isdir(sp):
                continue
            items = sorted(f for f in os.listdir(sp) if not f.endswith(".tmp"))
            for it in items:
                total += 1
                print("件 %s｜目标 slug=%s｜路径 %s" % (it, slug, os.path.join(sp, it)))
    print("待合并 %d 件（按时间升序；合并完用 --done 销件）" % total)
    return 0


CMDS = {
    "acquire": cmd_acquire,
    "heartbeat": cmd_heartbeat,
    "guard": cmd_guard,
    "takeover": cmd_takeover,
    "release": cmd_release,
    "status": cmd_status,
    "spool": cmd_spool,
    "queue": cmd_queue,
}


def main():
    setup_stdout()
    ap = argparse.ArgumentParser(description="真实编辑锁（原子获取 + 写入代次校验 + 单写队列）")
    ap.add_argument("command", choices=sorted(CMDS), help="acquire/heartbeat/guard/takeover/release/status/spool/queue")
    ap.add_argument("--root", default=".", help="项目根目录（锁存在 <root>/.治理锁/）")
    ap.add_argument("--target", help="要保护的目标（相对路径或逻辑名）")
    ap.add_argument("--holder", help="持有者/角色名")
    ap.add_argument("--gen", type=int, help="手里那张锁的代次（heartbeat/guard/release 必填）")
    ap.add_argument("--lease", type=int, default=None, help="租约秒数（默认 3600；超时后他人才能 takeover）")
    ap.add_argument("--note", default="", help="锁内备注")
    ap.add_argument("--file", help="spool 用：草稿文件路径")
    ap.add_argument("--done", help="queue 用：集成者合并完的队列件名")
    ap.add_argument("--force", action="store_true", help="takeover 用：租约未过期也强行接管（须在 --note 写理由）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("[用法] 项目根不存在：%s" % root)
        return 2
    if args.command in ("acquire", "heartbeat", "guard", "takeover", "release", "spool"):
        if not ensure_target(args) or not args.holder:
            print("[用法] 这些子命令都要 --target 和 --holder")
            return 2
    if args.command in ("heartbeat", "guard", "release") and args.gen is None:
        print("[用法] %s 必须带 --gen（代次校验）" % args.command)
        return 2
    if args.command == "spool" and not args.file:
        print("[用法] spool 必须带 --file <草稿路径>")
        return 2
    args.lease_given = args.lease is not None
    if args.lease is None:
        args.lease = DEFAULT_LEASE
    return CMDS[args.command](args, root)


if __name__ == "__main__":
    sys.exit(main())
