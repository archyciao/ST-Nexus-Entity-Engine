import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

function now() { return new Date().toISOString(); }

function parseJson(value, fallback) {
    try { return typeof value === 'string' && value ? JSON.parse(value) : fallback; } catch { return fallback; }
}

function cleanString(value, maxLength = 4000) { return String(value ?? '').trim().slice(0, maxLength); }

function cleanStringList(value, maxItems = 64, maxLength = 200) {
    if (!Array.isArray(value)) return [];
    return [...new Set(value.map(item => cleanString(item, maxLength)).filter(Boolean))].slice(0, maxItems);
}

function entityName(entity) {
    const identity = entity?.components?.identity?.data;
    const eventSummary = entity?.components?.event_content?.data?.story_summary;
    return cleanString(identity?.primary_name || entity?.title || entity?.name || eventSummary || entity?.description || entity?.id, 240);
}

function entityFromRow(row) {
    if (!row) return null;
    const entity = parseJson(row.entity_json, {});
    return {
        id: row.id,
        type: row.type,
        name: row.name,
        description: row.description,
        entity,
        extractedFloor: row.extracted_floor,
        lastRecalledAt: row.last_recalled_at,
        recallCount: Number(row.recall_count || 0),
        revision: Number(row.revision || 1),
        createdAt: row.created_at,
        updatedAt: row.updated_at,
    };
}

function relationFromRow(row) {
    if (!row) return null;
    return {
        id: row.id, sourceId: row.source_id, targetId: row.target_id,
        relationType: row.relation_type, description: row.description,
        sourceName: row.source_name, sourceType: row.source_type,
        targetName: row.target_name, targetType: row.target_type,
        createdAt: row.created_at, updatedAt: row.updated_at,
    };
}

function chatKey(chatId) {
    return crypto.createHash('sha256').update(String(chatId)).digest('hex').slice(0, 24);
}

export function validateEntity(input = {}, entityTypes = []) {
    const entity = input.entity && typeof input.entity === 'object' ? input.entity : input;
    const errors = [];
    if (!entityTypes.includes(entity?.type)) errors.push({ field: '/type', path: '/type', message: '请选择正式核心已发布的实体类型。' });
    if (!cleanString(entity?.description)) errors.push({ field: '/description', path: '/description', message: 'Description 不能为空。' });
    if (!entity?.components || typeof entity.components !== 'object') errors.push({ field: '/components', path: '/components', message: 'Components 必须是对象。' });
    return { valid: errors.length === 0, errors };
}

export class NexusStore {
    constructor({ dataRoot, entityTypes = [] } = {}) {
        this.dataRoot = path.resolve(dataRoot || path.join(process.cwd(), 'data', 'default-user', 'NexusEntityEngine'));
        this.chatRoot = path.join(this.dataRoot, 'chats');
        this.configPath = path.join(this.dataRoot, 'config.sqlite');
        this.entityTypes = Object.freeze([...entityTypes]);
        fs.mkdirSync(this.chatRoot, { recursive: true });
        this.config = new DatabaseSync(this.configPath);
        this.chatStores = new Map();
        this.#configure(this.config);
        this.#initializeConfig();
        this.#migrateLegacyApiPresets();
    }

