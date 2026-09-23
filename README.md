# ST-Nexus-Entity-Engine

The Entity, Event, Memory and Relation data foundation for long-form SillyTavern AIRP. This repository contains both the NexusEntityEngine core and its Tavern plugin: the frontend lives at the repository root, while `server/` connects the workbench to per-chat SQLite storage and the shared Schema, Registry, validators and Event V2 extractor.

The authoritative design documents are maintained in Obsidian; this repository holds the implementation and maintenance notes.

## Current Principles

- Identity, ID, Type, references, Relations, actual locations, current location, parent, item placement and the reverse Index use strict structures.
- Objective material goes into named, open semantic Components such as `character_behavior_profile`, `location_atmosphere` and `item_characteristic`. Models fill supported facts without completing a fixed profile template.
- `entity_facts` remains for legacy compatibility. Candidates that cannot be classified safely go into maintenance-only `entity_field_maintenance`, excluded from ordinary recall.
- An Event's `event_content` holds its three-stage state, story summary and a few key utterances/actions; the Entity Core holds a short Description.
- Events link Characters, Locations, Items, Organizations, Skills, Concepts and Character Relations through ordinary references. Actual locations are stored separately.
- Memory references its Owner and source Event. Hearing, reading or inferring an event does not make the Owner an event participant. Independent Memory extraction is not yet connected to the workbench.
- Scripts rebuild the Index from authoritative references; models do not maintain the reverse catalog.
- Raw source messages, evidence, runtime prompts and original replies belong to maintenance and run records, outside ordinary Event recall.

## Event V2

1. The model reads preceding context and consecutive AI messages, then returns coarse Event boundaries and entity rosters.
2. Scripts validate the boundaries and lock gapless, non-overlapping source segments.
3. Roster entries resolve to new, existing or unresolved identities. Unresolved entries are not silently created, and downstream tasks cannot bypass the roster.
4. Once segments are fixed, tasks generate Event content, new Entity material, existing Entity updates, Relations and strict references.
5. Updates preserve unspecified fields and revision history. Field names resolve only through registered names, deterministic formatting normalization and registered aliases; spelling similarity does not select a field.
6. Scripts preserve stable IDs and prior state, verify complete evidence quotes against source text, rebuild references and validate the closed entity network.
7. Formal records, reference projections, engine state and processed-range receipts commit together. Source or record changes detected before commit stop the write.

Production execution lives in `src/world_simulator_schema/extraction/`; `src/world_simulator_schema/event_extraction_v2.py` defines the current Event V2 contract and prompts. The original probe scripts in `tools/` remain compatible entry points.

## Workbench and Reliability

- Story navigation contains History, Events and Memories. Relations belong to World Data and can expose additional registered relation types.
- The floating entry is draggable and remembers its position. Clicking toggles the workbench; there is no separate minimize mode.
- Records open in a reading view, with editing explicit. Formal incoming and outgoing references are queried and paginated by the server.
- Each extraction task can use a different model preset. Explicit adapters cover Chat Completions, Responses, Anthropic Messages and Gemini GenerateContent; model names do not determine request parameters.
- Validated task caches and completed sub-batch checkpoints support recovery. Processed-range receipts prevent duplicate submissions and retain gaps in coverage.
- Commits protect existing manual fields and reject concurrent record changes. Message-version checks depend on Tavern event notifications.
- Truncated, refused, incomplete or ambiguous output is not treated as a successful extraction. Authentication and parameter errors are not automatically retried; transient failures have bounded retries and request timeouts.

History compression, independent Memory extraction, automatic triggering and workbench recall remain future work. The five-module extraction baseline is retained; a proposed two-task replacement has not been enabled. Source-change migration and full conflict-aware record merging also remain incomplete.

Protocol adapters and recovery behavior have been validated with fixed offline fixtures, not live model requests. These checks do not establish real-model accuracy, latency, cost or compatibility with every proxy endpoint.

## Directory

- `index.js`, `style.css`, `manifest.json`, `assets/`, `ui/`: Tavern frontend extension and workbench.
- `server/`: Tavern host integration, per-chat SQLite storage and batch job processes.
- `schemas/`: versioned JSON Schemas.
- `registry/`: Component hosts, permissions, authority and versions.
- `src/`: IDs, validation, reference projections, extraction runtime and recall foundations.
- `tools/`: plugin bridges, diagnostic commands and compatible probe entry points.
- `examples/`: valid, repairable and closed-network fixtures.
- `tests/`: Python regression tests; Node tests also live beside server and UI modules.
- `docs/`: maintenance notes and Chinese templates not involved in runtime.

## Development and Offline Checks

On Windows:

```powershell
.\nexus.cmd setup
$env:NEXUS_EXTRACTION_OFFLINE = '1'
.\nexus.cmd test
.\nexus.cmd check-registry
node --test server/storage.test.mjs server/batch.test.mjs ui/presentation.test.mjs
```

On macOS/Linux, use `sh ./nexus.sh` with the same commands and export `NEXUS_EXTRACTION_OFFLINE=1` before testing. The launcher creates or rebuilds the local `.venv`, using the lockfile with `uv` when available or Python 3.11+ and pip otherwise. The virtual environment is not distributed with the repository.

The current offline suite passes 195 Python tests and 20 Node tests. Production model transport refuses network requests when `NEXUS_EXTRACTION_OFFLINE=1`; integration fixtures substitute fixed responses.

Keep API credentials out of source control, command arguments and reports. Do not persist hidden reasoning in run reports. Restart the Tavern service to load changes to the server plugin.
