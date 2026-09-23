import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { formalConnections, referenceValues } from './presentation.js';

const fixture = JSON.parse(fs.readFileSync(new URL('../examples/networks/character_event_memory_relation.json', import.meta.url))).entities;
const registry = JSON.parse(fs.readFileSync(new URL('../registry/components.json', import.meta.url))).components;
const components = registry.map(item => ({ ...item, title: item.name, derived: item.authority === 'derived' }));
const records = fixture.map(entity => ({ id: entity.id, type: entity.type, name: entity.components.identity?.data.primary_name || entity.description, entity }));

test('formal links use authoritative registry paths in both directions, excluding derived indexes', () => {
    const character = records.find(item => item.type === 'character');
    const edges = formalConnections(character, records, components);
    assert.ok(edges.some(edge => !edge.outgoing && edge.type === 'event'));
    assert.ok(edges.some(edge => !edge.outgoing && edge.type === 'memory'));
    assert.ok(edges.some(edge => edge.outgoing && edge.type === 'memory'));
    assert.ok(edges.every(edge => !registry.find(component => component.name === edge.component)?.rebuildable || !components.find(component => component.name === edge.component)?.derived));
    assert.equal(edges.some(edge => edge.component === 'history_index'), false);
    assert.equal(edges.some(edge => edge.id === character.id), false);
});

test('same reference in multiple paths is deduplicated and unresolved targets retain their type', () => {
    const record = { id: 'source', entity: { components: { link: { data: { refs: [{ id: 'missing', type: 'location' }, { id: 'missing', type: 'location' }] } } } } };
    const edges = formalConnections(record, [], [{ name: 'link', references: [{ path: '/data/refs/*' }] }]);
    assert.equal(edges.length, 1);
    assert.equal(edges[0].type, 'location');
    assert.equal(edges[0].resolved, false);
});

test('reference paths respect escaped keys and ignore incomplete reference-shaped data', () => {
    const target = { id: 'character_1', type: 'character' };
    assert.deepEqual(referenceValues({ data: { 'a/b': [target, { id: 'not-a-reference' }] } }, '/data/a~1b/*'), [target]);
    assert.deepEqual(referenceValues({}, '/data/refs/*'), []);
});
