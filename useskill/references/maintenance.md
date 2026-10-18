# useskill · 维护手册（references）

> 人手改库、装新 skill、对账副本时才读；调度执行用不到本文件。
> 🔴 规则与判据只维护在 `../SKILL.md`，本手册只放操作步骤与命令形状；两边冲突以 SKILL.md 为准，并回来改本手册。
> 命令全部使用占位符（`<库根>` / `<本skill目录>` / `<调用键>`），实际值按 `personal.local.md`（本机件）落到具体路径。

## 目录
1. 新增 / 删除 / 改名 skill 的固定六步
2. 命令参考（含 shell 差异）
3. 体检器（lint）接线
4. 多宿主副本逐字一致
5. 命名与登记（中文目录名为什么不碍事）
6. 收口四检（防「执行着执行着又腐烂」）

## 1. 新增 / 删除 / 改名 skill 的固定六步

1. **动本体**：新建目录 / 改名现有目录；目录名遵用中文功能名（本手册 §5），frontmatter `name` 写英文调用键。
2. **改映射表**：库根 `__SKILL详细名映射表.md` 加 / 删 / 改一行；第五列「发布仓目录名」只能加在**表末**（体检器按第 1、2 格取键，中间插列会读错列）。
3. **重生成清单**（生成器在调度器 skill 目录的 `scripts/` 下，不是库级件）：

   ```text
   py <本skill目录>/scripts/gen_skill_manifest.py --root <库根>          # 生成（幂等：内容没变不重写文件）
   py <本skill目录>/scripts/gen_skill_manifest.py --root <库根> --check  # 纯读核对：与在场清单正文不一致就退 1
   ```

4. **体检**：对新增/被改的那一（几）支跑 lint（见 §3），PASS 才算完工。
5. **分类索引**（若在场，位置记在 `personal.local.md`）：按其头部口径处置新行；没有该文件就跳过，不算失败。
6. **公开仓同步**（可选）：`py <库根>/__tools/publish_sync.py <技能目录> <发布仓根>`（默认干跑，出读数确认后再 `--apply`）；替换表按 `__tools/publish_map.local.md`。

## 2. 命令参考（含 shell 差异）

| 事项 | 命令形状（占位） |
|---|---|
| 记一次 executed | `py <库根>/__tools/usage_update.py <调用键>` |
| 多事件同记 | `py <库根>/__tools/usage_update.py <调用键> --event loaded executed succeeded` |
| 显式指库根 | `py <库根>/__tools/usage_update.py <调用键> --root <库根>` |
| 旧台账只迁移不计数 | `py <库根>/__tools/usage_update.py --migrate-only --root <库根>` |
| 清单生成 / 核对 | 见 §1 第 3 步 |
| 体检单支 / 全库 / 对账 | 见 §3 |
| 按 name 兜底扫目录（跨平台首选） | `py -c "import re,sys;from pathlib import Path;[print(p.parent) for p in Path(sys.argv[2]).glob('*/SKILL.md') if re.search(r'(?m)^name: ' + re.escape(sys.argv[1]) + r'\s*$', p.read_text(encoding='utf-8', errors='ignore'))]" <调用键> <库根>` |
| 同上 PowerShell 等价 | `Select-String -Path "<库根>\*\SKILL.md" -Pattern "^name: <调用键>\s*$" \| Select-Object -ExpandProperty Path` |
| 同上 Git Bash 等价 | `grep -rl "^name: <调用键>" <库根>` |

- **shell 差异**：Windows 上 Python 启动器叫 `py`，类 Unix 叫 `python3`（或 `python`）；`Select-String`/`grep` 仅在对应 shell 里存在，混用会"命令不存在"。拿不准就统一走 `py -c` 一行式。
- 占位符替换时引号成对、别引入中文引号；PowerShell 5.1 不支持 `&&` 链。

## 3. 体检器（lint）接线

- 单支：`py <库根>/__tools/lint_skill.py <skill目录>`
- 全库（顺带映射表覆盖度）：`py <库根>/__tools/lint_skill.py <库根> --all`
- 连宿主挂载副本逐字对账：再加 `--host <宿主技能目录>`；没有副本层就不加，单支/全库体检照样能判定。
- 检查面：SKILL.md 存在；frontmatter 完整（name/description 必备，20–1024 字）；name 是 kebab-case；`metadata.version` 存在（缺→警告）；`--all` 时映射表覆盖度；`--host` 时权威本体与副本 diff。

## 4. 多宿主副本逐字一致

1. **只改权威本体**（库根里那份；宿主目录多为 junction 指回权威库，字节自动同步，无需双改）。
2. 真拷贝副本（非 junction）的宿主：改后跑 `--host` 对账，diff 为零才算同步完成。
3. 背景：调度器是全局单点，入口坏毒全局——多副本分叉曾是生产事故（详见 `design-decisions.md` §A1 后记与 CHANGELOG v2.2）。

## 5. 命名与登记（中文目录名为什么不碍事）

- 中文目录名是**给人一眼辨读的人造说明书**（防 AI 仓库）；机器解析唯一的键是 frontmatter `name`。
- 因此：**引用一律写调用键或走映射表**，不拿中文目录名当 id 去扫、不要求目录名与 name 相同。
- 发布仓目录名＝`name`（英文名，开放标准要求），由 `publish_sync.py --dst auto` 机械达成；权威库的中文目录名不动。

## 6. 收口四检（防「执行着执行着又腐烂」）

**凡是动了库结构的活**（装新 skill／删 skill／改 skill／挂载/摘挂载/改名），收口＝下面**四件全绿**，缺一件不许宣称「完成」；当场做不到的，在调度报告「异常与降级」行写明缺哪件与原因：

| # | 检 | 动作 |
|---|---|---|
| 1 | 登记 | 库根映射表加/删/改行（结构性事实落表）；同名 `.csv` 是冻结存档，不动 |
| 2 | 清单 | 重生成＋`--check` 退 0（读数直接写进收口汇报，别只说"已更新"） |
| 3 | 体检 | lint PASS——改了哪支就对哪支跑；动过映射表/全库结构才 `--all` |
| 4 | 同步 | 引用面现状核对：宿主注入卡/`--host` 副本/发布仓同步、README 等索引回写 |

> 为什么有这一节：v2.6→v2.7 整改的实测教训——本 SKILL.md 一次动了八处，下游至少三处引用面（宿主卡里的调度器摘要、发布仓替换键、第三方详解文档的引用段）每处忘跟都会让「规则中心唯一」变空话；**引用面腐烂＝规则腐烂**。库里还有更狠的一版口径：Q-008「改完本体只算改了一半」派生物四类核对（宿主记忆 CORE.md）；本表只收调度器眼界内可机械执行的四件，两套不冲突、都执行。
