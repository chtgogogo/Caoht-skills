#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence_chain —— 证据哈希链（EverSpec ⑧-5「控制的运行期证明」的最轻形态）。

零依赖（Python 3 标准库）。把「谁在什么时候用哪个产物做了什么」逐条追加成防篡改链：
每行 = 前条哈希(prev) ＋ 动作(action) ＋ 产物摘要(digest, sha256) ＋ 时间戳(ts) ＋ 本行哈希(hash)。
check 逐行重算：链断（prev 咬合不上/序号跳）、中间行被改（内容与行哈希不符）、产物被改
（digest 与落链值不符）＝红；产物不在盘上（可能被移档）＝警——链仍自洽，只是 digest 未能复核。

边界（诚实口径）：哈希链证明「落链之后没人动过」，不证明「落链那一刻产物是真的」——
产物真伪靠生成它的工具与人工验收（spec_check/测试/签核），本链只管防事后翻改。
🔴 链尾截断（删掉最后几条）**链内查不出**——没有任何行引用被删的尾条。兜法＝把链尾哈希
记到链外（check/append 都会打出链尾哈希，抄进台账或交接说明；对不上＝尾巴被动过）。
追加端在链损坏时拒笔（不延长坏链）——坏链要先修复或另起新链，不许带伤续写。

    py scripts/evidence_chain.py append --file <…/evidence.ndjson> --action <动作名> --artifact <产物路径> [--note 备注]
    py scripts/evidence_chain.py check  --file <…/evidence.ndjson> [--art-root <目录>]
    py scripts/evidence_chain.py selftest [--tmp-root <目录>]      # 红例自证，只写临时树

退出码：0 全绿（可含警）｜1 有红；链损坏时 append 也退 1（拒笔）｜2 用法不对（缺参/产物不在盘/链文件不在）
路径口径：产物路径推荐写「相对链文件所在目录」的路径（check 默认以链文件目录为解析根，--art-root 可换）；
append 按调用时的路径原样落链（当时能读到才算数）。
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.1.0"
GENESIS = "GENESIS"
ASSERT_FLOOR = 20   # selftest 断言分母闸：条数掉了就该红（v1.1.0 随 K02 runs.log 接线用例＋变异臂同升）


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def line_canon(d: dict) -> str:
    """参与哈希的字段与顺序单源：去掉 hash 字段后按 key 排序序列化。"""
    return json.dumps({k: v for k, v in d.items() if k != "hash"}, sort_keys=True, ensure_ascii=False)


def line_hash(d: dict) -> str:
    return sha256_bytes(line_canon(d).encode("utf-8"))


def load_lines(fp: Path):
    """读链文件 → [(行号, entry)]；坏 JSON 行原样带回（entry=None）交 check 点名，append 拒笔。"""
    out = []
    if not fp.is_file():
        return out
    for i, raw in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            out.append((i, json.loads(raw)))
        except json.JSONDecodeError:
            out.append((i, None))
    return out


def chain_problems(entries):
    """链完整性 → [(级别, 人话)]。只看链本身：行哈希重算、prev 咬合、seq 连续。"""
    problems = []
    prev_hash = GENESIS
    for idx, (no, d) in enumerate(entries, 1):
        if d is None or not isinstance(d, dict):
            problems.append(("红", f"第 {no} 行不是合法 JSON——链在此中断"))
            prev_hash = None
            continue
        if "hash" not in d:
            problems.append(("红", f"第 {no} 行缺 hash 字段——不是本链的行"))
            prev_hash = None
            continue
        if d.get("prev") != prev_hash:
            problems.append(("红", f"第 {no} 行 prev 与前条哈希咬合不上（断链或中间行被删）"))
        if d.get("seq") != idx:
            problems.append(("红", f"第 {no} 行 seq={d.get('seq')}，应为 {idx}（序号断档或重排）"))
        if line_hash(d) != d["hash"]:
            problems.append(("红", f"第 {no} 行内容与本行 hash 不符——落链后被改过"))
        prev_hash = d["hash"]
    return problems


