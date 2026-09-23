# NexusEntityEngine

[中文](README.md) | English

## What Is It?

NexusEntityEngine is a world information and memory management plugin for **long-form roleplay and storytelling in SillyTavern**.

It turns events, characters, locations and relationships from chat into connected records that you can browse and edit, providing a foundation for story continuity. Manual extraction and record management are available today; a complete long-term memory system is still in development.

## Features

- **Extract events from chat**: select a message range and organize event summaries, characters, locations, items, organizations, abilities and concepts.
- **Connected records**: explore links between events and world information, browse character relationships and edit records manually.
- **Local, per-chat storage**: manage each chat separately and preserve unchanged manual fields when records are updated.
- **Multiple model presets**: adapters support Chat Completions, Responses, Anthropic Messages and Gemini GenerateContent. Different extraction tasks can use different presets.
- **Recoverable extraction**: view progress, cancel or retry jobs and reuse completed steps. Source and record changes are checked before saving.
- **An in-chat workbench**: browse story and world information through a floating entry that you can drag to a saved position.

## Installation

This is an **Alpha source installation** requiring both a frontend extension and a server plugin. The steps below target a local Windows installation, with SillyTavern 1.19.0 as the reference environment. Installation on other platforms has not yet been verified.

### 1. Prepare Your Environment

Install Git, Python 3.11+ and Node.js 22.13+, and make sure `curl` is available. The Node.js runtime used to start SillyTavern must also meet this requirement, as the plugin uses its built-in [SQLite support](https://nodejs.org/download/release/v22.13.0/docs/api/sqlite.html).

Stop SillyTavern and open PowerShell in the **SillyTavern root directory**. These commands use the default user directory, `data/default-user`; adjust the paths if you use a different user or data directory. Keep the extension folder name as `NexusEntityEngine`.

### 2. Download the Plugin and Set Up Dependencies

```powershell
git clone https://github.com/archyciao/ST-Nexus-Entity-Engine.git .\data\default-user\extensions\NexusEntityEngine
.\data\default-user\extensions\NexusEntityEngine\nexus.cmd setup
```

### 3. Connect and Enable the Server Plugin

From the SillyTavern root directory, run:

```powershell
New-Item -ItemType Directory -Force .\plugins | Out-Null
New-Item -ItemType Junction -Path .\plugins\NexusEntityEngine -Target (Resolve-Path .\data\default-user\extensions\NexusEntityEngine\server).Path
```

In SillyTavern's `config.yaml`, set the existing `enableServerPlugins` option to `true`:

```yaml
enableServerPlugins: true
```

Server plugins load when SillyTavern starts. See the [official SillyTavern documentation](https://docs.sillytavern.app/for-contributors/server-plugins/) for this setting.

### 4. Start SillyTavern and Configure a Model

Start SillyTavern, refresh the page and ensure the extension is enabled. Click the NexusEntityEngine floating icon and open **模型连接 (Model Connections)**. Enter your API URL, key and model, then select the matching protocol. Open a chat and use **批量提取 (Batch Extraction)** to choose a message range.

Installation currently requires access to files and configuration on the computer running SillyTavern. Installing only the frontend through the Extensions panel is not sufficient.

## Development Status and Roadmap

**Current stage: Alpha, with manual extraction, record management and basic recovery available.** The plugin interface is currently primarily in Chinese. Validation has focused on the local UI and offline fixtures; live-model quality and long-chat stability still need further evaluation.

Planned work:

- **Extraction accuracy and efficiency**: improve discovery of missed entities, candidate review, field conflict handling and model compatibility.
- **Hierarchical history**: compress events into story summaries at several levels. The History page is currently a placeholder.
- **Independent memories and automatic recall**: distinguish character knowledge from objective facts and bring relevant memories into later conversations. These are not yet connected to the workbench.
- **Change management**: trace the impact of edited chat messages and improve record migration and safe merging.
- **Automation and easier installation**: add automatic triggers, shared rate limits, budget controls and simpler cross-platform setup.
