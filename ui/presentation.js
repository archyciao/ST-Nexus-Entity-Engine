// Read-only projections for the workbench. World data remains owned by the core.
export const PROMPT_LABELS = Object.freeze({
    narrative_map: '识别事件与对象', event_content: '整理事件内容',
    entity_create: '建立新资料', entity_update: '更新已有资料', relation_references: '连接关系与地点',
});

export const TYPE_LABELS = Object.freeze({ event: '事件', memory: '记忆', character_relation: '人物关系', character: '人物', location: '地点', item: '物品', organization: '组织', skill: '能力', concept: '概念' });

export function componentLabel(component) {
    return String(component.title || component.name).replace(/Character Relation/g, '人物关系').replace(/Event|Memory|Character|Location|Item|Organization|Skill|Concept|Identity/g, value => ({ Event: '事件', Memory: '记忆', Character: '人物', Location: '地点', Item: '物品', Organization: '组织', Skill: '能力', Concept: '概念', Identity: '身份' })[value]).replace(/组件$/, '').trim();
}

export const REFERENCE_LABELS = Object.freeze({ event_content: '关键细节中的人物', event_location_reference: '实际发生地点', event_related_entity_reference: '内容相关对象', source_event_reference: '记忆来源事件', memory_owner_reference: '记忆属于', relation_endpoint_reference: '关系中的人物', current_location_reference: '当前所在地点', parent_location_reference: '上级地点', parent_organization_reference: '上级组织', current_placement_reference: '当前放置于', memory_reference: '重点记忆' });

export function referenceValues(value, pointer) {
    const segments = String(pointer || '').split('/').slice(1).map(part => part.replace(/~1/g, '/').replace(/~0/g, '~'));
    let values = [value];
    for (const segment of segments) {
        values = values.flatMap(item => segment === '*'
            ? (Array.isArray(item) ? item : [])
            : item && typeof item === 'object' && Object.hasOwn(item, segment) ? [item[segment]] : []);
    }
    return values.filter(item => item && typeof item.id === 'string' && typeof item.type === 'string');
}

export function formalConnections(record, records, components) {
    const results = new Map();
    const byId = new Map(records.map(item => [item.id, item]));
    byId.set(record.id, record);
    for (const source of byId.values()) {
        for (const component of components) {
            if (component.derived || component.category === 'index') continue;
            const envelope = source.entity?.components?.[component.name];
            if (!envelope) continue;
            for (const reference of component.references || []) {
                for (const target of referenceValues(envelope, reference.path)) {
                    if (source.id !== record.id && target.id !== record.id) continue;
                    if (source.id === target.id) continue;
                    const outgoing = source.id === record.id;
                    const other = outgoing ? byId.get(target.id) : source;
                    const id = outgoing ? target.id : source.id;
                    const key = `${source.id}:${component.name}:${target.id}`;
                    results.set(key, { id, type: outgoing ? target.type : source.type,
                        name: other?.name || id, outgoing, label: REFERENCE_LABELS[component.name] || componentLabel(component),
                        resolved: Boolean(other), component: component.name });
                }
            }
        }
    }
    return [...results.values()];
}

export const FIELD_LABELS = Object.freeze({
    primary_name: '主要名称', aliases: '别名', summary: '摘要', story_summary: '故事经过',
    key_details: '关键细节', content: '内容', kind: '类别', status: '状态', description: '说明',
    background: '背景', species: '种族', personality: '性格', motivations: '动机', preferences: '偏好',
    physical_condition: '身体状况', mental_condition: '精神状态', current_emotions: '当前情绪',
    start_time: '开始时间', end_time: '结束时间', expression: '时间表述', precision: '精度',
    directional_states: '双方态度', aspects: '关系事实', tags: '标签', roles: '身份', fidelity: '记录方式', actor_ref: '相关人物',
    perspective: '观察角度', mode: '记录视角', participant_roles: '关系双方', character_ref: '人物',
    from_character_ref: '态度发出者', toward_character_ref: '态度指向者', disclosure: '知情范围',
    visibility: '公开程度', concealed_from_refs: '对其隐瞒', owner_ref: '记忆主人',
    source_event_ref: '来源事件', acquisition: '获知方式', acquisition_mode: '获知方式',
    evidence_refs: '来源依据', previous_value: '原有内容', value: '建议内容',
    proposed_field_name: '待归类字段', reason: '待确认原因', field_name: '资料字段',
});
