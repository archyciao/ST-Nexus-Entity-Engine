import { NexusStore } from './storage.mjs';
import { callEngine } from './engine.mjs';
import { cancelBatch, startBatch, stopAll } from './batch.mjs';

export const info = Object.freeze({
    id: 'nexus-entity-engine',
    name: 'NexusEntityEngine Local Storage',
    description: 'SillyTavern host adapter for the NexusEntityEngine formal core and per-chat SQLite storage.',
});

let store;

function asyncRoute(handler) {
    return async (request, response) => {
        try {
            await handler(request, response);
        } catch (error) {
            console.error('[NexusEntityEngine]', error);
            response.status(400).json({ error: error?.message || '请求失败。' });
        }
    };
}

function chatIdFrom(request) {
    return String(request.query.chatId || request.body?.chatId || '').trim();
}

export async function init(router) {
    const metadata = callEngine('metadata');
    store = new NexusStore({ dataRoot: process.env.NEXUS_ENTITY_ENGINE_DATA, entityTypes: metadata.entityTypes.map(item => item.id), components: metadata.components, validateModelProfile: input => callEngine('model_profile', input) });

    router.get('/health', asyncRoute(async (_request, response) => {
        response.json({ ok: true, version: '0.2.0', core: 'NexusEntityEngine', entityTypes: store.entityTypes, paths: store.getPaths() });
    }));

    router.get('/engine/metadata', asyncRoute(async (_request, response) => response.json(callEngine('metadata'))));
    router.post('/engine/prepare', asyncRoute(async (request, response) => response.json(callEngine('prepare_entity', request.body || {}))));
    router.post('/engine/validate', asyncRoute(async (request, response) => response.json(callEngine('validate_entity', request.body || {}))));

    router.get('/settings', asyncRoute(async (_request, response) => response.json(store.getSettings())));
    router.put('/settings', asyncRoute(async (request, response) => response.json(store.saveSettings(request.body || {}))));
    router.post('/settings/reset', asyncRoute(async (_request, response) => response.json(store.resetSettings())));

    router.post('/chat/bind', asyncRoute(async (request, response) => response.json(store.bindChat(request.body || {}))));
    router.get('/dashboard', asyncRoute(async (request, response) => response.json(store.getDashboard(chatIdFrom(request)))));
    router.delete('/chat', asyncRoute(async (request, response) => response.json(store.resetChat(chatIdFrom(request)))));

    router.get('/entities', asyncRoute(async (request, response) => response.json(store.listEntities({
        chatId: chatIdFrom(request),
        type: String(request.query.type || ''),
        query: String(request.query.query || ''),
        sort: String(request.query.sort || 'updated'),
        direction: String(request.query.direction || 'desc'),
        limit: Number(request.query.limit || 100),
    }))));
    router.get('/entities/:id', asyncRoute(async (request, response) => {
        const entity = store.getEntity(chatIdFrom(request), request.params.id);
        if (!entity) return response.status(404).json({ error: '实体不存在。' });
        response.json(entity);
    }));
    router.post('/entities/validate', asyncRoute(async (request, response) => response.json(callEngine('validate_entity', { entity: request.body?.entity || request.body || {} }))));
    router.put('/entities', asyncRoute(async (request, response) => {
        const body = request.body || {};
        const prepared = callEngine('prepare_entity', { entity: body.entity || body, type: body.type, name: body.name });
        const validation = callEngine('validate_entity', { entity: prepared });
        if (!validation.valid) return response.status(422).json(validation);
        const result = store.saveEntity(chatIdFrom(request), prepared, { expectedRevision: body.expectedRevision });
        response.status(result.valid ? 200 : result.conflict ? 409 : 422).json(result);
    }));
    router.delete('/entities', asyncRoute(async (request, response) => response.json(store.deleteEntities(chatIdFrom(request), request.body?.ids || []))));
    router.post('/entities/merge', asyncRoute(async (request, response) => response.json(store.mergeEntities(chatIdFrom(request), request.body?.ids || []))));

    router.post('/model-profile/validate', asyncRoute(async (request, response) => response.json(callEngine('model_profile', request.body || {}))));
    router.get('/formal-relations', asyncRoute(async (request, response) => response.json(store.listFormalRelations(chatIdFrom(request), String(request.query.entityId || ''), request.query))));
    router.get('/relations', asyncRoute(async (request, response) => response.json(store.listRelations(chatIdFrom(request), String(request.query.entityId || '')))));
    router.put('/relations', asyncRoute(async (request, response) => response.json(store.saveRelation(chatIdFrom(request), request.body || {}))));
    router.delete('/relations/:id', asyncRoute(async (request, response) => response.json(store.deleteRelation(chatIdFrom(request), request.params.id))));

    router.get('/api-presets', asyncRoute(async (_request, response) => response.json(store.listApiPresets())));
    router.put('/api-presets', asyncRoute(async (request, response) => response.json(store.saveApiPreset(request.body || {}))));
    router.delete('/api-presets/:id', asyncRoute(async (request, response) => response.json(store.deleteApiPreset(request.params.id))));
    router.get('/task-bindings', asyncRoute(async (_request, response) => response.json(store.getTaskBindings())));
    router.put('/task-bindings', asyncRoute(async (request, response) => response.json(store.saveTaskBindings(request.body || {}))));

    router.get('/batch-jobs', asyncRoute(async (request, response) => response.json(store.listBatchJobs(chatIdFrom(request)))));
    router.post('/batch-jobs', asyncRoute(async (request, response) => {
        const job = store.createBatchJob(request.body || {});
        try {
            response.json(startBatch({ store, chatId: job.chatId, job, messages: request.body?.messages || [], presetId: request.body?.presetId || '', connectionPresetIds: request.body?.connections || {} }));
        } catch (error) {
            store.updateBatchJob(job.chatId, job.id, { status: 'failed', currentStep: '启动条件未满足', error: error.message, completedAt: new Date().toISOString() });
            throw error;
        }
    }));
    router.post('/batch-jobs/:id/retry', asyncRoute(async (request, response) => {
        const chatId = chatIdFrom(request);
        const previous = store.getBatchJob(chatId, request.params.id);
        if (!previous) return response.status(404).json({ error: '批量任务不存在。' });
        const job = store.createBatchJob({ chatId, startFloor: previous.startFloor, endFloor: previous.endFloor, entityTypes: previous.entityTypes });
        try {
            response.json(startBatch({ store, chatId, job, messages: request.body?.messages || [], presetId: request.body?.presetId || '', connectionPresetIds: request.body?.connections || {} }));
        } catch (error) {
            store.updateBatchJob(chatId, job.id, { status: 'failed', currentStep: '启动条件未满足', error: error.message, completedAt: new Date().toISOString() });
            throw error;
        }
    }));
    router.post('/batch-jobs/:id/cancel', asyncRoute(async (request, response) => response.json(cancelBatch(store, chatIdFrom(request), request.params.id))));
}

export async function exit() {
    stopAll();
    store?.close();
    store = undefined;
}
