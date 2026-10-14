# 接口限流 / 大仓库 / 子模块 / 生成文件（本支的取数边界细则）

> 从 `SKILL.md` §2–§3 拆出。主文只留判据与动作，这里放**实测数值、形状样本和为什么**。
> 本机实测日期：2026-10-12（gh CLI 2.96.0／Python 3.13／账号已登录）。换工具版本或换出口后请重测。

## 一、限流：三套桶，数字不一样

`[实测]` 2026-10-12 现采：`gh api rate_limit`（已登录）返回

```
core    {"limit": 5000, "remaining": 4998, "used": 2}
graphql {"limit": 5000, "remaining": 5000, "used": 0}
search  {"limit":   30, "remaining":   30, "used": 0}
```

`[实测]` 匿名（Python `urllib`，不带任何凭据）GET `https://api.github.com/zen` 的响应头：

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 58
X-RateLimit-Resource: core
```

`[✅ 直读一手]` 官方文档（无版本日期标注）：
- 匿名请求上限 "The primary rate limit for unauthenticated requests is **60 requests per hour**."
- 已鉴权个人 "All of these requests count towards your personal rate limit of **5,000 requests per hour**."
- 细粒度令牌 "The rate limit for GITHUB_TOKEN is **1,000 requests per hour per repository**."（在 GitHub Actions 里跑批最容易撞的就是这条）
  https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

`[实测]` 本窗现采（已登录 gh）：core 5000／graphql 5000／search **30**——注意 search 的单位是**每分钟**，与 core 的每小时不同桶。

`[实测]` 本窗现采（匿名 urllib）：响应头 `X-RateLimit-Limit: 60` ⇒ 正文原来的「匿名 60 次／小时」断言**由响应头直接证实**（此前只有正文一句话，无可复核的出处）。

| 通道 | 配额 | 单位 | 怎么省 |
|---|---|---|---|
| REST core（已鉴权） | 5000 | 次／小时／账号 | 一次 `trees?recursive=1` 顶掉几十次 `contents/` 点取 |
| REST core（匿名） | 60 | 次／小时／出口 IP | 只用于兜底，别拿它批量拉 |
| GraphQL（`gh` 的 `--json` 走这条路） | 5000 | 点（points）／小时 | 一次复杂查询仍算一次请求，但字段多会多点消耗 |
| Actions 里的 `GITHUB_TOKEN` | 1000 | 次／小时／**仓库** | 跨仓库批处理会分摊，同仓库死磕会先撞 |
| 搜索接口（`gh search repos`） | 30 | 次／**分钟** | 选靶阶段最容易撞；批量筛候选时每轮 ≤ 30 次 |

`[✅ 直读一手]` **次级限流（secondary rate limits）**：官方原文 "In addition to primary rate limits, GitHub enforces **secondary rate limits** in order to prevent abuse and keep the API available for all users."；触发形状之一 "No more than **90 seconds of CPU time per 60 seconds of real time** is allowed."；处置 "If you exceed a secondary rate limit, you will receive a **403 or 429** response… If the **`retry-after`** response header is present, you should not retry your request until after that many seconds has elapsed."；并且 "There is **not a way to check the status** of your secondary rate limit."
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

**可执行判据（三条，缺一不算处理过限流）**：

1. **每次响应都读限流头**，不看状态码看不出配额见底：`X-RateLimit-Remaining` 接近 0 就停手，`X-RateLimit-Reset`（Unix 秒）告诉你什么时候恢复。
2. **限流是无声失败**：脚本不检查状态码时，403 的空响应会被当成「这个仓库没有内容」，于是你产出一条**错误否定结论**并沉淀进文档。凡是「查了但没拿到」的项，必须在报告里写「未取到（原因：限流／404／权限）」，不许留空当已排除。
3. **撞墙后的动作**：读 `Retry-After` 等待 → 降级为「关键路径点取」（`contents/{目录}` 单层列）→ 仍不够就把剩下的列为「未检查范围」写进报告，而不是继续打。

`[实测]` 现采命令（可复跑）：

```
gh api rate_limit --jq '.resources | {core,search,graphql}'
py - <匿名头读取脚本：见任务包 evidence/卡09/_dns_claim_probe.py 的 P3 段>
```

## 二、字段名跟着命令族走，不许跨族迁移

`[实测]` 2026-10-12 同一环境对同一件事的三套叫法：

| 命令族 | 背后接口 | star 数 | 许可证 | 主语言 | 体积 |
|---|---|---|---|---|---|
| `gh repo view --json` | GraphQL | `stargazerCount` | `licenseInfo` | `primaryLanguage` | `diskUsage` |
| `gh api /repos/{o}/{r}` | REST | `.stargazers_count` | `.license.spdx_id` | `.language` | `.size`（**KB**） |
| `gh search repos --json` | 搜索接口 | `stargazersCount` | `license` | `language` | `size` |

写错的现场形状（`[实测]`）：

```
$ gh repo view cli/cli --json stars
Unknown JSON field: "stars"
Available fields:
  ... stargazerCount ...
