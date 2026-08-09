import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const SERVER_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(SERVER_ROOT, '..');
const DEFAULT_PYTHON = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
const BRIDGE_PATH = path.join(PROJECT_ROOT, 'tools', 'plugin_bridge.py');

function pythonExecutable() {
    const configured = String(process.env.NEXUS_ENTITY_ENGINE_PYTHON || '').trim();
    if (configured) return configured;
    return fs.existsSync(DEFAULT_PYTHON) ? DEFAULT_PYTHON : 'python';
}

export function callEngine(command, payload = {}) {
    const result = spawnSync(pythonExecutable(), [BRIDGE_PATH], {
        cwd: PROJECT_ROOT,
        input: JSON.stringify({ command, payload }),
        encoding: 'utf8',
        timeout: 30_000,
        windowsHide: true,
        maxBuffer: 16 * 1024 * 1024,
        env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    });
    if (result.error) throw result.error;
    let response;
    try {
        response = JSON.parse(result.stdout || '{}');
    } catch {
        throw new Error(`正式核心返回了无法解析的结果：${String(result.stderr || result.stdout).trim()}`);
    }
    if (result.status !== 0 || !response.ok) {
        throw new Error(response.error || String(result.stderr || '正式核心调用失败。').trim());
    }
    return response.result;
}

export const enginePaths = Object.freeze({ projectRoot: PROJECT_ROOT, bridgePath: BRIDGE_PATH });
