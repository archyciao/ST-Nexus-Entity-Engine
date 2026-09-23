import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFile } from 'node:child_process';
import { enginePaths, callEngine } from './engine.mjs';
import { sourceVersionInput } from '../ui/source-version.js';

const running = new Map();
const TASKS = ['narrative_map', 'event_content', 'entity_create', 'entity_update', 'relation_references'];
const LABELS = { narrative_map: '识别事件与对象', event_content: '整理事件', entity_create: '建立资料', entity_update: '更新资料', relation_references: '连接关系与地点' };
const digest = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

function pythonExecutable() {
    if (process.env.NEXUS_ENTITY_ENGINE_PYTHON) return process.env.NEXUS_ENTITY_ENGINE_PYTHON;
    for (const suffix of [['.venv', 'Scripts', 'python.exe'], ['.venv', 'bin', 'python']]) {
        const file = path.join(enginePaths.projectRoot, ...suffix);
        if (fs.existsSync(file)) return file;
    }
    return process.platform === 'win32' ? 'python' : 'python3';
}

export function normalizeSources(messages = []) {
    return messages.map((message, floor) => {
        const mes = String(message?.mes || '');
        const name = String(message?.name || (message?.is_user ? 'user' : 'assistant'));
        const sourceId = String(message?.extra?.nexus_source_id || message?.extra?.message_id || `floor-${floor}`);
        const version = digest(sourceVersionInput(message, floor));
        return { name, is_user: Boolean(message?.is_user), is_system: Boolean(message?.is_system), mes, send_date: message?.send_date || '', extra: { nexus_source_floor: floor, nexus_source_id: sourceId, nexus_source_version: version } };
    });
}

export function selectUnprocessed(all, coverage, start, end) {
    const covered = new Set();
    for (const receipt of coverage) for (const old of receipt.sources) {
        const current = all[old.floor]?.extra;
        if (!current || current.nexus_source_id !== old.id || current.nexus_source_version !== old.version) throw new Error('已处理消息被编辑、删除或切换了版本。请先复核旧资料来源；本次没有覆盖或重复导入。');
        covered.add(old.floor);
    }
    return all.slice(start, end + 1).filter(message => !covered.has(message.extra.nexus_source_floor));
}

function log(store, chatId, jobId, line) {
    const current = store.getBatchJob(chatId, jobId);
    if (!current || current.status !== 'running') return;
    store.updateBatchJob(chatId, jobId, { logs: [...current.logs, line].slice(-200), currentStep: line.slice(0, 200) });
}
function terminate(child) {
    if (process.platform === 'win32') execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, () => {});
    else { try { process.kill(-child.pid, 'SIGTERM'); } catch { child.kill(); } }
}

