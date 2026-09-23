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

function commit(store, chatId, key, startFloor, endFloor, values = [], snapshot = store.captureExtractionSnapshot(chatId)) {
    return store.commitExtraction(chatId, values, {key, jobId: key, startFloor, endFloor, sources: Array.from({length: endFloor - startFloor + 1}, (_, i) => ({floor: startFloor+i,id: `m${startFloor+i}`,version:'v1'})), snapshot, state: {id_maps:{entity: Object.fromEntries(values.map(value => [value.type + ':' + value.id,value.id]))}}});
}

test('fact writes, state and coverage receipts commit once and respect gaps', () => withStore(store => {
    const chat = 'receipt-chat'; store.bindChat({chatId:chat});
    const value = entity('item','sword','旧剑','一柄旧剑');
    commit(store,chat,'later',4,6,[value]);
    assert.equal(store.coveredThrough(chat),-1);
    const first = store.captureExtractionSnapshot(chat);
    assert.equal(first.entities.length,1); assert.equal(first.state.id_maps.entity['item:sword'],'sword');
    assert.equal(commit(store,chat,'later',4,6,[value]).reused,true);
    assert.equal(store.getEntity(chat,'sword').revision,1);
    commit(store,chat,'early',0,3,[value]);
    assert.equal(store.coveredThrough(chat),6);
    assert.equal(store.sourceCoverage(chat).length,2);
}));

test('concurrent manual edit prevents every extraction write and receipt', () => withStore(store => {
    const chat='conflict'; store.bindChat({chatId:chat});
    const value=entity('item','sword','剑','原始资料'); store.saveEntity(chat,value);
    const before=store.captureExtractionSnapshot(chat);
    store.saveEntity(chat,{...value,description:'人工修改'});
    assert.throws(() => commit(store,chat,'conflict',0,1,[{...value,description:'模型修改'},entity('item','new','新物品','不应写入')],before),/资料已改变/);
    assert.equal(store.getEntity(chat,'sword').description,'人工修改');
    assert.equal(store.getEntity(chat,'new'),null); assert.equal(store.sourceCoverage(chat).length,0);
}));

test('formal references are paged across the entire database and protect deletion', () => withStore(store => {
    const chat='references'; store.bindChat({chatId:chat});
    store.components=[{name:'source',references:[{path:'/data/target'}]}];
    const target=entity('item','target','目标','被引用的目标');
    const values=[target,...Array.from({length:505},(_,i)=>({...entity('item',`source-${i}`,`来源${i}`,'来源'),components:{source:{data:{target:{id:'target',type:'item'}}}}}))];
    store.importEntities(chat,values);
    const first=store.listFormalRelations(chat,'target',{limit:200});
    assert.equal(first.total,505); assert.equal(first.items.length,200);
    assert.equal(store.listFormalRelations(chat,'target',{offset:500,limit:100}).items.length,5);
    assert.throws(()=>store.deleteEntities(chat,['target']),/引用/);
    assert.throws(()=>store.mergeEntities(chat,['target','source-0']),/正式引用/);
    assert.equal(store.getEntity(chat,'target').id,'target');
}));

test('receipt transaction rejects broken references and never advances coverage', () => withStore(store => {
    store.bindChat({chatId:'broken'});store.components=[{name:'source',references:[{path:'/data/target'}]}];
    const value={...entity('item','source','来源','来源'),components:{source:{data:{target:{id:'missing',type:'item'}}}}};
    assert.throws(()=>commit(store,'broken','broken',0,1,[value]),/引用目标/);
    assert.equal(store.listEntities({chatId:'broken'}).length,0);assert.equal(store.coveredThrough('broken'),-1);
}));

test('source changes block stale commits while appended messages do not', () => withStore(store => {
    store.setSourceVersions('chat',['a','b']);store.assertSourceVersions('chat',[{floor:0,version:'a'}]);
    store.setSourceVersions('chat',['a','b','c']);store.assertSourceVersions('chat',[{floor:0,version:'a'}]);
    store.setSourceVersions('chat',['edited','b']);assert.throws(()=>store.assertSourceVersions('chat',[{floor:0,version:'a'}]),/原消息发生变化/);
}));

test('restart interrupts abandoned jobs but keeps committed state and receipts', () => {
    const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'nexus-restart-'));let store;
    try {
        store=new NexusStore({dataRoot,entityTypes:TYPES});store.bindChat({chatId:'chat'});
        const job=store.createBatchJob({chatId:'chat',startFloor:0,endFloor:1,entityTypes:['event']});
        store.updateBatchJob('chat',job.id,{status:'running'});
        commit(store,'chat','receipt',0,1,[entity('item','sword','剑','旧剑')]);store.close();
        store=new NexusStore({dataRoot,entityTypes:TYPES});
        assert.equal(store.getBatchJob('chat',job.id).status,'failed');assert.equal(store.coveredThrough('chat'),1);
        assert.ok(store.captureExtractionSnapshot('chat').state);
    } finally {store?.close();fs.rmSync(dataRoot,{recursive:true,force:true});}
});

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
    assert.equal(dashboard.unextractedCount, 20); assert.equal(dashboard.nextTriggerFloor, 13);
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
