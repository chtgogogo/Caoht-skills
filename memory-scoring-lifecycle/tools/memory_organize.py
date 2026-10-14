#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_organize.py — 记忆整理器（理得清，保召回质量）

扫描记忆索引与详情正文，产出**整理计划**；默认 dry-run（只报告、不改动），加 `--apply` 才执行。

十项检查（①-⑥ 是老版六项，⑦-⑩ 是把「只有 AI 脑子里有数」的判定改成确定性输出）：
  1) 断链      —— 索引指向但文件不存在
  2) 孤儿      —— 文件存在但索引未登记
  3) 完全重复  —— 同一路径被索引多行
  4) 容量红线  —— CORE/MEMORY 超行数上限、index 超 80 行、单条正文超 120 行
  5) 冷记忆    —— 索引行日期早于 --older-than 天（归档候选，只报不移）
  6) 格式盲区  —— index.md 有正文却一行都解析不出 ⇒ 本报告作废（退 3，别当"查过了没问题"）
  7) 同根因聚合 —— 按 RT 号分组（判同型=RT 号相同，SKILL.md §6）；同 RT 号 ≥7 条 ⇒ 升华候选
  8) 检索字段齐备度 —— 各库索引的 触发词/分/状态/日期 填充率（缺列 ⇒ 召回只能逐条读正文）
  9) 分数漂移  —— 索引「分」列 ≠ 正文 frontmatter score
 10) 衰减结算  —— 按 §3 惰性衰减公式算出「该扣多少 / V 是否已 ≤0（FORGOTTEN 候选）」
  另：索引状态与正文状态不一致（正文已 superseded/expired，索引还在当 active 卖）

**解析器只有一套**：本文件 import 同目录 `memory_recall.py` 的 parse_index / read_body_frontmatter。
两套正则各说各话是 pit-001 的根因族——召回读到 20 条、整理读到 18 条，两头都报绿。

**纪律：只移不删。** `--apply` 只做两件安全动作：移除断链行、合并同路径重复行；
其余各项只报候选（§8「脚本只报候选不自动移」），改正文由收尾的 AI 判断。
**本工具一个正文文件都不移动、不删除**：真要归档，由 AI 把候选移进 `<store>/archive/`
（或压成 FORGOTTEN 的 1-2 行摘要），绝不物理删除——删了就没有"当初为什么记它"这条证据链。

**并发保护**（多窗共写一棵库已有三次实录，R-004）：`--apply` 先独占创建
`<store>/.organize.lock`（O_CREAT|O_EXCL），取不到就退 5 且**一个字节都不改**；
取到之后**才**解析索引——计划必须由锁内的磁盘现状算出，用锁前的旧快照写回会把
别的窗刚追加的行当成"没见过的行"抹掉。落盘＝临时文件 + os.replace 原子换 + .bak，
写完从磁盘读回、用同一个解析器验收（只准少掉计划删的那几行），误伤立刻回滚（退 4）。

用法:
  py memory_organize.py [--root <store>] [--older-than 90] [--apply]
退出码: 0=干净且尺子可信  1=有计划待处理  2=参数错误
        3=尺子作废（有正文却一行索引都没解析出来 ⇒ 「0 断链 / 0 孤儿」不成立；
          或同目录缺 memory_recall.py，解析器不可用）
        4=--apply 写后验收未过，已从 .bak 回滚（改动没留住，别当已整理）
        5=并发锁被占用（另一个整理器／另一个窗正在写这个库，本次未改动）
