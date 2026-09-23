export const EXTENSION_ROOT = '/scripts/extensions/third-party/NexusEntityEngine';

export const PROVIDERS = Object.freeze([
    { id: 'openai_compatible', label: 'OpenAI 兼容' },
    { id: 'openai', label: 'OpenAI' },
    { id: 'anthropic', label: 'Anthropic' },
    { id: 'gemini', label: 'Google Gemini' },
    { id: 'opencode_zen', label: 'OpenCode Zen' },
]);

export function detectProvider(url = '') {
    const value = String(url).toLowerCase();
    if (value.includes('opencode') || value.includes('zen')) return 'opencode_zen';
    if (value.includes('api.openai.com')) return 'openai';
    if (value.includes('anthropic.com')) return 'anthropic';
    if (value.includes('generativelanguage.googleapis.com')) return 'gemini';
    return 'openai_compatible';
}

export function providerLabel(id) { return PROVIDERS.find(item => item.id === id)?.label || id; }

export function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
}

export function icon(name, size = 18) {
    const paths = {
        dashboard: '<path d="M4 13a8 8 0 1 1 16 0"/><path d="m12 13 4-4"/><path d="M5 19h14"/>',
        api: '<path d="M8 3v4M16 3v4"/><path d="M6 7h12v4a6 6 0 0 1-12 0V7Z"/><path d="M12 17v4"/>',
        extraction: '<path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="12" r="2"/><circle cx="15" cy="18" r="2"/>',
        database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>',
        settings: '<circle cx="12" cy="12" r="3"/><path d="M19 15a2 2 0 0 0 .4 2l-2.4 2.4a2 2 0 0 0-2-.4 2 2 0 0 0-1 1.8h-4A2 2 0 0 0 9 19a2 2 0 0 0-2 .4L4.6 17a2 2 0 0 0 .4-2 2 2 0 0 0-1.8-1v-4A2 2 0 0 0 5 9a2 2 0 0 0-.4-2L7 4.6A2 2 0 0 0 9 5a2 2 0 0 0 1-1.8h4A2 2 0 0 0 15 5a2 2 0 0 0 2-.4L19.4 7A2 2 0 0 0 19 9a2 2 0 0 0 1.8 1v4A2 2 0 0 0 19 15Z"/>',
        search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>', plus: '<path d="M12 5v14M5 12h14"/>',
        save: '<path d="M5 3h12l3 3v15H4V3h1Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
        trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
        merge: '<path d="M6 4v4c0 3 2 4 6 4s6 1 6 4v4"/><path d="m15 17 3 3 3-3"/>',
        chevron: '<path d="m9 18 6-6-6-6"/>', close: '<path d="m6 6 12 12M18 6 6 18"/>',
        minimize: '<path d="M6 18h12"/>', maximize: '<rect x="5" y="5" width="14" height="14" rx="1"/>',
        restore: '<path d="M8 8h11v11H8z"/><path d="M5 16V5h11"/>',
        grip: '<circle cx="8" cy="7" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="7" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="17" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="17" r="1" fill="currentColor" stroke="none"/>',
        warning: '<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
        check: '<path d="m5 12 4 4L19 6"/>', refresh: '<path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6 8A7 7 0 0 1 18 7l2 5M4 12l2 5a7 7 0 0 0 12-1"/>',
        entity: '<circle cx="12" cy="5" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="18" r="3"/><path d="m10 7-3 8M14 7l3 8M9 18h6"/>',
        play: '<path d="m8 5 11 7-11 7Z"/>', stop: '<rect x="6" y="6" width="12" height="12"/>', retry: '<path d="M20 7v5h-5"/><path d="M20 12a8 8 0 1 0-2 5"/>',
    };
    return `<svg aria-hidden="true" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.entity}</svg>`;
}
