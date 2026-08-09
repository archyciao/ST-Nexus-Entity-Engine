import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { enginePaths } from './engine.mjs';

const running = new Map();

function endpointFor(preset) {
    const url = String(preset.baseUrl || '').replace(/\/$/, '');
    if (/\/chat\/completions$/i.test(url)) return url;
    if (/\/v1$/i.test(url)) return `${url}/chat/completions`;
    return `${url}/v1/chat/completions`;
}

function pythonExecutable() {
    const configured = String(process.env.NEXUS_ENTITY_ENGINE_PYTHON || '').trim();
    if (configured) return configured;
    const bundled = path.join(enginePaths.projectRoot, '.venv', 'Scripts', 'python.exe');
    return fs.existsSync(bundled) ? bundled : 'python';
}

function thinkingValue(preset) { return preset.reasoningEffort === 'none' ? 'off' : preset.reasoningEffort; }

function normalizedMessages(messages = [], startFloor, endFloor) {
    return messages.slice(startFloor, endFloor + 1).map((message, index) => ({
        name: String(message?.name || (message?.is_user ? 'user' : 'assistant')),
        is_user: Boolean(message?.is_user),
        mes: String(message?.mes || ''),
        send_date: message?.send_date || '',
        extra: { ...(message?.extra || {}), nexus_source_floor: startFloor + index },
    })).filter(message => message.mes.trim());
}

function entityClosure(entities, selectedTypes) {
    const byId = new Map(entities.filter(item => item?.id).map(item => [item.id, item]));
    const selected = new Set(entities.filter(item => selectedTypes.has(item.type)).map(item => item.id));
    const visit = value => {
        if (typeof value === 'string' && byId.has(value)) selected.add(value);
        else if (Array.isArray(value)) for (const item of value) visit(item);
        else if (value && typeof value === 'object') for (const item of Object.values(value)) visit(item);
    };
    let size = -1;
    while (size !== selected.size) {
        size = selected.size;
        for (const id of [...selected]) visit(byId.get(id));
    }
    return [...selected].map(id => byId.get(id)).filter(Boolean);
}

function appendLog(store, chatId, jobId, line) {
    const current = store.getBatchJob(chatId, jobId);
    if (!current) return;
    const logs = [...(current.logs || []), String(line).trim()].filter(Boolean).slice(-120);
    let progress = Number(current.progress || 1);
    if (line.includes('Narrative Map')) progress = Math.max(progress, line.includes('已返回') ? 24 : 8);
    if (line.includes('Event Content') || line.includes('Entity Create') || line.includes('Entity Update') || line.includes('Relation')) progress = Math.max(progress, 52);
    if (line.includes('固定脚本') || line.includes('校验')) progress = Math.max(progress, 78);
    store.updateBatchJob(chatId, jobId, { logs, progress: Math.min(progress, 92), currentStep: String(line).trim().slice(0, 200) });
}

