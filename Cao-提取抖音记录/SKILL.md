---
name: douyin-chat-export
version: v1.0
description: 把抖音网页版私信导出为按天 Markdown（本地运行，不用 Docker；说话人 A=对方 B=我，语音转文字走抖音官方接口，引用/表情/分享视频按规则清洗）。适用于备份自己抖音账号的私信。
---

# 提取抖音记录

把抖音私信导出为按天 Markdown，清洗规则与微信导出一致。仅处理用户自己的账号。

## 关键约束

- 数据敏感：聊天记录、登录态（data/browser_profile 含 Cookie）不要提交 git、网盘或公开位置。
- 本地运行：上游 [douyin-chat-export](https://github.com/TeamBreakerr/douyin-chat-export)（MIT）源码在 `D:\AI提炼\抖音聊天记录导出\app`，依赖装在其 `.venv`，浏览器内核在 `D:\AI提炼\models\playwright`（均非 C 盘）。
- 登录态会过期：抖音 Cookie 失效后需重新登录（`login.py` 扫码）。
- 语音转文字走抖音官方接口，免费、无需本地模型，结果自动简体。

## 首次搭建

完整读 [references/使用教程.md](references/使用教程.md)。核心命令（在 app 目录）：

1. `py -3 -m venv .venv` 并 `pip install -r requirements.txt`；
2. `PLAYWRIGHT_BROWSERS_PATH=D:\AI提炼\models\playwright python -m playwright install chromium`；
3. `python login.py` 扫码登录（登录态存 data/browser_profile）；
4. `python extract.py --incremental` 增量采集；
5. 双击 `导出.bat` 生成按天 Markdown。

## 日常使用

- 双击 `导出.bat`：自动补充语音转写 → 清洗 → 按天生成 Markdown → 打开输出文件夹。
- 输出：`<output_dir>/抖音聊天记录_与A/YYYY-MM-DD.md`，另有 `索引.md` 和增量状态 `_export_state.json`。
- 增量逻辑：只有某天数据变化才重写当天。
- 重新登录：`cd D:\AI提炼\抖音聊天记录导出\app` 后 `python login.py`。

## 清洗规则（与微信一致）

- 说话人：我→B，她→A；引用显示 `引用【A/B：原话】 回复`。
- 文本原样；表情有名字显示 `[名字]`、无名字显示 `[表情]`；图片 `[图片]`；语音 `[语音 N秒] 转写文字`；视频 `[视频 N秒]`。
- 分享视频 `[分享视频] 有效标题`（去掉 `#话题`、`@提及`、作者、链接）；系统消息跳过；转发聊天记录展开。

## 排查

- 采集/转写提示未登录：跑 `login.py` 重新扫码。
- 缺最新消息：再跑一次 `extract.py --incremental`。
- 格式与底层原理：读 [references/技术原理.md](references/技术原理.md)。
