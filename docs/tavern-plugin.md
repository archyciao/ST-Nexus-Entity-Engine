# NexusEntityEngine for SillyTavern

The repository root is both the canonical engine source and the installable SillyTavern extension. It is not an engine repository containing a second, separately editable Tavern implementation.

## Single-source development

On the current development machine:

- `data/default-user/extensions/NexusEntityEngine` is a directory junction to the repository root;
- `plugins/NexusEntityEngine` is a directory junction to the root `server` folder.

Edit only this repository. Reload SillyTavern after frontend changes and restart its process after server changes. Git synchronizes separate checkouts through commits; it is not used to reconcile two live local copies.

The host bridge reads current Entity Types, Component Registry, JSON Schemas, extraction prompts and validation results from the Python core. UI overrides remain configuration and never replace the formal defaults.

## Local data

Durable data lives under the active SillyTavern user data directory:

- `NexusEntityEngine/config.sqlite`: global settings and API presets;
- `NexusEntityEngine/chats/<chat-hash>/entities.sqlite`: full formal Entity JSON and relation query data for one chat;
- `NexusEntityEngine/chats/<chat-hash>/runtime.sqlite`: state, jobs, progress, failures and audit for one chat;
- `NexusEntityEngine/chats/<chat-hash>/runs/`: batch inputs, reports and import results.

The browser is not authoritative. API keys are stored locally and are never rendered back after saving.
When upgrading from the earlier shared-database prototype, saved API presets are copied once into `config.sqlite`; the old shared databases are left untouched as a recoverable legacy backup and are no longer queried for world data.

## Verification

```powershell
node --test .\server\storage.test.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Manual batch extraction now starts the formal five-stage runner and exposes progress, logs, cancellation and retry. Cross-job idempotency, checkpoint continuation, automatic floor triggers and recall remain follow-up work.