    #configure(database) {
        database.exec('PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;');
    }

    #initializeConfig() {
        this.config.exec(`
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS api_presets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL, base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', transport TEXT NOT NULL DEFAULT 'chat_completions',
                reasoning_effort TEXT NOT NULL DEFAULT 'none', is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_bindings (task_id TEXT PRIMARY KEY, preset_id TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_index (
                chat_id TEXT PRIMARY KEY, chat_key TEXT NOT NULL UNIQUE, chat_name TEXT NOT NULL,
                character_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
            );
        `);
        if (!this.getSettings().initialized) {
            this.saveSettings({
                initialized: true,
                extraction: {
                    floorInterval: 10, retainTail: 4, parallelStaggerSeconds: 0.5, serialRpm: 30,
                    workflow: [
                        { id: 'narrative_map', presetId: '', locked: true },
                        { id: 'event_content', presetId: '', row: 1 },
                        { id: 'entity_create', presetId: '', row: 1 },
                        { id: 'entity_update', presetId: '', row: 1 },
                        { id: 'relation_references', presetId: '', row: 1 },
                    ],
                },
                promptOverrides: {},
                ui: { width: 1240, height: 790 },
            });
        }
    }

    #migrateLegacyApiPresets() {
        if (Number(this.config.prepare('SELECT COUNT(*) AS count FROM api_presets').get().count) > 0) return;
        const legacyPath = path.join(this.dataRoot, 'runtime.sqlite');
        if (!fs.existsSync(legacyPath)) return;
        const legacy = new DatabaseSync(legacyPath, { readOnly: true });
        try {
            const exists = legacy.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='api_presets'").get();
            if (!exists) return;
            const rows = legacy.prepare('SELECT * FROM api_presets').all();
            const insert = this.config.prepare(`INSERT OR IGNORE INTO api_presets(id,name,provider,base_url,api_key,model,transport,reasoning_effort,is_primary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`);
            this.config.exec('BEGIN IMMEDIATE');
            try {
                for (const row of rows) insert.run(row.id, row.name, row.provider, row.base_url, row.api_key, row.model, row.transport, row.reasoning_effort, row.is_primary, row.created_at, row.updated_at);
                this.config.exec('COMMIT');
            } catch (error) { this.config.exec('ROLLBACK'); throw error; }
        } finally { legacy.close(); }
    }

    #chatStore(chatId) {
        const id = cleanString(chatId, 500);
        if (!id) throw new Error('请先在酒馆中打开一个聊天。');
        const key = chatKey(id);
        if (this.chatStores.has(key)) return this.chatStores.get(key);
        const root = path.join(this.chatRoot, key);
        fs.mkdirSync(root, { recursive: true });
        const entityPath = path.join(root, 'entities.sqlite');
        const runtimePath = path.join(root, 'runtime.sqlite');
        const entities = new DatabaseSync(entityPath);
        const runtime = new DatabaseSync(runtimePath);
        this.#configure(entities);
        this.#configure(runtime);
        entities.exec(`
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
                entity_json TEXT NOT NULL, extracted_floor INTEGER, last_recalled_at TEXT,
                recall_count INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_entities_updated ON entities(updated_at DESC);
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(source_id, target_id, relation_type),
                FOREIGN KEY(source_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES entities(id) ON DELETE CASCADE
            );
        `);
        runtime.exec(`
            CREATE TABLE IF NOT EXISTS chat_state (
                id INTEGER PRIMARY KEY CHECK(id = 1), chat_id TEXT NOT NULL, chat_name TEXT NOT NULL,
                character_name TEXT NOT NULL DEFAULT '', message_count INTEGER NOT NULL DEFAULT 0,
                extracted_through INTEGER NOT NULL DEFAULT 0, workflow_stage TEXT NOT NULL DEFAULT 'idle',
                workflow_progress INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id TEXT PRIMARY KEY, start_floor INTEGER NOT NULL, end_floor INTEGER NOT NULL,
                entity_types_json TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                current_step TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', log_json TEXT NOT NULL DEFAULT '[]',
                input_path TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, action TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
        `);
        const value = { key, root, entityPath, runtimePath, entities, runtime };
        this.chatStores.set(key, value);
        return value;
    }

    getPaths(chatId = '') {
        const paths = { dataRoot: this.dataRoot, configDatabase: this.configPath, chatRoot: this.chatRoot };
        if (!chatId) return paths;
        const chat = this.#chatStore(chatId);
        return { ...paths, chatKey: chat.key, chatDirectory: chat.root, entityDatabase: chat.entityPath, runtimeDatabase: chat.runtimePath };
    }

    getSettings() {
        return parseJson(this.config.prepare('SELECT value_json FROM settings WHERE key = ?').get('application')?.value_json, {});
    }

    saveSettings(value) {
        this.config.prepare(`INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at`)
            .run('application', JSON.stringify(value || {}), now());
        return this.getSettings();
    }

    resetSettings() {
        this.config.prepare('DELETE FROM settings WHERE key = ?').run('application');
        this.#initializeConfig();
        return this.getSettings();
    }

    bindChat(input = {}) {
        const chatId = cleanString(input.chatId, 500);
        if (!chatId) throw new Error('当前没有可绑定的聊天。');
        const chat = this.#chatStore(chatId);
        const timestamp = now();
        const chatName = cleanString(input.chatName || chatId, 240);
        const characterName = cleanString(input.characterName, 160);
        const messageCount = Math.max(0, Number(input.messageCount) || 0);
        this.config.prepare(`INSERT INTO chat_index(chat_id,chat_key,chat_name,character_name,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET chat_name=excluded.chat_name, character_name=excluded.character_name, updated_at=excluded.updated_at`)
            .run(chatId, chat.key, chatName, characterName, timestamp);
        chat.runtime.prepare(`INSERT INTO chat_state(id,chat_id,chat_name,character_name,message_count,updated_at) VALUES(1,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id, chat_name=excluded.chat_name,
            character_name=excluded.character_name, message_count=excluded.message_count, updated_at=excluded.updated_at`)
            .run(chatId, chatName, characterName, messageCount, timestamp);
        return this.getDashboard(chatId);
    }

    getDashboard(chatId) {
        const chat = this.#chatStore(chatId);
        const state = chat.runtime.prepare('SELECT * FROM chat_state WHERE id = 1').get() || null;
        const rows = chat.entities.prepare('SELECT type, COUNT(*) AS count FROM entities GROUP BY type').all();
        const entityByType = Object.fromEntries(this.entityTypes.map(type => [type, 0]));
        for (const row of rows) entityByType[row.type] = Number(row.count);
        const extraction = this.getSettings().extraction || {};
        const interval = Math.max(1, Number(extraction.floorInterval) || 10);
        const retainTail = Math.max(0, Number(extraction.retainTail) || 4);
        const messageCount = Number(state?.message_count || 0);
        const extractedThrough = Number(state?.extracted_through || 0);
        return {
            chat: state ? { id: state.chat_id, name: state.chat_name, characterName: state.character_name, messageCount } : null,
            entityTotal: Object.values(entityByType).reduce((sum, count) => sum + count, 0), entityByType,
            extractedThrough, unextractedCount: Math.max(0, messageCount - retainTail - extractedThrough),
            nextTriggerFloor: extractedThrough + interval + retainTail,
            workflow: { stage: state?.workflow_stage || 'idle', progress: Number(state?.workflow_progress || 0) },
            extraction: { floorInterval: interval, retainTail }, paths: this.getPaths(chatId),
        };
    }

    listEntities({ chatId, type = '', query = '', sort = 'updated', direction = 'desc', limit = 100 } = {}) {
        const db = this.#chatStore(chatId).entities;
        const clauses = ['1=1']; const parameters = [];
        if (this.entityTypes.includes(type)) { clauses.push('type = ?'); parameters.push(type); }
        if (query) { clauses.push('(name LIKE ? ESCAPE \'\\\' OR description LIKE ? ESCAPE \'\\\')'); const q = `%${String(query).replace(/[\\%_]/g, '\\$&')}%`; parameters.push(q, q); }
        const column = { name: 'name COLLATE NOCASE', extracted: 'extracted_floor', recall: 'last_recalled_at', updated: 'updated_at' }[sort] || 'updated_at';
        parameters.push(Math.min(500, Math.max(1, Number(limit) || 100)));
        return db.prepare(`SELECT * FROM entities WHERE ${clauses.join(' AND ')} ORDER BY ${column} ${direction === 'asc' ? 'ASC' : 'DESC'}, name COLLATE NOCASE ASC LIMIT ?`).all(...parameters).map(entityFromRow);
    }

    getEntity(chatId, id) {
        return entityFromRow(this.#chatStore(chatId).entities.prepare('SELECT * FROM entities WHERE id = ?').get(id));
    }

    saveEntity(chatId, entity, { expectedRevision } = {}) {
        const chat = this.#chatStore(chatId); const db = chat.entities;
        const existing = entity?.id ? this.getEntity(chatId, entity.id) : null;
        if (existing && expectedRevision != null && Number(expectedRevision) !== existing.revision) {
            return { valid: false, conflict: true, errors: [{ path: '/', field: '_form', message: '实体已在其他位置更新，请刷新后再保存。' }] };
        }
        const timestamp = now(); const name = entityName(entity); const description = cleanString(entity.description, 12000);
        if (existing) {
            db.prepare(`UPDATE entities SET type=?,name=?,description=?,entity_json=?,revision=revision+1,updated_at=? WHERE id=?`)
                .run(entity.type, name, description, JSON.stringify(entity), timestamp, entity.id);
        } else {
            db.prepare(`INSERT INTO entities(id,type,name,description,entity_json,revision,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)`)
                .run(entity.id, entity.type, name, description, JSON.stringify(entity), timestamp, timestamp);
        }
        this.audit(chatId, 'entity', existing ? 'update' : 'create', { id: entity.id, type: entity.type, name });
        return { valid: true, entity: this.getEntity(chatId, entity.id) };
    }

    importEntities(chatId, entities = [], extractedThrough = null) {
        const chat = this.#chatStore(chatId); let saved = 0;
        chat.entities.exec('BEGIN IMMEDIATE');
        try {
            for (const entity of entities) {
                if (!entity?.id || !this.entityTypes.includes(entity.type)) continue;
                const timestamp = now(); const name = entityName(entity); const description = cleanString(entity.description, 12000);
                chat.entities.prepare(`INSERT INTO entities(id,type,name,description,entity_json,revision,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)
                    ON CONFLICT(id) DO UPDATE SET type=excluded.type,name=excluded.name,description=excluded.description,
                    entity_json=excluded.entity_json,revision=entities.revision+1,updated_at=excluded.updated_at`)
                    .run(entity.id, entity.type, name, description, JSON.stringify(entity), timestamp, timestamp);
                saved += 1;
            }
            chat.entities.exec('COMMIT');
        } catch (error) { chat.entities.exec('ROLLBACK'); throw error; }
        if (extractedThrough != null) chat.runtime.prepare('UPDATE chat_state SET extracted_through=?,workflow_stage=?,workflow_progress=100,updated_at=? WHERE id=1').run(Number(extractedThrough), 'completed', now());
        this.audit(chatId, 'batch', 'import_entities', { saved, extractedThrough });
        return { saved };
    }

    deleteEntities(chatId, ids = []) {
        const db = this.#chatStore(chatId).entities; const cleanIds = cleanStringList(ids, 500);
        db.exec('BEGIN IMMEDIATE');
        try { const statement = db.prepare('DELETE FROM entities WHERE id = ?'); let deleted = 0; for (const id of cleanIds) deleted += Number(statement.run(id).changes || 0); db.exec('COMMIT'); this.audit(chatId, 'entity', 'delete', { ids: cleanIds }); return { deleted }; }
        catch (error) { db.exec('ROLLBACK'); throw error; }
    }

    mergeEntities(chatId, ids = []) {
        const cleanIds = cleanStringList(ids, 50); if (cleanIds.length < 2) throw new Error('至少选择两个实体才能合并。');
        const entities = cleanIds.map(id => this.getEntity(chatId, id)).filter(Boolean);
        if (entities.length !== cleanIds.length || new Set(entities.map(item => item.type)).size !== 1) throw new Error('只能合并同一类型且仍存在的实体。');
        const [target, ...sources] = entities.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
        const merged = structuredClone(target.entity);
        merged.description = [...new Set(entities.map(item => item.description).filter(Boolean))].join('\n\n');
        const identity = merged.components?.identity?.data;
        if (identity) identity.aliases = [...new Set(entities.flatMap(item => [item.name, ...(item.entity?.components?.identity?.data?.aliases || [])]))].filter(name => name !== identity.primary_name);
        const db = this.#chatStore(chatId).entities; const placeholders = cleanIds.map(() => '?').join(','); const timestamp = now();
        db.exec('BEGIN IMMEDIATE');
        try {
            db.prepare('UPDATE entities SET name=?,description=?,entity_json=?,revision=revision+1,updated_at=? WHERE id=?').run(entityName(merged), merged.description, JSON.stringify(merged), timestamp, target.id);
            const relations = db.prepare(`SELECT * FROM relations WHERE source_id IN (${placeholders}) OR target_id IN (${placeholders})`).all(...cleanIds, ...cleanIds);
            db.prepare(`DELETE FROM relations WHERE source_id IN (${placeholders}) OR target_id IN (${placeholders})`).run(...cleanIds, ...cleanIds);
            const insert = db.prepare('INSERT OR IGNORE INTO relations(id,source_id,target_id,relation_type,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)');
            for (const relation of relations) {
                const sourceId = cleanIds.includes(relation.source_id) ? target.id : relation.source_id;
                const targetId = cleanIds.includes(relation.target_id) ? target.id : relation.target_id;
                if (sourceId !== targetId) insert.run(`relation_${crypto.randomUUID()}`, sourceId, targetId, relation.relation_type, relation.description, relation.created_at, timestamp);
            }
            const remove = db.prepare('DELETE FROM entities WHERE id=?'); for (const source of sources) remove.run(source.id);
            db.exec('COMMIT');
        } catch (error) { db.exec('ROLLBACK'); throw error; }
        this.audit(chatId, 'entity', 'merge', { targetId: target.id, sourceIds: sources.map(item => item.id) });
        return this.getEntity(chatId, target.id);
    }

    listRelations(chatId, entityId) {
        return this.#chatStore(chatId).entities.prepare(`SELECT r.*,s.name source_name,s.type source_type,t.name target_name,t.type target_type FROM relations r JOIN entities s ON s.id=r.source_id JOIN entities t ON t.id=r.target_id WHERE r.source_id=? OR r.target_id=? ORDER BY r.updated_at DESC`).all(entityId, entityId).map(relationFromRow);
    }

    saveRelation(chatId, input = {}) {
        const db = this.#chatStore(chatId).entities; const sourceId = cleanString(input.sourceId); const targetId = cleanString(input.targetId); const relationType = cleanString(input.relationType, 120);
        if (!sourceId || !targetId || sourceId === targetId || !relationType) throw new Error('关联两端和关系类型必须有效。');
        if (!this.getEntity(chatId, sourceId) || !this.getEntity(chatId, targetId)) throw new Error('关联目标不存在。');
        const id = cleanString(input.id) || `relation_${crypto.randomUUID()}`; const timestamp = now();
        db.prepare(`INSERT INTO relations(id,source_id,target_id,relation_type,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET relation_type=excluded.relation_type,description=excluded.description,updated_at=excluded.updated_at`).run(id, sourceId, targetId, relationType, cleanString(input.description, 2000), timestamp, timestamp);
        return this.listRelations(chatId, sourceId).find(item => item.id === id);
    }

    deleteRelation(chatId, id) { return { deleted: Number(this.#chatStore(chatId).entities.prepare('DELETE FROM relations WHERE id=?').run(id).changes || 0) }; }

    listApiPresets({ includeSecret = false } = {}) {
        return this.config.prepare('SELECT * FROM api_presets ORDER BY is_primary DESC,updated_at DESC').all().map(row => ({
            id: row.id, name: row.name, provider: row.provider, baseUrl: row.base_url,
            ...(includeSecret ? { apiKey: row.api_key } : {}), hasApiKey: Boolean(row.api_key), model: row.model,
            transport: row.transport, reasoningEffort: row.reasoning_effort, isPrimary: Boolean(row.is_primary),
        }));
    }

    getApiPreset(id, { includeSecret = false } = {}) { return this.listApiPresets({ includeSecret }).find(item => item.id === id) || null; }

    saveApiPreset(input = {}) {
        const name = cleanString(input.name, 120); const baseUrl = cleanString(input.baseUrl, 600);
        if (!name || !baseUrl) throw new Error('预设名称和 API 地址不能为空。');
        let parsed; try { parsed = new URL(baseUrl); } catch { throw new Error('API 地址格式不正确。'); }
        if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('API 地址只允许 HTTP 或 HTTPS。');
        const id = cleanString(input.id, 200) || `api_${crypto.randomUUID()}`;
        const oldKey = this.config.prepare('SELECT api_key FROM api_presets WHERE id=?').get(id)?.api_key || '';
        if (input.isPrimary) this.config.prepare('UPDATE api_presets SET is_primary=0').run();
        const timestamp = now();
        this.config.prepare(`INSERT INTO api_presets(id,name,provider,base_url,api_key,model,transport,reasoning_effort,is_primary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,provider=excluded.provider,base_url=excluded.base_url,api_key=excluded.api_key,model=excluded.model,transport=excluded.transport,reasoning_effort=excluded.reasoning_effort,is_primary=excluded.is_primary,updated_at=excluded.updated_at`)
            .run(id, name, cleanString(input.provider || 'openai_compatible', 80), baseUrl, cleanString(input.apiKey, 600) || oldKey, cleanString(input.model, 200), cleanString(input.transport || 'chat_completions', 80), cleanString(input.reasoningEffort || 'none', 30), input.isPrimary ? 1 : 0, timestamp, timestamp);
        return this.getApiPreset(id);
    }

    deleteApiPreset(id) { this.config.prepare('DELETE FROM task_bindings WHERE preset_id=?').run(id); return { deleted: Number(this.config.prepare('DELETE FROM api_presets WHERE id=?').run(id).changes || 0) }; }
    getTaskBindings() { return Object.fromEntries(this.config.prepare('SELECT task_id,preset_id FROM task_bindings').all().map(row => [row.task_id, row.preset_id])); }
    saveTaskBindings(bindings = {}) { this.config.exec('BEGIN IMMEDIATE'); try { this.config.prepare('DELETE FROM task_bindings').run(); const statement = this.config.prepare('INSERT INTO task_bindings(task_id,preset_id,updated_at) VALUES(?,?,?)'); for (const [taskId,presetId] of Object.entries(bindings)) statement.run(cleanString(taskId, 120), cleanString(presetId, 200) || null, now()); this.config.exec('COMMIT'); return this.getTaskBindings(); } catch (error) { this.config.exec('ROLLBACK'); throw error; } }

    createBatchJob(input = {}) {
        const chatId = cleanString(input.chatId, 500); const chat = this.#chatStore(chatId);
        const startFloor = Math.max(0, Number(input.startFloor) || 0); const endFloor = Math.max(0, Number(input.endFloor) || 0);
        const entityTypes = cleanStringList(input.entityTypes, this.entityTypes.length, 50).filter(type => this.entityTypes.includes(type));
        if (endFloor < startFloor) throw new Error('结束楼层不能小于开始楼层。'); if (!entityTypes.length) throw new Error('至少选择一种实体类型。');
        const id = `batch_${crypto.randomUUID()}`; const timestamp = now();
        chat.runtime.prepare(`INSERT INTO batch_jobs(id,start_floor,end_floor,entity_types_json,status,progress,current_step,created_at,updated_at) VALUES(?,?,?,?, 'queued',0,'等待执行',?,?)`).run(id, startFloor, endFloor, JSON.stringify(entityTypes), timestamp, timestamp);
        this.audit(chatId, 'batch', 'queue', { id, startFloor, endFloor, entityTypes });
        return this.getBatchJob(chatId, id);
    }

    getBatchJob(chatId, id) { const row = this.#chatStore(chatId).runtime.prepare('SELECT * FROM batch_jobs WHERE id=?').get(id); return row ? this.#batchFromRow(chatId, row) : null; }
    #batchFromRow(chatId, row) { return { id: row.id, chatId, startFloor: row.start_floor, endFloor: row.end_floor, entityTypes: parseJson(row.entity_types_json, []), status: row.status, progress: row.progress, currentStep: row.current_step, error: row.error, logs: parseJson(row.log_json, []), inputPath: row.input_path, outputPath: row.output_path, createdAt: row.created_at, startedAt: row.started_at, completedAt: row.completed_at, updatedAt: row.updated_at }; }
    listBatchJobs(chatId) { return this.#chatStore(chatId).runtime.prepare('SELECT * FROM batch_jobs ORDER BY created_at DESC LIMIT 50').all().map(row => this.#batchFromRow(chatId, row)); }
    updateBatchJob(chatId, id, patch = {}) {
        const current = this.getBatchJob(chatId, id); if (!current) throw new Error('批量任务不存在。');
        const next = { ...current, ...patch }; const db = this.#chatStore(chatId).runtime;
        db.prepare(`UPDATE batch_jobs SET status=?,progress=?,current_step=?,error=?,log_json=?,input_path=?,output_path=?,started_at=?,completed_at=?,updated_at=? WHERE id=?`)
            .run(next.status, Number(next.progress)||0, cleanString(next.currentStep, 240), cleanString(next.error, 8000), JSON.stringify(next.logs || []), cleanString(next.inputPath, 1000), cleanString(next.outputPath, 1000), next.startedAt || null, next.completedAt || null, now(), id);
        db.prepare('UPDATE chat_state SET workflow_stage=?,workflow_progress=?,updated_at=? WHERE id=1').run(next.status, Number(next.progress)||0, now());
        return this.getBatchJob(chatId, id);
    }

    resetChat(chatId) {
        const chat = this.#chatStore(chatId); chat.entities.exec('DELETE FROM relations; DELETE FROM entities;'); chat.runtime.exec('DELETE FROM batch_jobs; DELETE FROM audit_log; UPDATE chat_state SET extracted_through=0,workflow_stage=\'idle\',workflow_progress=0,updated_at=datetime(\'now\') WHERE id=1;');
        return { deleted: true, paths: this.getPaths(chatId) };
    }

    audit(chatId, category, action, detail) { if (!chatId) return; this.#chatStore(chatId).runtime.prepare('INSERT INTO audit_log(category,action,detail_json,created_at) VALUES(?,?,?,?)').run(category, action, JSON.stringify(detail || {}), now()); }

    close() { for (const chat of this.chatStores.values()) { chat.entities.close(); chat.runtime.close(); } this.chatStores.clear(); this.config.close(); }
}
