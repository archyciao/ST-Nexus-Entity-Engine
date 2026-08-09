import { api } from './api.js';
import { PROVIDERS, REASONING_LEVELS, detectProvider, escapeHtml, icon, providerLabel, reasoningPayload } from './constants.js';

const TAB_DEFAULTS = Object.freeze({ dashboard: 'overview', extraction: 'pipeline', settings: 'guide' });

function selectOptions(options, selected) {
    return options.map(option => {
        const [value, label] = Array.isArray(option) ? option : [option.id, option.label];
        return `<option value="${escapeHtml(value)}" ${String(value) === String(selected ?? '') ? 'selected' : ''}>${escapeHtml(label)}</option>`;
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
    return `<div class="nee-tabs" role="tablist">${items.map(item => `<button type="button" class="${active === item.id ? 'is-active' : ''}" data-tab="${item.id}" role="tab" aria-selected="${active === item.id}">${escapeHtml(item.label)}</button>`).join('')}</div>`;
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
    return { queued: '等待执行', running: '正在提取', completed: '已完成', failed: '失败', cancelled: '已取消' }[status] || status;
}

export class Workbench {
    constructor(root) {
        this.root = root;
        this.state = {
            active: 'dashboard', tab: 'overview', ready: false, serviceError: '', health: null,
            metadata: null, settings: null, dashboard: null, entities: [], entityType: 'event',
            entityQuery: '', entitySort: 'updated', entityDirection: 'desc', selectedIds: new Set(),
            detail: null, relations: [], relationTargets: [], apiPresets: [], selectedPresetId: '',
            batchJobs: [], databasePaths: null, busy: false,
        };
        this.searchTimer = null; this.pollTimer = null; this.draggedBlock = ''; this.eventsBound = false; this.boundEvents = false;
    }

    get entityTypes() { return this.state.metadata?.entityTypes || []; }
    typeInfo(type) { return this.entityTypes.find(item => item.id === type) || { id: type, label: type, description: '' }; }
    promptInfo(id) { return this.state.metadata?.prompts?.find(item => item.id === id) || { id, label: id, systemPrompt: '' }; }

    async initialize() {
        if (!this.root) return;
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
        const snapshot = this.hostSnapshot(); if (!snapshot.chatId) { this.state.dashboard = null; return null; }
        this.state.dashboard = await api.bindChat(snapshot); this.state.databasePaths = this.state.dashboard.paths || this.state.databasePaths; return this.state.dashboard;
    }

    subscribeToHost() {
        if (this.boundEvents) return;
        const context = globalThis.SillyTavern?.getContext?.(); const source = context?.eventSource; const types = context?.eventTypes || {};
        if (!source?.on) return; const refresh = () => this.refreshHostState();
        for (const eventName of [types.CHAT_CHANGED, types.MESSAGE_SENT, types.MESSAGE_RECEIVED, types.MESSAGE_DELETED]) if (eventName) source.on(eventName, refresh);
        this.boundEvents = true;
    }

    async refreshHostState() { try { await this.bindCurrentChat(); if (this.state.active === 'dashboard') this.render(); } catch (error) { this.toast(error.message, 'error'); } }

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
            [this.state.dashboard, this.state.batchJobs] = await Promise.all([api.dashboard(chatId), api.listBatchJobs(chatId)]);
            this.scheduleJobPoll();
        } else if (this.state.active === 'api' || this.state.active === 'extraction') {
            this.state.apiPresets = await api.listApiPresets();
        } else if (this.state.active.startsWith('entity:')) {
            this.state.entityType = this.state.active.split(':')[1] || this.entityTypes[0]?.id; await this.loadEntities();
        }
    }

    scheduleJobPoll() {
        clearTimeout(this.pollTimer);
        if (!this.state.batchJobs.some(job => ['queued', 'running'].includes(job.status))) return;
        this.pollTimer = setTimeout(async () => {
            if (this.state.active !== 'dashboard' || !this.hostSnapshot().chatId) return;
            try { this.state.batchJobs = await api.listBatchJobs(this.hostSnapshot().chatId); this.render(); this.scheduleJobPoll(); } catch {}
        }, 1500);
    }

    async loadEntities() {
        const chatId = this.hostSnapshot().chatId; if (!chatId) { this.state.entities = []; return; }
        this.state.entities = await api.listEntities({ chatId, type: this.state.entityType, query: this.state.entityQuery, sort: this.state.entitySort, direction: this.state.entityDirection, limit: 200 });
        const ids = new Set(this.state.entities.map(item => item.id)); this.state.selectedIds = new Set([...this.state.selectedIds].filter(id => ids.has(id)));
    }

    render() {
        if (this.state.serviceError) { this.root.innerHTML = this.renderServiceError(); return; }
        if (!this.state.ready || !this.state.settings || !this.state.metadata) { this.renderLoading(); return; }
        const scrollPositions = Object.fromEntries(['.nee-main', '.nee-page-content', '.nee-entity-list', '.nee-detail-body'].map(selector => [selector, this.root.querySelector(selector)?.scrollTop || 0]));
        this.root.innerHTML = `<div class="nee-layout">${this.renderSidebar()}<main class="nee-main">${this.renderMain()}</main>${this.renderDetailPane()}</div><div class="nee-toast-region" aria-live="polite"></div>`;
        for (const [selector, top] of Object.entries(scrollPositions)) { const node = this.root.querySelector(selector); if (node) node.scrollTop = top; }
    }

    renderServiceError() { return `<div class="nee-service-error">${icon('warning', 34)}<h2>正式核心未连接</h2><p>${escapeHtml(this.state.serviceError)}</p><p>请确认酒馆已加载仓库根目录插件和 server 子目录。</p><button type="button" class="nee-button primary" data-action="retry-service">重新连接</button></div>`; }

    renderSidebar() {
        const entityItems = this.entityTypes.map(type => `<button type="button" class="nee-nav-item ${this.state.active === `entity:${type.id}` ? 'is-active' : ''}" data-nav="entity:${type.id}" title="${escapeHtml(type.description)}"><span>${escapeHtml(type.label)}</span><small>${this.state.dashboard?.entityByType?.[type.id] ?? 0}</small></button>`).join('');
        return `<aside class="nee-sidebar"><div class="nee-nav-section">
            <button type="button" class="nee-nav-item ${this.state.active === 'dashboard' ? 'is-active' : ''}" data-nav="dashboard">${icon('dashboard')}<span>仪表盘</span></button>
            <button type="button" class="nee-nav-item ${this.state.active === 'api' ? 'is-active' : ''}" data-nav="api">${icon('api')}<span>API 设置</span></button>
            <button type="button" class="nee-nav-item ${this.state.active === 'extraction' ? 'is-active' : ''}" data-nav="extraction">${icon('extraction')}<span>提取与召回</span></button>
            </div><div class="nee-nav-label">实体类型</div><div class="nee-nav-section nee-entity-nav">${entityItems}</div><div class="nee-nav-spacer"></div><div class="nee-nav-section"><button type="button" class="nee-nav-item ${this.state.active === 'settings' ? 'is-active' : ''}" data-nav="settings">${icon('settings')}<span>设置</span></button></div></aside>`;
    }

    currentTitle() { if (this.state.active.startsWith('entity:')) return `${this.typeInfo(this.state.entityType).label} 名录`; return { dashboard: '仪表盘', api: 'API 设置', extraction: '提取与召回设置', settings: '设置' }[this.state.active] || 'NexusEntityEngine'; }
    currentSubtitle() { if (this.state.active.startsWith('entity:')) return this.typeInfo(this.state.entityType).description; return { dashboard: '当前聊天、正式实体与提取运行状态', api: '管理模型连接；渠道随地址自动识别', extraction: '正式提取阶段、请求编排与阶段 API', settings: '使用说明、物理存储位置与重置' }[this.state.active] || ''; }
    renderMain() { return `<div class="nee-page-header"><div><h1>${escapeHtml(this.currentTitle())}</h1><p>${escapeHtml(this.currentSubtitle())}</p></div><div class="nee-service-state"><span></span>Registry ${escapeHtml(this.state.metadata.registryVersion)}</div></div>${this.renderActiveView()}`; }
    renderActiveView() { if (this.state.active === 'dashboard') return this.renderDashboard(); if (this.state.active === 'api') return this.renderApi(); if (this.state.active === 'extraction') return this.renderExtraction(); if (this.state.active.startsWith('entity:')) return this.renderEntityDirectory(); if (this.state.active === 'settings') return this.renderSettings(); return ''; }

    renderDashboard() { return `<div class="nee-page-tabs">${tabs(this.state.tab, [{ id: 'overview', label: '运行概览' }, { id: 'batch', label: '批量提取' }])}</div><div class="nee-page-content">${this.state.tab === 'batch' ? this.renderBatch() : this.renderOverview()}</div>`; }

    renderOverview() {
        const data = this.state.dashboard; if (!data?.chat) return emptyState('尚未绑定聊天', '请先在酒馆中打开一个角色聊天。');
        const progress = Math.max(0, Math.min(100, Number(data.workflow.progress) || 0));
        const typeRows = this.entityTypes.filter(type => data.entityByType?.[type.id]).map(type => `<button type="button" data-nav="entity:${type.id}"><span>${escapeHtml(type.label)}</span><b>${data.entityByType[type.id]}</b></button>`).join('') || '<p class="nee-muted">当前聊天还没有实体记录。</p>';
        return `<div class="nee-dashboard-grid">
            <section class="nee-panel nee-bound-chat"><div class="nee-section-heading"><div><span>当前绑定</span><h2>${escapeHtml(data.chat.characterName || data.chat.name)}</h2></div><button type="button" class="nee-icon-button" data-action="refresh-dashboard">${icon('refresh')}</button></div><dl><div><dt>聊天标识</dt><dd>${escapeHtml(data.chat.name)}</dd></div><div><dt>当前楼层</dt><dd>${data.chat.messageCount}</dd></div><div><dt>已提取至</dt><dd>${data.extractedThrough}</dd></div></dl></section>
            <section class="nee-panel nee-stat-panel"><span>实体总数</span><strong>${data.entityTotal}</strong><small>当前聊天实体库</small></section>
            <section class="nee-panel nee-stat-panel"><span>未提取楼层</span><strong>${data.unextractedCount}</strong><small>已扣除尾部保留区</small></section>
            <section class="nee-panel nee-stat-panel"><span>下次触发</span><strong>${data.nextTriggerFloor}</strong><small>达到该层后进入任务</small></section>
            <section class="nee-panel nee-workflow-status"><div class="nee-section-heading"><div><span>提取流程</span><h2>${escapeHtml(statusLabel(data.workflow.stage))}</h2></div><b>${progress}%</b></div><div class="nee-progress"><span style="width:${progress}%"></span></div><ol>${this.state.metadata.prompts.map((prompt, index) => `<li class="${progress >= ((index + 1) / this.state.metadata.prompts.length) * 100 ? 'is-done' : ''}"><span>${index + 1}</span>${escapeHtml(prompt.label)}</li>`).join('')}</ol></section>
            <section class="nee-panel nee-type-summary"><div class="nee-section-heading"><div><span>实体分布</span><h2>当前聊天</h2></div></div><div class="nee-type-counts">${typeRows}</div></section>
            <section class="nee-panel nee-trigger-settings"><div class="nee-section-heading"><div><span>自动提取</span><h2>楼层触发条件</h2></div></div><form data-form="trigger-settings" class="nee-form-grid compact">${field({ label: '每隔多少层提取', name: 'floorInterval', type: 'number', value: data.extraction.floorInterval, required: true })}${field({ label: '保留最后多少层', name: 'retainTail', type: 'number', value: data.extraction.retainTail, required: true })}<div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}保存</button></div></form></section>
        </div>`;
    }

    renderBatch() {
        const chat = this.state.dashboard?.chat; const messageCount = chat?.messageCount || 0;
        const jobs = this.state.batchJobs.map(job => `<article class="nee-run-row"><div class="nee-run-main"><span class="nee-status status-${job.status}">${statusLabel(job.status)}</span><strong>${job.startFloor}—${job.endFloor} 层</strong><small>${job.entityTypes.map(type => this.typeInfo(type).label).join('、')}</small></div><div class="nee-run-progress"><span style="width:${job.progress}%"></span></div><p>${escapeHtml(job.currentStep || '')}</p>${job.error ? `<details><summary>失败信息</summary><pre>${escapeHtml(job.error)}</pre></details>` : ''}<div class="nee-run-actions">${job.status === 'running' ? `<button type="button" class="nee-button secondary" data-action="cancel-job" data-id="${job.id}">${icon('stop', 14)}取消</button>` : ''}${['failed', 'cancelled'].includes(job.status) ? `<button type="button" class="nee-button secondary" data-action="retry-job" data-id="${job.id}">${icon('retry', 14)}重试</button>` : ''}${job.logs?.length ? `<details><summary>运行日志</summary><pre>${escapeHtml(job.logs.join('\n'))}</pre></details>` : ''}</div></article>`).join('') || '<p class="nee-muted">还没有批量任务。</p>';
        return `<div class="nee-stack"><section class="nee-panel"><div class="nee-section-heading"><div><span>历史聊天</span><h2>执行批量提取</h2></div></div>${chat ? `<form data-form="batch-job" class="nee-form-grid">${field({ label: '开始楼层', name: 'startFloor', type: 'number', value: 0, required: true })}${field({ label: '结束楼层', name: 'endFloor', type: 'number', value: Math.max(0, messageCount - 1), required: true })}<fieldset class="nee-check-grid"><legend>主要写入范围</legend>${this.entityTypes.map(type => `<label><input type="checkbox" name="entityTypes" value="${type.id}" checked><span>${escapeHtml(type.label)}</span></label>`).join('')}</fieldset><p class="nee-muted">提交后立即运行正式五阶段流水线。为保持引用网络封闭，所选实体引用到的必要实体会自动随同写入；未配置 API、网络失败或校验失败都会进入可重试状态。</p><div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('play', 15)}开始提取</button></div></form>` : emptyState('尚未绑定聊天', '请先打开需要处理的聊天。')}</section><section class="nee-panel is-disabled"><div class="nee-section-heading"><div><span>世界书</span><h2>批量提取世界书</h2></div><span class="nee-badge">待讨论</span></div></section><section class="nee-panel"><div class="nee-section-heading"><div><span>运行记录</span><h2>批量任务</h2></div></div><div class="nee-run-list">${jobs}</div></section></div>`;
    }

    renderApi() {
        const selected = this.state.apiPresets.find(item => item.id === this.state.selectedPresetId) || null;
        const list = this.state.apiPresets.map(item => `<button type="button" class="nee-preset-row ${selected?.id === item.id ? 'is-active' : ''}" data-action="select-preset" data-id="${item.id}"><div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(providerLabel(item.provider))} · ${escapeHtml(item.model || '未设置模型')}</span></div>${item.isPrimary ? '<b>主 API</b>' : ''}</button>`).join('') || '<p class="nee-muted">还没有 API 预设。</p>';
        const provider = selected?.provider || 'openai_compatible'; const effort = selected?.reasoningEffort || 'none';
        return `<div class="nee-page-content"><div class="nee-master-detail api-master-detail"><section class="nee-list-pane"><div class="nee-pane-toolbar"><h2>API 预设</h2><button type="button" class="nee-icon-button" data-action="new-preset">${icon('plus')}</button></div><div class="nee-preset-list">${list}</div></section><section class="nee-editor-pane"><form data-form="api-preset" class="nee-form-grid single"><input type="hidden" name="id" value="${escapeHtml(selected?.id || '')}"><input type="hidden" name="provider" value="${provider}"><div class="nee-editor-title"><div><span>${selected ? '编辑预设' : '新建预设'}</span><h2>${escapeHtml(selected?.name || '未命名 API')}</h2></div>${selected ? `<button type="button" class="nee-button danger ghost" data-action="delete-preset" data-id="${selected.id}">${icon('trash', 15)}删除</button>` : ''}</div><div class="nee-form-grid two">${field({ label: '预设名称', name: 'name', value: selected?.name || '', required: true })}${field({ label: '模型名称', name: 'model', value: selected?.model || '', required: true, placeholder: 'model-id' })}</div><div class="nee-auto-provider">${field({ label: 'API 地址', name: 'baseUrl', value: selected?.baseUrl || '', required: true, placeholder: 'https://...' })}<span data-provider-badge>已识别：${escapeHtml(providerLabel(provider))}</span></div><div class="nee-form-grid two">${field({ label: '传输协议', name: 'transport', value: selected?.transport || 'chat_completions', options: [['chat_completions', 'Chat Completions'], ['responses', 'Responses'], ['messages', 'Anthropic Messages'], ['gemini', 'Gemini GenerateContent']] })}${field({ label: '思考强度', name: 'reasoningEffort', value: effort, options: REASONING_LEVELS })}</div>${field({ label: 'API Key', name: 'apiKey', value: '', type: 'password', placeholder: selected?.hasApiKey ? '已保存；留空保持不变' : '仅保存在本机 config.sqlite' })}<label class="nee-check-card"><input type="checkbox" name="isPrimary" ${selected?.isPrimary ? 'checked' : ''}><span><b>设为主 API</b><small>模块未单独选择时使用。</small></span></label><div class="nee-payload-preview"><span>兼容参数预览</span><pre data-reasoning-preview>${escapeHtml(JSON.stringify(reasoningPayload(provider, effort), null, 2))}</pre></div><div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}保存 API 预设</button></div></form></section></div></div>`;
    }

    normalizedWorkflow() {
        const prompts = this.state.metadata.prompts; const raw = this.state.settings.extraction?.workflow;
        if (Array.isArray(raw) && raw.some(stage => Array.isArray(stage.groups))) return deepClone(raw);
        const locked = prompts.find(item => item.lockedFirst) || prompts[0]; const remaining = prompts.filter(item => item.id !== locked?.id);
        return [{ id: 'stage-fixed', locked: true, groups: [{ id: 'request-fixed', blocks: [locked.id], presetId: raw?.find(item => item.id === locked.id)?.presetId || '' }] }, { id: 'stage-main', locked: false, groups: remaining.map(item => ({ id: `request-${item.id}`, blocks: [item.id], presetId: raw?.find(row => row.id === item.id)?.presetId || '' })) }];
    }

    renderExtraction() { return `<div class="nee-page-tabs">${tabs(this.state.tab, [{ id: 'pipeline', label: '提取设置' }, { id: 'recall', label: '召回设置' }])}</div><div class="nee-page-content">${this.state.tab === 'recall' ? `<section class="nee-panel is-disabled">${emptyState('召回配置尚未开放', '保留边界，等待后续专项讨论。')}</section>` : this.renderPipeline()}</div>`; }

    renderPipeline() {
        const workflow = this.normalizedWorkflow(); const used = new Set(workflow.flatMap(stage => stage.groups.flatMap(group => group.blocks || []))); const unassigned = this.state.metadata.prompts.filter(item => !used.has(item.id));
        return `<section class="nee-panel nee-compact-pipeline"><div class="nee-section-heading"><div><span>正式提取器</span><h2>请求顺序</h2></div><button type="button" class="nee-button secondary" data-action="add-stage">${icon('plus', 14)}串行步骤</button></div><p class="nee-muted">纵向步骤依次执行；同一步骤中的请求并发；把多个模块拖进同一请求即合并。点击模块后在右侧编辑提示词覆盖，并可就地选择 API。</p><div class="nee-pipeline-board">${workflow.map((stage, index) => this.renderPipelineStage(stage, index)).join('')}</div>${unassigned.length ? `<div class="nee-unassigned"><span>已移除模块</span>${unassigned.map(item => `<button type="button" data-action="restore-module" data-id="${item.id}">${icon('plus', 13)}${escapeHtml(item.label)}</button>`).join('')}</div>` : ''}<div class="nee-pipeline-rate">${field({ label: '并发错峰（秒）', name: 'parallelStaggerSeconds', type: 'number', value: this.state.settings.extraction?.parallelStaggerSeconds ?? 0.5 })}${field({ label: '串行速率（RPM）', name: 'serialRpm', type: 'number', value: this.state.settings.extraction?.serialRpm ?? 30 })}</div><div class="nee-form-actions"><button type="button" class="nee-button primary" data-action="save-pipeline">${icon('save', 15)}保存编排</button></div></section>`;
    }

    renderPipelineStage(stage, stageIndex) {
        return `<section class="nee-sequence-row ${stage.locked ? 'is-locked' : ''}" data-stage-index="${stageIndex}"><header><span>步骤 ${stageIndex + 1}</span><small>${stage.locked ? '固定先行' : `${stage.groups.length} 个并发请求`}</small><div>${!stage.locked ? `<button type="button" data-action="add-group" data-stage="${stageIndex}">${icon('plus', 13)}请求</button><button type="button" data-action="delete-stage" data-stage="${stageIndex}">${icon('trash', 13)}</button>` : ''}</div></header><div class="nee-request-row">${stage.groups.map((group, groupIndex) => this.renderRequestGroup(group, stageIndex, groupIndex, stage.locked)).join('')}</div></section>`;
    }

    renderRequestGroup(group, stageIndex, groupIndex, locked) {
        const presetOptions = [['', '主 API'], ...this.state.apiPresets.map(item => [item.id, item.name])];
        return `<div class="nee-compact-request" data-drop-group="${stageIndex}:${groupIndex}"><div class="nee-request-toolbar"><span>请求 ${groupIndex + 1}${(group.blocks || []).length > 1 ? ' · 合并' : ''}</span><select data-group-preset="${stageIndex}:${groupIndex}" title="阶段 API">${selectOptions(presetOptions, group.presetId || '')}</select>${!locked ? `<button type="button" data-action="delete-group" data-stage="${stageIndex}" data-group="${groupIndex}">${icon('trash', 13)}</button>` : ''}</div><div class="nee-module-list">${(group.blocks || []).map(id => this.renderPipelineBlock(id, locked)).join('') || '<span class="nee-drop-hint">拖入模块</span>'}</div></div>`;
    }

    renderPipelineBlock(id, locked) {
        const prompt = this.promptInfo(id); const selected = this.state.detail?.kind === 'prompt' && this.state.detail.id === id;
        const required = locked || ['event_content', 'relation_references'].includes(id);
        return `<button type="button" class="nee-prompt-block ${selected ? 'is-active' : ''}" data-action="select-prompt" data-id="${id}" draggable="${locked ? 'false' : 'true'}" data-block="${id}">${locked ? icon('check', 14) : icon('grip', 14)}<span>${escapeHtml(prompt.label)}</span>${!required ? `<i data-action="delete-module" data-id="${id}" title="移除">×</i>` : ''}</button>`;
    }

    renderEntityDirectory() {
        const count = this.state.selectedIds.size;
        const rows = this.state.entities.map(item => `<article class="nee-entity-row ${this.state.detail?.kind === 'entity' && this.state.detail.record?.id === item.id ? 'is-active' : ''}" data-action="open-entity" data-id="${item.id}"><label class="nee-row-check"><input type="checkbox" data-select-entity="${item.id}" ${this.state.selectedIds.has(item.id) ? 'checked' : ''}><span></span></label><button type="button"><div><strong>${escapeHtml(item.name)}</strong></div><p>${escapeHtml(item.description)}</p><small>更新于 ${escapeHtml(new Date(item.updatedAt).toLocaleString())}</small></button></article>`).join('');
        return `<div class="nee-directory"><div class="nee-directory-toolbar"><label class="nee-search">${icon('search', 16)}<input type="search" data-entity-search value="${escapeHtml(this.state.entityQuery)}" placeholder="搜索名称或 Description"></label><select data-entity-sort><option value="updated" ${this.state.entitySort === 'updated' ? 'selected' : ''}>按提取时间</option><option value="recall" ${this.state.entitySort === 'recall' ? 'selected' : ''}>按最近召回</option><option value="name" ${this.state.entitySort === 'name' ? 'selected' : ''}>按名称</option></select><button type="button" class="nee-icon-button" data-action="toggle-sort">${this.state.entityDirection === 'asc' ? '↑' : '↓'}</button><button type="button" class="nee-button primary" data-action="new-entity">${icon('plus', 15)}新建</button></div>${count ? `<div class="nee-selection-bar"><span>已选 ${count} 项</span><button type="button" data-action="merge-selected">${icon('merge', 14)}合并</button><button type="button" data-action="delete-selected">${icon('trash', 14)}删除</button></div>` : ''}<div class="nee-entity-list">${rows || emptyState('没有实体', '当前聊天还没有这一类型的正式实体。')}</div></div>`;
    }

    renderDetailPane() {
        if (!this.state.detail) return '';
        if (this.state.detail.kind === 'prompt') return this.renderPromptDetail();
        const record = this.state.detail.record; const entity = record.entity || {}; const name = entity.components?.identity?.data?.primary_name || record.name || '新实体';
        return `<aside class="nee-detail-pane"><div class="nee-detail-header"><div><span>${escapeHtml(this.typeInfo(entity.type).label)}</span><h2>${escapeHtml(name)}</h2></div><button type="button" class="nee-icon-button" data-action="close-detail">${icon('close')}</button></div><div class="nee-detail-body"><form data-form="entity-editor" class="nee-entity-form"><input type="hidden" name="expectedRevision" value="${record.revision || ''}">${field({ label: 'Description', name: 'description', type: 'textarea', rows: 4, value: entity.description || '', required: true, attrs: 'data-json-path="/description"' })}<div class="nee-component-list">${this.renderEntityComponents(entity)}</div><div class="nee-form-error" data-form-error></div><div class="nee-sticky-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}校验并保存</button>${record.id ? `<button type="button" class="nee-button danger ghost" data-action="delete-entity" data-id="${record.id}">${icon('trash', 15)}删除</button>` : ''}</div></form>${record.id ? this.renderRelations(record) : ''}</div></aside>`;
    }

    renderPromptDetail() {
        const prompt = this.promptInfo(this.state.detail.id); const override = this.state.settings.promptOverrides?.[prompt.id];
        return `<aside class="nee-detail-pane"><div class="nee-detail-header"><div><span>提示词</span><h2>${escapeHtml(prompt.label)}</h2></div><button type="button" class="nee-icon-button" data-action="close-detail">${icon('close')}</button></div><div class="nee-detail-body"><form data-form="prompt-editor" class="nee-form-grid single"><input type="hidden" name="promptId" value="${prompt.id}"><p class="nee-muted">默认内容直接来自正式核心常量。保存后只记录宿主覆盖；清空覆盖可恢复正式默认。</p>${field({ label: 'System Prompt', name: 'systemPrompt', type: 'textarea', rows: 22, value: override ?? prompt.systemPrompt, required: true })}<div class="nee-form-actions"><button class="nee-button primary" type="submit">${icon('save', 15)}保存覆盖</button>${override != null ? '<button class="nee-button secondary" type="button" data-action="reset-prompt">恢复正式默认</button>' : ''}</div></form></div></aside>`;
    }

    applicableComponents(type) { return (this.state.metadata.components || []).filter(item => item.allowedEntityTypes.includes(type) && item.editable && !item.derived); }

    renderEntityComponents(entity) {
        return this.applicableComponents(entity.type).map(component => {
            const current = entity.components?.[component.name]; const enabled = Boolean(current); const schema = component.dataSchema || {};
            return `<details class="nee-component" data-component="${component.name}" data-enabled="${enabled}" ${enabled ? 'open' : ''}><summary><span><b>${escapeHtml(component.title)}</b><small>${escapeHtml(component.description || component.name)}</small></span><i>${enabled ? '已启用' : '未添加'}</i></summary>${enabled ? `<div class="nee-component-fields">${this.renderSchema(schema, current?.data || {}, `/components/${component.name}/data`, component.name)}</div>` : `<button type="button" class="nee-button secondary" data-action="enable-component" data-id="${component.name}">${icon('plus', 14)}添加此资料</button>`}</details>`;
        }).join('');
    }

    renderSchema(schema, value, pointer, label) {
        const resolved = schema?.oneOf?.[0] || schema?.anyOf?.[0] || schema || {}; const type = resolved.type;
        if (resolved.enum) return field({ label, name: pointer, value: value ?? '', options: resolved.enum.map(item => [item, item]), help: resolved.description || '', attrs: `data-json-path="${pointer}"` });
        if (type === 'object' || resolved.properties || resolved.additionalProperties) {
            const properties = resolved.properties || {};
            if (!Object.keys(properties).length && resolved.additionalProperties) return field({ label: '语义字段', name: pointer, type: 'textarea', rows: 8, value: Object.keys(value || {}).length ? JSON.stringify(value, null, 2) : '{}', help: `${label} 使用正式开放语义 Schema；按 JSON 键值填写明确事实。`, attrs: `data-json-path="${pointer}" data-value-kind="json"` });
            return `<div class="nee-schema-group">${Object.entries(properties).map(([key, child]) => this.renderSchema(child, value?.[key], `${pointer}/${key}`, child.title || key)).join('')}</div>`;
        }
        if (type === 'array') {
            const complex = resolved.items?.type === 'object' || resolved.items?.properties;
            return field({ label, name: pointer, type: 'textarea', rows: complex ? 7 : 3, value: value == null ? '' : complex ? JSON.stringify(value, null, 2) : value.join('\n'), help: resolved.description || (complex ? '按 JSON 数组填写。' : '每行一项。'), attrs: `data-json-path="${pointer}" data-value-kind="${complex ? 'json' : 'array'}"` });
        }
        if (type === 'boolean') return `<label class="nee-check-card"><input type="checkbox" data-json-path="${pointer}" data-value-kind="boolean" ${value ? 'checked' : ''}><span><b>${escapeHtml(label)}</b><small>${escapeHtml(resolved.description || '')}</small></span></label>`;
        const numeric = ['integer', 'number'].includes(type); const long = Number(resolved.maxLength || 0) > 180 || (resolved.description || '').length > 80;
        return field({ label, name: pointer, type: long ? 'textarea' : numeric ? 'number' : 'text', rows: 4, value: value ?? '', required: false, help: resolved.description || '', attrs: `data-json-path="${pointer}" data-value-kind="${numeric ? type : 'string'}"` });
    }

    renderRelations(record) {
        const rows = this.state.relations.map(relation => { const outgoing = relation.sourceId === record.id; const otherId = outgoing ? relation.targetId : relation.sourceId; const otherName = outgoing ? relation.targetName : relation.sourceName; const otherType = outgoing ? relation.targetType : relation.sourceType; return `<div class="nee-relation-row"><button type="button" data-action="jump-relation" data-id="${otherId}" data-type="${otherType}"><span>${outgoing ? '关联到' : '被关联'}</span><b>${escapeHtml(otherName)}</b><small>${escapeHtml(relation.relationType)}</small></button><button type="button" data-action="delete-relation" data-id="${relation.id}">${icon('trash', 13)}</button></div>`; }).join('') || '<p class="nee-muted">暂无显式关系。</p>';
        const targets = this.state.relationTargets.filter(item => item.id !== record.id).map(item => [item.id, `${item.name} · ${this.typeInfo(item.type).label}`]);
        return `<section class="nee-relations"><div class="nee-section-heading"><div><span>关系</span><h3>关联及被关联</h3></div></div>${rows}<form data-form="relation" class="nee-form-grid single"><input type="hidden" name="sourceId" value="${record.id}">${field({ label: '目标实体', name: 'targetId', options: [['', '选择实体'], ...targets], required: true })}${field({ label: '关系类型', name: 'relationType', value: '', required: true })}${field({ label: '说明', name: 'description', value: '' })}<button class="nee-button secondary" type="submit">添加关系</button></form></section>`;
    }

    renderSettings() { return `<div class="nee-page-tabs">${tabs(this.state.tab, [{ id: 'guide', label: '使用说明' }, { id: 'reset', label: '重置' }])}</div><div class="nee-page-content">${this.state.tab === 'reset' ? this.renderReset() : this.renderGuide()}</div>`; }
    renderGuide() { return `<section class="nee-panel nee-guide"><h2>使用流程</h2><ol><li>在 API 设置中保存主 API；渠道会随地址自动识别。</li><li>在提取设置中编排正式五阶段，并可给每个请求选择 API。</li><li>批量提取会真正启动核心流水线；只有脚本校验通过才写入当前聊天实体库。</li><li>实体字段来自正式 Component Schema；保存前再次调用正式 EntityValidator。</li></ol><h3>同源边界</h3><p>这个目录本身就是 NexusEntityEngine 插件。酒馆界面只调用本仓库 Registry、Schema、提示词与验证器，不保存第二套业务规则。</p></section>`; }
    renderReset() { const paths = this.state.databasePaths || {}; return `<div class="nee-stack"><section class="nee-panel"><h2>数据库位置</h2><dl class="nee-path-list"><div><dt>全局配置</dt><dd>${escapeHtml(paths.configDatabase || '')}</dd></div><div><dt>当前聊天实体库</dt><dd>${escapeHtml(paths.entityDatabase || '请先绑定聊天')}</dd></div><div><dt>当前聊天运行库</dt><dd>${escapeHtml(paths.runtimeDatabase || '请先绑定聊天')}</dd></div></dl></section><section class="nee-panel nee-danger-zone"><h2>重置</h2><button type="button" class="nee-button secondary" data-action="reset-settings">重置插件设置</button><button type="button" class="nee-button danger" data-action="reset-chat">删除当前聊天数据库内容</button></section></div>`; }

    async onClick(event) {
        const nav = event.target.closest('[data-nav]'); if (nav) return this.navigate(nav.dataset.nav);
        const tab = event.target.closest('[data-tab]'); if (tab) { this.state.tab = tab.dataset.tab; if (this.state.tab === 'batch') this.scheduleJobPoll(); this.render(); return; }
        const target = event.target.closest('[data-action]'); if (!target) return; const action = target.dataset.action;
        try {
            if (action === 'retry-service') return this.initialize();
            if (action === 'refresh-dashboard') { await this.bindCurrentChat(); await this.loadActiveView(); this.render(); }
            else if (action === 'select-preset') { this.state.selectedPresetId = target.dataset.id; this.render(); }
            else if (action === 'new-preset') { this.state.selectedPresetId = ''; this.render(); }
            else if (action === 'delete-preset') await this.deletePreset(target.dataset.id);
            else if (action === 'select-prompt') { this.state.detail = { kind: 'prompt', id: target.dataset.id }; this.render(); }
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

    async navigate(target) { this.state.active = target; this.state.tab = TAB_DEFAULTS[target] || (target.startsWith('entity:') ? '' : this.state.tab); this.state.detail = null; this.state.selectedIds.clear(); await this.loadActiveView(); this.render(); }

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
            if (provider === 'anthropic') form.elements.transport.value = 'messages'; else if (provider === 'gemini') form.elements.transport.value = 'gemini'; else if (['messages', 'gemini'].includes(form.elements.transport.value)) form.elements.transport.value = 'chat_completions';
            this.updateReasoningPreview(form);
        }
    }

    async onChange(event) {
        if (event.target.matches('[data-entity-sort]')) { this.state.entitySort = event.target.value; await this.loadEntities(); this.render(); }
        else if (event.target.matches('[data-select-entity]')) { const id = event.target.dataset.selectEntity; event.target.checked ? this.state.selectedIds.add(id) : this.state.selectedIds.delete(id); this.render(); }
        else if (event.target.matches('[data-group-preset]')) { const [stage, group] = event.target.dataset.groupPreset.split(':').map(Number); this.mutateWorkflow(workflow => { workflow[stage].groups[group].presetId = event.target.value; }, false); }
        else if (event.target.name === 'reasoningEffort') this.updateReasoningPreview(event.target.form);
    }

    updateReasoningPreview(form) { const preview = form?.querySelector('[data-reasoning-preview]'); if (preview) preview.textContent = JSON.stringify(reasoningPayload(form.elements.provider.value, form.elements.reasoningEffort.value), null, 2); }

    onDragStart(event) { const block = event.target.closest('[data-block]'); if (!block || block.getAttribute('draggable') === 'false') return; this.draggedBlock = block.dataset.block; event.dataTransfer.effectAllowed = 'move'; }
    onDragOver(event) { if (event.target.closest('[data-drop-group]')) event.preventDefault(); }
    onDrop(event) { const group = event.target.closest('[data-drop-group]'); if (!group || !this.draggedBlock) return; event.preventDefault(); const [stageIndex, groupIndex] = group.dataset.dropGroup.split(':').map(Number); const id = this.draggedBlock; this.mutateWorkflow(workflow => { for (const stage of workflow) for (const request of stage.groups) request.blocks = request.blocks.filter(item => item !== id); workflow[stageIndex].groups[groupIndex].blocks.push(id); }); this.draggedBlock = ''; }

    mutateWorkflow(callback, rerender = true) { const workflow = this.normalizedWorkflow(); callback(workflow); this.state.settings.extraction = { ...(this.state.settings.extraction || {}), workflow }; if (rerender) this.render(); }
    requiredBlocks(blocks = []) { return blocks.filter(id => ['narrative_map', 'event_content', 'relation_references'].includes(id)); }
    deleteStage(index) { const workflow = this.normalizedWorkflow(); if (this.requiredBlocks(workflow[index]?.groups.flatMap(group => group.blocks) || []).length) throw new Error('Narrative Map、Event Content 和 Relation Reference 是正式提交闭环的必需模块，不能随步骤删除。'); this.mutateWorkflow(value => value.splice(index, 1)); }
    deleteGroup(stage, group) { const workflow = this.normalizedWorkflow(); if (this.requiredBlocks(workflow[stage]?.groups[group]?.blocks || []).length) throw new Error('这个请求含有正式提交闭环的必需模块，不能整组删除。'); this.mutateWorkflow(value => value[stage].groups.splice(group, 1)); }
    removeModule(id) { if (this.requiredBlocks([id]).length) throw new Error('该模块属于正式提交闭环，不能移除。'); this.mutateWorkflow(workflow => { for (const stage of workflow) for (const group of stage.groups) group.blocks = group.blocks.filter(item => item !== id); }); if (this.state.detail?.id === id) this.state.detail = null; }
    restoreModule(id) { this.mutateWorkflow(workflow => { let stage = workflow.find(item => !item.locked); if (!stage) { stage = { id: `stage-${Date.now()}`, locked: false, groups: [] }; workflow.push(stage); } stage.groups.push({ id: `request-${Date.now()}`, blocks: [id], presetId: '' }); }); }

    async withBusy(callback, rerender = true) { if (this.state.busy) return; this.state.busy = true; try { return await callback(); } finally { this.state.busy = false; if (rerender) this.render(); } }
    toast(message, type = 'success') { const region = this.root.querySelector('.nee-toast-region'); if (!region) return; const item = document.createElement('div'); item.className = `nee-toast ${type}`; item.textContent = message; region.append(item); setTimeout(() => item.remove(), 3500); }

    async saveTriggerSettings(form) { const data = new FormData(form); this.state.settings.extraction = { ...(this.state.settings.extraction || {}), floorInterval: Math.max(1, Number(data.get('floorInterval')) || 10), retainTail: Math.max(0, Number(data.get('retainTail')) || 0) }; this.state.settings = await api.saveSettings(this.state.settings); await this.loadActiveView(); this.render(); this.toast('触发设置已保存。'); }

    selectedPipelinePreset() { const first = this.normalizedWorkflow().flatMap(stage => stage.groups).find(group => group.blocks.includes('narrative_map')); return first?.presetId || ''; }
    pipelineConnections() { const result = {}; for (const group of this.normalizedWorkflow().flatMap(stage => stage.groups)) for (const block of group.blocks) result[block] = group.presetId || ''; return result; }
    async createBatchJob(form) { const data = new FormData(form); const snapshot = this.hostSnapshot(); const job = await api.createBatchJob({ chatId: snapshot.chatId, startFloor: Number(data.get('startFloor')), endFloor: Number(data.get('endFloor')), entityTypes: data.getAll('entityTypes'), messages: this.hostMessages(), presetId: this.selectedPipelinePreset(), connections: this.pipelineConnections() }); this.state.batchJobs = [job, ...this.state.batchJobs]; this.render(); this.scheduleJobPoll(); this.toast('批量提取已开始。'); }
    async retryJob(id) { const job = await api.retryBatchJob(this.hostSnapshot().chatId, id, { messages: this.hostMessages(), presetId: this.selectedPipelinePreset(), connections: this.pipelineConnections() }); this.state.batchJobs = [job, ...this.state.batchJobs]; this.render(); this.scheduleJobPoll(); }
    async cancelJob(id) { await api.cancelBatchJob(this.hostSnapshot().chatId, id); this.state.batchJobs = await api.listBatchJobs(this.hostSnapshot().chatId); this.render(); }

    async saveApiPreset(form) { const data = new FormData(form); await api.saveApiPreset({ id: data.get('id'), name: data.get('name'), baseUrl: data.get('baseUrl'), provider: detectProvider(data.get('baseUrl')), model: data.get('model'), transport: data.get('transport'), apiKey: data.get('apiKey'), reasoningEffort: data.get('reasoningEffort'), isPrimary: data.get('isPrimary') === 'on' }); this.state.apiPresets = await api.listApiPresets(); this.state.selectedPresetId = this.state.apiPresets.find(item => item.name === data.get('name'))?.id || ''; this.render(); this.toast('API 预设已保存。'); }
    async deletePreset(id) { if (!globalThis.confirm('删除这个 API 预设？')) return; await api.deleteApiPreset(id); this.state.apiPresets = await api.listApiPresets(); this.state.selectedPresetId = ''; this.render(); }

    async savePipeline() { const parallel = Number(this.root.querySelector('[name="parallelStaggerSeconds"]')?.value); const serial = Number(this.root.querySelector('[name="serialRpm"]')?.value); this.state.settings.extraction = { ...this.state.settings.extraction, parallelStaggerSeconds: Math.max(0, parallel || 0), serialRpm: Math.max(1, serial || 30), workflow: this.normalizedWorkflow() }; this.state.settings = await api.saveSettings(this.state.settings); this.toast('提取编排与模块 API 已保存。'); }
    async savePrompt(form) { const data = new FormData(form); this.state.settings.promptOverrides = { ...(this.state.settings.promptOverrides || {}), [data.get('promptId')]: String(data.get('systemPrompt') || '') }; this.state.settings = await api.saveSettings(this.state.settings); this.render(); this.toast('提示词覆盖已保存。'); }
    async resetPrompt() { const id = this.state.detail?.id; if (!id) return; const overrides = { ...(this.state.settings.promptOverrides || {}) }; delete overrides[id]; this.state.settings.promptOverrides = overrides; this.state.settings = await api.saveSettings(this.state.settings); this.render(); this.toast('已恢复正式核心默认提示词。'); }

    async newEntity() { const entity = await api.prepareEntity({ type: this.state.entityType, name: '' }); this.state.detail = { kind: 'entity', record: { id: '', type: entity.type, name: '', description: '', entity, revision: null } }; this.state.relations = []; this.state.relationTargets = []; this.render(); }
    async openEntity(id) { const chatId = this.hostSnapshot().chatId; const [record, relations, targets] = await Promise.all([api.getEntity(chatId, id), api.listRelations(chatId, id), api.listEntities({ chatId, limit: 500, sort: 'name', direction: 'asc' })]); this.state.detail = { kind: 'entity', record }; this.state.relations = relations; this.state.relationTargets = targets; this.render(); }
    enableComponent(name) { const record = this.state.detail?.record; if (!record) return; const component = this.state.metadata.components.find(item => item.name === name); record.entity.components[name] = { schema_version: component.schemaVersion, data: {} }; this.render(); }

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
