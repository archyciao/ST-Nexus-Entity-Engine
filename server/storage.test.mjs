import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';

import { NexusStore } from './storage.mjs';

const TYPES = ['event', 'character', 'location', 'item', 'organization', 'skill', 'concept', 'memory', 'character_relation'];

function withStore(run) {
    const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-entity-engine-test-'));
    const store = new NexusStore({ dataRoot, entityTypes: TYPES });
    try { return run(store, dataRoot); }
    finally { store.close(); fs.rmSync(dataRoot, { recursive: true, force: true }); }
}

function entity(type, id, name, description) {
    return {
        id, type, description,
        components: {
            entity_management: { schema_version: '0.2.0', data: { revision: 1, lifecycle_status: 'active' } },
            identity: { schema_version: '0.1.0', data: { primary_name: name, aliases: [] } },
        },
    };
}

test('physically separates global config and every chat entity/runtime pair', () => withStore((store, dataRoot) => {
    store.bindChat({ chatId: 'chat-a', chatName: 'A' }); store.bindChat({ chatId: 'chat-b', chatName: 'B' });
    const a = store.getPaths('chat-a'); const b = store.getPaths('chat-b');
    assert.equal(path.dirname(store.getPaths().configDatabase), dataRoot);
    assert.notEqual(a.chatDirectory, b.chatDirectory);
    assert.notEqual(a.entityDatabase, a.runtimeDatabase);
    assert.ok(fs.existsSync(a.entityDatabase)); assert.ok(fs.existsSync(a.runtimeDatabase));
}));

test('saves formal entity JSON and detects revision conflicts', () => withStore(store => {
    store.bindChat({ chatId: 'chat-a', chatName: 'A' });
    const value = entity('character', 'character_test', '沈砚', '一名谨慎的年轻刀客。');
    const created = store.saveEntity('chat-a', value); assert.equal(created.valid, true); assert.equal(created.entity.revision, 1);
    value.description = '一名谨慎的年轻刀客，正在追查旧案。';
    const updated = store.saveEntity('chat-a', value, { expectedRevision: 1 }); assert.equal(updated.entity.revision, 2);
    const conflict = store.saveEntity('chat-a', value, { expectedRevision: 1 }); assert.equal(conflict.conflict, true);
}));

test('keeps chats isolated', () => withStore(store => {
    store.bindChat({ chatId: 'chat-a', chatName: 'A' }); store.bindChat({ chatId: 'chat-b', chatName: 'B' });
    store.saveEntity('chat-a', entity('character', 'character_a', '甲', '只属于聊天 A。'));
    assert.equal(store.listEntities({ chatId: 'chat-a' }).length, 1);
    assert.equal(store.listEntities({ chatId: 'chat-b' }).length, 0);
}));

test('redirects explicit relations when same-type entities merge', () => withStore(store => {
    const chatId = 'chat-b'; store.bindChat({ chatId, chatName: 'B' });
    const first = store.saveEntity(chatId, entity('location', 'location_a', '青竹客栈', '官道旁的客栈。')).entity;
    const duplicate = store.saveEntity(chatId, entity('location', 'location_b', '青竹旅店', '同一客栈的别名。')).entity;
    const character = store.saveEntity(chatId, entity('character', 'character_a', '掌柜', '客栈掌柜。')).entity;
    store.saveRelation(chatId, { sourceId: character.id, targetId: duplicate.id, relationType: 'located_at' });
    const merged = store.mergeEntities(chatId, [first.id, duplicate.id]);
    assert.equal(merged.id, first.id); assert.match(merged.description, /同一客栈/);
    assert.equal(store.listRelations(chatId, character.id)[0].targetId, first.id);
    assert.equal(store.getEntity(chatId, duplicate.id), null);
}));

test('stores chat runtime jobs outside the entity database', () => withStore(store => {
    const dashboard = store.bindChat({ chatId: 'chat-c', chatName: '测试聊天', messageCount: 24 });
    assert.equal(dashboard.unextractedCount, 20); assert.equal(dashboard.nextTriggerFloor, 14);
    const job = store.createBatchJob({ chatId: 'chat-c', startFloor: 0, endFloor: 20, entityTypes: ['event', 'character'] });
    assert.equal(job.status, 'queued'); assert.equal(store.listBatchJobs('chat-c').length, 1);
}));

test('never returns API keys and preserves a saved key on blank edit', () => withStore(store => {
    const saved = store.saveApiPreset({ name: 'Local test', baseUrl: 'http://127.0.0.1:1234/v1', apiKey: 'local-secret', model: 'test-model' });
    assert.equal(saved.hasApiKey, true); assert.equal('apiKey' in saved, false);
    const edited = store.saveApiPreset({ ...saved, model: 'test-model-2', apiKey: '' });
    assert.equal(edited.hasApiKey, true); assert.equal(store.getApiPreset(saved.id, { includeSecret: true }).apiKey, 'local-secret');
}));

test('migrates legacy API presets without importing legacy prompt settings', () => {
    const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-entity-engine-legacy-test-'));
    const legacy = new DatabaseSync(path.join(dataRoot, 'runtime.sqlite'));
    legacy.exec(`CREATE TABLE api_presets(id TEXT PRIMARY KEY,name TEXT,provider TEXT,base_url TEXT,api_key TEXT,model TEXT,transport TEXT,reasoning_effort TEXT,is_primary INTEGER,created_at TEXT,updated_at TEXT);`);
    legacy.prepare('INSERT INTO api_presets VALUES(?,?,?,?,?,?,?,?,?,?,?)').run('old', '旧 API', 'openai_compatible', 'http://localhost:1234/v1', 'secret', 'model', 'chat_completions', 'none', 1, 'now', 'now');
    legacy.close();
    const store = new NexusStore({ dataRoot, entityTypes: TYPES });
    try { assert.equal(store.listApiPresets()[0].name, '旧 API'); assert.equal(store.getApiPreset('old', { includeSecret: true }).apiKey, 'secret'); }
    finally { store.close(); fs.rmSync(dataRoot, { recursive: true, force: true }); }
});
