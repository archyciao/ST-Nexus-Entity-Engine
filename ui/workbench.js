import { api } from './api.js';
import { sourceVersions } from './source-version.js';
import { PROMPT_LABELS, TYPE_LABELS, FIELD_LABELS, REFERENCE_LABELS, componentLabel, formalConnections } from './presentation.js';
import { PROVIDERS, detectProvider, escapeHtml, icon, providerLabel } from './constants.js';

const TAB_DEFAULTS = Object.freeze({ dashboard: 'overview', extraction: 'pipeline', settings: 'guide' });

function selectOptions(options, selected) {
    return options.map(option => {
        const [value, label] = Array.isArray(option) ? option : [option.id, option.label];
        return `<option value="${escapeHtml(value)}" ${String(value) === String(selected ?? '') ? 'selected' : ''} ${option.disabled ? 'disabled' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
}

function field({ label, name = '', value = '', type = 'text', help = '', required = false, options = null, rows = 4, placeholder = '', attrs = '' }) {
    const control = options
        ? `<select name="${escapeHtml(name)}" ${required ? 'required' : ''} ${attrs}>${selectOptions(options, value)}</select>`
        : type === 'textarea'
            ? `<textarea name="${escapeHtml(name)}" rows="${rows}" placeholder="${escapeHtml(placeholder)}" ${required ? 'required' : ''} ${attrs}>${escapeHtml(value)}</textarea>`
            : `<input name="${escapeHtml(name)}" type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" ${required ? 'required' : ''} ${attrs}>`;
    return `<label class="nee-field" data-field="${escapeHtml(name)}"><span>${escapeHtml(label)}${required ? '<b aria-hidden="true">*</b>' : ''}</span>${control}${help ? `<small>${escapeHtml(help)}</small>` : ''}<em data-field-error></em></label>`;
}

function tabs(active, items) {
    return `<div class="nee-tabs" role="group" aria-label="页面视图">${items.map(item => `<button type="button" class="${active === item.id ? 'is-active' : ''}" data-tab="${item.id}" aria-pressed="${active === item.id}">${escapeHtml(item.label)}</button>`).join('')}</div>`;
}

function emptyState(title, description) { return `<div class="nee-empty">${icon('database', 28)}<h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p></div>`; }

function deepClone(value) { return JSON.parse(JSON.stringify(value ?? {})); }

function pointerSegments(pointer) { return String(pointer).split('/').slice(1).map(item => item.replace(/~1/g, '/').replace(/~0/g, '~')); }

function setPointer(root, pointer, value) {
    const segments = pointerSegments(pointer); let target = root;
    for (let index = 0; index < segments.length - 1; index += 1) {
        const segment = segments[index];
        if (!target[segment] || typeof target[segment] !== 'object') target[segment] = {};
        target = target[segment];
    }
    target[segments.at(-1)] = value;
}

function getPointer(root, pointer) { return pointerSegments(pointer).reduce((value, key) => value?.[key], root); }

function statusLabel(status) {
    return { idle: '等待开始', queued: '等待执行', running: '正在提取', completed: '已完成', failed: '需要处理', cancelled: '已取消' }[status] || status;
}

export class Workbench {
    constructor(root) {
        this.root = root;
        this.state = {
            active: 'dashboard', tab: 'overview', ready: false, serviceError: '', health: null,
            metadata: null, settings: null, dashboard: null, entities: [], entityType: 'event',
            entityQuery: '', entitySort: 'updated', entityDirection: 'desc', selectedIds: new Set(),
            detail: null, relations: [], relationTargets: [], apiPresets: [], selectedPresetId: '',
            batchJobs: [], databasePaths: null, busy: false, formalLinks: null, detailTab: 'profile', editingEntity: false,
        };
        this.searchTimer = null; this.pollTimer = null; this.draggedBlock = ''; this.eventsBound = false; this.boundEvents = false;
    }

    get entityTypes() { return (this.state.metadata?.entityTypes || []).map(item => ({ ...item, label: TYPE_LABELS[item.id] || item.label })); }
    get relationshipTypes() { return this.entityTypes.filter(type => type.category === 'relation' || type.id === 'relation' || type.id.endsWith('_relation')); }
    typeInfo(type) { return this.entityTypes.find(item => item.id === type) || { id: type, label: type, description: '' }; }
    promptInfo(id) { const prompt = this.state.metadata?.prompts?.find(item => item.id === id) || { id, label: id, systemPrompt: '' }; return { ...prompt, label: PROMPT_LABELS[id] || prompt.label }; }

    async initialize() {
        if (!this.root) return;
        this.state.serviceError = '';
        this.bindEvents(); this.renderLoading();
        try {
            const [health, settings, metadata] = await Promise.all([api.health(), api.getSettings(), api.engineMetadata()]);
            this.state.health = health; this.state.settings = settings; this.state.metadata = metadata;
            this.state.entityType = metadata.entityTypes?.[0]?.id || 'event'; this.state.databasePaths = health.paths; this.state.ready = true;
            await this.bindCurrentChat(); await this.loadActiveView(); this.subscribeToHost();
        } catch (error) { this.state.serviceError = error.message; this.state.ready = true; }
        this.render();
    }

    renderLoading() { this.root.innerHTML = '<div class="nee-loading"><span></span><p>正在连接 NexusEntityEngine 正式核心</p></div>'; }

    hostSnapshot() {
        const context = globalThis.SillyTavern?.getContext?.(); const chat = Array.isArray(context?.chat) ? context.chat : [];
        return { chatId: String(context?.chatId || ''), chatName: String(context?.chatId || context?.name2 || '未命名聊天'), characterName: String(context?.name2 || ''), messageCount: chat.length };
    }

    hostMessages() { const chat = globalThis.SillyTavern?.getContext?.()?.chat; return Array.isArray(chat) ? chat : []; }

    async bindCurrentChat() {
        const generation = this.bindGeneration = (this.bindGeneration || 0) + 1;
        const snapshot = this.hostSnapshot(); if (!snapshot.chatId) { this.state.dashboard = null; return null; }
        const versions = await sourceVersions(this.hostMessages());
        if (generation !== this.bindGeneration || snapshot.chatId !== this.hostSnapshot().chatId) return null;
        const dashboard = await api.bindChat({ ...snapshot, sourceVersions: versions });
        if (generation !== this.bindGeneration || snapshot.chatId !== this.hostSnapshot().chatId) return null;
        this.state.dashboard = dashboard; this.state.databasePaths = dashboard.paths || this.state.databasePaths; return dashboard;
    }

    subscribeToHost() {
        if (this.boundEvents) return;
        const context = globalThis.SillyTavern?.getContext?.(); const source = context?.eventSource; const types = context?.eventTypes || {};
        if (!source?.on) return; const refresh = () => this.refreshHostState();
        for (const eventName of [types.CHAT_CHANGED, types.MESSAGE_SENT, types.MESSAGE_RECEIVED, types.MESSAGE_DELETED, types.MESSAGE_EDITED, types.MESSAGE_SWIPED]) if (eventName) source.on(eventName, refresh);
        this.boundEvents = true;
    }

    async refreshHostState() {
        try {
            const changed = this.state.dashboard?.chat?.id !== this.hostSnapshot().chatId;
            if (changed) {
                clearTimeout(this.pollTimer); this.state.detail = null; this.state.entities = [];
                this.state.selectedIds.clear(); this.state.batchJobs = []; this.state.relations = [];
                this.state.relationTargets = []; this.state.dashboard = null; this.render();
            }
            await this.bindCurrentChat();
            if (changed) await this.loadActiveView();
            if (changed || (this.state.active === 'dashboard' && this.state.tab === 'overview')) this.render();
        } catch (error) { this.toast(error.message, 'error'); }
    }

    bindEvents() {
        if (this.eventsBound) return;
        this.root.addEventListener('click', event => this.onClick(event));
        this.root.addEventListener('submit', event => this.onSubmit(event));
        this.root.addEventListener('input', event => this.onInput(event));
        this.root.addEventListener('change', event => this.onChange(event));
        this.root.addEventListener('dragstart', event => this.onDragStart(event));
        this.root.addEventListener('dragover', event => this.onDragOver(event));
        this.root.addEventListener('drop', event => this.onDrop(event)); this.eventsBound = true;
    }

    async loadActiveView() {
        const chatId = this.hostSnapshot().chatId;
        if (this.state.active === 'dashboard' && chatId) {
            const [dashboard, jobs] = await Promise.all([api.dashboard(chatId), api.listBatchJobs(chatId)]);
            if (chatId !== this.hostSnapshot().chatId) return;
            this.state.dashboard = dashboard; this.state.batchJobs = jobs;
            this.scheduleJobPoll();
        } else if (this.state.active === 'api' || this.state.active === 'extraction') {
            this.state.apiPresets = await api.listApiPresets();
        } else if (this.state.active === 'relationships') {
            if (!this.relationshipTypes.some(type => type.id === this.state.entityType)) this.state.entityType = this.relationshipTypes[0]?.id;
            await this.loadEntities();
        } else if (this.state.active.startsWith('entity:')) {
            this.state.entityType = this.state.active.split(':')[1] || this.entityTypes[0]?.id; await this.loadEntities();
        }
    }

    scheduleJobPoll() {
        clearTimeout(this.pollTimer);
        if (!this.state.batchJobs.some(job => ['queued', 'running'].includes(job.status))) return;
        this.pollTimer = setTimeout(async () => {
            if (this.state.active !== 'dashboard' || !this.hostSnapshot().chatId) return;
            try {
                const chatId = this.hostSnapshot().chatId;
                const [jobs, dashboard] = await Promise.all([api.listBatchJobs(chatId), api.dashboard(chatId)]);
                if (chatId !== this.hostSnapshot().chatId) return;
                this.state.batchJobs = jobs; this.state.dashboard = dashboard;
                if (this.state.active === 'dashboard' && this.state.tab === 'batch') {
                    const list = this.root.querySelector('[data-run-list]');
                    if (list) list.innerHTML = this.renderJobRows();
                } else if (this.state.active === 'dashboard') this.render();
                this.scheduleJobPoll();
            } catch (error) { this.toast(`运行状态暂未刷新：${error.message}`, 'error'); this.scheduleJobPoll(); }
        }, 1500);
    }

    async loadEntities() {
        const chatId = this.hostSnapshot().chatId; if (!chatId) { this.state.entities = []; return; }
        const requestKey = this.entityRequestKey = Symbol();
        const entities = await api.listEntities({ chatId, type: this.state.entityType, query: this.state.entityQuery, sort: this.state.entitySort, direction: this.state.entityDirection, limit: 200 });
        if (requestKey !== this.entityRequestKey || chatId !== this.hostSnapshot().chatId) return;
        this.state.entities = entities;
        const ids = new Set(this.state.entities.map(item => item.id)); this.state.selectedIds = new Set([...this.state.selectedIds].filter(id => ids.has(id)));
    }

    render() {
        if (this.state.serviceError) { this.root.innerHTML = this.renderServiceError(); return; }
        if (!this.state.ready || !this.state.settings || !this.state.metadata) { this.renderLoading(); return; }
        const scrollPositions = Object.fromEntries(['.nee-main', '.nee-page-content', '.nee-entity-list', '.nee-detail-body'].map(selector => [selector, this.root.querySelector(selector)?.scrollTop || 0]));
        const searchFocused = this.root.querySelector('[data-entity-search]') === document.activeElement;
        const searchSelection = searchFocused ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
        this.root.innerHTML = `<div class="nee-layout">${this.renderSidebar()}<main class="nee-main">${this.renderMain()}</main>${this.renderDetailPane()}</div><div class="nee-toast-region" aria-live="polite"></div>`;
        for (const [selector, top] of Object.entries(scrollPositions)) { const node = this.root.querySelector(selector); if (node) node.scrollTop = top; }
        if (searchFocused) { const input = this.root.querySelector('[data-entity-search]'); input?.focus(); input?.setSelectionRange(...searchSelection); }
    }

    renderServiceError() { return `<div class="nee-service-error">${icon('warning', 34)}<h2>正式核心未连接</h2><p>${escapeHtml(this.state.serviceError)}</p><p>请确认酒馆已加载仓库根目录插件和 server 子目录。</p><button type="button" class="nee-button primary" data-action="retry-service">重新连接</button></div>`; }

    renderSidebar() {
        const nav = (target, label, glyph) => `<button type="button" class="nee-nav-item ${this.state.active === target ? 'is-active' : ''}" data-nav="${target}">${icon(glyph)}<span>${label}</span></button>`;
        const group = (label, types, prefix = '') => `<div class="nee-nav-label">${label}</div><div class="nee-nav-section">${prefix}${types.map(type => `<button type="button" class="nee-nav-item ${this.state.active === `entity:${type.id}` ? 'is-active' : ''}" data-nav="entity:${type.id}"><span>${escapeHtml(type.label)}</span><small>${this.state.dashboard?.entityByType?.[type.id] ?? 0}</small></button>`).join('')}</div>`;
        const narrative = new Set(['event', 'memory']); const relations = new Set(this.relationshipTypes.map(type => type.id));
        return `<aside class="nee-sidebar"><div class="nee-sidebar-heading"><span class="nee-wordmark">NEXUS</span><small>让故事持续生长</small></div><div class="nee-nav-section">${nav('dashboard', '工作台', 'dashboard')}</div>${group('故事', this.entityTypes.filter(type => narrative.has(type.id)), nav('history', '历史', 'database'))}${group('世界资料', this.entityTypes.filter(type => !narrative.has(type.id) && !relations.has(type.id)), nav('relationships', '关系', 'link'))}<div class="nee-nav-spacer"></div><div class="nee-nav-label">配置</div><div class="nee-nav-section">${nav('api', '模型连接', 'api')}${nav('extraction', '提取配置', 'extraction')}${nav('settings', '帮助与设置', 'settings')}</div></aside>`;
    }

    currentTitle() { if (this.state.active === 'history') return '历史'; if (this.state.active === 'relationships') return '关系'; if (this.state.active.startsWith('entity:')) return this.typeInfo(this.state.entityType).label; return { dashboard: '故事工作台', api: '模型连接', extraction: '提取配置', settings: '帮助与设置' }[this.state.active] || 'Nexus'; }
    currentSubtitle() { if (this.state.active === 'history') return '从元事件到章节，再到更高层的故事总结。'; if (this.state.active === 'relationships') return '浏览世界中的关系；当前已开放人物关系。'; if (this.state.active.startsWith('entity:')) return this.typeInfo(this.state.entityType).description; return { dashboard: '从对话中整理故事，查看人物、事实与它们之间的联系。', api: '保存常用模型，在提取配置中分配给不同任务。', extraction: '先了解处理过程，再按需要调整请求与提示词。', settings: '使用说明、功能进度与数据管理。' }[this.state.active] || ''; }
    renderMain() { const chat = this.state.dashboard?.chat; return `<div class="nee-page-header"><div><h1>${escapeHtml(this.currentTitle())}</h1><p>${escapeHtml(this.currentSubtitle())}</p></div><div class="nee-service-state" title="${escapeHtml(chat?.name || '请在酒馆中打开聊天')}"><span></span>${escapeHtml(chat?.characterName || (chat ? '当前聊天' : '未选择聊天'))}</div></div>${this.renderActiveView()}`; }

    renderActiveView() { if (this.state.active === 'history') return this.renderHistory(); if (this.state.active === 'relationships') return this.renderEntityDirectory(); if (this.state.active === 'dashboard') return this.renderDashboard(); if (this.state.active === 'api') return this.renderApi(); if (this.state.active === 'extraction') return this.renderExtraction(); if (this.state.active.startsWith('entity:')) return this.renderEntityDirectory(); if (this.state.active === 'settings') return this.renderSettings(); return ''; }

    renderHistory() { return `<div class="nee-page-content"><section class="nee-panel"><span class="nee-eyebrow">分层整理</span><h2>历史由事件逐层提炼</h2><div class="nee-flow-explainer"><div><b>元事件</b><p>保留具体发生了什么，以及对应来源。</p></div><span>→</span><div><b>阶段历史</b><p>把一组相关事件压缩为章节或阶段总结。</p></div><span>→</span><div><b>更高层历史</b><p>继续提炼多个阶段，保留长期脉络。</p></div></div><p class="nee-muted">历史总结保留下层来源，事件仍可独立查阅。记忆则属于具体角色的主观认知。</p></section><section class="nee-panel">${emptyState('暂无分层历史', '分层历史暂未开放。已提取的具体事件可在“事件”中查看。')}<button type="button" class="nee-button secondary" data-nav="entity:event">查看元事件</button></section></div>`; }

    renderDashboard() { return `<div class="nee-page-tabs">${tabs(this.state.tab, [{ id: 'overview', label: '运行概览' }, { id: 'batch', label: '批量提取' }])}</div><div class="nee-page-content">${this.state.tab === 'batch' ? this.renderBatch() : this.renderOverview()}</div>`; }

    renderOverview() {
        const data = this.state.dashboard;
        if (!data?.chat) return emptyState('从一个故事开始', '先在酒馆中打开聊天，再到这里整理和查看资料。');
        const latest = this.state.batchJobs[0];
        const progress = Math.max(0, Math.min(100, Number(latest?.progress) || 0));
        const typeRows = this.entityTypes.filter(type => data.entityByType?.[type.id]).map(type => `<button type="button" data-nav="entity:${type.id}"><span>${escapeHtml(type.label)}</span><b>${data.entityByType[type.id]}</b>${icon('chevron', 14)}</button>`).join('') || '<p class="nee-muted">提取完成后，人物、事件和世界资料会出现在这里。</p>';
        return `<div class="nee-overview"><section class="nee-story-hero"><div><span class="nee-eyebrow">当前故事</span><h2>${escapeHtml(data.chat.characterName || data.chat.name)}</h2><p>${escapeHtml(data.chat.name)}</p><div class="nee-hero-tags"><span>手动提取已开放</span><span>资料按聊天独立保存</span></div></div><button type="button" class="nee-button primary" data-tab="batch">${icon('play', 16)}提取聊天</button></section><div class="nee-metrics"><section><span>聊天消息</span><strong>${data.chat.messageCount}</strong><small>用户与 AI 的全部消息</small></section><section><span>已保存资料</span><strong>${data.entityTotal}</strong><small>当前聊天中的正式记录</small></section><section><span>最近任务</span><strong class="nee-metric-text">${latest ? statusLabel(latest.status) : '尚未开始'}</strong><small>${latest ? `${latest.startFloor}—${latest.endFloor} 层` : '选择范围后开始提取'}</small></section></div><div class="nee-overview-columns"><section class="nee-panel"><div class="nee-section-heading"><div><span>资料库</span><h2>故事里的人与事</h2></div>${icon('entity', 22)}</div><div class="nee-type-counts">${typeRows}</div></section><section class="nee-panel"><div class="nee-section-heading"><div><span>运行状态</span><h2>${latest ? statusLabel(latest.status) : '准备好再出发'}</h2></div><button type="button" class="nee-icon-button" data-action="refresh-dashboard" aria-label="刷新运行状态">${icon('refresh')}</button></div>${latest ? `<p>${escapeHtml(latest.currentStep || '等待任务开始')}</p><div class="nee-progress"><span style="width:${progress}%"></span></div><p class="nee-muted">${latest.status === 'completed' ? '本次任务已写入资料。内容准确性仍需结合原文复核。' : '进度表示处理阶段，不代表内容准确率。'}</p><button type="button" class="nee-button secondary" data-tab="batch">查看任务与日志</button>` : '<p class="nee-muted">先保存模型连接，再选择一段聊天。提取后可以从事件进入人物和地点，核对保存的资料。</p>'}<div class="nee-capability-note">自动触发、自动召回与独立主观记忆提取尚未接通。</div></section></div><div class="nee-quick-links"><button type="button" data-nav="api"><span>01</span><div><b>连接模型</b><small>设置提取使用的 API</small></div>${icon('chevron', 16)}</button><button type="button" data-tab="batch"><span>02</span><div><b>整理对话</b><small>选取楼层并查看运行记录</small></div>${icon('chevron', 16)}</button><button type="button" data-nav="entity:event"><span>03</span><div><b>查看故事</b><small>浏览事件与正式关联</small></div>${icon('chevron', 16)}</button></div></div>`;
    }

    renderJobRows() {
        return this.state.batchJobs.map(job => `<article class="nee-run-row"><div class="nee-run-main"><span class="nee-status status-${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span><strong>${job.startFloor}—${job.endFloor} 层</strong><small>${job.entityTypes.map(type => escapeHtml(this.typeInfo(type).label)).join('、')}</small></div><div class="nee-run-progress"><span style="width:${Math.max(0, Math.min(100, Number(job.progress) || 0))}%"></span></div><p>${escapeHtml(job.currentStep || '')}</p>${job.error ? `<details open><summary>需要处理的问题</summary><pre>${escapeHtml(job.error)}</pre></details>` : ''}<div class="nee-run-actions">${job.status === 'running' ? `<button type="button" class="nee-button secondary" data-action="cancel-job" data-id="${escapeHtml(job.id)}">${icon('stop', 14)}取消</button>` : ''}${['failed', 'cancelled'].includes(job.status) ? `<button type="button" class="nee-button secondary" data-action="retry-job" data-id="${escapeHtml(job.id)}">${icon('retry', 14)}恢复重试</button>` : ''}${job.logs?.length ? `<details><summary>运行日志</summary><pre>${escapeHtml(job.logs.join('\n'))}</pre></details>` : ''}</div></article>`).join('') || '<div class="nee-inline-empty">还没有任务。选择范围后，运行进度会显示在这里。</div>';
    }

    renderBatch() {
        const chat = this.state.dashboard?.chat; const messageCount = chat?.messageCount || 0;
        return `<div class="nee-stack"><section class="nee-panel"><div class="nee-section-heading"><div><span>整理历史对话</span><h2>选择提取范围</h2></div><button type="button" class="nee-button secondary" data-nav="api">${icon('api', 15)}模型连接</button></div>${chat ? `<form data-form="batch-job" class="nee-form-grid">${field({ label: '开始楼层', name: 'startFloor', type: 'number', value: 0, required: true, attrs: `min="0" max="${Math.max(0, messageCount - 1)}" step="1"` })}${field({ label: '结束楼层', name: 'endFloor', type: 'number', value: Math.max(0, messageCount - 1), required: true, attrs: `min="0" max="${Math.max(0, messageCount - 1)}" step="1"` })}<p class="nee-muted nee-full-width">楼层从 0 开始，包含首尾。当前以 AI 正文为依据，支持开场和连续回复；用户及系统消息保留来源记录，不据此自动新增事实。</p><p class="nee-muted nee-full-width">本次会保存事件及其关联世界资料，保持引用完整。历史压缩与记忆提取尚未接入。</p><div class="nee-capability-note nee-full-width">已保存范围会跳过；失败后可复用已通过的缓存结果。原消息被编辑或切换版本时，会先要求复核旧来源。</div><div class="nee-form-actions nee-full-width"><button class="nee-button primary" type="submit" ${messageCount < 1 ? 'disabled' : ''}>${icon('play', 15)}开始提取</button></div></form>` : emptyState('尚未选择聊天', '请先打开需要处理的聊天。')}</section><section class="nee-panel"><div class="nee-section-heading"><div><span>当前聊天</span><h2>任务记录</h2></div><span class="nee-badge">${this.state.batchJobs.length} 个任务</span></div><div class="nee-run-list" data-run-list>${this.renderJobRows()}</div></section></div>`;
    }

    renderApi() {
        const selected = this.state.apiPresets.find(item => item.id === this.state.selectedPresetId) || null;
        const list = this.state.apiPresets.map(item => `<button type="button" class="nee-preset-row ${selected?.id === item.id ? 'is-active' : ''}" data-action="select-preset" data-id="${item.id}"><div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(providerLabel(item.provider))} · ${escapeHtml(item.model || '未设置模型')}</span></div>${item.isPrimary ? '<b>主 API</b>' : ''}</button>`).join('') || '<p class="nee-muted">还没有 API 预设。</p>';
        const provider = selected?.provider || 'openai_compatible'; const effort = selected?.reasoningEffort || 'default';
        return `<div class="nee-page-content"><div class="nee-master-detail api-master-detail"><section class="nee-list-pane"><div class="nee-pane-toolbar"><h2>API 预设</h2><button type="button" class="nee-icon-button" data-action="new-preset" aria-label="新建模型预设" title="新建模型预设">${icon('plus')}</button></div><div class="nee-preset-list">${list}</div></section><section class="nee-editor-pane"><form data-form="api-preset" class="nee-form-grid single"><input type="hidden" name="id" value="${escapeHtml(selected?.id || '')}"><input type="hidden" name="provider" value="${provider}"><div class="nee-editor-title"><div><span>${selected ? '编辑预设' : '新建预设'}</span><h2>${escapeHtml(selected?.name || '未命名 API')}</h2></div>${selected ? `<button type="button" class="nee-button danger ghost" data-action="delete-preset" data-id="${selected.id}">${icon('trash', 15)}删除</button>` : ''}</div><div class="nee-form-grid two">${field({ label: '预设名称', name: 'name', value: selected?.name || '', required: true })}${field({ label: '模型名称', name: 'model', value: selected?.model || '', required: true, placeholder: 'model-id' })}</div><div class="nee-auto-provider">${field({ label: 'API 地址', name: 'baseUrl', value: selected?.baseUrl || '', required: true, placeholder: 'https://...' })}<span data-provider-badge>已识别：${escapeHtml(providerLabel(provider))}</span></div><div class="nee-form-grid two">${field({ label: '传输协议', name: 'transport', value: selected?.transport || 'chat_completions', options: this.state.metadata.modelCapabilities?.transports || [] })}${field({ label: '思考强度', name: 'reasoningEffort', value: effort, help: '需在下方选择思考参数格式；不发送或跟随默认时不附加思考参数。', options: (this.state.metadata.modelCapabilities?.reasoningLevels || ['default']).map(value => [value, ({ default: '跟随模型默认', none: '关闭（需模型支持）', low: '低', medium: '中', high: '高', minimal: '最低', xhigh: '很高', max: '最高' })[value] || value]) })}</div>${field({ label: 'API Key', name: 'apiKey', value: '', type: 'password', placeholder: selected?.hasApiKey ? '已保存；留空保持不变' : '仅保存在本机 config.sqlite' })}<label class="nee-check-card"><input type="checkbox" name="isPrimary" ${selected?.isPrimary ? 'checked' : ''}><span><b>设为主 API</b><small>模块未单独选择时使用。</small></span></label>${this.renderModelOptions(selected)}<p class="nee-muted">兼容能力按预设保存。不同模型可分配给不同任务；更换模型后请核对协议和参数，默认不发送专用思考参数。</p><div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}保存 API 预设</button></div></form></section></div></div>`;
    }

    renderModelOptions(selected) {
        const defaults = this.state.metadata.modelCapabilities?.defaults || {};
        const options = { ...defaults, ...selected?.options };
        const protocol = selected?.transport || 'chat_completions';
        const modes = this.state.metadata.modelCapabilities?.transports?.find(item => item.id === protocol)?.reasoningModes || ['omit'];
        const modeLabel = { omit: '不发送（兼容优先）', effort: '强度参数', thinking: '思考开关', thinking_and_effort: '思考开关＋强度', budget: 'Token 预算', adaptive: '自适应思考', level: '思考等级' };
        return `<details class="nee-advanced" open><summary>模型兼容与预算</summary><div class="nee-form-grid two">${field({ label: '思考参数格式', name: 'reasoningMode', value: options.reasoningMode, options: modes.map(mode => [mode, modeLabel[mode] || mode]) })}${field({ label: '输出上限', name: 'maxOutputTokens', type: 'number', value: options.maxOutputTokens, attrs: 'min="256" max="131072"' })}${field({ label: 'Chat 输出上限字段', name: 'tokenParameter', value: options.tokenParameter, options: [['max_tokens','max_tokens'],['max_completion_tokens','max_completion_tokens']] })}${field({ label: '返回格式', name: 'responseFormat', value: options.responseFormat, options: [['text','提示词约束 JSON'],['json_object','接口 JSON 模式（需支持）']] })}${field({ label: '思考 Token 预算', name: 'thinkingBudget', type: 'number', value: options.thinkingBudget, attrs: 'min="0" max="131071"' })}${field({ label: '总超时（秒）', name: 'timeoutSeconds', type: 'number', value: options.timeoutSeconds, attrs: 'min="10" max="1800"' })}</div><label class="nee-check-card"><input name="stream" type="checkbox" ${options.stream ? 'checked' : ''}><span>使用流式响应（端点需要支持）</span></label><p class="nee-muted">只启用端点明确支持的参数。预算上限同时约束独立请求和合并请求。</p></details>`;
    }

    modelOptionsFromForm(form) { const data = new FormData(form); return { reasoningMode: data.get('reasoningMode'), tokenParameter: data.get('tokenParameter'), responseFormat: data.get('responseFormat'), maxOutputTokens: Number(data.get('maxOutputTokens')), thinkingBudget: Number(data.get('thinkingBudget')), timeoutSeconds: Number(data.get('timeoutSeconds')), stream: data.get('stream') === 'on' }; }

    normalizedWorkflow() {
        const prompts = this.state.metadata.prompts; const raw = this.state.settings.extraction?.workflow;
        if (Array.isArray(raw) && raw.some(stage => Array.isArray(stage.groups))) {
            const workflow = deepClone(raw);
            const present = new Set(workflow.flatMap(stage => (stage.groups || []).flatMap(group => group.blocks || [])));
            const missing = prompts.filter(prompt => !present.has(prompt.id));
            if (missing.length) workflow.push({ id: 'stage-required', locked: false, groups: missing.map(prompt => ({ id: `required-${prompt.id}`, blocks: [prompt.id], presetId: '' })) });
            return workflow;
        }
        const locked = prompts.find(item => item.lockedFirst) || prompts[0]; const remaining = prompts.filter(item => item.id !== locked?.id);
        return [{ id: 'stage-fixed', locked: true, groups: [{ id: 'request-fixed', blocks: [locked.id], presetId: raw?.find(item => item.id === locked.id)?.presetId || '' }] }, { id: 'stage-main', locked: false, groups: remaining.map(item => ({ id: `request-${item.id}`, blocks: [item.id], presetId: raw?.find(row => row.id === item.id)?.presetId || '' })) }];
    }

    renderExtraction() { return `<div class="nee-page-content">${this.renderPipeline()}</div>`; }

    renderPipeline() {
        const workflow = this.normalizedWorkflow(); const used = new Set(workflow.flatMap(stage => stage.groups.flatMap(group => group.blocks || []))); const unassigned = this.state.metadata.prompts.filter(item => !used.has(item.id));
        return `<div class="nee-stack"><section class="nee-panel"><span class="nee-eyebrow">当前处理流程</span><h2>先识别，再整理，最后保存</h2><div class="nee-flow-explainer"><div><b>01 · 识别故事</b><p>确定事件范围与涉及的对象。</p></div><span aria-hidden="true">→</span><div><b>02 · 整理资料</b><p>生成事件、人物资料和关联。</p></div><span aria-hidden="true">→</span><div><b>03 · 校验保存</b><p>检查结构与引用，写入当前聊天。</p></div></div><p class="nee-muted">下方配置只影响提取。自动召回尚未开放。</p></section><section class="nee-panel nee-compact-pipeline"><div class="nee-section-heading"><div><span>模型任务</span><h2>请求与模型分配</h2></div></div><p class="nee-muted">步骤依次执行，同一步中的请求并发。点击任务可查看或编辑提示词。</p><div class="nee-pipeline-board">${workflow.map((stage, index) => this.renderPipelineStage(stage, index)).join('')}</div><details class="nee-advanced"><summary>高级编排与速率</summary><p class="nee-muted">可把模块拖入同一请求合并执行。合并后的模块共用一个模型；先识别故事的步骤保持独立。</p><button type="button" class="nee-button secondary" data-action="add-stage">${icon('plus', 14)}添加后续步骤</button>${unassigned.length ? `<div class="nee-unassigned"><span>已移除任务</span>${unassigned.map(item => `<button type="button" data-action="restore-module" data-id="${item.id}">${icon('plus', 13)}${escapeHtml(this.promptInfo(item.id).label)}</button>`).join('')}</div>` : ''}<div class="nee-pipeline-rate">${field({ label: '并发请求错开（秒）', name: 'parallelStaggerSeconds', type: 'number', value: this.state.settings.extraction?.parallelStaggerSeconds ?? 0.5, attrs: 'min="0" step="0.1"' })}${field({ label: '串行步骤速率（每分钟）', name: 'serialRpm', type: 'number', value: this.state.settings.extraction?.serialRpm ?? 30, attrs: 'min="1"' })}</div></details><div class="nee-form-actions"><button type="button" class="nee-button primary" data-action="save-pipeline">${icon('save', 15)}保存配置</button></div></section></div>`;
    }

    renderPipelineStage(stage, stageIndex) {
        return `<section class="nee-sequence-row ${stage.locked ? 'is-locked' : ''}" data-stage-index="${stageIndex}"><header><span>步骤 ${stageIndex + 1}</span><small>${stage.locked ? '固定先行' : `${stage.groups.length} 个并发请求`}</small><div>${!stage.locked ? `<button type="button" data-action="add-group" data-stage="${stageIndex}">${icon('plus', 13)}请求</button><button type="button" aria-label="删除此步骤" title="删除此步骤" data-action="delete-stage" data-stage="${stageIndex}">${icon('trash', 13)}</button>` : ''}</div></header><div class="nee-request-row">${stage.groups.map((group, groupIndex) => this.renderRequestGroup(group, stageIndex, groupIndex, stage.locked)).join('')}</div></section>`;
    }

    renderRequestGroup(group, stageIndex, groupIndex, locked) {
        const presetOptions = [['', '主 API'], ...this.state.apiPresets.map(item => [item.id, item.name])];
        return `<div class="nee-compact-request" data-drop-group="${stageIndex}:${groupIndex}"><div class="nee-request-toolbar"><span>请求 ${groupIndex + 1}${(group.blocks || []).length > 1 ? ' · 合并' : ''}</span><select data-group-preset="${stageIndex}:${groupIndex}" title="阶段 API">${selectOptions(presetOptions, group.presetId || '')}</select>${!locked ? `<button type="button" aria-label="删除此请求" title="删除此请求" data-action="delete-group" data-stage="${stageIndex}" data-group="${groupIndex}">${icon('trash', 13)}</button>` : ''}</div><div class="nee-module-list">${(group.blocks || []).map(id => this.renderPipelineBlock(id, locked)).join('') || '<span class="nee-drop-hint">拖入模块</span>'}</div></div>`;
    }

    renderPipelineBlock(id, locked) {
        const prompt = this.promptInfo(id); const selected = this.state.detail?.kind === 'prompt' && this.state.detail.id === id;
        const required = locked || this.requiredBlocks([id]).length > 0;
        return `<button type="button" class="nee-prompt-block ${selected ? 'is-active' : ''}" data-action="select-prompt" data-id="${id}" draggable="${locked ? 'false' : 'true'}" data-block="${id}">${locked ? icon('check', 14) : icon('grip', 14)}<span>${escapeHtml(prompt.label)}</span>${!required ? `<i data-action="delete-module" data-id="${id}" title="移除">×</i>` : ''}</button>`;
    }

    renderEntityDirectory() {
        const count = this.state.selectedIds.size;
        const rows = this.state.entities.map(item => `<article class="nee-entity-row ${this.state.detail?.kind === 'entity' && this.state.detail.record?.id === item.id ? 'is-active' : ''}" data-action="open-entity" data-id="${item.id}"><label class="nee-row-check"><input type="checkbox" aria-label="选择${escapeHtml(item.name)}" data-select-entity="${item.id}" ${this.state.selectedIds.has(item.id) ? 'checked' : ''}><span></span></label><button type="button"><div><strong>${escapeHtml(item.name)}</strong></div><p>${escapeHtml(item.description)}</p><small>更新于 ${escapeHtml(new Date(item.updatedAt).toLocaleString())}</small></button></article>`).join('');
        return `<div class="nee-directory"><div class="nee-directory-toolbar">${this.state.active === 'relationships' ? `<select data-relation-type aria-label="关系类型">${selectOptions(this.relationshipTypes, this.state.entityType)}</select>` : ''}<label class="nee-search">${icon('search', 16)}<input type="search" aria-label="搜索名称或说明" data-entity-search value="${escapeHtml(this.state.entityQuery)}" placeholder="搜索名称或说明"></label><select data-entity-sort aria-label="资料排序"><option value="updated" ${this.state.entitySort === 'updated' ? 'selected' : ''}>按更新时间</option><option value="name" ${this.state.entitySort === 'name' ? 'selected' : ''}>按名称</option></select><button type="button" class="nee-icon-button" data-action="toggle-sort" aria-label="切换排序方向" title="切换排序方向">${this.state.entityDirection === 'asc' ? '↑' : '↓'}</button><button type="button" class="nee-button primary" data-action="new-entity">${icon('plus', 15)}新建</button></div>${count ? `<div class="nee-selection-bar"><span>已选 ${count} 项</span><button type="button" data-action="merge-selected">${icon('merge', 14)}合并</button><button type="button" data-action="delete-selected">${icon('trash', 14)}删除</button></div>` : ''}<div class="nee-entity-list">${rows || emptyState('没有实体', '当前聊天还没有这一类型的正式实体。')}</div></div>`;
    }

    renderDetailPane() {
        if (!this.state.detail) return '';
        if (this.state.detail.kind === 'prompt') return this.renderPromptDetail();
        const record = this.state.detail.record; const entity = record.entity || {}; const name = entity.components?.identity?.data?.primary_name || record.name || '新资料';
        const editing = this.state.editingEntity || !record.id;
        const connections = record.id ? this.formalConnectionsFor(record) : [];
        const body = this.state.detailTab === 'relations' && record.id ? this.renderRelations(record) : editing ? `<form data-form="entity-editor" class="nee-entity-form"><input type="hidden" name="expectedRevision" value="${record.revision || ''}">${field({ label: '简要说明', name: 'description', type: 'textarea', rows: 4, value: entity.description || '', required: true, attrs: 'data-json-path="/description"' })}<div class="nee-component-list">${this.renderEntityComponents(entity)}</div><div class="nee-form-error" data-form-error></div><div class="nee-sticky-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}校验并保存</button>${record.id ? '<button type="button" class="nee-button secondary" data-action="cancel-entity-edit">取消编辑</button>' : ''}</div></form>` : `<div class="nee-reading-view"><p class="nee-entity-description">${escapeHtml(entity.description || '尚无说明')}</p>${this.renderReadableComponents(entity)}<div class="nee-form-actions"><button type="button" class="nee-button secondary" data-action="edit-entity">编辑资料</button></div><details class="nee-advanced"><summary>管理此记录</summary><p class="nee-muted">删除会影响其他记录对它的引用，操作前请先检查关联。</p><button type="button" class="nee-button danger ghost" data-action="delete-entity" data-id="${escapeHtml(record.id)}">${icon('trash', 15)}删除记录</button></details></div>`;
        return `<aside class="nee-detail-pane" aria-label="资料详情"><div class="nee-detail-header"><div><span>${escapeHtml(this.typeInfo(entity.type).label)}</span><h2>${escapeHtml(name)}</h2></div><button type="button" class="nee-icon-button" data-action="close-detail" aria-label="关闭详情">${icon('close')}</button></div>${record.id && !editing ? `<div class="nee-detail-tabs"><button type="button" data-action="detail-profile" aria-pressed="${this.state.detailTab !== 'relations'}">资料</button><button type="button" data-action="detail-relations" aria-pressed="${this.state.detailTab === 'relations'}">关联 <small>${this.state.formalLinks?.total ?? connections.length}</small></button></div>` : ''}<div class="nee-detail-body">${body}</div></aside>`;
    }

    renderReadableComponents(entity) {
        return (this.state.metadata.components || []).filter(component => !component.derived && component.category !== 'index' && !['identity', 'entity_management', 'entity_field_maintenance', 'entity_field_revision_history'].includes(component.name) && component.category !== 'reference').map(component => {
            const data = entity.components?.[component.name]?.data;
            if (!data || !Object.keys(data).length) return '';
            return `<section class="nee-reading-section"><h3>${escapeHtml(componentLabel(component))}</h3>${this.renderReadableValue(data)}</section>`;
        }).join('') || '<p class="nee-muted">尚无更多资料。关联对象可在“关联”页查看。</p>';
    }

    renderReadableValue(value, depth = 0) {
        if (value == null || value === '') return '<span class="nee-muted">未记录</span>';
        if (depth > 8) return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        if (Array.isArray(value)) return value.length ? `<ul class="nee-reading-list">${value.map(item => `<li>${this.renderReadableValue(item, depth + 1)}</li>`).join('')}</ul>` : '<span class="nee-muted">无</span>';
        if (typeof value === 'object') {
            if (value.id && value.type) { const target = this.state.relationTargets.find(item => item.id === value.id); return `<button type="button" class="nee-inline-link" data-action="jump-relation" data-id="${escapeHtml(value.id)}" data-type="${escapeHtml(value.type)}">${escapeHtml(target?.name || value.id)}</button>`; }
            return `<dl class="nee-reading-fields">${Object.entries(value).filter(([key]) => !['detail_id', 'aspect_id', 'objective_id', 'motivation_id', 'preference_id'].includes(key)).map(([key, item]) => `<div><dt>${escapeHtml(FIELD_LABELS[key] || key)}</dt><dd>${this.renderReadableValue(item, depth + 1)}</dd></div>`).join('')}</dl>`;
        }
        const labels = { forming: '进行中', pending_finalization: '待定稿', finalized: '已定稿', active: '有效', statement: '言语', action: '动作', approximate: '约略时间', exact: '明确时间', paraphrase: '忠实转述', verbatim: '原文引用', shared_fact: '共同事实', subjective: '主观看法', public: '公开', private: '私下', secret: '秘密', trusted_companions: '可信赖的同伴', companion: '同伴', trusting: '信任' };
        return `<span>${escapeHtml(typeof value === 'boolean' ? (value ? '是' : '否') : labels[value] || value)}</span>`;
    }

    renderPromptDetail() {
        const prompt = this.promptInfo(this.state.detail.id); const override = this.state.settings.promptOverrides?.[prompt.id];
        return `<aside class="nee-detail-pane"><div class="nee-detail-header"><div><span>提示词</span><h2>${escapeHtml(prompt.label)}</h2></div><button type="button" class="nee-icon-button" data-action="close-detail" aria-label="关闭详情">${icon('close')}</button></div><div class="nee-detail-body"><form data-form="prompt-editor" class="nee-form-grid single"><input type="hidden" name="promptId" value="${prompt.id}"><p class="nee-muted">默认内容直接来自正式核心常量。保存后只记录宿主覆盖；清空覆盖可恢复正式默认。</p>${field({ label: 'System Prompt', name: 'systemPrompt', type: 'textarea', rows: 22, value: override ?? prompt.systemPrompt, required: true })}<div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}保存覆盖</button>${override != null ? '<button class="nee-button secondary" type="button" data-action="reset-prompt">恢复正式默认</button>' : ''}</div></form></div></aside>`;
    }

    applicableComponents(type) { return (this.state.metadata.components || []).filter(item => item.allowedEntityTypes.includes(type) && item.editable && !item.derived); }

    renderEntityComponents(entity) {
        return this.applicableComponents(entity.type).map(component => {
            const current = entity.components?.[component.name]; const enabled = Boolean(current); const schema = component.dataSchema || {};
            return `<details class="nee-component" data-component="${component.name}" data-enabled="${enabled}" ${enabled ? 'open' : ''}><summary><span><b>${escapeHtml(componentLabel(component))}</b><small>${escapeHtml(component.description || component.name)}</small></span><i>${enabled ? '已启用' : '未添加'}</i></summary>${enabled ? `<div class="nee-component-fields">${this.renderSchema(schema, current?.data || {}, `/components/${component.name}/data`, component.name)}</div>` : `<button type="button" class="nee-button secondary" data-action="enable-component" data-id="${component.name}">${icon('plus', 14)}添加此资料</button>`}</details>`;
        }).join('');
    }

    renderSchema(schema, value, pointer, label) {
        const resolved = schema?.oneOf?.[0] || schema?.anyOf?.[0] || schema || {}; const type = resolved.type;
        if (resolved.enum) return field({ label, name: pointer, value: value ?? '', options: resolved.enum.map(item => [item, item]), help: resolved.description || '', attrs: `data-json-path="${pointer}"` });
        if (type === 'object' || resolved.properties || resolved.additionalProperties) {
            const properties = resolved.properties || {};
            if (!Object.keys(properties).length && resolved.additionalProperties) return field({ label: '语义字段', name: pointer, type: 'textarea', rows: 8, value: Object.keys(value || {}).length ? JSON.stringify(value, null, 2) : '{}', help: `${label} 使用正式开放语义 Schema；按 JSON 键值填写明确事实。`, attrs: `data-json-path="${pointer}" data-value-kind="json"` });
            return `<div class="nee-schema-group">${Object.entries(properties).map(([key, child]) => this.renderSchema(child, value?.[key], `${pointer}/${key}`, child.title || FIELD_LABELS[key] || key)).join('')}</div>`;
        }
        if (type === 'array') {
            const complex = resolved.items?.type === 'object' || resolved.items?.properties;
            return field({ label, name: pointer, type: 'textarea', rows: complex ? 7 : 3, value: value == null ? '' : complex ? JSON.stringify(value, null, 2) : value.join('\n'), help: resolved.description || (complex ? '按 JSON 数组填写。' : '每行一项。'), attrs: `data-json-path="${pointer}" data-value-kind="${complex ? 'json' : 'array'}"` });
        }
        if (type === 'boolean') return `<label class="nee-check-card"><input type="checkbox" data-json-path="${pointer}" data-value-kind="boolean" ${value ? 'checked' : ''}><span><b>${escapeHtml(label)}</b><small>${escapeHtml(resolved.description || '')}</small></span></label>`;
        const numeric = ['integer', 'number'].includes(type); const long = Number(resolved.maxLength || 0) > 180 || (resolved.description || '').length > 80;
        return field({ label, name: pointer, type: long ? 'textarea' : numeric ? 'number' : 'text', rows: 4, value: value ?? '', required: false, help: resolved.description || '', attrs: `data-json-path="${pointer}" data-value-kind="${numeric ? type : 'string'}"` });
    }

    formalConnectionsFor(record) { return (this.state.formalLinks?.items || formalConnections(record, this.state.relationTargets, this.state.metadata.components)).map(item => ({ ...item, label: REFERENCE_LABELS[item.component] || componentLabel(this.state.metadata.components.find(component => component.name === item.component) || { name: item.component }) })); }

    async moreFormalLinks() { const id = this.state.detail?.record?.id; if (!id) return; const chatId = this.hostSnapshot().chatId; const page = await api.listFormalRelations(chatId, id, this.state.formalLinks.items.length); if (this.state.detail?.record?.id !== id || this.hostSnapshot().chatId !== chatId) return; this.state.formalLinks = { total: page.total, items: [...this.state.formalLinks.items, ...page.items] }; this.render(); }

    renderRelations(record) {
        const connections = this.formalConnectionsFor(record);
        const group = (title, outgoing) => {
            const rows = connections.filter(item => item.outgoing === outgoing);
            return `<section class="nee-connection-group"><h3>${title} <small>${rows.length}</small></h3>${rows.map(item => `<button type="button" class="nee-formal-link" data-action="jump-relation" data-id="${escapeHtml(item.id)}" data-type="${escapeHtml(item.type)}"><span class="nee-link-direction" aria-hidden="true">${outgoing ? '↗' : '↙'}</span><span><small>${escapeHtml(item.label)}</small><b>${escapeHtml(item.name)}</b><small>${escapeHtml(this.typeInfo(item.type).label)}</small></span>${icon('chevron', 15)}</button>`).join('') || '<p class="nee-muted">暂未发现关联。</p>'}</section>`;
        };
        const rows = this.state.relations.map(relation => { const outgoing = relation.sourceId === record.id; const otherId = outgoing ? relation.targetId : relation.sourceId; const otherName = outgoing ? relation.targetName : relation.sourceName; const otherType = outgoing ? relation.targetType : relation.sourceType; return `<div class="nee-relation-row"><button type="button" data-action="jump-relation" data-id="${escapeHtml(otherId)}" data-type="${escapeHtml(otherType)}"><span>${outgoing ? '关联到' : '被关联'}</span><b>${escapeHtml(otherName)}</b><small>${escapeHtml(relation.relationType)}</small></button><button type="button" data-action="delete-relation" data-id="${escapeHtml(relation.id)}" aria-label="删除手动关联">${icon('trash', 13)}</button></div>`; }).join('') || '<p class="nee-muted">没有手动关联。</p>';
        const targets = this.state.relationTargets.filter(item => item.id !== record.id).map(item => [item.id, `${item.name} · ${this.typeInfo(item.type).label}`]);
        return `<div class="nee-relations"><p class="nee-muted">正式关联来自已保存资料中的引用。这里按方向显示，点击可查看另一端。</p>${group('此记录指向', true)}${group('指向此记录', false)}${this.state.formalLinks && this.state.formalLinks.items.length < this.state.formalLinks.total ? '<button type="button" class="nee-button secondary" data-action="more-formal-links">加载更多关联</button>' : ''}<details class="nee-advanced"><summary>手动关联 · ${this.state.relations.length}</summary><p class="nee-muted">这些是额外添加的链接，不会改写人物关系、地点或事件中的正式引用。</p>${rows}<form data-form="relation" class="nee-form-grid single"><input type="hidden" name="sourceId" value="${escapeHtml(record.id)}">${field({ label: '目标资料', name: 'targetId', options: [['', '选择资料'], ...targets], required: true })}${field({ label: '关联名称', name: 'relationType', value: '', required: true, placeholder: '例如：补充说明' })}${field({ label: '说明', name: 'description', value: '' })}<button class="nee-button secondary" type="submit">添加手动关联</button></form></details></div>`;
    }

    renderSettings() { return `<div class="nee-page-tabs">${tabs(this.state.tab, [{ id: 'guide', label: '使用说明' }, { id: 'reset', label: '重置' }])}</div><div class="nee-page-content">${this.state.tab === 'reset' ? this.renderReset() : this.renderGuide()}</div>`; }
    renderGuide() { return `<section class="nee-panel nee-guide"><h2>整理一个故事</h2><ol><li>在酒馆打开聊天，在“模型连接”保存一个主模型。按接口选择协议与模型兼容参数。</li><li>从工作台进入“批量提取”，选择首尾楼层。当前以 AI 正文为依据，开场和连续 AI 消息也可提取。</li><li>在任务记录查看结果。恢复重试会复用符合当前输入和配置的缓存；已经保存的消息不会重复导入。</li><li>进入事件或人物，阅读资料、查看正式关联；需要修改时再进入编辑。</li></ol><h3>资料与关联</h3><p>正式关联来自已保存资料中的引用。手动关联是额外链接，不会直接改变人物关系、当前地点或事件事实。</p><h3>尚未开放</h3><p>自动触发、自动召回、独立主观记忆提取及世界书批量提取。现有记忆资料仍可查看和编辑。</p><details class="nee-advanced"><summary>核心版本</summary><p>Registry ${escapeHtml(this.state.metadata.registryVersion)}。界面使用正式核心的类型、字段与校验规则。</p></details></section>`; }
    renderReset() { const paths = this.state.databasePaths || {}; return `<div class="nee-stack"><section class="nee-panel"><h2>数据库位置</h2><dl class="nee-path-list"><div><dt>全局配置</dt><dd>${escapeHtml(paths.configDatabase || '')}</dd></div><div><dt>当前聊天实体库</dt><dd>${escapeHtml(paths.entityDatabase || '请先绑定聊天')}</dd></div><div><dt>当前聊天运行库</dt><dd>${escapeHtml(paths.runtimeDatabase || '请先绑定聊天')}</dd></div></dl></section><section class="nee-panel nee-danger-zone"><h2>重置</h2><button type="button" class="nee-button secondary" data-action="reset-settings">重置插件设置</button><button type="button" class="nee-button danger" data-action="reset-chat">删除当前聊天数据库内容</button></section></div>`; }

    async onClick(event) {
        const nav = event.target.closest('[data-nav]'); if (nav) return this.navigate(nav.dataset.nav);
        const tab = event.target.closest('[data-tab]'); if (tab) { this.state.tab = tab.dataset.tab; if (this.state.tab === 'batch') this.scheduleJobPoll(); this.render(); return; }
        if (event.target.closest('[data-select-entity]')) return;
        const target = event.target.closest('[data-action]'); if (!target) return; const action = target.dataset.action;
        try {
            if (action === 'retry-service') return this.initialize();
            if (action === 'refresh-dashboard') { await this.bindCurrentChat(); await this.loadActiveView(); this.render(); }
            else if (action === 'select-preset') { this.state.selectedPresetId = target.dataset.id; this.render(); }
            else if (action === 'new-preset') { this.state.selectedPresetId = ''; this.render(); }
            else if (action === 'delete-preset') await this.deletePreset(target.dataset.id);
            else if (action === 'select-prompt') { this.state.detail = { kind: 'prompt', id: target.dataset.id }; this.render(); }
            else if (action === 'detail-profile' || action === 'detail-relations') { this.state.detailTab = action === 'detail-profile' ? 'profile' : 'relations'; this.render(); }
            else if (action === 'more-formal-links') await this.moreFormalLinks();
            else if (action === 'edit-entity') { this.state.editingEntity = true; this.render(); }
            else if (action === 'cancel-entity-edit') { await this.openEntity(this.state.detail.record.id); }
            else if (action === 'close-detail') { this.state.detail = null; this.render(); }
            else if (action === 'reset-prompt') await this.resetPrompt();
            else if (action === 'add-stage') this.mutateWorkflow(workflow => workflow.push({ id: `stage-${Date.now()}`, locked: false, groups: [{ id: `request-${Date.now()}`, blocks: [], presetId: '' }] }));
            else if (action === 'delete-stage') this.deleteStage(Number(target.dataset.stage));
            else if (action === 'add-group') this.mutateWorkflow(workflow => workflow[Number(target.dataset.stage)].groups.push({ id: `request-${Date.now()}`, blocks: [], presetId: '' }));
            else if (action === 'delete-group') this.deleteGroup(Number(target.dataset.stage), Number(target.dataset.group));
            else if (action === 'delete-module') { event.stopPropagation(); this.removeModule(target.dataset.id); }
            else if (action === 'restore-module') this.restoreModule(target.dataset.id);
            else if (action === 'save-pipeline') await this.savePipeline();
            else if (action === 'new-entity') await this.newEntity();
            else if (action === 'open-entity') await this.openEntity(target.dataset.id);
            else if (action === 'delete-entity') await this.deleteEntity(target.dataset.id);
            else if (action === 'delete-selected') await this.deleteSelected();
            else if (action === 'merge-selected') await this.mergeSelected();
            else if (action === 'toggle-sort') { this.state.entityDirection = this.state.entityDirection === 'asc' ? 'desc' : 'asc'; await this.loadEntities(); this.render(); }
            else if (action === 'enable-component') this.enableComponent(target.dataset.id);
            else if (action === 'delete-relation') await this.deleteRelation(target.dataset.id);
            else if (action === 'jump-relation') await this.jumpRelation(target.dataset.type, target.dataset.id);
            else if (action === 'cancel-job') await this.cancelJob(target.dataset.id);
            else if (action === 'retry-job') await this.retryJob(target.dataset.id);
            else if (action === 'reset-settings') await this.resetSettings();
            else if (action === 'reset-chat') await this.resetChat();
        } catch (error) { this.toast(error.message, 'error'); }
    }

    async navigate(target) { clearTimeout(this.pollTimer); this.state.active = target; this.state.tab = TAB_DEFAULTS[target] || (target.startsWith('entity:') ? '' : this.state.tab); this.state.detail = null; this.state.selectedIds.clear(); await this.loadActiveView(); this.render(); }

    async onSubmit(event) {
        const form = event.target.closest('form[data-form]'); if (!form) return; event.preventDefault();
        const kind = form.dataset.form;
        try {
            if (kind === 'trigger-settings') await this.saveTriggerSettings(form);
            else if (kind === 'batch-job') await this.createBatchJob(form);
            else if (kind === 'api-preset') await this.saveApiPreset(form);
            else if (kind === 'prompt-editor') await this.savePrompt(form);
            else if (kind === 'entity-editor') await this.saveEntity(form);
            else if (kind === 'relation') await this.saveRelation(form);
        } catch (error) { this.toast(error.message, 'error'); }
    }

    onInput(event) {
        if (event.target.matches('[data-entity-search]')) { clearTimeout(this.searchTimer); this.state.entityQuery = event.target.value; this.searchTimer = setTimeout(async () => { await this.loadEntities(); this.render(); }, 250); }
        if (event.target.name === 'baseUrl') {
            const form = event.target.form; const provider = detectProvider(event.target.value); form.elements.provider.value = provider;
            const badge = form.querySelector('[data-provider-badge]'); if (badge) badge.textContent = `已识别：${providerLabel(provider)}`;
        }
    }

    async onChange(event) {
        if (event.target.matches('[data-relation-type]')) { this.state.entityType = event.target.value; await this.loadEntities(); this.render(); }
        else if (event.target.matches('[data-entity-sort]')) { this.state.entitySort = event.target.value; await this.loadEntities(); this.render(); }
        else if (event.target.matches('[data-select-entity]')) { const id = event.target.dataset.selectEntity; event.target.checked ? this.state.selectedIds.add(id) : this.state.selectedIds.delete(id); this.render(); }
        else if (event.target.matches('[data-group-preset]')) { const [stage, group] = event.target.dataset.groupPreset.split(':').map(Number); this.mutateWorkflow(workflow => { workflow[stage].groups[group].presetId = event.target.value; }, false); }
        else if (event.target.name === 'transport') { const form = event.target.form; const modes = this.state.metadata.modelCapabilities.transports.find(item => item.id === event.target.value)?.reasoningModes || ['omit']; form.elements.reasoningMode.innerHTML = selectOptions(modes.map(mode => [mode, ({ omit: '不发送（兼容优先）', effort: '强度参数', thinking: '思考开关', thinking_and_effort: '思考开关＋强度', budget: 'Token 预算', adaptive: '自适应思考', level: '思考等级' })[mode] || mode]), 'omit'); }
    }


    onDragStart(event) { const block = event.target.closest('[data-block]'); if (!block || block.getAttribute('draggable') === 'false') return; this.draggedBlock = block.dataset.block; event.dataTransfer.effectAllowed = 'move'; }
    onDragOver(event) { if (event.target.closest('[data-drop-group]')) event.preventDefault(); }
    onDrop(event) { const group = event.target.closest('[data-drop-group]'); if (!group || !this.draggedBlock) return; event.preventDefault(); if (group.closest('.is-locked')) { this.toast('识别故事需要独立先行，请选择后续请求。', 'error'); return; } const [stageIndex, groupIndex] = group.dataset.dropGroup.split(':').map(Number); const id = this.draggedBlock; this.mutateWorkflow(workflow => { for (const stage of workflow) for (const request of stage.groups) request.blocks = request.blocks.filter(item => item !== id); workflow[stageIndex].groups[groupIndex].blocks.push(id); }); this.draggedBlock = ''; }

    mutateWorkflow(callback, rerender = true) { const workflow = this.normalizedWorkflow(); callback(workflow); this.state.settings.extraction = { ...(this.state.settings.extraction || {}), workflow }; if (rerender) this.render(); }
    requiredBlocks(blocks = []) { return blocks.filter(id => ['narrative_map', 'event_content', 'entity_create', 'entity_update', 'relation_references'].includes(id)); }
    deleteStage(index) { const workflow = this.normalizedWorkflow(); if (this.requiredBlocks(workflow[index]?.groups.flatMap(group => group.blocks) || []).length) throw new Error('事件、资料与关系模块需要共同完成才能保存，不能随步骤删除。'); this.mutateWorkflow(value => value.splice(index, 1)); }
    deleteGroup(stage, group) { const workflow = this.normalizedWorkflow(); if (this.requiredBlocks(workflow[stage]?.groups[group]?.blocks || []).length) throw new Error('这个请求含有正式提交闭环的必需模块，不能整组删除。'); this.mutateWorkflow(value => value[stage].groups.splice(group, 1)); }
    removeModule(id) { if (this.requiredBlocks([id]).length) throw new Error('该模块属于正式提交闭环，不能移除。'); this.mutateWorkflow(workflow => { for (const stage of workflow) for (const group of stage.groups) group.blocks = group.blocks.filter(item => item !== id); }); if (this.state.detail?.id === id) this.state.detail = null; }
    restoreModule(id) { this.mutateWorkflow(workflow => { let stage = workflow.find(item => !item.locked); if (!stage) { stage = { id: `stage-${Date.now()}`, locked: false, groups: [] }; workflow.push(stage); } stage.groups.push({ id: `request-${Date.now()}`, blocks: [id], presetId: '' }); }); }

    async withBusy(callback, rerender = true) { if (this.state.busy) return; this.state.busy = true; try { return await callback(); } finally { this.state.busy = false; if (rerender) this.render(); } }
    toast(message, type = 'success') { const region = this.root.querySelector('.nee-toast-region'); if (!region) return; const item = document.createElement('div'); item.className = `nee-toast ${type}`; item.textContent = message; region.append(item); setTimeout(() => item.remove(), 3500); }

    async saveTriggerSettings(form) { const data = new FormData(form); this.state.settings.extraction = { ...(this.state.settings.extraction || {}), floorInterval: Math.max(1, Number(data.get('floorInterval')) || 10), retainTail: Math.max(0, Number(data.get('retainTail')) || 0) }; this.state.settings = await api.saveSettings(this.state.settings); await this.loadActiveView(); this.render(); this.toast('触发设置已保存。'); }

    selectedPipelinePreset() { const first = this.normalizedWorkflow().flatMap(stage => stage.groups).find(group => group.blocks.includes('narrative_map')); return first?.presetId || ''; }
    pipelineConnections() { const result = {}; for (const group of this.normalizedWorkflow().flatMap(stage => stage.groups)) for (const block of group.blocks) result[block] = group.presetId || ''; return result; }
    async checkRunConnections() {
        const presets = await api.listApiPresets();
        const primary = presets.find(item => item.isPrimary);
        const mapId = this.selectedPipelinePreset();
        const mapPreset = mapId ? presets.find(item => item.id === mapId) : primary;
        if (!mapPreset) throw new Error('请先在“模型连接”保存主模型，或为“识别事件与对象”选择模型。');
        const connections = this.pipelineConnections();
        for (const id of ['narrative_map', 'event_content', 'entity_create', 'entity_update', 'relation_references']) {
            const selectedId = connections[id];
            const preset = selectedId ? presets.find(item => item.id === selectedId) : id === 'narrative_map' ? mapPreset : primary || mapPreset;
            const label = this.promptInfo(id).label;
            if (!preset?.hasApiKey || !preset.model) throw new Error(`${label}的模型未配置完整，请检查模型名称与密钥。`);
            await api.validateModelProfile(preset);
        }
    }
    async createBatchJob(form) { const data = new FormData(form); const snapshot = this.hostSnapshot(); const messages = this.hostMessages(); if (Number(data.get('startFloor')) > Number(data.get('endFloor'))) throw new Error('开始楼层不能大于结束楼层。'); await this.checkRunConnections(); if (snapshot.chatId !== this.hostSnapshot().chatId) throw new Error('聊天已切换，请重新选择提取范围。'); const job = await api.createBatchJob({ chatId: snapshot.chatId, startFloor: Number(data.get('startFloor')), endFloor: Number(data.get('endFloor')), entityTypes: this.entityTypes.filter(type => type.id !== 'memory').map(type => type.id), messages, presetId: this.selectedPipelinePreset(), connections: this.pipelineConnections() }); this.state.batchJobs = [job, ...this.state.batchJobs]; this.render(); this.scheduleJobPoll(); this.toast('批量提取已开始。'); }
    async retryJob(id) { const snapshot = this.hostSnapshot(); const messages = this.hostMessages(); await this.checkRunConnections(); if (snapshot.chatId !== this.hostSnapshot().chatId) throw new Error('聊天已切换，请在原聊天中重新运行。'); const job = await api.retryBatchJob(snapshot.chatId, id, { messages, presetId: this.selectedPipelinePreset(), connections: this.pipelineConnections() }); this.state.batchJobs = [job, ...this.state.batchJobs]; this.render(); this.scheduleJobPoll(); }
    async cancelJob(id) { await api.cancelBatchJob(this.hostSnapshot().chatId, id); this.state.batchJobs = await api.listBatchJobs(this.hostSnapshot().chatId); this.render(); }

    async saveApiPreset(form) { const data = new FormData(form); await api.saveApiPreset({ id: data.get('id'), name: data.get('name'), baseUrl: data.get('baseUrl'), provider: detectProvider(data.get('baseUrl')), model: data.get('model'), transport: data.get('transport'), apiKey: data.get('apiKey'), reasoningEffort: data.get('reasoningEffort'), options: this.modelOptionsFromForm(form), isPrimary: data.get('isPrimary') === 'on' }); this.state.apiPresets = await api.listApiPresets(); this.state.selectedPresetId = this.state.apiPresets.find(item => item.name === data.get('name'))?.id || ''; this.render(); this.toast('API 预设已保存。'); }
    async deletePreset(id) { if (!globalThis.confirm('删除这个 API 预设？')) return; await api.deleteApiPreset(id); this.state.apiPresets = await api.listApiPresets(); this.state.selectedPresetId = ''; this.render(); }

    async savePipeline() { const parallel = Number(this.root.querySelector('[name="parallelStaggerSeconds"]')?.value); const serial = Number(this.root.querySelector('[name="serialRpm"]')?.value); this.state.settings.extraction = { ...this.state.settings.extraction, parallelStaggerSeconds: Math.max(0, parallel || 0), serialRpm: Math.max(1, serial || 30), workflow: this.normalizedWorkflow() }; this.state.settings = await api.saveSettings(this.state.settings); this.toast('提取编排与模块 API 已保存。'); }
    async savePrompt(form) { const data = new FormData(form); this.state.settings.promptOverrides = { ...(this.state.settings.promptOverrides || {}), [data.get('promptId')]: String(data.get('systemPrompt') || '') }; this.state.settings = await api.saveSettings(this.state.settings); this.render(); this.toast('提示词覆盖已保存。'); }
    async resetPrompt() { const id = this.state.detail?.id; if (!id) return; const overrides = { ...(this.state.settings.promptOverrides || {}) }; delete overrides[id]; this.state.settings.promptOverrides = overrides; this.state.settings = await api.saveSettings(this.state.settings); this.render(); this.toast('已恢复正式核心默认提示词。'); }

    async newEntity() { this.state.detailTab = 'profile'; this.state.editingEntity = true; const entity = await api.prepareEntity({ type: this.state.entityType, name: '' }); this.state.detail = { kind: 'entity', record: { id: '', type: entity.type, name: '', description: '', entity, revision: null } }; this.state.relations = []; this.state.relationTargets = []; this.render(); }
    async openEntity(id) { const chatId = this.hostSnapshot().chatId; const [record, relations, targets, links] = await Promise.all([api.getEntity(chatId, id), api.listRelations(chatId, id), api.listEntities({ chatId, limit: 500, sort: 'name', direction: 'asc' }), api.listFormalRelations(chatId, id)]); if (chatId !== this.hostSnapshot().chatId) return; this.state.detailTab = 'profile'; this.state.editingEntity = false; if (!record?.entity) throw new Error('这条资料已不存在，请刷新列表。'); this.state.detail = { kind: 'entity', record }; this.state.relations = relations; this.state.relationTargets = targets; this.state.formalLinks = links; this.render(); }
    enableComponent(name) { const form = this.root.querySelector('[data-form="entity-editor"]'); if (form) this.state.detail.record.entity = this.entityFromForm(form); const record = this.state.detail?.record; if (!record) return; const component = this.state.metadata.components.find(item => item.name === name); record.entity.components[name] = { schema_version: component.schemaVersion, data: {} }; this.render(); }

    entityFromForm(form) {
        const entity = deepClone(this.state.detail.record.entity);
        for (const control of form.querySelectorAll('[data-json-path]')) {
            const kind = control.dataset.valueKind || 'string'; let value;
            if (kind === 'boolean') value = control.checked;
            else if (kind === 'array') value = control.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
            else if (kind === 'json') { try { value = JSON.parse(control.value || '{}'); } catch { throw new Error(`${control.closest('.nee-field')?.querySelector('span')?.textContent || 'JSON 字段'}格式不正确。`); } }
            else if (kind === 'integer') value = control.value === '' ? undefined : Number.parseInt(control.value, 10);
            else if (kind === 'number') value = control.value === '' ? undefined : Number(control.value);
            else value = control.value;
            if (value !== undefined) setPointer(entity, control.dataset.jsonPath, value);
        }
        return entity;
    }

    showEntityErrors(form, errors = []) {
        form.querySelectorAll('.has-error').forEach(node => node.classList.remove('has-error')); form.querySelectorAll('[data-field-error]').forEach(node => { node.textContent = ''; });
        const summary = form.querySelector('[data-form-error]'); if (summary) summary.innerHTML = errors.map(error => `<p>${escapeHtml(error.path || error.field || '')}：${escapeHtml(error.message)}</p>`).join('');
        for (const error of errors) { const pointer = error.path || error.field; const control = [...form.querySelectorAll('[data-json-path]')].find(item => pointer === item.dataset.jsonPath || pointer?.startsWith(item.dataset.jsonPath)); const wrapper = control?.closest('.nee-field'); if (wrapper) { wrapper.classList.add('has-error'); const message = wrapper.querySelector('[data-field-error]'); if (message) message.textContent = error.message; } }
    }

    async saveEntity(form) {
        const entity = this.entityFromForm(form); const validation = await api.validateCoreEntity({ entity });
        if (!validation.valid) { this.showEntityErrors(form, validation.errors); this.toast('校验未通过，请检查标红字段和错误列表。', 'error'); return; }
        const result = await api.saveEntity(this.hostSnapshot().chatId, { entity, expectedRevision: Number(form.elements.expectedRevision.value) || undefined });
        if (!result.valid) { this.showEntityErrors(form, result.errors); return; }
        await this.loadEntities(); await this.openEntity(result.entity.id); this.toast('实体已通过正式校验并保存。');
    }

    async deleteEntity(id) { if (!globalThis.confirm('删除这个实体？此操作会同时删除显式关系。')) return; await api.deleteEntities(this.hostSnapshot().chatId, [id]); this.state.detail = null; await this.loadEntities(); this.render(); }
    async deleteSelected() { if (!globalThis.confirm(`删除选中的 ${this.state.selectedIds.size} 个实体？`)) return; await api.deleteEntities(this.hostSnapshot().chatId, [...this.state.selectedIds]); this.state.selectedIds.clear(); this.state.detail = null; await this.loadEntities(); this.render(); }
    async mergeSelected() { if (!globalThis.confirm('合并所选实体？最早创建的实体将作为目标，语义内容由脚本拼接。')) return; const entity = await api.mergeEntities(this.hostSnapshot().chatId, [...this.state.selectedIds]); this.state.selectedIds.clear(); await this.loadEntities(); await this.openEntity(entity.id); }
    async saveRelation(form) { const data = Object.fromEntries(new FormData(form)); await api.saveRelation(this.hostSnapshot().chatId, data); await this.openEntity(data.sourceId); this.toast('关系已添加。'); }
    async deleteRelation(id) { await api.deleteRelation(this.hostSnapshot().chatId, id); await this.openEntity(this.state.detail.record.id); }
    async jumpRelation(type, id) { this.state.active = `entity:${type}`; this.state.entityType = type; await this.loadEntities(); await this.openEntity(id); }

    async resetSettings() { if (!globalThis.confirm('重置插件设置？API 预设和聊天数据库不会删除。')) return; this.state.settings = await api.resetSettings(); this.render(); }
    async resetChat() { const chatId = this.hostSnapshot().chatId; if (!chatId || !globalThis.confirm('删除当前聊天的实体与运行记录？其他聊天和全局 API 不受影响。')) return; await api.resetChat(chatId); await this.bindCurrentChat(); await this.loadActiveView(); this.state.detail = null; this.render(); }
}
