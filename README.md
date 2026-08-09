# ST-Nexus-Entity-Engine

The Entity, Event, Memory and Relation data foundation for long-form SillyTavern AIRP. This repository currently provides Schemas, a Registry, validators, reference networks, Event V2 extraction probes and budgeted recall; it is not yet wired into the official Tavern frontend or a persistent database.

The authoritative design documents live in `D:\Obsidian Vault\AI_World_Simulator`; this repository only holds the machine implementation and maintenance notes.

## Current Principles

- Identity, ID, Type, references, Relations, actual locations, current location, parent, item placement and the reverse Index use strict structures;
- Other objective material goes into the open `entity_facts` by default, without requiring the model to fill a fixed Profile; existing dedicated Profile/State/Stage Schemas are kept for compatibility and as future rule interfaces;
- An Event's `event_content` holds the three-stage state, story summary and a few key utterances/actions; the Entity Core holds a short Description;
- Events link Characters, Locations, Items, Organizations, Skills, Concepts and Character Relations with clear facts via ordinary references, without classifying them as "present, mentioned, planned or recalled";
- Locations actually visited or passed through are stored separately;
- Memory directly references its Owner and source Event. An Owner may have heard of, read about or inferred something, and must not be inferred back to be an Event participant;
- The Index is rebuilt by scripts from authoritative references; the AI does not maintain the reverse catalog;
- Raw messages/source evidence and runtime prompts/original replies belong to the maintenance layer and run logs respectively, and do not enter regular Event recall.

## Event V2

1. The model reads the adjacent preceding context and this batch of contiguous AI prose, and returns coarse Event boundaries and the entity list within each Event;
2. Scripts validate the boundaries and lock in gapless, non-overlapping segments of the original text;
3. Once the segments are fixed, Event content, open Entity facts, Relations and strict references are generated concurrently;
4. Scripts complete name normalization, linkage with older Events, fact merging, Memory binding, actual locations, ordinary associations and the reverse Index;
5. Commit only after Schema and closed-network validation pass.

The current prompt lives in `src/world_simulator_schema/event_extraction_v2.py`; the development re-test entry point is `tools/airp_extraction_v2_probe.py`. Historical probes remain for regression and for reusing stable utility functions, and do not represent the current prompt.

## Directory

- `schemas/`: versioned JSON Schemas;
- `registry/`: Component hosts, permissions, authority and versions;
- `src/`: IDs, validation, network projection, extraction planning and recall;
- `tools/`: development diagnostics and re-test entry points;
- `examples/`: valid, repairable and closed-network samples;
- `tests/`: regression tests;
- `docs/`: code maintenance notes and Chinese templates not involved in runtime.