"""
import os
import re
import sys
import time
import shutil
import socket
from datetime import datetime, timezone

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:
    import memory_recall as R
except Exception as _e:
    # 缺召回器＝没有解析器。此时**不出报告**，而不是自己现编一套正则量一遍：
    # 空白输出会被读成"体检通过"，现编的第二套尺子则会把同一棵树量出两个结论。
    print("[organize] state=tool_error｜无法导入同目录的 memory_recall.py（索引解析器唯一来源）：%r\n"
          "  两个脚本必须同目录一起部署。降级流程：按 SKILL.md §8 清单手工体检；"
          "不要拿本工具的零输出判「查过了没问题」。" % (_e,), file=sys.stderr)
    sys.exit(3)

try:  # GBK 控制台防护（同型坑第 2 次，Codex/DSH 实测 2026-09-27）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LIMITS = {"MEMORY.md": 200, "CORE.md": 150, "index.md": 80}   # 索引文件行数硬上限
SINGLE_FILE_LIMIT = 120                                        # 单条记忆正文行数上限
SUBLIMATE_MIN = 7      # 同 RT 号攒够 7 条才升华（v3.5-sc1.5，天 2026-10-03 拍板 3→7，勿回退成 3）
TTL_DAYS = {"pinned": None, "slow": 30, "normal": 14, "fast": 7}
TYPE_TTL = {"profile": "pinned", "decision": "pinned", "work": "fast",
            "detail": "normal", "pitfall": "slow", "rejected": "slow"}
RT_RE = re.compile(r"RT[-_]?[A-Za-z0-9]+")   # 只认大写 RT；小写 rt-xxx 是文件名，不是根因号
EXTRA_FM = ("strength", "ttl", "created")    # recall 的 FM_FIELDS 没带、衰减公式要用
LOCK_STALE_SEC = 120                          # 锁文件超过这么久视作上次崩溃的残留，抢占并明说
MAX_ITEMS = 40                                # 每节最多列这么多条，其余给计数（报告不是台账）
UNREAD_NOTE = "超出正文读取上限，未读"          # 大库保护：读了多少份、没读多少份必须摊开


# ---------- 读盘 ----------

def slurp(path):
    """严格 UTF-8 读。返回 (text|None, eol, 告警)。

    非 UTF-8 字节改用 replace 兜底读取并**记为不可回写**：老写法读的时候 errors='ignore'、
    写的时候把这份残缺文本原样落回去，等于静默吃掉非法字节还宣称"整理完成"。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "\n", "读取失败：%s" % (e.strerror or e)
    eol = "\r\n" if b"\r\n" in raw else "\n"
    try:
        return raw.decode("utf-8").lstrip("\ufeff"), eol, ""
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), eol, "含非 UTF-8 字节（替换读取，本次不回写本文件）"


def walk_md(store, include_archived=False):
    """store 下的正文 .md（不含索引与引导卡），返回 [(绝对路径, 相对路径)]。"""
    skip = {".git", "__pycache__", "tools"} if include_archived else R.EXCLUDED_DIRS
    out = []
    for dirpath, dirnames, filenames in os.walk(store):
        dirnames[:] = [x for x in dirnames
                       if not x.startswith(".") and x.lower() not in skip]
        for fn in filenames:
            if fn.endswith(".md") and fn not in R.INDEX_CANDIDATES and fn != "index.md":
                out.append((os.path.abspath(os.path.join(dirpath, fn)),
                            os.path.relpath(os.path.join(dirpath, fn), store)))
    return out


# ---------- 索引行 → 正文核对 ----------

def enrich(rows, cache, budget):
    """补齐正文侧字段：RT 号、frontmatter 状态/有效期、score/strength/ttl/created、正文路径。

    只读 frontmatter 区（≤1600B，与召回同一限额）；bodies_used 受 budget 约束，
    超了就明说「后面 N 条没读正文」——聚合与衰减的口径必须带着分母一起报。
    """
    bodies_used = 0
    for r in rows:
        body = os.path.normpath(os.path.join(r["base"] or ".", r["path"])) if r["path"] else ""
        r["body_abs"] = body
        r["fm"] = {}
        r["fm_note"] = ""
        if not r["rt"]:
            m = RT_RE.search(" ".join([r["triggers"], r["conclusion"], r["extra"]]))
            if m:
                r["rt"] = m.group(0)
        if body and os.path.isfile(body):
            if budget is None or bodies_used < budget:
                n, fields, closed, text = R.read_body_frontmatter(body, cache)
                bodies_used += 1
                if fields:
                    fm = dict(fields)
                    for key in EXTRA_FM:
                        mm = re.search(r"^%s\s*:\s*(.*)$" % key, text, re.M)
                        if mm:
                            fm[key] = mm.group(1).strip()
                    r["fm"] = fm
                if not closed:
                    r["fm_note"] = "frontmatter 超 %dB 未读全" % R.FM_READ_LIMIT
                elif not fields:
                    r["fm_note"] = "正文无 frontmatter 区"
            else:
                r["fm_note"] = UNREAD_NOTE
        if not r["rt"] and r["fm"].get("root_cause_signature"):
            r["rt"] = r["fm"]["root_cause_signature"]
        # 正文状态优先于索引状态（索引可能忘了同步；召回侧也是这么核的）
        r["status_body"] = R.norm_status(r["fm"].get("status"))[0]
        r["status_final"], _ = R.norm_status(r["status_body"] or r["status_raw"])
        sig = (r["fm"].get("root_cause_signature") or r["rt"] or "").strip()
        r["rt_tag"], r["rt_phrase"] = split_rt(sig)
    return bodies_used