def artifact_problems(entries, art_root: Path):
    """产物面 → [(级别, 人话)]。digest 不符＝红；产物不在盘＝警（可能移档，链仍自洽）。"""
    problems = []
    for no, d in entries:
        if not isinstance(d, dict) or "artifact" not in d:
            continue
        ap = Path(d["artifact"])
        if not ap.is_absolute():
            ap = art_root / ap
        if not ap.is_file():
            problems.append(("警", f"第 {no} 行产物 {d['artifact']} 不在盘上（可能被移档）——digest 未能复核"))
            continue
        if sha256_bytes(ap.read_bytes()) != d.get("digest"):
            problems.append(("红", f"第 {no} 行产物 {d['artifact']} 当前 digest 与落链值不符——产物在落链后被改过"))
    return problems


# v1.1.0（K02 · MUST-17 运行读数）：append/check 各向本件 evidence/runs.log 追加一行读数——
# 链文件零触碰（只读承诺不变），读数落本件自己的 evidence/；写不进去静默跳过（尽力而为，
# 绝不影响退出码）。拒笔/用法错（退 1/2）不记读数——没落成的账不算跑过。行格式单源＝细则 §6.7。
RUNSLOG_ON = True   # 🔴 单占一行：变异探针臂（S18）按行首锚点整行替换，拆行=探针失锚。


def _append_runs_log(tool, action, where, red=None, warn=None, detail=""):
    try:
        log = Path(os.environ.get("EVSPEC_RUNS_LOG") or
                   (Path(__file__).resolve().parent.parent / "evidence" / "runs.log"))
        log.parent.mkdir(parents=True, exist_ok=True)
        parts = [datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), tool, action, where]
        if red is not None:
            parts.append(f"红 {red}｜警 {warn}")
        if detail:
            parts.append(detail)
        with log.open("a", encoding="utf-8") as f:
            f.write("｜".join(parts) + "\n")
    except Exception:
        pass


def cmd_append(args):
    fp = Path(args.file)
    art = Path(args.artifact)
    if not art.is_file():
        print(f"[用法错] 产物不存在：{art} ⇒ 退 2")
        return 2
    entries = load_lines(fp)
    if entries:
        base = chain_problems(entries)
        if any(lv == "红" for lv, _ in base):
            for lv, msg in base:
                if lv == "红":
                    print(f"[红] {msg}")
            print("[拒笔] 链已损坏，append 不延长坏链——先修复或另起新链 ⇒ 退 1")
            return 1
        prev = entries[-1][1]["hash"]
        seq = len(entries) + 1
    else:
        prev, seq = GENESIS, 1
    entry = {
        "seq": seq,
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "action": args.action,
        "artifact": args.artifact,
        "digest": sha256_bytes(art.read_bytes()),
        "prev": prev,
    }
    if args.note:
        entry["note"] = args.note
    entry["hash"] = line_hash(entry)
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"[已追加] 第 {seq} 条 · {args.action} · {args.artifact} · {entry['hash'][:21]}…")
    if RUNSLOG_ON:
        _append_runs_log("evidence_chain", "append", f"file={fp}", detail=f"seq={seq}")
    return 0


