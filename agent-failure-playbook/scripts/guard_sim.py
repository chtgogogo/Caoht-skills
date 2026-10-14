# -*- coding: utf-8 -*-
"""可观察行为探针（B-01…B-05）：防线到底拦没拦在半路，看打印出来的量，不看说明书。

每个 case 都跑**两遍**：
  naive   ＝审查报告点名的那种最小实现（没有迭代上限 / 只比上一轮 / 返回后才累计 / 按请求体哈希去重 / 只靠提示词隔离）
  guarded ＝本库 P-02 / P-07 / P-08 与 GOV-3 的写法
判据必须能红：naive 版必须失败。naive 也通过 ⇒ 探针是恒真判据 ⇒ 本脚本退出码 1 并点名哪条断言废了。
这是本地"变异检验／红绿证据"口径在 skill 里的落地：不是"跑出绿"就算，而是"该红的红、该绿的绿"。

零依赖、无随机、只跑内存模拟（不碰盘、不发网络）。命令：
  py scripts/guard_sim.py                 # 五个 case 全跑
  py scripts/guard_sim.py --case budget   # 只跑一个（loop / cycle / budget / idem / tenant）
退出码：0 全部符合预期｜1 有断言不符合（含"探针失效"）｜2 用法错误。
"""
import argparse
import json
import sys

CHECKS = []


def obs(**kv):
    print("  " + " ".join("%s=%s" % (k, v) for k, v in kv.items()))


def check(name, ok, detail=""):
    CHECKS.append((name, ok))
    print(("[绿] " if ok else "[红] ") + name + ("｜" + detail if detail else ""))


# ------------------------------------------------------------------ B-01 循环真的停了

def case_loop():
    print("case loop（B-01 循环真的停了）")
    injected, max_rounds, trip_after = 200, 20, 3

    def run(limit, breaker):
        no_progress = 0
        for r in range(1, injected + 1):
            no_progress += 1                     # 每轮同工具同参数＝没有进展
            if breaker and no_progress >= trip_after:
                return r, "circuit_open"
            if limit is not None and r >= limit:
                return r, "iteration_limit"
        return injected, "natural_end"           # 注入的 200 轮全跑完，没停

    naive, guarded = run(None, False), run(max_rounds, True)
    obs(naive_halt_round=naive[0], naive_exit_reason=naive[1],
        guarded_halt_round=guarded[0], guarded_exit_reason=guarded[1])
    check("B-01 naive：没有上限的循环把注入的 200 轮跑满了（没停）", naive[1] == "natural_end",
          "naive 也停了＝探针恒真")
    check("B-01 guarded：第 %d 轮退出且原因是熔断触发（不是人工打断）" % guarded[0],
          guarded[0] <= max_rounds and guarded[1] == "circuit_open")


# ------------------------------------------------------------------ B-02 周期不为 1 的交替

def detect(seq, window, mode):
    """mode=lag1 只比上一轮；mode=set 与保留窗口内的历史集合比对。返回 (判出轮次, 周期)。"""
    seen = []
    for i, fp in enumerate(seq):
        if mode == "lag1":
            if i > 0 and fp == seq[i - 1]:
                return i + 1, 1
        else:
            recent = seen[-window:]
            if fp in recent:
                last = len(seen) - 1 - recent[::-1].index(fp)
                return i + 1, i - last
        seen.append(fp)
    return None, None


def case_cycle():
    print("case cycle（B-02 A→B→A→B 这种周期不为 1 的交替）")
    seq = ["A", "B"] * 30                        # 相邻两轮永不相等
    naive, guarded = detect(seq, 5, "lag1"), detect(seq, 5, "set")
    obs(naive_detected_at=naive[0], naive_period=naive[1],
        guarded_detected_at=guarded[0], guarded_period=guarded[1])
    check("B-02 naive：只比上一轮的指纹在 60 轮交替里一次都没判出循环（定义级盲区）",
          naive[0] is None, "naive 若判出来了，说明序列构造错了（存在相邻相等）")
    check("B-02 guarded：命中历史集合，第 %s 轮判出周期 %s" % (guarded[0], guarded[1]),
          guarded[0] is not None and guarded[1] == 2)
    self_loop = detect(["A", "A"], 5, "lag1")
    obs(lag1_selfloop_round=self_loop[0], lag1_selfloop_period=self_loop[1])
    check("B-02 同尺子的能红面：lag-1 对周期 1（原地重试）确实判得出（否则是尺子坏了）",
          self_loop == (2, 1))


