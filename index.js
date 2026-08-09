import { Workbench } from './ui/workbench.js';
import { createWindowShell } from './ui/window.js';

let workbench;
let shell;

export async function init() {
    if (shell) return;
    shell = createWindowShell({
        onOpen: async root => {
            if (!workbench) {
                workbench = new Workbench(root);
                await workbench.initialize();
            } else {
                await workbench.refreshHostState();
            }
        },
    });
    console.info('[NexusEntityEngine] Frontend extension loaded.');
}
