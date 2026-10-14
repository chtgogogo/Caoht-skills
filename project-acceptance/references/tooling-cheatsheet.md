# 工具命令速查（验收官工具箱）

> 原则：项目里已有配置的工具优先用项目自己的命令；没有的按本表补。工具不可用时该项记 ⬜ 并附安装命令，不许跳过不提。所有输出重定向落盘到 `acceptance/evidence/`（按测试纪律）。
> Windows 注意：以下命令在 Git Bash 下运行；pip 系工具优先 `python -m xxx` 调用避免 PATH 坑。

## 凭据 / 密钥扫描（初筛 + 成熟组件补扫，两层都要做）

**定位**：验收官自带正则（`scripts/profile_project.py` 的 `suspected_secrets`）是**初筛**，不是终判——它按格式特征与赋值形态命中，报 0 命中不等于没有凭据。任何「密钥零硬编码」的 ✅ 结论必须有下面至少一个成熟组件的补跑结果作支撑。

```bash
# ① 初筛（纯标准库，不需要联网装东西；语言分析与凭据扫描是两条独立通道）
py "<本skill目录>/scripts/profile_project.py" "<项目根>" > acceptance/evidence/profile.json
# 大项目放宽上限用下面这几个参数（触顶会自动在 coverage.limits_triggered 里登记并置 coverage.insufficient）
#   --max-depth N            目录深度上限（默认 8）
#   --max-files N            遍历文件数上限（默认 20000）
#   --max-language-files N   语言分析通道文件数上限
#   --max-credential-files N 凭据扫描通道文件数上限
#   --max-read-bytes N       单文件读取上限（字节，默认 262144），超过计入未检查清单
#   --max-findings N         疑似凭据上报条数上限（默认 40），超出登记为 truncated
#   --strict-coverage        coverage.insufficient 为真时退出码 3（给 CI/门禁接住，别让人读 JSON 才发现）
py "<本skill目录>/scripts/profile_project.py" "<项目根>" --strict-coverage > acceptance/evidence/profile.json
echo "exit=$?   # 3 = 覆盖范围不足，必须写进报告 §6b，不许当扫过了"
# 读结果时只看三样：hits（命中，值已掩码）／coverage.declaration（覆盖声明）／
# placeholder_filtered 与 shape_rejected（被过滤项计数——它们不是「没问题」，是「被规则挡在门外」）

# ② 补扫（成熟组件，任选项目装得下的；命中值默认含明文，见下方红线）
# gitleaks：Go 二进制，brew install gitleaks / docker / releases 页下载，**不是 pip 也不是 npm 包**
gitleaks dir "<项目根>" -v --redact=100 --report-path acceptance/evidence/gitleaks-dir.json
gitleaks git "<项目根>" -v --redact=100 --report-path acceptance/evidence/gitleaks-git.json  # 扫历史提交
#   v9 的命令是 `git`（仓库/历史）与 `dir`（目录与文件）；v8 时代的 `detect --no-git` 写法已不在当前文档里，
#   照抄会报 unknown command。--exit-code N 可让命中直接非零退出接门禁。
# detect-secrets（Yelp）：pip 系，装完命令名是 detect-secrets（带连字符）
pip install detect-secrets
detect-secrets -C "<项目根>" scan --all-files > acceptance/evidence/secrets.baseline
detect-secrets audit acceptance/evidence/secrets.baseline    # 逐条人工判定，结果写回 baseline
#   --all-files 才会扫未被 git 跟踪的文件（.env 常常就没被跟踪）
# TruffleHog：brew install trufflehog / go install / 官方 install.sh
trufflehog filesystem "<项目根>" --results=verified,unknown --json > acceptance/evidence/trufflehog.json
#   --results=verified 只看校验过的真凭据；unknown 表示没校验（本机常常连不上校验接口，别据此判「都是假的」）

# ③ 红线：外部组件的报告默认**含明文密钥原文**，本身即敏感件
#   - 落盘文件设成不可公开：不进 git（.gitignore 或 acceptance/evidence/ 整体排除），不外发、不贴进验收报告正文
#   - 报告正文只写「组件名 + 命中条数 + 文件:行号 + 掩码值」，与 §6b 的覆盖声明同一套口径
#   - 已确认泄露的历史凭据：作废并轮换才算处置完成；只删文件里那行不算修好
```


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
# 密钥扫描：用上面「凭据 / 密钥扫描」小节（profile_project.py 初筛 + 成熟组件补扫）
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
# 密钥扫描同上面「凭据 / 密钥扫描」小节：gitleaks 是 Go 二进制（brew / docker / releases），没有 npm 包，别写 `npx gitleaks`
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