export function startBatch({ store, chatId, job, messages, presetId = '', connectionPresetIds = {} }) {
    if (running.has(job.id)) throw new Error('该批量任务正在运行。');
    const primary = store.listApiPresets({ includeSecret: true }).find(item => item.isPrimary);
    const preset = presetId
        ? store.getApiPreset(presetId, { includeSecret: true })
        : primary;
    if (!preset) throw new Error('请先设置主 API，或在 Narrative Map 模块选择一个 API。');
    if (!preset.apiKey) throw new Error('所选 API 没有保存密钥。');
    if (!preset.model) throw new Error('所选 API 没有填写模型名称。');
    if (preset.transport !== 'chat_completions') throw new Error('当前正式批量提取器只支持 Chat Completions 传输协议。');

    const connectionPresets = {};
    for (const [task, fallback] of Object.entries({ narrative_map: preset, event_content: primary || preset, entity_create: primary || preset, entity_update: primary || preset, relation_references: primary || preset })) {
        const selectedId = connectionPresetIds[task] || (task === 'narrative_map' ? presetId : '');
        const selected = selectedId ? store.getApiPreset(selectedId, { includeSecret: true }) : fallback;
        if (!selected?.apiKey || !selected.model) throw new Error(`${task} 所选 API 缺少密钥或模型名称。`);
        if (selected.transport !== 'chat_completions') throw new Error(`${task} 当前只支持 Chat Completions 传输协议。`);
        connectionPresets[task] = selected;
    }

    const selected = normalizedMessages(messages, job.startFloor, job.endFloor);
    if (!selected.some(item => item.is_user) || !selected.some(item => !item.is_user)) throw new Error('所选楼层必须至少包含一轮用户消息和 AI 回复。');
    const paths = store.getPaths(chatId);
    const runRoot = path.join(paths.chatDirectory, 'runs', job.id);
    fs.mkdirSync(runRoot, { recursive: true });
    const inputPath = path.join(runRoot, 'chat.jsonl');
    const outputPath = path.join(runRoot, 'run.md');
    const resultPath = path.join(runRoot, 'result.json');
    const promptOverridePath = path.join(runRoot, 'prompt-overrides.json');
    const workflowPath = path.join(runRoot, 'workflow.json');
    fs.writeFileSync(inputPath, `${selected.map(item => JSON.stringify(item)).join('\n')}\n`, 'utf8');
    fs.writeFileSync(promptOverridePath, JSON.stringify(store.getSettings().promptOverrides || {}, null, 2), 'utf8');
    fs.writeFileSync(workflowPath, JSON.stringify(store.getSettings().extraction || {}, null, 2), 'utf8');
    const pairedRounds = Math.max(1, Math.floor(selected.length / 2));
    const batchSize = 4;
    const args = [
        path.join(enginePaths.projectRoot, 'tools', 'tavern_batch_runner.py'),
        '--result-json', resultPath,
        inputPath, '--endpoint', endpointFor(preset), '--model', preset.model,
        '--output', outputPath, '--prompt-overrides', promptOverridePath, '--workflow', workflowPath, '--batch-size', String(batchSize), '--batches', String(Math.ceil(pairedRounds / batchSize)),
        '--stage2-workers', '4', '--map-thinking', thinkingValue(connectionPresets.narrative_map),
        '--event-thinking', thinkingValue(connectionPresets.event_content),
        '--entity-thinking', thinkingValue(connectionPresets.entity_create),
        '--create-thinking', thinkingValue(connectionPresets.entity_create),
        '--update-thinking', thinkingValue(connectionPresets.entity_update),
        '--relation-thinking', thinkingValue(connectionPresets.relation_references),
    ];
    const connectionGroups = {
        map: connectionPresets.narrative_map,
        event: connectionPresets.event_content,
        create: connectionPresets.entity_create,
        update: connectionPresets.entity_update,
        relation: connectionPresets.relation_references,
    };
    const childEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
    for (const [prefix, selected] of Object.entries(connectionGroups)) {
        const envName = `NEXUS_BATCH_KEY_${prefix.toUpperCase()}`;
        childEnv[envName] = selected.apiKey;
        args.push(`--${prefix}-endpoint`, endpointFor(selected), `--${prefix}-model`, selected.model, `--${prefix}-api-key-env`, envName);
    }
    const startedAt = new Date().toISOString();
    store.updateBatchJob(chatId, job.id, { status: 'running', progress: 2, currentStep: '启动正式提取流水线', startedAt, error: '', inputPath, outputPath, logs: [] });
    const child = spawn(pythonExecutable(), args, {
        cwd: enginePaths.projectRoot, windowsHide: true,
        env: { ...childEnv, OPENCODE_API_KEY: preset.apiKey },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    running.set(job.id, child);
    let stderr = '';
    for (const stream of [child.stdout, child.stderr]) {
        let buffered = '';
        stream.setEncoding('utf8');
        stream.on('data', chunk => {
            if (stream === child.stderr) stderr += chunk;
            buffered += chunk;
            const lines = buffered.split(/\r?\n/); buffered = lines.pop() || '';
            for (const line of lines) if (line.trim()) appendLog(store, chatId, job.id, line);
        });
    }
    child.on('error', error => {
        running.delete(job.id);
        store.updateBatchJob(chatId, job.id, { status: 'failed', progress: 0, currentStep: '启动失败', error: error.message, completedAt: new Date().toISOString() });
    });
    child.on('close', code => {
        running.delete(job.id);
        if (store.getBatchJob(chatId, job.id)?.status === 'cancelled') return;
        let result = {};
        try { result = JSON.parse(fs.readFileSync(resultPath, 'utf8')); } catch { result = { status: 'failed', error: stderr.trim() || `提取进程退出码 ${code}` }; }
        if (code === 0 && result.status === 'completed' && result.validation?.valid !== false) {
            const entities = entityClosure(result.entities || [], new Set(job.entityTypes));
            const imported = store.importEntities(chatId, entities, job.endFloor);
            store.updateBatchJob(chatId, job.id, { status: 'completed', progress: 100, currentStep: `校验通过并写入 ${imported.saved} 个实体`, error: '', completedAt: new Date().toISOString() });
        } else {
            store.updateBatchJob(chatId, job.id, { status: 'failed', progress: 0, currentStep: '提取失败，可查看日志后重试', error: result.error || stderr.trim() || '正式提取或网络校验未通过。', completedAt: new Date().toISOString() });
        }
    });
    return store.getBatchJob(chatId, job.id);
}

export function cancelBatch(store, chatId, jobId) {
    const child = running.get(jobId);
    if (!child) throw new Error('该任务当前没有运行中的进程。');
    child.kill(); running.delete(jobId);
    return store.updateBatchJob(chatId, jobId, { status: 'cancelled', progress: 0, currentStep: '已取消', completedAt: new Date().toISOString() });
}

export function stopAll() { for (const child of running.values()) child.kill(); running.clear(); }
