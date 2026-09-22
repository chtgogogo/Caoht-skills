
> 【自包含副本】来源：功能实现-按spec工单写代码（用户本机 skill 库）（原 skill 若升级，本副本不自动跟随）。落档日期：2026-09-20。
---
name: implement
description: 基于 spec 或 ticket 集合实现一段工作。适用于用户给出明确实现任务、需求文档或工单时。需用户显式调用，不自动触发。
metadata:
  version: 1.0.0
---

> 来源：Matt Pocock Agent Skills 中文版（仓库 vinvcn/mattpocock-skills-zh-CN，原作 Matt Pocock，MIT License）。本文件为中文本地化版本的转写，保留原作者署名。

# 功能实现

实现用户在 spec 或 tickets 中描述的工作。
- 尽可能在预先约定好的 seams 上使用 TDD 流程（测试驱动开发）。
- 定期运行 typechecking，定期运行单个测试文件，并在最后运行完整测试套件。
- 完成后，使用代码审查流程审查这次工作。
- 把工作提交到当前 branch。
