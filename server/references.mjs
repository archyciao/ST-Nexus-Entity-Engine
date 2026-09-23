// Only registry-declared authoritative references belong in this projection.
export function collectReferences(entity, components = []) {
    const result = [];
    for (const component of components) {
        if (component.derived || component.authority === 'derived' || component.category === 'index') continue;
        const envelope = entity.components?.[component.name];
        if (!envelope) continue;
        for (const reference of component.references || []) {
            let values = [envelope];
            const parts = reference.path.split('/').slice(1).map(p => p.replace(/~1/g, '/').replace(/~0/g, '~'));
            for (const part of parts) values = values.flatMap(value => part === '*' ? (Array.isArray(value) ? value : []) : value && typeof value === 'object' && Object.hasOwn(value, part) ? [value[part]] : []);
            for (const value of values) if (typeof value?.id === 'string' && typeof value?.type === 'string') result.push({ component: component.name, path: reference.path, targetId: value.id, targetType: value.type });
        }
    }
    return result;
}
