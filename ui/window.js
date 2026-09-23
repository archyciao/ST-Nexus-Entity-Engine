import { EXTENSION_ROOT, icon } from './constants.js';

const BUTTON_ID = 'nee-floating-entry';
const WINDOW_ID = 'nee-workbench-window';
const ENTRY_POSITION_KEY = 'nexus-entity-engine.entry-position.v1';

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

export function createWindowShell({ onOpen, onClose } = {}) {
    let button = document.getElementById(BUTTON_ID);
    let windowElement = document.getElementById(WINDOW_ID);

    if (!button) {
        button = document.createElement('button');
        button.id = BUTTON_ID;
        button.type = 'button';
        button.title = '打开 NexusEntityEngine';
        button.setAttribute('aria-label', '打开 NexusEntityEngine');
        button.innerHTML = `<img src="${EXTENSION_ROOT}/assets/nexus-node.svg" alt="">`;
        document.body.appendChild(button);
    }

    if (!windowElement) {
        windowElement = document.createElement('section');
        windowElement.id = WINDOW_ID;
        windowElement.className = 'nee-window';
        windowElement.setAttribute('aria-label', 'NexusEntityEngine 工作台');
        windowElement.innerHTML = `
            <header class="nee-titlebar" data-window-drag>
                <div class="nee-brand">
                    <img src="${EXTENSION_ROOT}/assets/nexus-node.svg" alt="">
                    <div><strong>NexusEntityEngine</strong><span>故事与世界资料</span></div>
                </div>
                <div class="nee-window-actions">
                    <button type="button" data-window-action="fullscreen" aria-label="全屏" title="全屏">${icon('maximize', 15)}</button>
                    <button type="button" data-window-action="close" aria-label="关闭" title="关闭">${icon('close', 16)}</button>
                </div>
            </header>
            <div class="nee-window-body" data-workbench-root></div>
            <div class="nee-resize-handle" data-window-resize aria-hidden="true"></div>
        `;
        document.body.appendChild(windowElement);
    }

    let dragState = null;
    let resizeState = null;

    const open = () => {
        windowElement.classList.add('is-open');
        button.classList.add('is-open');
        button.setAttribute('aria-expanded', 'true');
        onOpen?.(windowElement.querySelector('[data-workbench-root]'));
    };
    const close = () => {
        windowElement.classList.remove('is-open');
        button.classList.remove('is-open');
        button.setAttribute('aria-expanded', 'false');
        onClose?.();
    };
    const toggleFullscreen = () => {
        windowElement.classList.toggle('is-fullscreen');
        const target = windowElement.querySelector('[data-window-action="fullscreen"]');
        if (target) {
            const full = windowElement.classList.contains('is-fullscreen');
            target.innerHTML = icon(full ? 'restore' : 'maximize', 15);
            target.title = full ? '还原窗口' : '全屏';
            target.setAttribute('aria-label', target.title);
        }
    };

    let entryDrag = null;
    let suppressClick = false;
    const placeEntry = (left, top) => {
        const rect = button.getBoundingClientRect();
        button.style.left = `${clamp(left, 4, Math.max(4, innerWidth - rect.width - 4))}px`;
        button.style.top = `${clamp(top, 4, Math.max(4, innerHeight - rect.height - 4))}px`;
        button.style.right = 'auto'; button.style.bottom = 'auto';
    };
    const saveEntry = () => {
        const rect = button.getBoundingClientRect();
        try { localStorage.setItem(ENTRY_POSITION_KEY, JSON.stringify({ x: rect.left / innerWidth, y: rect.top / innerHeight })); } catch { /* Storage may be disabled. */ }
    };
    try {
        const saved = JSON.parse(localStorage.getItem(ENTRY_POSITION_KEY) || 'null');
        if (Number.isFinite(saved?.x) && Number.isFinite(saved?.y)) placeEntry(saved.x * innerWidth, saved.y * innerHeight);
    } catch { /* Ignore malformed or inaccessible preferences. */ }
    button.title = '打开或收起 NexusEntityEngine；拖动调整位置';
    button.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        const rect = button.getBoundingClientRect();
        suppressClick = false;
        entryDrag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false };
        button.setPointerCapture(event.pointerId);
    });
    button.addEventListener('pointermove', event => {
        if (!entryDrag || entryDrag.id !== event.pointerId) return;
        const dx = event.clientX - entryDrag.x, dy = event.clientY - entryDrag.y;
        if (Math.hypot(dx, dy) >= 4) entryDrag.moved = true;
        if (entryDrag.moved) { placeEntry(entryDrag.left + dx, entryDrag.top + dy); button.classList.add('is-dragging'); }
    });
    const endEntryDrag = event => {
        if (!entryDrag || entryDrag.id !== event.pointerId) return;
        suppressClick = entryDrag.moved;
        if (entryDrag.moved) saveEntry();
        entryDrag = null; button.classList.remove('is-dragging');
    };
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(event, endEntryDrag);
    button.addEventListener('click', event => {
        if (suppressClick && event.detail !== 0) { suppressClick = false; return; }
        windowElement.classList.contains('is-open') ? close() : open();
    });
    window.addEventListener('resize', () => { const rect = button.getBoundingClientRect(); placeEntry(rect.left, rect.top); });
    windowElement.querySelector('[data-window-action="close"]')?.addEventListener('click', close);
    windowElement.querySelector('[data-window-action="fullscreen"]')?.addEventListener('click', toggleFullscreen);

    windowElement.querySelector('[data-window-drag]')?.addEventListener('pointerdown', event => {
        if (event.target.closest('button') || windowElement.classList.contains('is-fullscreen')) return;
        const rectangle = windowElement.getBoundingClientRect();
        dragState = { pointerId: event.pointerId, offsetX: event.clientX - rectangle.left, offsetY: event.clientY - rectangle.top };
        event.currentTarget.setPointerCapture(event.pointerId);
        windowElement.classList.add('is-moving');
    });
    windowElement.querySelector('[data-window-drag]')?.addEventListener('pointermove', event => {
        if (!dragState || dragState.pointerId !== event.pointerId) return;
        const rectangle = windowElement.getBoundingClientRect();
        const left = clamp(event.clientX - dragState.offsetX, 0, Math.max(0, window.innerWidth - rectangle.width));
        const top = clamp(event.clientY - dragState.offsetY, 0, Math.max(0, window.innerHeight - 46));
        windowElement.style.left = `${left}px`;
        windowElement.style.top = `${top}px`;
        windowElement.style.transform = 'none';
    });
    const endDrag = event => {
        if (!dragState || dragState.pointerId !== event.pointerId) return;
        dragState = null;
        windowElement.classList.remove('is-moving');
    };
    windowElement.querySelector('[data-window-drag]')?.addEventListener('pointerup', endDrag);
    windowElement.querySelector('[data-window-drag]')?.addEventListener('pointercancel', endDrag);

    const resizeHandle = windowElement.querySelector('[data-window-resize]');
    resizeHandle?.addEventListener('pointerdown', event => {
        if (windowElement.classList.contains('is-fullscreen')) return;
        const rectangle = windowElement.getBoundingClientRect();
        resizeState = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, width: rectangle.width, height: rectangle.height };
        resizeHandle.setPointerCapture(event.pointerId);
        windowElement.classList.add('is-moving');
    });
    resizeHandle?.addEventListener('pointermove', event => {
        if (!resizeState || resizeState.pointerId !== event.pointerId) return;
        const availableWidth = window.innerWidth - windowElement.getBoundingClientRect().left;
        const width = clamp(resizeState.width + event.clientX - resizeState.x, Math.min(760, availableWidth), availableWidth);
        const availableHeight = window.innerHeight - windowElement.getBoundingClientRect().top;
        const height = clamp(resizeState.height + event.clientY - resizeState.y, Math.min(520, availableHeight), availableHeight);
        windowElement.style.width = `${width}px`;
        windowElement.style.height = `${height}px`;
    });
    const endResize = event => {
        if (!resizeState || resizeState.pointerId !== event.pointerId) return;
        resizeState = null;
        windowElement.classList.remove('is-moving');
    };
    resizeHandle?.addEventListener('pointerup', endResize);
    resizeHandle?.addEventListener('pointercancel', endResize);

    return { open, close, element: windowElement, root: windowElement.querySelector('[data-workbench-root]') };
}