```

**报错清单就是字典**：`gh` 在被拒时会把该命令族全部可用字段打出来，比任何速查表都新。别背字段名，撞上就打印一次。同族另一面：`gh search repos --json licenseInfo` 也会报 `Unknown JSON field`（搜索族里它叫 `license`）。

## 三、目录树会被悄悄剪掉：判据读 `truncated`

`[实测]` 2026-10-12 两个真实仓库对照：

| 仓库 | 递归树条目数 | `truncated` | 结论 |
|---|---|---|---|
| `torvalds/linux`（master） | 71,638 | **true** | 清单不完整，**不报错、状态码正常** |
| `pytorch/pytorch`（main） | 24,141 | false | 全量 |
| `cli/cli` | 未取树 | — | 中等规模仓库通常不触发 |

现采命令：

```
gh api "repos/torvalds/linux/git/trees/master?recursive=1" --jq '{truncated:.truncated, entries:(.tree|length)}'
```

**截断上限 `[✅ 直读一手]`**：官方 Git Trees 文档原文 "If `truncated` is true in the response then the number of items in the tree array exceeded our maximum limit." ＋ "The limit for the tree array is **100,000 entries** with a maximum size of **7 MB** when using the `recursive` parameter."
https://docs.github.com/en/rest/git/trees

`[实测]` 对照：`torvalds/linux` 递归树返回 71,638 条却已 `truncated=true` ⇒ **实际剪枝点比 100,000 更早**（7 MB 体积上限先撞），所以「条目数没到 10 万就没事」是错的读法——判据只认 `truncated` 字段，不认条目数。

**动作（三条）**：

1. 拿树之后**第一件事读 `.truncated`**，第二件事才看条目数。
2. `truncated=true` ⇒ 禁止把它当全量清单做「分层／有没有某模块」这类**否定结论**。要判「有没有 X」必须改用点取：`contents/{候选目录}` 逐层列，或用 `--jq` 在返回里找标记文件（`[实测]` 划算写法：`[.tree[].path|select(endswith("project.godot"))]` 一次拿到全部子项目标记）。
3. 报告里写死一句：**本次树为截断／未截断，条目 N，未覆盖的可能性＝……**（`truncated=true` 却没写这句＝未完成）。

## 四、子模块（gitlink）：不在本库里，别当本库代码读

`[实测]` `pytorch/pytorch` 递归树里 36 条子模块条目，形状与文件完全不同：

```
{"mode":"160000","path":"android/libs/fbjni","sha":"7e1e1fe...","type":"commit"}
```

- `type` 是 **`commit`**（不是 `blob`/`tree`），`mode` 是 **`160000`**：那个 sha 指向**另一个仓库的某个提交**，本仓库里根本没有这些文件内容。
- 判别式：树里有 `mode==160000` 的条目 ⇒ 该路径是子模块；`.gitmodules` 文件（树里以普通 blob 出现）给出它的上游 URL。
- `contents/{子模块路径}` 回的是另一种形状（`[实测]`）：

```
{"name":"cutlass","type":"submodule","submodule_git_url":"https://github.com/NVIDIA/cutlass.git"}
```

**标注方式（写进报告的形状）**：`子模块 <本地路径> → <上游仓库>（独立许可证，另行判定）`。三条纪律：① 子模块的代码**不计入**「本仓库怎么实现」的结论；② 子模块的许可证**独立判定**，外层是 MIT 不代表里面那份也是 MIT；③ 想读子模块内容得去它自己的仓库重新固定 commit，不能用外层 sha。

## 五、生成文件与打包产物：标出来，别当设计证据

**判据**：文件名／路径形状命中下表 ⇒ 在目录清单里标注 `generated`，不进入价值判断矩阵的「架构分层／设计提炼」两维证据，只可作为「它用了什么依赖版本」这类事实线索。

| 形状 | 例子 | 为什么不能当设计证据 |
|---|---|---|
| 锁文件 | `package-lock.json`、`yarn.lock`、`pnpm-lock.yaml`、`Cargo.lock`、`poetry.lock`、`uv.lock` | 机器写的依赖图，行数巨大，没有设计信息 |
| 校验清单 | `go.sum`、`requirements.txt` 里的 hash 段 | 同上 |
| 压缩／打包产物 | `*.min.js`、`*.min.css`、`dist/`、`build/`、`bundle/`、`vendor/` | 是编译结果不是源码，读它得到的「架构」是假象 |
| 代码生成物 | `*_generated.*`、`*.pb.go`、`*_pb2.py`、`*.g.cs`、`gen/`、`target/`、`__generated__/` | 由 schema／模板产出，改动要回到源头文件 |
| 快照／测试数据 | `__snapshots__/`、`*.snap`、`testdata/`、`fixtures/` | 是期望值转储，读它会误读成实现 |

**动作**：报告里给一行「已排除生成物 N 个／清单见附录」，并在目录树上标 `generated`。判不准时（例如某库 `src/` 里混着手写与生成）在条目里写「未验证」，不猜。

## 六、名称解析：`urllib` 兜底到底绕过了什么

正文原来写「GitHub 域名被 hosts 拦截时用 `urllib.request` 直连 `api.github.com`（绕过 DNS）」——**这半句不实**（`[实测]` 2026-10-12，取证脚本 `_dns_claim_probe.py`）：

- P1 计数器探针：给 `socket.getaddrinfo`（系统域名解析函数）套一层计数，再跑 `urllib.request.urlopen` → **调用 1 次，主机名 `api.github.com`**。`urllib` 自己不做解析，它把域名交给系统解析器，而 hosts 文件、DNS、代理都是这一环的一部分。
- P2 能红对照：把 `getaddrinfo` 换成返回文档保留地址 `192.0.2.1` 的假解析器 → 请求立刻失败（`WinError 10049`）。**换解析器能打穿它** ⇒ 解析环节确实在链路里，`urllib` 绕不过。
- P3 它真正换来的是：**不依赖 `gh` 的登录态**（标准库自带、凭据无关），代价是掉回匿名 60 次／小时的桶（实测响应头 `X-RateLimit-Limit: 60`）。
- P4 真要在名称解析被污染时取证：连 DNS 查询本身都不能走本机解析器——用 **DNS-over-HTTPS**（DNS over HTTPS＝用 HTTPS 加密通道问「这个域名对应哪个 IP」）。`[实测]` 本窗用「IP 直连 ＋ 手工 SNI」走通阿里公共 DNS：

```python
import socket, ssl
raw = socket.create_connection(("223.5.5.5", 443), timeout=20)          # 直接连 IP，不解析任何域名
s = ssl.create_default_context().wrap_socket(raw, server_hostname="dns.alidns.com")  # 手工给出 SNI（TLS 里告诉服务器你要哪个域名，否则证书校验对不上）
s.sendall(b"GET /resolve?name=api.github.com&type=A HTTP/1.1\r\nHost: dns.alidns.com\r\nAccept: application/dns-json\r\nConnection: close\r\n\r\n")
print(s.recv(65536).decode("utf-8", "replace"))   # 实测返回 200 ＋ Answer[] 里的 IP 列表
```

  拿到 IP 之后还要连得上 GitHub：**目标端同样不能靠本机解析器**，得用「连 IP ＋ 手工 SNI ＋ 手工 `Host:` 头」这套写法（与上面同构）。
- 判「谁拦了我」看异常形状（三种病三张药，别混）：

| 现场形状 | 病根 | 动作 |
|---|---|---|
| `gaierror` / `WinError 11001` / 「不知道这样的主机」 | 名称解析本身失败 | 走 P4 的 DoH 拿 IP，再连 IP＋SNI；或先修 hosts／DNS |
| `ConnectionReset` / 连接超时 / `WinError 10049` | 通了域名但连不上（被拦／代理／假 IP） | 换出口或显式走代理；`getproxies()` 先看 `urllib` 会听哪些代理环境变量 |
| `SSLCertVerificationError`（域名不匹配） | 你连的是 IP 或别的站点，SNI／证书对不上 | 补 `server_hostname`；**不要**关 `check_hostname` 来「绕过」，那是把中间人攻击的门打开 |
