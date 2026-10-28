# 强制触发点：pre-commit 钩子样例＋三态 waiver 纪律

> **何时读**：团队决定把规格尺挂进 git 提交流程（强制触发点），不再纯靠「记得跑尺」时。
> **不读会怎样**：钩子装错位置、waiver 三态分不清——带红提交不留痕，waiver 审计按静默跳过点名。
> **上一层指针**：`references/规格层规范.md` §6 边界 2——尺子本身不装 hook、不进流水线；挂不挂强制点是团队的另一个决定，本目录是那个决定的现成样例。

## 三态纪律

| 态 | 现象 | 判定 |
|---|---|---|
| ① 跑过绿 | pre-commit 跑规格尺全绿 | 放行；`evidence/pre-commit.log` 追加一行机读读数（`check --json` 原始输出） |
| ② 豁免跳过 | 规格尺红，但暂存变更包的在期 waiver 在案 | 放行＋log 落 `WAIVER-OK` 决策行；有效期过了未销账＝重新拦截 |
| ③ 静默跳过 | `git commit --no-verify` 绕过钩子 | 无 log 行、无 waiver＝违规态；waiver 审计事后点名（跳过检查必有 waiver 记录） |

## 安装（三步）

1. 把 `pre-commit` 复制进项目的 `.githooks/pre-commit`；Linux/macOS 设环境变量 `PY=python3`。
2. 项目根执行 `git config core.hooksPath .githooks`，并加执行位：`git update-index --chmod=+x .githooks/pre-commit`。
3. 钩子靠环境变量 `EVSPEC_ROOT=<EverSpec 根目录>`（含 scripts/ 的那层）定位规格尺；Windows 默认 `py`，Linux/macOS 设 `PY=python3`。

## waiver 记录（七字段契约）

- 位置：`changes/<slug>/waiver.md`，从 `waiver-template.md` 复制填全；随变更包归档，证据随包走。
- 七字段：变更包／日期／签发人／原因／影响范围／有效至／销账。
- 「有效至：YYYY-MM-DD」（全角冒号）是钩子认账行：当天日期未超过它才算在期；过期＝未豁免。
- 审计不变式（waiver 检查的判据）：**每个归档变更包，要么存续期内有绿读数，要么有 waiver 记录在案；两者皆无＝静默跳过，点名。** 机检命令（skill v1.13.0 交付）：`py <EverSpec根>/scripts/doc_lint.py waiver --root <项目根>`——红即点名；六路判据＝静默跳过／七字段缺一／过期未销账／有效至形状／slug 与包目录不符／销空账（细则 §6.8）。

## 诚实边界

- 钩子管「提交那一刻留痕」，管不住 `--no-verify`——③ 态只能事后审计，这正是 waiver 审计存在的理由。
- 规格尺红是仓库级信号，waiver 按变更包登记是妥协：原因与影响范围必填，有效期到期强制重新拍板，别当长期通行证。
- 尺子只查机械面，语义漂移查不到——钩子放行不等于行为正确（细则 §6 边界 1）。
