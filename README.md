# NexusEntityEngine

中文 | [English](README_EN.md)

## 这是什么

NexusEntityEngine 是面向 **SillyTavern（酒馆）长篇角色扮演与故事创作**的世界资料与记忆管理插件。

它将聊天中的事件、人物、地点和关系整理成相互关联、可以查阅和修改的资料，为故事的长期连续性提供基础。目前已实现手动提取与资料管理，正在向完整的长期记忆系统发展。

## 功能与特色

- **从聊天整理事件**：选择聊天楼层，提取事件摘要及相关人物、地点、物品、组织、能力和概念。
- **关联式资料库**：浏览事件与世界资料之间的联系，查看人物关系，并手动编辑资料。
- **按聊天独立保存**：资料保存在本地，不同聊天分别管理；更新资料时保留未修改的人工字段。
- **多模型配置**：支持 Chat Completions、Responses、Anthropic Messages 和 Gemini GenerateContent 协议，不同提取任务可使用不同模型预设。
- **可恢复的提取流程**：支持进度查看、取消和失败重试，复用已完成步骤，减少重复处理；保存前检查来源和资料是否发生变化。
- **酒馆内工作台**：集中查看故事与世界资料，浮动入口可以拖动并记住位置。

## 如何安装

当前为 **Alpha 源码安装版**，需要同时配置前端扩展和服务端插件。以下步骤面向 Windows 本地部署，以 SillyTavern 1.19.0 为参考；其他环境尚未完成安装验收。

### 1. 准备环境

安装 Git、Python 3.11+、Node.js 22.13+，并确保 `curl` 命令可用。运行酒馆的 Node.js 也需满足版本要求，插件使用其内置 [SQLite 支持](https://nodejs.org/download/release/v22.13.0/docs/api/sqlite.html)。

关闭酒馆，在 **SillyTavern 根目录**打开 PowerShell。下面使用默认用户目录 `data/default-user`；如果使用其他用户或数据目录，请替换对应路径。扩展文件夹名称请保留为 `NexusEntityEngine`。

### 2. 下载插件并准备依赖

```powershell
git clone https://github.com/archyciao/ST-Nexus-Entity-Engine.git .\data\default-user\extensions\NexusEntityEngine
.\data\default-user\extensions\NexusEntityEngine\nexus.cmd setup
```

### 3. 连接并启用服务端插件

仍在 SillyTavern 根目录执行：

```powershell
New-Item -ItemType Directory -Force .\plugins | Out-Null
New-Item -ItemType Junction -Path .\plugins\NexusEntityEngine -Target (Resolve-Path .\data\default-user\extensions\NexusEntityEngine\server).Path
```

在酒馆 `config.yaml` 中，将已有的 `enableServerPlugins` 设置为 `true`：

```yaml
enableServerPlugins: true
```

服务端插件在酒馆启动时加载，配置说明见 [SillyTavern 官方文档](https://docs.sillytavern.app/for-contributors/server-plugins/)。

### 4. 启动并配置模型

启动酒馆并刷新页面，确认扩展已启用。点击 NexusEntityEngine 浮动图标，在“模型连接”中填写 API 地址、密钥和模型，选择对应协议；随后打开聊天，在“批量提取”中选择处理范围。

当前安装需要访问酒馆所在电脑的文件和配置，仅在扩展面板安装前端不足以运行。

## 开发阶段与后续计划

**当前阶段：Alpha，已具备手动提取、资料管理和基础恢复能力。** 当前插件界面以中文为主，验证以本地界面和离线样例为主；真实模型效果与长聊天稳定性仍需持续验证。

后续重点：

- **提取准确性与效率**：改进遗漏对象发现、候选结果复核、字段冲突处理和多模型适配。
- **分层历史**：将事件逐步压缩成不同层级的故事总结。目前历史页面仅为功能入口。
- **独立记忆与自动召回**：区分角色所知与客观事实，将相关记忆带回后续对话；当前尚未接通。
- **资料变更管理**：完善聊天修改后的影响追踪、资料迁移与安全合并。
- **自动化与安装体验**：自动触发、共享限流与预算控制，以及更简单的安装和跨平台支持。
