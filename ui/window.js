import { EXTENSION_ROOT, icon } from './constants.js';

const BUTTON_ID = 'nee-floating-entry';
const WINDOW_ID = 'nee-workbench-window';

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
                    <div><strong>NexusEntityEngine</strong><span>World data workbench</span></div>
                </div>
                <div class="nee-window-actions">
                    <button type="button" data-window-action="minimize" aria-label="最小化" title="最小化">${icon('minimize', 16)}</button>
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
        windowElement.classList.remove('is-minimized');
        button.classList.add('is-open');
        onOpen?.(windowElement.querySelector('[data-workbench-root]'));
    };
    const close = () => {
        windowElement.classList.remove('is-open', 'is-minimized');
        button.classList.remove('is-open');
        onClose?.();
    };
    const toggleMinimize = () => windowElement.classList.toggle('is-minimized');
    const toggleFullscreen = () => {
        windowElement.classList.toggle('is-fullscreen');
        const target = windowElement.querySelector('[data-window-action="fullscreen"]');
        if (target) target.innerHTML = icon(windowElement.classList.contains('is-fullscreen') ? 'restore' : 'maximize', 15);
    };

    button.addEventListener('click', () => windowElement.classList.contains('is-open') ? close() : open());
    windowElement.querySelector('[data-window-action="close"]')?.addEventListener('click', close);
    windowElement.querySelector('[data-window-action="minimize"]')?.addEventListener('click', toggleMinimize);
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
        if (windowElement.classList.contains('is-fullscreen') || windowElement.classList.contains('is-minimized')) return;
        const rectangle = windowElement.getBoundingClientRect();
        resizeState = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, width: rectangle.width, height: rectangle.height };
        resizeHandle.setPointerCapture(event.pointerId);
        windowElement.classList.add('is-moving');
    });
    resizeHandle?.addEventListener('pointermove', event => {
        if (!resizeState || resizeState.pointerId !== event.pointerId) return;
        const width = clamp(resizeState.width + event.clientX - resizeState.x, 760, window.innerWidth - windowElement.getBoundingClientRect().left);
        const height = clamp(resizeState.height + event.clientY - resizeState.y, 520, window.innerHeight - windowElement.getBoundingClientRect().top);
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