# ------------------------------------------------------------------ B-03 预算拦在半路

def case_budget():
    print("case budget（B-03 预算拦在半路，不是事后算账）")
    cap, est, n_calls, actual = 100.0, 30.0, 5, 30.0

    def spend(reserve):
        """reserve=True：发起前预留额度、在途计入已用；False：等返回后累计才发现超支。"""
        pool_left, spent, inflight, rejected_at = cap, 0.0, 0.0, None
        for i in range(1, n_calls + 1):
            if reserve:
                if pool_left < est:
                    rejected_at = i              # 拦在发起**之前**
                    continue
                pool_left -= est
                inflight += est
            inflight -= est
            spent += actual
            pool_left += est - actual
            if not reserve and spent > cap:
                rejected_at = i                  # 拦在返回**之后**：钱已经花了
                break
        return spent, rejected_at

    naive, guarded = spend(False), spend(True)
    obs(naive_spent=naive[0], naive_overshoot=round(naive[0] - cap, 2), naive_rejected_after_call=naive[1],
        guarded_spent=guarded[0], guarded_overshoot=round(guarded[0] - cap, 2),
        guarded_rejected_before_call=guarded[1])
    check("B-03 naive：返回后才累计，并发 5 笔冲破上限（超支 %.1f，发生在第 %s 笔之后）"
          % (naive[0] - cap, naive[1]), naive[0] > cap, "naive 都没超支＝负载太轻，探针不承载信息")
    check("B-03 guarded：第 %s 笔在发起前被拒，且总花费 %.1f 未超上限" % (guarded[1], guarded[0]),
          guarded[1] is not None and guarded[0] <= cap)


# ------------------------------------------------------------------ B-04 幂等三情形

def run_idem(mode):
    """mode=semantic 键＝业务语义键；mode=body_hash 键＝请求体哈希（naive，带时间戳就绕开）。"""
    table, pending, log = {}, set(), []
    st = {"writes": 0}

    def key_of(key, params, ts):
        return "%s|%s|%s" % (key, ts, json.dumps(params, sort_keys=True)) if mode == "body_hash" else key

    def arrive(key, params, ts, reply=True):
        k = key_of(key, params, ts)
        if mode == "semantic" and key in pending:
            log.append("409_inflight_not_saved")           # 首次还没返回：这次不落键，稍后可重试
            return
        cur = table.get(k)
        if cur is not None and cur["params"] != params:
            log.append("422_param_mismatch")               # 同键异参：报错，不静默执行
            return
        if cur is not None:
            log.append("replay_%s" % cur["status"])        # 断点后重试：回放首次结果（连 500 也回放）
            return
        st["writes"] += 1
        pending.add(key)
        log.append("execute_started")
        if reply:
            pending.discard(key)
            table[k] = {"params": params, "status": 500}   # 首次其实失败了，也要把结果落键
            log[-1] = "executed_500"

    p1, p2 = {"acct": "x", "amt": 10}, {"acct": "x", "amt": 99}
    arrive("K1", p1, ts=1, reply=False)                   # 1 发起后崩溃／未返回
    arrive("K1", p1, ts=2)                                # 2 首次仍在执行时重复到达
    arrive("K1", p1, ts=3)                                # 3 同上（换个时间戳）
    table[key_of("K1", p1, 1 if mode == "semantic" else 1)] = {"params": p1, "status": 500}
    pending.discard("K1")
    before_prune = st["writes"]
    arrive("K1", p1, ts=4)                                # 4 断点恢复后同键重试 → 应回放
    arrive("K1", p2, ts=5)                                # 5 同键不同参数 → 应报错
    table.clear()                                         # 6 键过了保留期被清理
    arrive("K1", p1, ts=6)
    return st["writes"], before_prune, log


