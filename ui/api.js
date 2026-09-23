const API_ROOT = '/api/plugins/nexus-entity-engine';

function requestHeaders() {
    const context = globalThis.SillyTavern?.getContext?.();
    return context?.getRequestHeaders?.() || { 'Content-Type': 'application/json' };
}

async function request(path, options = {}) {
    const response = await fetch(`${API_ROOT}${path}`, {
        ...options,
        headers: { ...requestHeaders(), ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload.error || payload.errors?.[0]?.message || `请求失败：${response.status}`);
        error.status = response.status;
        error.payload = payload;
        throw error;
    }
    return payload;
}

function queryString(parameters = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(parameters)) {
        if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
    }
    const result = query.toString();
    return result ? `?${result}` : '';
}

export const api = Object.freeze({
    health: () => request('/health'),
    engineMetadata: () => request('/engine/metadata'),
    prepareEntity: value => request('/engine/prepare', { method: 'POST', body: JSON.stringify(value) }),
    validateCoreEntity: value => request('/engine/validate', { method: 'POST', body: JSON.stringify(value) }),
    getSettings: () => request('/settings'),
    saveSettings: value => request('/settings', { method: 'PUT', body: JSON.stringify(value) }),
    resetSettings: () => request('/settings/reset', { method: 'POST', body: '{}' }),
    bindChat: value => request('/chat/bind', { method: 'POST', body: JSON.stringify(value) }),
    dashboard: chatId => request(`/dashboard${queryString({ chatId })}`),
    resetChat: chatId => request(`/chat${queryString({ chatId })}`, { method: 'DELETE', body: JSON.stringify({ chatId }) }),
    listEntities: parameters => request(`/entities${queryString(parameters)}`),
    getEntity: (chatId, id) => request(`/entities/${encodeURIComponent(id)}${queryString({ chatId })}`),
    saveEntity: (chatId, value) => request(`/entities${queryString({ chatId })}`, { method: 'PUT', body: JSON.stringify({ ...value, chatId }) }),
    deleteEntities: (chatId, ids) => request(`/entities${queryString({ chatId })}`, { method: 'DELETE', body: JSON.stringify({ chatId, ids }) }),
    mergeEntities: (chatId, ids) => request('/entities/merge', { method: 'POST', body: JSON.stringify({ chatId, ids }) }),
    validateModelProfile: value => request('/model-profile/validate', { method: 'POST', body: JSON.stringify(value) }),
    listFormalRelations: (chatId, entityId, offset = 0) => request(`/formal-relations${queryString({ chatId, entityId, offset, limit: 100 })}`),
    listRelations: (chatId, entityId) => request(`/relations${queryString({ chatId, entityId })}`),
    saveRelation: (chatId, value) => request(`/relations${queryString({ chatId })}`, { method: 'PUT', body: JSON.stringify({ ...value, chatId }) }),
    deleteRelation: (chatId, id) => request(`/relations/${encodeURIComponent(id)}${queryString({ chatId })}`, { method: 'DELETE', body: JSON.stringify({ chatId }) }),
    listApiPresets: () => request('/api-presets'),
    saveApiPreset: value => request('/api-presets', { method: 'PUT', body: JSON.stringify(value) }),
    deleteApiPreset: id => request(`/api-presets/${encodeURIComponent(id)}`, { method: 'DELETE', body: '{}' }),
    getTaskBindings: () => request('/task-bindings'),
    saveTaskBindings: value => request('/task-bindings', { method: 'PUT', body: JSON.stringify(value) }),
    listBatchJobs: chatId => request(`/batch-jobs${queryString({ chatId })}`),
    createBatchJob: value => request('/batch-jobs', { method: 'POST', body: JSON.stringify(value) }),
    retryBatchJob: (chatId, id, value) => request(`/batch-jobs/${encodeURIComponent(id)}/retry`, { method: 'POST', body: JSON.stringify({ ...value, chatId }) }),
    cancelBatchJob: (chatId, id) => request(`/batch-jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: JSON.stringify({ chatId }) }),
});
