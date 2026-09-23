// Shared canonical input: browser and host agree on edits, swipes and identity.
export function sourceVersionInput(message = {}, floor = 0) {
    return [String(message.mes || ''), String(message.name || (message.is_user ? 'user' : 'assistant')), Boolean(message.is_user), Boolean(message.is_system), message.swipe_id ?? null, String(message.extra?.nexus_source_id || message.extra?.message_id || `floor-${floor}`)];
}

export async function sourceVersions(messages, digestProvider = globalThis.crypto?.subtle) {
    // LAN HTTP pages may lack WebCrypto; let the same local host hash the inputs.
    if (!digestProvider) return messages.map(sourceVersionInput);
    return Promise.all(messages.map(async (message, floor) => {
        const bytes = await digestProvider.digest('SHA-256', new TextEncoder().encode(JSON.stringify(sourceVersionInput(message, floor))));
        return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
    }));
}