export function startBatch({ store, chatId, job, messages, presetId = '', connectionPresetIds = {}, spawnProcess = spawn }) {
    if ([...running.values()].some(value => value.chatId === chatId)) throw new Error('当前聊天已有提取任务，请完成或取消后再运行。');
    if (!Array.isArray(messages) || job.endFloor >= messages.length) throw new Error('提取范围超出当前聊天消息，请重新选择楼层。');
    const allSources = normalizeSources(messages);
    const selected = selectUnprocessed(allSources, store.sourceCoverage(chatId), job.startFloor, job.endFloor);
    store.setSourceVersions(chatId, allSources.map(m => m.extra.nexus_source_version));
    if (!selected.length) return store.updateBatchJob(chatId, job.id, { status: 'completed', progress: 100, currentStep: '该范围已保存，未重复调用模型或创建资料', completedAt: new Date().toISOString() });
    if (!selected.some(m => !m.is_user && !m.is_system && m.mes.trim())) throw new Error('当前范围没有可提取的 AI 正文；开场和连续 AI 消息可以独立提取。');
    const snapshot = store.captureExtractionSnapshot(chatId);
    const presets = store.listApiPresets({ includeSecret: true }), primary = presets.find(p => p.isPrimary);
    const mapPreset = presetId ? presets.find(p => p.id === presetId) : primary;
    if (!mapPreset) throw new Error('请先配置主模型或识别任务的模型。');
    const connections = {}, prepared = new Map();
    for (const task of TASKS) {
        const id = connectionPresetIds[task];
        const preset = id ? presets.find(p => p.id === id) : task === 'narrative_map' ? mapPreset : primary || mapPreset;
        if (!preset?.apiKey || !preset.model) throw new Error(`${LABELS[task]}缺少密钥或模型名称。`);
        if (!prepared.has(preset.id)) prepared.set(preset.id, callEngine('model_profile', { ...preset, apiKey: undefined }));
        connections[task] = { ...preset, ...prepared.get(preset.id) };
    }
    const sources = selected.map(m => ({ floor: m.extra.nexus_source_floor, id: m.extra.nexus_source_id, version: m.extra.nexus_source_version, role: m.is_system ? 'system' : m.is_user ? 'user' : 'assistant' }));
    const key = digest(['nexus-extraction-3', sources]);
    const paths = store.getPaths(chatId), runRoot = path.join(paths.chatDirectory, 'runs', job.id);
    fs.mkdirSync(runRoot, { recursive: true });
    const files = Object.fromEntries(['chat.jsonl', 'run.md', 'result.json', 'snapshot.json', 'profiles.json', 'prompt-overrides.json', 'workflow.json'].map(name => [name, path.join(runRoot, name)]));
    const settings = store.getSettings();
    fs.writeFileSync(files['chat.jsonl'], selected.map(m => JSON.stringify(m)).join('\n') + '\n');
    fs.writeFileSync(files['snapshot.json'], JSON.stringify(snapshot));
    fs.writeFileSync(files['prompt-overrides.json'], JSON.stringify(settings.promptOverrides || {}));
    fs.writeFileSync(files['workflow.json'], JSON.stringify(settings.extraction || {}));
    const prefixes = { map: 'narrative_map', event: 'event_content', create: 'entity_create', update: 'entity_update', relation: 'relation_references' };
    fs.writeFileSync(files['profiles.json'], JSON.stringify(Object.fromEntries(Object.entries(prefixes).map(([prefix, task]) => [prefix, { transport: connections[task].transport, options: connections[task].options }]))));
    const args = [path.join(enginePaths.projectRoot, 'tools', 'tavern_batch_runner.py'), '--result-json', files['result.json'], files['chat.jsonl'], '--endpoint', connections.narrative_map.endpoint, '--model', mapPreset.model, '--output', files['run.md'], '--snapshot', files['snapshot.json'], '--profiles', files['profiles.json'], '--cache-dir', path.join(paths.chatDirectory, 'task-cache'), '--prompt-overrides', files['prompt-overrides.json'], '--workflow', files['workflow.json'], '--batch-size', String(Math.max(1, Math.min(32, Number(settings.extraction?.batchMessages) || 8))), '--batch-chars', String(Math.max(2000, Math.min(200000, Number(settings.extraction?.batchChars) || 24000))), '--batches', String(selected.length), '--timeout', '1800'];
    const childEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', OPENCODE_API_KEY: mapPreset.apiKey };
    for (const [prefix, task] of Object.entries(prefixes)) {
        const preset = connections[task], name = `NEXUS_BATCH_KEY_${prefix.toUpperCase()}`;
        childEnv[name] = preset.apiKey;
        args.push(`--${prefix}-endpoint`, preset.endpoint, `--${prefix}-model`, preset.model, `--${prefix}-api-key-env`, name, `--${prefix}-thinking`, preset.reasoningEffort === 'none' ? 'off' : preset.reasoningEffort || 'default');
    }
    const secrets = [...new Set(Object.values(connections).map(p => p.apiKey))];
    const redact = value => secrets.reduce((text, secret) => text.split(secret).join('[已隐藏]'), String(value));
    store.updateBatchJob(chatId, job.id, { status: 'running', progress: 0, currentStep: '正在提取；有效结果会自动缓存', startedAt: new Date().toISOString(), error: '', inputPath: files['chat.jsonl'], outputPath: files['run.md'], logs: [] });
    const child = spawnProcess(pythonExecutable(), args, { cwd: enginePaths.projectRoot, windowsHide: true, detached: process.platform !== 'win32', env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] });
    running.set(job.id, { child, chatId });
    let stderr = '', failedToStart = false;
    for (const stream of [child.stdout, child.stderr]) {
        let buffered = '';
        stream.setEncoding('utf8');
        stream.on('data', chunk => {
            if (stream === child.stderr) stderr = (stderr + redact(chunk)).slice(-8000);
            buffered += chunk;
            const lines = buffered.split(/\r?\n/); buffered = lines.pop() || '';
            for (const line of lines) if (line.trim()) log(store, chatId, job.id, redact(line.trim()));
        });
    }
    child.on('error', error => {
        failedToStart = true; running.delete(job.id);
        store.updateBatchJob(chatId, job.id, { status: 'failed', currentStep: '执行器启动失败', error: redact(error.message), completedAt: new Date().toISOString() });
    });
    child.on('close', code => {
        running.delete(job.id);
        if (failedToStart || store.getBatchJob(chatId, job.id)?.status === 'cancelled') return;
        try {
            const result = JSON.parse(fs.readFileSync(files['result.json'], 'utf8'));
            const diagnostics = (result.diagnostics || []).map(item => `[第 ${item.batch} 批 · ${LABELS[item.task] || item.task}] ${item.message}`);
            if (code !== 0 || result.status !== 'completed' || result.validation?.valid !== true) throw new Error(result.error || diagnostics.join('\n') || stderr || '提取或保存校验未通过');
            store.assertSourceVersions(chatId, sources);
            const imported = store.commitExtraction(chatId, result.entities || [], { key, jobId: job.id, startFloor: sources[0].floor, endFloor: sources.at(-1).floor, sources, snapshot, state: result.state });
            store.updateBatchJob(chatId, job.id, { status: 'completed', progress: 100, currentStep: `已保存 ${imported.saved} 条变更${diagnostics.length ? `，有 ${diagnostics.length} 条提示待复核` : ''}`, error: '', logs: [...store.getBatchJob(chatId, job.id).logs, ...diagnostics.map(redact)].slice(-200), completedAt: new Date().toISOString() });
        } catch (error) {
            store.updateBatchJob(chatId, job.id, { status: 'failed', progress: 0, currentStep: '需要处理；有效结果已缓存', error: redact(error.message || stderr || `进程退出 ${code}`), completedAt: new Date().toISOString() });
        }
    });
    return store.getBatchJob(chatId, job.id);
}

export function cancelBatch(store, chatId, jobId) {
    const active = running.get(jobId);
    if (!active || active.chatId !== chatId) throw new Error('任务当前没有运行。');
    store.updateBatchJob(chatId, jobId, { status: 'cancelled', progress: 0, currentStep: '已取消；已缓存结果可在重试时复用', completedAt: new Date().toISOString() });
    terminate(active.child); running.delete(jobId);
    return store.getBatchJob(chatId, jobId);
}

export function stopAll() { for (const { child } of running.values()) terminate(child); running.clear(); }