def cmd_check(args):
    fp = Path(args.file)
    if not fp.is_file():
        print(f"[没查] 链文件不存在：{fp} ⇒ 退 2")
        return 2
    entries = load_lines(fp)
    art_root = Path(args.art_root).resolve() if args.art_root else fp.resolve().parent
    problems = chain_problems(entries) + artifact_problems(entries, art_root)
    print(f"[evidence_chain v{VERSION}] file={fp} 条目={len(entries)}")
    for lv, msg in problems:
        print(f"[{lv}] {msg}")
    if not problems:
        print(f"[全绿] {len(entries)} 条链完整，逐行哈希重算一致")
    tip = entries[-1][1].get("hash") if entries and isinstance(entries[-1][1], dict) else None
    print(f"[链尾] {tip or '（链尾损坏）'}——抄进台账/交接说明当外部锚，防链尾截断（链内查不出删尾）")
    if args.tip and tip != args.tip:
        problems.append(("红", f"链尾哈希与外部记录不符（记录 {args.tip[:21]}…，实际 {str(tip)[:21]}…）——链尾被截断或替换"))
        print("[红] 链尾哈希与外部记录不符——链尾被截断或替换")
    reds = sum(1 for lv, _ in problems if lv == "红")
    print(f"[汇总] 红 {reds}｜警 {len(problems) - reds}")
    if RUNSLOG_ON:
        _append_runs_log("evidence_chain", "check", f"file={fp}", red=reds, warn=len(problems) - reds)
    return 1 if reds else 0


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    home = Path(__file__).resolve().parent
    tmp_root = Path(args.tmp_root) if args.tmp_root else home.parent / "_tmp-chain-selftest"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["EVSPEC_RUNS_LOG"] = str(tmp_root / "runs.log")   # v1.1.0（K02）：运行读数落临时树
    scripts_before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}

    results = []
    seq = [0]

    def mk_case():
        seq[0] += 1
        d = tmp_root / f"c{seq[0]}"
        (d / "ev").mkdir(parents=True)
        (d / "ev" / "a1.txt").write_text("证据一", encoding="utf-8")
        (d / "ev" / "a2.txt").write_text("证据二", encoding="utf-8")
        return d, d / "ev" / "chain.ndjson"

    def run(*cli):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(cli))
        return rc, buf.getvalue()

    def green(desc, cond, detail=""):
        results.append((desc, bool(cond), detail))

    _src_self = Path(__file__).resolve().read_text(encoding="utf-8")

    def run_mutant(anchor, replacement, cli):
        """变异探针：打坏实现本体后重载，点名现象必须翻面（原样对照臂=对应 S 用例）。"""
        hits = [l for l in _src_self.splitlines() if l.strip().startswith(anchor)]
        if not hits:
            return None, ""
        g = {"__file__": str(Path(__file__).resolve()), "__name__": "chain_mutant"}
        try:
            exec(compile(_src_self.replace(hits[0], replacement), "<chain_mutant>", "exec"), g)
        except Exception:
            return None, ""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = g["main"](list(cli))
        return rc, buf.getvalue()

    # —— S01 地基：两条追加 + check 全绿退 0
    d1, fp1 = mk_case()
    rc_a1, _ = run("append", "--file", str(fp1), "--action", "selftest跑尺", "--artifact", str(d1 / "ev" / "a1.txt"))
    rc_a2, _ = run("append", "--file", str(fp1), "--action", "安检跑", "--artifact", str(d1 / "ev" / "a2.txt"))
    rc_c1, out_c1 = run("check", "--file", str(fp1))
    green("S01 合法两跳链：append×2 退 0，check 全绿退 0",
          rc_a1 == 0 and rc_a2 == 0 and rc_c1 == 0 and "[全绿]" in out_c1,
          f"退码={rc_a1}/{rc_a2}/{rc_c1}")

    # —— S02 结构哨兵：首条 prev=GENESIS、prev 咬合、seq 连续、ts 形状
    ent = [json.loads(l) for l in fp1.read_text(encoding="utf-8").splitlines() if l.strip()]
    green("S02 结构：首条 prev=GENESIS，次条 prev=首条 hash，seq 连续，ts 为本地 ISO 形",
          ent[0]["prev"] == GENESIS and ent[1]["prev"] == ent[0]["hash"]
          and [e["seq"] for e in ent] == [1, 2]
          and len(ent[0]["ts"]) == 19 and "T" in ent[0]["ts"],
          f"prev0={ent[0]['prev'][:9]}… prev1={ent[1]['prev'][:9]}… seq={[e['seq'] for e in ent]} ts={ent[0]['ts']}")

    def tamper(fp, old, new):
        txt = fp.read_text(encoding="utf-8")
        assert old in txt, f"变异锚点丢了：{old[:40]}"
        fp.write_text(txt.replace(old, new), encoding="utf-8")

    # —— S03 中间行被改 ⇒ 红且点名行
    d3, fp3 = mk_case()
    run("append", "--file", str(fp3), "--action", "第一跳", "--artifact", str(d3 / "ev" / "a1.txt"))
    run("append", "--file", str(fp3), "--action", "第二跳", "--artifact", str(d3 / "ev" / "a2.txt"))
    tamper(fp3, "第二跳", "第二跳改")
    rc_c3, out_c3 = run("check", "--file", str(fp3))
    green("S03 中间行 action 被改一个字 ⇒ check 退 1 且点名该行",
          rc_c3 == 1 and "落链后被改过" in out_c3,
          f"退码={rc_c3} 首红行={[l for l in out_c3.splitlines() if l.startswith('[红]')][:1]}")

    # —— S04 删真中间行（三跳链）⇒ 断链红；S04b 哨兵：删链尾查不出（诚实边界，靠外部链尾哈希兜）
    d4, fp4 = mk_case()
    run("append", "--file", str(fp4), "--action", "第一跳", "--artifact", str(d4 / "ev" / "a1.txt"))
    run("append", "--file", str(fp4), "--action", "第二跳", "--artifact", str(d4 / "ev" / "a2.txt"))
    run("append", "--file", str(fp4), "--action", "第三跳", "--artifact", str(d4 / "ev" / "a1.txt"))
    lines4 = fp4.read_text(encoding="utf-8").splitlines()
    fp4.write_text("\n".join(lines4[:1] + lines4[2:]) + "\n", encoding="utf-8")
    rc_c4, out_c4 = run("check", "--file", str(fp4))
    green("S04 三跳链删掉中间一行 ⇒ 断链红（prev 咬合不上）",
          rc_c4 == 1 and "咬合不上" in out_c4,
          f"退码={rc_c4}")
    rc_t, out_t = run("check", "--file", str(fp4))
    tip_t = [l for l in out_t.splitlines() if l.startswith("[链尾]")]
    green("S04b check 打出链尾哈希（外部锚燃料；删链尾链内查不出=已知边界）",
          len(tip_t) == 1 and "sha256:" in tip_t[0],
          f"链尾行={tip_t[:1]}")

    # —— S04c 外部链尾锚：删链尾后 check --tip 对不上 ⇒ 红（链尾截断也能查出了）
    d4c, fp4c = mk_case()
    run("append", "--file", str(fp4c), "--action", "第一跳", "--artifact", str(d4c / "ev" / "a1.txt"))
    run("append", "--file", str(fp4c), "--action", "第二跳", "--artifact", str(d4c / "ev" / "a2.txt"))
    tip_recorded = json.loads(fp4c.read_text(encoding="utf-8").splitlines()[-1])["hash"]
    lines4c = fp4c.read_text(encoding="utf-8").splitlines()
    fp4c.write_text("\n".join(lines4c[:-1]) + "\n", encoding="utf-8")   # 删链尾
    rc_c4c, out_c4c = run("check", "--file", str(fp4c), "--tip", tip_recorded)
    green("S04c 删链尾后 check --tip 外部记录 ⇒ 红（截断可查，配套台账记链尾）",
          rc_c4c == 1 and "链尾哈希与外部记录不符" in out_c4c,
          f"退码={rc_c4c}")

    # —— S05 只改 hash 字段本身 ⇒ 红（内容哈希对不上）
    d5, fp5 = mk_case()
    run("append", "--file", str(fp5), "--action", "第一跳", "--artifact", str(d5 / "ev" / "a1.txt"))
    e5 = json.loads(fp5.read_text(encoding="utf-8").splitlines()[0])
    e5["hash"] = "sha256:" + "0" * 64
    fp5.write_text(json.dumps(e5, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    rc_c5, out_c5 = run("check", "--file", str(fp5))
    green("S05 只改 hash 字段 ⇒ 红（重算哈希对不上，防「改了账再改哈希」）",
          rc_c5 == 1 and "落链后被改过" in out_c5, f"退码={rc_c5}")

    # —— S06 产物被改写 ⇒ digest 红；S07 产物移走 ⇒ 警不红
    d6, fp6 = mk_case()
    run("append", "--file", str(fp6), "--action", "评测跑", "--artifact", str(d6 / "ev" / "a1.txt"))
    (d6 / "ev" / "a1.txt").write_text("证据一（被改）", encoding="utf-8")
    rc_c6, out_c6 = run("check", "--file", str(fp6))
    green("S06 产物落链后被改写 ⇒ digest 不符红",
          rc_c6 == 1 and "产物在落链后被改过" in out_c6, f"退码={rc_c6}")
    d7, fp7 = mk_case()
    run("append", "--file", str(fp7), "--action", "评测跑", "--artifact", str(d7 / "ev" / "a1.txt"))
    (d7 / "ev" / "a1.txt").rename(d7 / "ev" / "a1-移档.txt")
    rc_c7, out_c7 = run("check", "--file", str(fp7))
    green("S07 产物移档不在盘 ⇒ 警不红退 0（链仍自洽，点名 digest 未能复核）",
          rc_c7 == 0 and "digest 未能复核" in out_c7, f"退码={rc_c7}")

    # —— S08 断链后 append 拒笔（退 1 且行数不变）；S09 产物缺失 append 退 2
    d8, fp8 = mk_case()
    run("append", "--file", str(fp8), "--action", "第一跳", "--artifact", str(d8 / "ev" / "a1.txt"))
    run("append", "--file", str(fp8), "--action", "第二跳", "--artifact", str(d8 / "ev" / "a2.txt"))
    tamper(fp8, "第一跳", "第一跳改")
    n8 = len(fp8.read_text(encoding="utf-8").splitlines())
    rc_a8, out_a8 = run("append", "--file", str(fp8), "--action", "第三跳", "--artifact", str(d8 / "ev" / "a1.txt"))
    green("S08 链损坏后 append 拒笔 ⇒ 退 1 且行数不变（不延长坏链）",
          rc_a8 == 1 and "拒笔" in out_a8 and len(fp8.read_text(encoding='utf-8').splitlines()) == n8,
          f"退码={rc_a8} 行数 {n8}→{len(fp8.read_text(encoding='utf-8').splitlines())}")
    rc_a9, _ = run("append", "--file", str(d8 / "ev" / "x.ndjson"), "--action", "缺产物", "--artifact", str(d8 / "ev" / "不在.txt"))
    green("S09 append 的产物不存在 ⇒ 退 2（不落一行没有 digest 的账）", rc_a9 == 2, f"退码={rc_a9}")

    # —— S10 链文件不存在 check 退 2（没查 ≠ 全绿）
    rc_c10, _ = run("check", "--file", str(tmp_root / "无此链.ndjson"))
    green("S10 链文件不存在 check 退 2", rc_c10 == 2, f"退码={rc_c10}")

    # —— S11 反查：哈希灵敏度（一字之差必不同）
    h_a = line_hash({"a": 1})
    h_b = line_hash({"a": 2})
    green("S11 反查：line_hash 对一字之差必不同（哈希不是恒等）", h_a != h_b,
          f"{h_a[:16]}… vs {h_b[:16]}…")

    # —— S12 变异臂：line_hash 打哑成常量 ⇒ S03 树由红变绿（探针钉住实现）
    d12, fp12 = mk_case()
    cli_a1 = ["append", "--file", str(fp12), "--action", "第一跳", "--artifact", str(d12 / "ev" / "a1.txt")]
    cli_a2 = ["append", "--file", str(fp12), "--action", "第二跳", "--artifact", str(d12 / "ev" / "a2.txt")]
    run_mutant("return sha256_bytes(line_canon", '    return "sha256:mutated-constant"', cli_a1)
    run_mutant("return sha256_bytes(line_canon", '    return "sha256:mutated-constant"', cli_a2)
    tamper(fp12, "第二跳", "第二跳改")
    rc_m12, out_m12 = run_mutant("return sha256_bytes(line_canon", '    return "sha256:mutated-constant"',
                                 ["check", "--file", str(fp12)])
    green("S12 变异臂：line_hash 打哑成常量 ⇒ 篡改不再被识破（S03 现象翻面=实现被钉住）",
          rc_m12 == 0 and "落链后被改过" not in out_m12,
          f"变异后退码={rc_m12}")

    # —— v1.1.0 S16~S18（K02 · MUST-17）：runs.log 运行读数接线
    rlog = tmp_root / "runs.log"

    def _rl_lines():
        return rlog.read_text(encoding="utf-8").splitlines() if rlog.is_file() else []

    d16, fp16 = mk_case()
    n16 = len(_rl_lines())
    run("append", "--file", str(fp16), "--action", "读数接线", "--artifact", str(d16 / "ev" / "a1.txt"))
    ls16 = _rl_lines()
    green("S16 runs.log 接线：append 落一行读数（带 evidence_chain 与 file=）",
          len(ls16) == n16 + 1 and "evidence_chain" in ls16[-1] and "file=" in ls16[-1],
          f"行数 {n16}→{len(ls16)} 尾行={ls16[-1][:64] if ls16 else '（无）'}")

    n17 = len(ls16)
    run("check", "--file", str(fp16))
    ls17 = _rl_lines()
    green("S17 runs.log 接线：check 落一行且红警读数与链状态一致（全绿链 红 0）",
          len(ls17) == n17 + 1 and "红 0" in ls17[-1] and "check" in ls17[-1],
          f"行数 {n17}→{len(ls17)} 尾行={ls17[-1][:64] if ls17 else '（无）'}")

    d18, fp18 = mk_case()
    run("append", "--file", str(fp18), "--action", "第一跳", "--artifact", str(d18 / "ev" / "a1.txt"))
    n18 = len(_rl_lines())
    rc_m18, _ = run_mutant("RUNSLOG_ON = ", "RUNSLOG_ON = False", ["check", "--file", str(fp18)])
    green("S18 变异臂：RUNSLOG_ON 打哑 ⇒ 读数不再追加（接线被钉住）",
          rc_m18 == 0 and len(_rl_lines()) == n18,
          f"行数 {n18}→{len(_rl_lines())} 变异退码={rc_m18}")

    # —— S13 分母闸 / S14 scripts 零写入 / S15 收尾清理
    green(f"S13 断言条数 ≥ 分母闸 {ASSERT_FLOOR}", len(results) + 3 >= ASSERT_FLOOR,
          f"本轮 {len(results) + 3} 条")
    scripts_after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    green("S14 scripts/ 目录零写入（兄弟文件 mtime 全等）", scripts_before == scripts_after,
          f"变化={sorted(set(scripts_before) ^ set(scripts_after))}")
    shutil.rmtree(tmp_root, ignore_errors=True)
    green("S15 收尾清理临时树", not tmp_root.exists(), f"残留={tmp_root.exists()}")

    total = len(results)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"[evidence_chain selftest v{VERSION}] 临时树={tmp_root}")
    for desc, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}｜{detail}")
    print(f"断言 {total} 条：PASS={npass} FAIL={total - npass}")
    return 0 if npass == total else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="EverSpec 证据哈希链（⑧-5 控制的运行期证明，零依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("append", help="追加一条（前条哈希＋动作＋产物 digest＋时间戳）")
    a1.add_argument("--file", required=True, help="链文件（ndjson，逐行一条）")
    a1.add_argument("--action", required=True, help="动作名（如 spec_check.check／安检跑／评测跑）")
    a1.add_argument("--artifact", required=True, help="产物路径（原样落链）")
    a1.add_argument("--note", default=None, help="备注（可选）")
    a1.set_defaults(func=cmd_append)
    a2 = sub.add_parser("check", help="验链：断链/中间改/产物改=红，产物移档=警")
    a2.add_argument("--file", required=True)
    a2.add_argument("--art-root", default=None, help="相对产物路径的解析根（默认=链文件所在目录）")
    a2.add_argument("--tip", default=None, help="外部记录的链尾哈希（台账/交接说明里抄的那串；不符=链尾被截断或替换，红）")
    a2.set_defaults(func=cmd_check)
    a3 = sub.add_parser("selftest", help="红例自证（只写临时树）")
    a3.add_argument("--tmp-root", default=None)
    a3.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