def case_idem():
    print("case idem（B-04 同键重试／同键异参／首次未返回时重复到达／键的保留期）")
    naive_w, naive_before, naive_log = run_idem("body_hash")
    guarded_w, guarded_before, guarded_log = run_idem("semantic")
    obs(naive_writes_before_prune=naive_before, naive_events=",".join(naive_log),
        guarded_writes_before_prune=guarded_before, guarded_events=",".join(guarded_log))
    check("B-04 naive：按请求体哈希去重时时间戳一变就重复执行（保留期内真实写入 %d 次）" % naive_before,
          naive_before > 1, "naive 也只写一次＝去重没做错，探针失效")
    check("B-04 guarded：保留期内真实写入 1 次", guarded_before == 1)
    check("B-04 guarded：断点后同键重试走回放（返回首次的 500，不是又执行一遍）",
          "replay_500" in guarded_log)
    check("B-04 guarded：同键异参被拒绝而不是静默执行", "422_param_mismatch" in guarded_log)
    check("B-04 guarded：首次仍在执行时不落键（允许稍后重试）", "409_inflight_not_saved" in guarded_log)
    check("B-04 保留期边界（两种实现一样）：键被清理后同一个键会被当成新请求真的再执行",
          guarded_w == guarded_before + 1)


# ------------------------------------------------------------------ B-05 跨租户读取

ROWS = [{"tenant": "t-A", "id": 1, "text": "A 的报销单"},
        {"tenant": "t-B", "id": 2, "text": "B 的病历"}]


def search(session_tenant, use_tenant_key, direct_id=None):
    """use_tenant_key=False＝只在提示词里写了"绝不返回其他租户的数据"，查询本身不带租户键。"""
    if direct_id is not None:
        row = [r for r in ROWS if r["id"] == direct_id][0]
        if row["tenant"] != session_tenant and use_tenant_key:
            raise PermissionError("tenant_isolation_violation:%s" % row["id"])
        return [row["tenant"]]
    return [r["tenant"] for r in ROWS if not use_tenant_key or r["tenant"] == session_tenant]


def case_tenant():
    print("case tenant（B-05 跨租户读取：代码层拒绝 vs 提示词自律）")
    naive = search("t-A", False)
    naive_direct = search("t-A", False, direct_id=2)
    guarded = search("t-A", True)
    try:
        direct = search("t-A", True, direct_id=2)
        raised = None
    except PermissionError as exc:
        direct, raised = None, str(exc)
    obs(naive_hits=",".join(naive), naive_direct_hits=",".join(naive_direct),
        guarded_hits=",".join(guarded), guarded_direct=raised or "no-error")
    check("B-05 naive：检索不带租户键时，A 的会话拿到了 %s（提示词没挡住）" % ",".join(naive),
          "t-B" in naive and "t-B" in naive_direct,
          "提示词居然挡住了＝这条探针测的是模型不是代码，判据作废")
    check("B-05 guarded：检索强制带租户键，他人数据 0 条", "t-B" not in guarded)
    check("B-05 guarded：直查他人 id 时在代码层抛错（不是静默过滤，让调用方知道被拒）",
          raised is not None and raised.startswith("tenant_isolation_violation"))


CASES = {"loop": case_loop, "cycle": case_cycle, "budget": case_budget,
         "idem": case_idem, "tenant": case_tenant}


def main(argv=None):
    ap = argparse.ArgumentParser(description="失败模式对策的可观察行为探针（内存模拟，不碰盘不发网络）")
    ap.add_argument("--case", choices=sorted(CASES), default=None)
    ap.add_argument("--json", action="store_true", help="把断言结果另收成一份 JSON 打印（供体检消费）")
    a = ap.parse_args(argv)
    for n in ([a.case] if a.case else sorted(CASES)):
        CASES[n]()
        print("")
    n_pass = sum(1 for _k, ok in CHECKS if ok)
    print("断言 %d 条：绿 %d／红 %d" % (len(CHECKS), n_pass, len(CHECKS) - n_pass))
    if a.json:
        print(json.dumps([{"check": k, "ok": ok} for k, ok in CHECKS], ensure_ascii=False))
    return 0 if n_pass == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