def split_rt(sig):
    """`RT-003|静默失败未查退出码` → ('RT-003', '静默失败未查退出码')。

    RT-new 不合并：§6 说归不进现有 RT 的写 RT-new|短语，各条短语是不同根因，
    若一律折成 'RT-NEW' 这个桶，攒满 7 条就会把七件不相干的事升华成一条铁律。
    """
    m = RT_RE.search(sig or "")
    if not m:
        return "", ""
    tag = m.group(0).upper().replace("_", "-")
    phrase = (sig[m.end():].lstrip("|：: \t") or "").strip()
    if tag == "RT-NEW" and phrase:
        tag = "RT-new|" + phrase
    return tag, phrase


# ---------- 各项检查 ----------

def check_rows(store, idx_files, rows, older_than, cache, body_budget):
    """返回 (报告段落 list[(标题, 行列表, 提示)], 统计 dict)。"""
    today = datetime.now(timezone.utc).date()
    bodies_used = enrich(rows, cache, body_budget)

    dead, dup_rows, over_limit, cold = [], [], [], []
    drift, status_gap, unreadable = [], [], []
    indexed = {}
    per_index = {}

    for idx in idx_files:
        rel = os.path.relpath(idx, store)
        rows_here = [r for r in rows if os.path.abspath(r["index"]) == os.path.abspath(idx)]
        text, eol, warn = slurp(idx)
        if warn:
            unreadable.append("%s：%s" % (rel, warn))
        n_lines = len((text or "").splitlines())
        cap = LIMITS.get(os.path.basename(idx), 150)
        if n_lines > cap:
            over_limit.append("%s %d 行 > 上限 %d" % (rel, n_lines, cap))

        seen = {}
        stat = {"行": len(rows_here), "触发词": 0, "分": 0, "状态": 0, "日期": 0}
        for r in rows_here:
            if (r["triggers"] or "").strip():
                stat["触发词"] += 1
            if r["score"] is not None:
                stat["分"] += 1
            if (r["status_raw"] or "").strip():
                stat["状态"] += 1
            if r["date"]:
                stat["日期"] += 1
            full = os.path.normpath(os.path.join(r["base"] or ".", r["path"]))
            key = os.path.normcase(full)
            indexed[key] = r
            if not os.path.isfile(full):
                dead.append("%s → %s（索引行 %s）" % (rel, r["path"], r["id"] or "?"))
            if key in seen:
                dup_rows.append("%s：%s 与 %s 同路径重复" % (rel, r["id"], seen[key]))
            else:
                seen[key] = r["id"] or "?"
            # 冷记忆：按索引行日期（正文 created 在衰减节里单独算）
            if r["date"]:
                d = R.parse_iso(r["date"])
                if d and (today - d).days > older_than:
                    cold.append("%s %s（%d 天）%s" % (rel, r["date"], (today - d).days,
                                                      r["id"] or r["conclusion"][:24]))
            # 分数漂移：索引「分」vs 正文 score
            bs = r["fm"].get("score")
            if r["score"] is not None and bs:
                m = re.search(r"-?\d+(\.\d+)?", bs)
                if m and abs(float(m.group(0)) - r["score"]) >= 1:
                    drift.append("%s 索引分 %g ≠ 正文 score %s（差 %g）"
                                 % (r["id"] or r["path"], r["score"], m.group(0),
                                    abs(float(m.group(0)) - r["score"])))
            # 状态不同步：①索引与正文两个状态打架 ②正文有效期已过但状态还没转成失效
            st_idx, _ = R.norm_status(r["status_raw"])
            if r["status_body"] and st_idx and r["status_body"] != st_idx:
                status_gap.append("%s 索引状态 %r ≠ 正文 status %r（召回按正文拦，索引在骗人）"
                                  % (r["id"] or r["path"], r["status_raw"] or "(空)", r["status_body"]))
            vu = R.parse_iso(r["fm"].get("valid_until"))
            if vu and vu < today and r["status_final"] not in R.DROPPED_STATUS:
                status_gap.append("%s 正文 valid_until=%s 已过，状态却还是 %r（该转 superseded 或 REJECTED）"
                                  % (r["id"] or r["path"], vu, r["status_final"] or "(空)"))
        per_index[rel] = stat

    # 孤儿（格式盲区库除外：解析不出行 ⇒ 判定必然全错，见 unparsed）
    return {
        "dead": dead, "dup": dup_rows, "over_limit": over_limit, "cold": cold,
        "drift": drift, "status_gap": status_gap, "unreadable": unreadable,
        "indexed": indexed, "per_index": per_index, "bodies_used": bodies_used,
        "rows": rows, "today": today,
    }


def find_orphans(store, indexed, unparsed_dirs):
    out = []
    for full, rel in walk_md(store):
        if os.path.normcase(os.path.dirname(full)) in unparsed_dirs:
            continue
        if os.path.normcase(full) not in indexed:
            out.append(rel)
    return out


