# concurrency — 并发、原子性与多库同步（v1.7.0）

给多 agent 共写一个库、以及要把本 skill 分发/同步到其他宿主的人。

## 写路径的三道防线

1. **锁**：主命令开跑先抢库级锁文件（抢占失败退出码 5）。持锁者信息写入锁文件，陈旧锁会被后来者清扫——防「上个会话崩了把库锁死」。
2. **原子写**：每次落盘都是 临时文件 → `os.replace` → **写后读回验收**。写一半崩溃不会留下半截文件；`os.replace` 保证旁观者要么看到旧文件要么看到新文件，没有中间态。
3. **读回验收＋单代 .bak**：写完后重新读、跑计数校验；验收不过退出码 1，且旧内容保留一份 `.bak` 可人工回退。**.bak 是单代事故兜底，不是版本史**——真正的版本史请把 log-root 放进 git（`check` R13 会提醒）。

## 诚实边界（锁不保证什么）

- 锁管的是**进程级互斥**：同一目录两条命令同时写不会交错损坏。
- 锁**不管理念冲突**：两个 agent 同时决定「该建 RT-005」但写了两份不同的根因文件时，先提交者赢、后提交者收到「已存在」拒绝——此时走 `merge-roots`，不要静默双立。
- 手工编辑器开着的未保存缓冲区不受任何保护：`add/hit/counts` 不会传心通给你，它只保证**文件层面**的一致。手工改完务必 `counts --apply`（或至少 `check`）。
- 同一会话里连续两次提交同一事件：幂等 no-op（A03 场景），不会双计；带不同内容的同 ID 会被拒（退出码 4）——想另立就换 `--slug`。

## Windows 管道与编码（入库输出 vs 外部消费）

- 工具写入文件的编码与换行是受控的（UTF-8、保留原文件换行风格）。
- 工具往 stdout 吐的中文／emoji 在 Windows GBK 管道下会 UnicodeEncodeError（Python 3.8–3.14 全中招，3.15 才默认 UTF-8）。入口已做 `reconfigure(encoding="utf-8", errors="replace")` 自保护；如果你的宿主壳更老，捕获自己那层。
- 从 PowerShell 消费输出时优先整段捕获（`Out-String`）再 `Select-String`；别用会提前关管道的流式截断（`Select-Object -First`），提前关管会让子进程吃 BrokenPipe（见 incidents 判例 11）。

## 共享库与发布仓的同步纪律（本机事实，机制描述）

- 权威库（写作发生地）与发布仓分开；发布仓是**只读消费端**，不许直接改。
- 权威库每次升级后按序走：
  1. `python scripts/pitfall_log.py selftest` —— 断言 30 项全 PASS 才算改完；
  2. `check`（红 0）；若 `$` 数字有漂移，`counts --apply` 修齐再查；
  3. 用发布同步工具跑一次同步（目录名映射以 publish_map 的映射节为准）；
  4. **md5 对账**（防「同步了但没同步全」）：对 SKILL.md、CHANGELOG.md、scripts/、assets/、references/ 逐一比对权威库与发布仓的哈希，PowerShell 示例：
     ```powershell
     Get-FileHash "权威库\SKILL.md","发布仓\SKILL.md" -Algorithm MD5
     ```
- 宿主 junction 指向权威库的：免同步、免动；若是**复制型**宿主：每轮升级后必须巡检一遍四件套（SKILL.md / scripts / assets / references）是否换新——巡检判据键见 `references/interop.md`。

## 多人/多 agent 手工协同的最小公约

- 任何人在文件里手工改了数字：立刻 `counts --apply` 把现扫刷成单一事实（R9/R3 会指名道姓）。
- 任何人在文件里加了案例行：检查是否带了事件标记行；没有的话下次重复提交不会被挡。
- 大批量搬运（手动合并两个分支的库）：先各自 `counts --apply` + `check`，再合并文件树，合并完再 `check`——三步缺一会把两个小的漂移合成一个大的。
