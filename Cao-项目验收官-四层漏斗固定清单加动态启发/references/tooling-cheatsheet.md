# 工具命令速查（验收官工具箱）

> 原则：项目里已有配置的工具优先用项目自己的命令；没有的按本表补。工具不可用时该项记 ⬜ 并附安装命令，不许跳过不提。所有输出重定向落盘到 `acceptance/evidence/`（按测试纪律）。
> Windows 注意：以下命令在 Git Bash 下运行；pip 系工具优先 `python -m xxx` 调用避免 PATH 坑。

## Python 项目

```bash
# 测试（证据落盘）
python -m pytest tests -q > acceptance/evidence/pytest_$(date +%Y%m%d).txt 2>&1; echo "exit=$?"
# 类型检查
python -m mypy <包名> || pip install mypy
# Lint
python -m ruff check . || python -m flake8 .
# 依赖漏洞
python -m pip_audit || pip install pip-audit        # 备选：osv-scanner
# 冗余依赖参考
pipreqs . --print                                    # 对比声明与实际 import
# 密钥扫描
gitleaks detect --source . --no-git || pip install detect-secrets
# 覆盖率
python -m pytest tests --cov=<包名> --cov-report=term
```

## Node/前端项目

```bash
npm test -- --run > acceptance/evidence/jest_$(date +%Y%m%d).txt 2>&1
npx tsc --noEmit          # 类型
npx eslint .              # Lint
npm audit --audit-level=high
npx depcheck              # 冗余依赖
npx gitleaks detect --source . --no-git
```

## Go 项目

```bash
go test ./... -count=1 > acceptance/evidence/gotest_$(date +%Y%m%d).txt 2>&1
go vet ./...
staticcheck ./...
govulncheck ./...
```

## 通用

```bash
git status --porcelain            # 工作区
git log --oneline -20             # 近期变更（diff 驱动）
git ls-files | grep -i "\.env"    # .env 是否被跟踪（= P0）
gh run list --limit 5             # CI 最近状态
```

## 性能测量（无现成压测工具时的最小方案）

```bash
# Python 串行/并发计时（写临时脚本，结果落盘）
python - <<'EOF'
import time, statistics, concurrent.futures, json
# 替换为项目核心链路调用
def call(): ...
times = []
for _ in range(10): call()          # 预热（剔除冷启动）
with concurrent.futures.ThreadPoolExecutor(5) as ex:
    futs = [ex.submit(lambda: (call(), time.perf_counter())[1]) for _ in range(100)]
times.sort()
print(json.dumps({"p50": times[49], "p95": times[94], "p99": times[98]}))
EOF

# 或用项目栈的标准工具：locust / autocannon / wrk / k6（有哪个用哪个）
```

## LLM 评测（第三层）

- 有自研评测脚本 → 用项目的（口径以项目 README/ACCEPTANCE.md 为准），检查其温度固定与判分规则。
- 无脚本、依赖已装 → RAGAS（忠实度/上下文查全查准）/ DeepEval（幻觉/忠实度/G-Eval）；RAG 项目优先核对该项目已有的 Hit@k 脚本。
- 都没有 → 不硬造框架：手写最小评测（题目清单 + 逐题记录 + 判分规则落盘）即可，重要的是**可复现**而不是工具高级。跑付费 API 前先报价（题目数 × 轮数 × 单次 token）。
- LLM-as-judge：judge 提示词要落盘存档；抽 10-20 题人工对齐并报告一致率。

## 安全用例（第三层注入/越狱、第四层降级演练）

- 注入三件套模板见 `heuristics-playbook.md` S4。
- 降级演练：把模型 baseURL 指向 `http://127.0.0.1:1`（必连不上）或环境变量置空，观察系统行为；**只对本地实例做**，严禁对生产/共享环境做故障注入。