def aggregate_rt(rows):
    """按 RT 号分组。返回 (达标组, 未达标组, RT-new 待沉淀组, 无 RT 号行)。"""
    groups, no_rt = {}, []
    for r in rows:
        tag = r["rt_tag"]
        if not tag:
            no_rt.append(r)
            continue
        groups.setdefault(tag, []).append(r)
    ready, below, newish = [], [], []
    for tag, members in sorted(groups.items()):
        rec = {"tag": tag, "members": members,
               "phrase": next((m["rt_phrase"] for m in members if m["rt_phrase"]), ""),
               "dates": sorted([m["date"] or (m["fm"].get("created") or "") for m in members
                                if (m["date"] or m["fm"].get("created"))])}
        if tag.startswith("RT-new|"):
            newish.append(rec)
        elif len(members) >= SUBLIMATE_MIN:
            ready.append(rec)
        else:
            below.append(rec)
    ready.sort(key=lambda x: -len(x["members"]))
    below.sort(key=lambda x: (-len(x["members"]), x["tag"]))
    newish.sort(key=lambda x: (-len(x["members"]), x["tag"]))
    return ready, below, newish, no_rt


# ---------- 衰减结算（SKILL.md §3 惰性衰减，读时结算） ----------

def settle(rows, today):
    """算「该扣多少」。分母必须自洽：应扣>0 ＋ V≤0 ＋ 未到期 ＋ pinned ＋ 无分 ＋ 无日期 ＝ 索引行数。"""
    decay, forgot, skipped = [], [], {"无分可算": 0, "无日期": 0, "pinned": 0, "未读正文": 0, "未到期": 0}
    skipped["未读正文"] = sum(1 for r in rows if r.get("fm_note") == UNREAD_NOTE)
    for r in rows:
        if r.get("fm_note") == UNREAD_NOTE:      # 没读正文就没法算，单列一档，不混进「无分/无日期」
            continue
        fm = r["fm"]
        v, v_src = r["score"], "索引「分」列"
        m = re.search(r"-?\d+(\.\d+)?", fm.get("score") or "")
        if m:
            v, v_src = float(m.group(0)), "正文 score"
        if v is None:
            skipped["无分可算"] += 1
            continue
        base = R.parse_iso(r["date"] or fm.get("last_hit") or fm.get("created"))
        for cand in (fm.get("last_hit"), fm.get("created")):
            d2 = R.parse_iso(cand)
            if d2 and (base is None or d2 > base):
                base = d2
        if base is None:
            skipped["无日期"] += 1
            continue
        ttl = (fm.get("ttl") or "").strip().lower()
        if ttl not in TTL_DAYS:
            ttl = TYPE_TTL.get((r["fm"].get("type") or r["type"] or "").strip().lower(), "normal")
        if TTL_DAYS[ttl] is None:
            skipped["pinned"] += 1
            continue
        try:
            strength = max(0.0, min(10.0, float(re.search(r"-?\d+", fm.get("strength") or "0").group(0))))
        except (AttributeError, ValueError):
            strength = 0.0
        period = TTL_DAYS[ttl] * (1 + strength / 10.0)      # 被反复想起的记忆衰减变慢
        days = (today - base).days
        cycles = int(days // period)
        deduct = cycles * (1 + int(v // 25))
        if deduct <= 0:
            skipped["未到期"] += 1
            continue
        v_new = v - deduct
        line = "%s V=%g（%s）· ttl=%s(%d天)×(1+strength %g/10)=%d天 · 距 %s 已 %d 天 = %d 周期 ⇒ 应扣 %d → V=%g" % (
            r["id"] or r["path"], v, v_src or "?", ttl, TTL_DAYS[ttl], strength, round(period),
            base.isoformat(), days, cycles, deduct, v_new)
        (forgot if v_new <= 0 else decay).append((v_new, line))
    decay.sort(key=lambda x: x[0])
    forgot.sort(key=lambda x: x[0])
    return [x[1] for x in decay], [x[1] for x in forgot], skipped


# ---------- 并发锁 ----------

def acquire_lock(store):
    """O_CREAT|O_EXCL 独占创建锁文件。返回 (锁路径|None, 说明)。"""
    path = os.path.join(store, ".organize.lock")
    holder = "%s@%s pid=%d started=%s" % (os.path.basename(store), socket.gethostname(),
                                          os.getpid(), datetime.now().isoformat(timespec="seconds"))
    waited, backoff, grabbed = 0.0, 0.2, []
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, (holder + "\n").encode("utf-8", errors="replace"))
            os.close(fd)
            note = "已独占" if not grabbed else "已抢下残留锁（上一个：%s）" % grabbed[-1]
            return path, (("等待 %.1fs 后抢到锁；" % waited) if waited else "") + note
        except FileExistsError:
            age = None
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                pass
            if age is not None and age > LOCK_STALE_SEC:
                who = ""
                try:
                    who = open(path, encoding="utf-8", errors="replace").read().strip()
                except OSError:
                    pass
                try:
                    os.remove(path)
                except OSError:
                    return None, "残留锁 %s（%.0fs）删不掉（权限？）" % (path, age)
                grabbed.append("%.0fs 前的 %s" % (age, who or "未知"))
                continue
            if waited >= 5.0:
                who = ""
                try:
                    who = open(path, encoding="utf-8", errors="replace").read().strip()
                except OSError:
                    pass
                return None, ("锁被占用（%.0fs 前由 %s 建；本进程已等 5s 未撤，残留锁判定阈值 %ds）%s"
                              % (age if age is not None else -1, who or "未知", LOCK_STALE_SEC, path))
            time.sleep(backoff)
            waited += backoff
            backoff = min(backoff * 2, 1.5)
        except OSError as e:
            return None, "无法创建锁文件：%s（%s）" % (path, e.strerror or e)


def release_lock(path):
    try:
        os.remove(path)
    except OSError:
        pass


def touch_lock(path):
    """心跳：残留锁的判据是「锁文件超过 LOCK_STALE_SEC 没人碰过」，所以持锁期间必须刷新 mtime；
    否则一个大库的整理会被第二个窗误判成崩溃残留而抢走，互斥静默失效。"""
    if path:
        try:
            os.utime(path, None)
        except OSError:
            pass


def write_index(path, keep_lines, eol):
    """原子换：临时文件写完再 os.replace，中途崩溃不会留下半截索引。

    newline="" 是硬要求：这里已经用 eol 手工拼了行尾，若按 newline="\\r\\n" 打开，
    Python 会把串里每个 "\\n" 再翻译一遍 ⇒ 落盘成 "\\r\\r\\n"，读回行数直接翻倍，
    写后验收必然判「读回 N 行 ≠ 期望 M 行」并从 .bak 回滚 ⇒ CRLF 库上 --apply 永久失效
    （2026-10-11 变异探针 M1 的基线跑红的就是这条）。
    """
    tmp = "%s.organize-tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(eol.join(keep_lines) + eol)
    shutil.copy2(path, path + ".bak")
    os.replace(tmp, path)


# ---------- 主流程 ----------

def parse_args(argv):
    root, older_than, apply_changes = os.getcwd(), 90, False
    unknown, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--root", "--older-than"):
            if i + 1 >= len(argv):
                print("[error] %s 后面缺参数值（悬空的 --root 会静默改用当前目录，"
                      "量错库零报警）" % a, file=sys.stderr)
                return None, None, None, 2
            if a == "--root":
                root = argv[i + 1]
            else:
                try:
                    older_than = int(argv[i + 1])
                except ValueError:
                    print("[error] --older-than 需要整数，收到 %r" % argv[i + 1], file=sys.stderr)
                    return None, None, None, 2
            i += 2
        elif a == "--apply":
            apply_changes, i = True, i + 1
        else:
            unknown.append(a)
            i += 1
    if unknown:
        # 认不出的参数一律拒绝：静默丢弃时 `--roots` 拼错一个字母就变成「扫当前目录」的假报告。
        print("[error] 未知参数：%s\n（只接受 --root / --older-than / --apply）"
              % " ".join(unknown), file=sys.stderr)
        return None, None, None, 2
    return root, older_than, apply_changes, 0


def main():
    root, older_than, apply_changes, rc = parse_args(sys.argv[1:])
    if rc:
        return rc

    store, index_path, single = R.resolve_index(root)
    if not index_path:
        print("[organize] state=index_error｜未找到索引（试过 %s，从 %s 向上回溯 store 根都失败）"
              % (" / ".join(R.INDEX_CANDIDATES), root), file=sys.stderr)
        return 3
    files = [index_path]
    if not single:
        files += [p for p in R.sub_index_files(store) if p != index_path]

    lock_path, lock_msg = (None, "dry-run 不取锁")
    if apply_changes:
        lock_path, lock_msg = acquire_lock(store)
        if not lock_path:
            print("[organize] state=locked｜%s ⇒ 本次**一个字节都没改**。"
                  "等另一个窗结束后重跑，或确认它是残留锁（>%ds）再手工删掉锁文件。"
                  % (lock_msg, LOCK_STALE_SEC), file=sys.stderr)
            print("[organize] state=locked｜%s" % lock_msg)
            return 5

    try:
        rows, blind, blind_dirs, cache = [], [], set(), {}
        for idx in files:
            text, _eol, warn = slurp(idx)
            if text is None:
                blind.append("%s：%s" % (os.path.relpath(idx, store), warn))
                continue
            got = R.parse_index(text, idx, store)
            rows += got
            if os.path.basename(idx) == "index.md" and not got:
                lib = os.path.dirname(idx)
                n_rec = len([fn for fn in os.listdir(lib)
                             if fn.endswith(".md") and fn != "index.md"])
                if n_rec:
                    blind.append("%s：%d 个正文无法核对，该库孤儿判定已跳过"
                                 % (os.path.relpath(idx, store), n_rec))
                    blind_dirs.add(os.path.normcase(lib))
        n_bodies = len(walk_md(store))
        touch_lock(lock_path)
        if not rows:
            print("[organize] state=index_error｜索引解析出 0 行%s｜store=%s"
                  % ("，而库内有 %d 份正文 ⇒ 行格式本工具不认（不是「没有记忆」）" % n_bodies
                     if n_bodies else "", store), file=sys.stderr)
            print("[organize] state=index_error｜索引解析出 0 行，本报告不可采信")
            return 3

        stats = check_rows(store, files, rows, older_than, cache, R.MAX_BODY_SCAN)
        orphan = find_orphans(store, stats["indexed"], blind_dirs)
        ready, below, newish, no_rt = aggregate_rt(rows)
        decay, forgot, skipped = settle(rows, stats["today"])

        # ---- 报告 ----
        print("# 记忆整理计划（store = %s｜%s型）" % (store, "单索引" if single else "七库"))
        print("# 模式 = %s｜锁：%s" % ("APPLY（会改动）" if apply_changes else "DRY-RUN（只报告）", lock_msg))
        print("# 索引文件 %d 个 · 解析出索引行 %d 条 · 正文文件 %d 份 · 其中核对过 frontmatter %d 行（正文读取上限 %d）\n"
              % (len(files), len(rows), n_bodies, stats["bodies_used"], R.MAX_BODY_SCAN))

        sections = [
            ("格式盲区／无法读取（这些库的孤儿判定已跳过）", blind,
             "行内须有 [标题](文件.md) 或 ID 表体；解析不出 ⇒ 下面几项的分母不完整"),
            ("断链（索引指向但文件不存在）", stats["dead"], "--apply 可自动移除索引行"),
            ("同路径重复行", stats["dup"], "--apply 可自动合并（保留首行）"),
            ("孤儿文件（存在但未登记）", orphan, "需人工判断：补登记 / 归档"),
            ("容量超限", stats["over_limit"], "索引 CORE 150 / MEMORY 200 / index 80 行，单条正文 120 行"),
            ("编码告警（本次不回写这些文件）", stats["unreadable"], "非 UTF-8 字节先人工转码，否则写回会静默改内容"),
            ("索引状态与正文状态不一致", stats["status_gap"],
             "召回按正文拦，索引那行还在骗人 ⇒ 把正文的 status/valid_until 同步进索引列"),
            ("分数漂移（索引「分」↔正文 score）", stats["drift"], "以正文为准回写索引列，别让排序分和体检各认一个"),
            (f"冷记忆（索引行日期 > {older_than} 天，候选归档）", stats["cold"],
             "§8：脚本只报候选，AI 逐条复核后移 archive/ 或加分续命"),
            ("V 已 ≤0（FORGOTTEN 归档候选）", forgot, "同上：只报不自动移"),
        ]
        total = 0
        for title, items, hint in sections:
            total += len(items)
            print("## %s — %d 项%s" % (title, len(items), "（" + hint + "）" if items else ""))
            for it in items[:MAX_ITEMS]:
                print("   " + it)
            if len(items) > MAX_ITEMS:
                print("   … 另有 %d 项" % (len(items) - MAX_ITEMS))
            print()

        # ⑦ 同根因聚合（本节是卡03 的主验收项）
        print("## 同根因聚合（RT 号分组，判同型=RT 号相同）— 达标 %d 组 / 未达标 %d 组 / "
              "待沉淀 RT-new %d 组 / 无 RT 号 %d 条（阈值 SUBLIMATE_MIN=%d）"
              % (len(ready), len(below), len(newish), len(no_rt), SUBLIMATE_MIN))
        for g in ready:
            ids = " ".join(m["id"] or os.path.basename(m["path"]) for m in g["members"])
            print("   ▶ %s%s —— %d 条 ≥ %d ⇒ **升华候选**"
                  % (g["tag"], "｜" + g["phrase"] if g["phrase"] else "", len(g["members"]), SUBLIMATE_MIN))
            print("       成员：%s" % ids)
            if g["dates"]:
                print("       日期跨度：%s ~ %s" % (g["dates"][0], g["dates"][-1]))
            print("       §6 产物：一句话规则 + 关键约束 + 反例 + 来源指针；"
                  "CORE 铁律行草稿：R-<取号>｜%s（同根因第 %d 次复现）｜来源 %s"
                  % (g["phrase"] or g["tag"], len(g["members"]), ids))
        for g in below:
            print("   · %s%s —— %d 条（未达阈值，还差 %d 条）成员 %s"
                  % (g["tag"], "｜" + g["phrase"] if g["phrase"] else "", len(g["members"]),
                     SUBLIMATE_MIN - len(g["members"]),
                     " ".join(m["id"] or os.path.basename(m["path"]) for m in g["members"])))
        for g in newish:
            print("   ✚ %s —— %d 条：RT-new 不合并（各条短语是不同根因）⇒ 体检时沉淀为新的 RT 文件"
                  % (g["tag"], len(g["members"])))
        if no_rt:
            print("   ? 无 RT 号 %d 条（坑类应按 §6 补 root_cause_signature；"
                  "归不进现有 RT 的写 RT-new|短语）：%s"
                  % (len(no_rt), " ".join((m["id"] or os.path.basename(m["path"])) for m in no_rt[:15])))
        total += len(ready)
        print()

        # ⑧ 检索字段齐备度
        print("## 检索字段齐备度（缺列 ⇒ 召回只能逐条读正文，又贵又慢；缺「日期」列则排序拿不到新近度）")
        for rel, st in sorted(stats["per_index"].items()):
            if not st["行"]:
                print("   %-28s 行   0 ｜ 该文件没有索引行（引导卡／路由表），不参与齐备度判定" % rel)
                continue
            miss = [k for k in ("触发词", "分", "状态", "日期") if st[k] == 0]
            part = [k for k in ("触发词", "分", "状态", "日期") if 0 < st[k] < st["行"]]
            tag = "齐" if not (miss or part) else ("整列缺失：%s" % "、".join(miss) if miss else "部分空：%s" % "、".join(part))
            print("   %-28s 行 %3d ｜ 触发词 %d 分 %d 状态 %d 日期 %d ｜ %s"
                  % (rel, st["行"], st["触发词"], st["分"], st["状态"], st["日期"], tag))
        print()

        # ⑩ 衰减结算
        s = skipped
        accounted = (len(decay) + len(forgot) + s["未到期"] + s["pinned"]
                     + s["无分可算"] + s["无日期"] + s["未读正文"])
        print("## 衰减结算（§3 惰性衰减：应扣=周期数×(1+⌊V/25⌋)，实际周期=TTL档×(1+strength/10)）")
        print("   索引行 %d 条 ＝ 应扣>0 %d ＋ V≤0 %d ＋ 未到期 %d ＋ pinned 不衰减 %d ＋ "
              "无分可算 %d ＋ 无日期 %d ＋ 未读正文 %d%s"
              % (len(rows), len(decay), len(forgot), s["未到期"], s["pinned"],
                 s["无分可算"], s["无日期"], s["未读正文"],
                 "" if accounted == len(rows) else " ｜⚠️ 分母不自洽（少 %d 条），本节日程不可采信"
                 % (len(rows) - accounted)))
        for line in decay[:MAX_ITEMS]:
            print("   ↓ " + line)
        if len(decay) > MAX_ITEMS:
            print("   … 另有 %d 条" % (len(decay) - MAX_ITEMS))
        print("   （写回正文由收尾的 AI 执行：本工具只算不写 —— §8「脚本只报候选不自动移」）\n")

        n_fail = len(stats["dead"]) + len(stats["dup"]) + len(blind) + len(stats["unreadable"])
        n_warn = len(orphan) + len(stats["over_limit"]) + len(stats["cold"]) + len(forgot)
        n_hard = len(ready) + len(stats["drift"]) + len(stats["status_gap"])
        print("[判定] state=%s｜索引行 %d · 正文 %d | FAIL %d（断链/重复/盲区） · "
              "WARN %d（孤儿/超限/冷记忆/V≤0） · 一致性 %d（升华候选/分数漂移/状态不同步） | "
              "RT 达标组 %d · 未读全 %d"
              % ("plan" if (n_fail or n_warn or n_hard) else "clean",
                 len(rows), n_bodies, n_fail, n_warn, n_hard, len(ready),
                 sum(1 for r in rows if r.get("fm_note"))))

        # ---- apply（锁内：现在读到的就是别人写完之后的现状）----
        if not apply_changes:
            print("\n合计 %d 项待处理。加 --apply 执行「移除断链 + 合并重复」；"
                  "其余各项需人工（正文一律不动）。" % total)
            return 1 if (total or n_hard) else 0

        failed, skipped_files, changed, unlabeled = [], [], 0, []
        for idx in files:
            rel = os.path.relpath(idx, store)
            touch_lock(lock_path)
            text, eol, warn = slurp(idx)
            if text is None or warn:
                skipped_files.append("%s：%s ⇒ 本文件一字未动" % (rel, warn or "读不出来"))
                continue
            lines = text.splitlines()
            fresh = R.parse_index(text, idx, store)      # 重新解析，不复用扫描阶段的行对象
            nos = line_of_row(text, fresh)
            drop, seen = set(), set()
            for r, ln in zip(fresh, nos):
                tgt = os.path.join(r["base"] or ".", r["path"])
                key = os.path.normcase(os.path.normpath(tgt))
                if not os.path.isfile(tgt) or key in seen:
                    if ln:
                        drop.add(ln)
                    else:
                        unlabeled.append("%s %s（认不出该行的原文行号 ⇒ 宁可不删）"
                                         % (rel, r["id"] or r["path"]))
                seen.add(key)
            if not drop:
                continue
            keep = [ln for i, ln in enumerate(lines, 1) if i not in drop]
            try:
                write_index(idx, keep, eol)
            except OSError as e:
                failed.append("%s：写入失败（%s）" % (rel, e.strerror or e))
                continue
            changed += len(drop)
            back, _eol2, warn2 = slurp(idx)
            bad = []
            if warn2:
                bad.append("读回时解码异常")
            else:
                bl = back.splitlines()
                if bl != keep:
                    bad.append("读回 %d 行 ≠ 期望 %d 行" % (len(bl), len(keep)))
                lost = len(fresh) - len(R.parse_index(back, idx, store))
                if lost != len(drop):
                    bad.append("解析行数少 %d ≠ 计划删 %d（误伤非目标行）" % (lost, len(drop)))
            if bad:
                shutil.copy2(idx + ".bak", idx)
                failed.append("%s：%s ⇒ 已从 .bak 回滚" % (rel, "；".join(bad)))
            else:
                print("  ✏️  已更新 %s（备份 %s.bak，写后验收通过：删 %d 行、误伤 0、行尾 %s）"
                      % (rel, os.path.basename(idx), len(drop), "CRLF" if eol == "\r\n" else "LF"))

        if failed:
            print("\n[FAIL] APPLY 写后验收未过，已回滚（原计划改动 %d 行未生效）：" % changed)
            for s in failed:
                print("   " + s)
            return 4
        for s in skipped_files + unlabeled:
            print("  ⚠️  " + s)
        print("\nAPPLY 完成：改动 %d 行。文件未删；冷记忆/V≤0 请人工确认后移入 archive/。"
              "升华候选 %d 组需按 §6 写进 CORE。" % (changed, len(ready)))
        return 3 if blind else (1 if (total or n_hard or skipped_files or unlabeled) else 0)
    finally:
        if lock_path:
            release_lock(lock_path)


def line_of_row(text, rows):
    """把 parse_index 出来的行按文档顺序对回原文行号（apply 只按行号删，不按内容删）。

    不用「ID 在行里出现过」这类模糊匹配：pit-002 的结论句里就写着 pit-001，
    模糊匹配会把邻居圈进删除范围（RT-005 那一族：替换范围过大吞掉别人的行）。
    这里只认「表格行首格＝ID」与「链接文字＝ID」两种体式；
    同 ID 出现多行（真重复）时按顺序逐行配对——要删的是后一行，不是首行。
    认不出行号返回 0，调用方据此放弃删除（宁可不删，不可删错）。
    """
    cand = {}
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not s:
            continue
        rid = ""
        if s.startswith("|"):
            if not R.SEP_RE.match(s):
                rid = s.strip("|").split("|")[0].strip()
        else:
            m = R.LINE_RE.match(s)
            if m:
                rid = m.group("title").strip()
        if rid:
            cand.setdefault(rid, []).append(i)
    out = []
    for r in rows:
        bucket = cand.get((r["id"] or "").strip())
        out.append(bucket.pop(0) if bucket else 0)
    return out



if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        # 崩溃不能退 1（1=有整理动作），也不能退 0；单独一个码，免得被读成「库很干净」。
        msg = "[organize] state=tool_error｜工具自身异常，没有得出整理结论（这不是「没有问题」）\n" + \
            __import__("traceback").format_exc()
        print(msg, file=sys.stderr)
        print(msg)
        sys.exit(3)
