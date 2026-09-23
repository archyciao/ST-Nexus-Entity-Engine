"""Production pipeline regression with local fixtures only; no model is contacted."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from world_simulator_schema.extraction import pipeline, legacy, transport
from world_simulator_schema.extraction.cache import TaskCache
from world_simulator_schema.extraction.json_output import extract_object
from world_simulator_schema.extraction.protocols import build_request, endpoint_for, parse_response
from world_simulator_schema.extraction.sources import message_rounds, budget_batches
from world_simulator_schema import event_extraction_v2 as v2


class ProtocolTests(unittest.TestCase):
    def test_request_formats_and_medium_are_explicit(self):
        for protocol in ['chat_completions', 'responses', 'messages', 'gemini']:
            with self.subTest(protocol=protocol):
                endpoint = endpoint_for('https://example.invalid/proxy/v1', protocol, 'any-model')
                _, headers, body = build_request(endpoint, 'fixture-secret', 'any-model', 'system', 'user', transport=protocol, effort='medium')
                self.assertNotIn('thinking', body)
                self.assertNotIn('reasoning', body)
                self.assertNotIn('reasoning_effort', body)
                self.assertNotIn('temperature', body)
                self.assertTrue(endpoint.startswith('https://example.invalid/proxy/v1/'))
        _, _, chat = build_request('unused', 'x', 'new-model', '', '', options={'reasoningMode': 'effort', 'tokenParameter': 'max_completion_tokens'}, effort='medium')
        self.assertEqual(chat['reasoning_effort'], 'medium')
        self.assertIn('max_completion_tokens', chat)
        self.assertNotIn('max_tokens', chat)
        _, _, response = build_request('unused', 'x', 'new-model', '', '', transport='responses', options={'reasoningMode': 'effort'}, effort='medium')
        self.assertEqual(response['reasoning'], {'effort': 'medium'})
        _, _, message = build_request('unused', 'x', 'm', '', '', transport='messages', options={'reasoningMode': 'budget'}, effort='high')
        self.assertEqual(message['thinking']['budget_tokens'], 2048)
        _, _, gemini = build_request('unused', 'x', 'm', '', '', transport='gemini', options={'reasoningMode': 'budget'}, effort='off')
        self.assertEqual(gemini['generationConfig']['thinkingConfig'], {'thinkingBudget': 0})

    def test_endpoint_switch_and_invalid_budget_fail_locally(self):
        self.assertEqual(endpoint_for('https://example.invalid/v1beta/models/old:generateContent', 'gemini', 'new'), 'https://example.invalid/v1beta/models/new:generateContent')
        with self.assertRaises(ValueError): endpoint_for('https://example.invalid/v1/messages', 'responses', 'm')
        with self.assertRaises(ValueError): build_request('unused', 'x', 'm', '', '', transport='messages', options={'reasoningMode': 'budget', 'thinkingBudget': 8192}, effort='high')
        with self.assertRaises(ValueError): build_request('unused', 'x', 'm', '', '', transport='gemini', options={'reasoningMode': 'level'}, effort='max')

    def test_visible_answers_exclude_reasoning(self):
        fixtures = {
            'chat_completions': {'choices': [{'message': {'content': '{"ok":true}', 'reasoning_content': 'private'}, 'finish_reason': 'stop'}]},
            'responses': {'status': 'completed', 'output': [{'type': 'reasoning', 'content': [{'text': 'private'}]}, {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}]},
            'messages': {'content': [{'type': 'thinking', 'thinking': 'private'}, {'type': 'text', 'text': '{"ok":true}'}], 'stop_reason': 'end_turn'},
            'gemini': {'candidates': [{'content': {'parts': [{'text': 'private', 'thought': True}, {'text': '{"ok":true}'}]}, 'finishReason': 'STOP'}]},
        }
        for protocol, value in fixtures.items():
            self.assertEqual(parse_response(value, protocol)[0], '{"ok":true}')
            self.assertEqual(transport.parse_wire(json.dumps(value), protocol)[0], '{"ok":true}')

    def test_stream_completion_refusal_and_truncation(self):
        streams = {
            'chat_completions': [{'choices': [{'delta': {'reasoning_content': 'private', 'content': '{}'}, 'finish_reason': 'stop'}]}],
            'responses': [{'type': 'response.output_text.delta', 'delta': '{}'}, {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}],
            'messages': [{'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'private'}}, {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '{}'}}, {'type': 'message_stop'}],
            'gemini': [{'candidates': [{'content': {'parts': [{'text': '{}'}]}, 'finishReason': 'STOP'}]}],
        }
        for protocol, rows in streams.items():
            wire = '\n'.join('data: ' + json.dumps(row) for row in rows)
            self.assertEqual(transport.parse_wire(wire, protocol)[0], '{}')
        with self.assertRaises(ValueError): transport.parse_wire('data: {"choices":[{"delta":{"content":"{}"}}]}', 'chat_completions')
        with self.assertRaises(ValueError): transport.parse_wire('data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"content_filter"}]}', 'chat_completions')
        self.assertEqual(parse_response({'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'max_tokens'}, 'messages')[1]['finish_reason'], 'length')

    def test_offline_mode_rejects_network_before_launch(self):
        with patch.dict(os.environ, {'NEXUS_EXTRACTION_OFFLINE': '1'}), patch.object(transport.subprocess, 'run') as launch:
            with self.assertRaisesRegex(RuntimeError, '禁止'): transport.call_model('unused', 'x', 'm', '', '', 10, 1000, client_options={})
            launch.assert_not_called()

    def test_json_recovery_does_not_accept_truncated_inner_objects(self):
        self.assertEqual(extract_object('```json\n{"s":"a,}","items":[1,],}\n```'), {'s': 'a,}', 'items': [1]})
        for value in ['{"events":[{"description":"x"}', '{"a":1} {"b":2}', '[{"a":1}]', '{"text":"unterminated']:
            with self.subTest(value=value), self.assertRaises(ValueError): extract_object(value)

    def test_authentication_is_not_retried_and_length_never_commits(self):
        kwargs = dict(task='test', endpoint='unused', api_key='fixture-secret', model='m', system_prompt='', user_prompt='', timeout=10, max_tokens=1024, normalizer=lambda p:p, validator=lambda p:([], []), client_options={}, transport_attempt_limit=3)
        error = transport.ChatCompletionTransportError('HTTP 401', metadata={'retryable': False, 'error_code': 'authentication'})
        with patch.object(transport, 'call_model', side_effect=error) as request:
            result = legacy.run_model_task(**kwargs)
            self.assertFalse(result['ok']); self.assertEqual(request.call_count, 1)
        with patch.object(transport, 'call_model', return_value=('{}', {'finish_reason': 'length'})):
            self.assertFalse(legacy.run_model_task(**kwargs)['ok'])


class SourcesAndCacheTests(unittest.TestCase):
    def test_directional_relation_output_without_tags_passes_current_contract(self):
        state = legacy.initial_unified_state()
        state['entity_candidates'] = {f'character:{name}': {'entity_key': f'character:{name}', 'type': 'character', 'primary_name': name, 'aliases': [], 'description': name} for name in ['甲', '乙']}
        message = {'ref': 'r0001.assistant', 'role': 'assistant', 'name': '旁白', 'source_line': 1, 'content': '甲与乙互相信任。'}
        rounds = [{'round': 1, 'assistant': message, 'user': {**message, 'ref': 'r0001.user', 'role': 'user', 'content': ''}}]
        raw = {'relations': [{'participant_keys': ['character:甲','character:乙'], 'description': '彼此信任的同伴。', 'evidence': [{'source_ref': message['ref'], 'quote': message['content']}], 'directional_views': [{'from_key': 'character:甲', 'toward_key': 'character:乙', 'description': '信任乙。'}]}]}
        normalized = pipeline.normalize_relation_reference_plan(raw, event_rosters=[], state=state, rounds=rounds)
        self.assertEqual(normalized['relations'][0]['directional_states'][0]['tags'], [])
        errors, _ = pipeline.validate_entity_content_plan(normalized, state=state, rounds=rounds)
        self.assertEqual(errors, [])

    def test_opening_continuations_and_trailing_context_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.jsonl'
            messages = [{'mes': '开场'}, {'mes': '问题一', 'is_user': True}, {'mes': '问题二', 'is_user': True}, {'mes': '回复'}, {'mes': '续写', 'extra': {'nexus_source_version': 'swipe-version'}}, {'mes': '尾部上下文', 'is_system': True}]
            path.write_text('\n'.join(json.dumps(m) for m in messages), encoding='utf-8')
            rounds = list(message_rounds(path))
            self.assertEqual([r['assistant']['content'] for r in rounds], ['开场', '回复', '续写'])
            records = legacy.normalized_source_records(rounds)
            self.assertEqual([r['raw_content'] for r in records], [m['mes'] for m in messages])
            self.assertEqual(records[4]['version'], 'swipe-version')
            self.assertEqual(len(list(budget_batches(rounds, 1, 100))), 3)
            with self.assertRaises(ValueError): list(budget_batches(rounds, 8, 2))

    def test_cache_scope_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = TaskCache(Path(directory), 'snapshot-a')
            calls = []
            def runner(**kwargs):
                calls.append(kwargs); return {'ok': kwargs['model'] != 'broken', 'plan': {'entities': []}}
            settings = dict(task='event', api_key='not-persisted-secret', model='m', user_prompt='text', client_options={'transport': 'messages'})
            cache.run(runner, **settings)
            self.assertTrue(cache.run(runner, **settings)['cache_hit'])
            cache.run(runner, **{**settings, 'model': 'other'})
            cache.run(runner, **{**settings, 'model': 'broken'})
            cache.run(runner, **{**settings, 'model': 'broken'})
            TaskCache(Path(directory), 'snapshot-b').run(runner, **settings)
            self.assertEqual(len(calls), 5)
            self.assertNotIn('not-persisted-secret', ''.join(p.read_text('utf-8') for p in Path(directory).glob('*.json')))


class PipelineIntegrationTests(unittest.TestCase):
    def fixture_reply(self, endpoint, key, model, system, user, *args, **kwargs):
        if system == v2.NARRATIVE_MAP_SYSTEM_PROMPT:
            story = user.split('本批连续故事：\n', 1)[1].split('\n\n直接返回')[0]
            result = {'events': [{'start_quote': story[:15], 'entity_roster': [{'type': 'item', 'primary_name': '旧剑', 'aliases': [], 'evidence_quote': '旧剑'}]}]}
        else:
            payload = json.loads(user[user.index('{'):])
            if system == v2.EVENT_CONTENT_SYSTEM_PROMPT:
                result = {'event_updates': [{'partition_key': s['partition_key'], 'description': '甲检查旧剑。', 'story_summary_add': ''.join(m['content'] for m in s['assigned_messages']), 'new_key_details': []} for s in payload['fixed_segments']]}
            elif system == v2.RELATION_REFERENCE_SYSTEM_PROMPT:
                result = {'entities': [], 'relations': [], 'entity_event_references': [], 'event_locations': []}
            else:
                message = payload['fixed_event_segments'][0]['assigned_messages'][0]
                evidence = [{'source_ref': message['ref'], 'quote': '旧剑'}]
                if system == v2.ENTITY_CREATE_SYSTEM_PROMPT:
                    result = {'entities': [{'entity_key': 'item:旧剑', 'type': 'item', 'primary_name': '旧剑', 'aliases_add': [], 'description': '一柄尚可使用的旧剑。', 'semantic_fields': {'item_profile': {'材质': '凡铁', '形制': '长剑'}}, 'evidence': evidence}]}
                else:
                    result = {'entities': [{'entity_key': 'item:旧剑', 'field_updates': [{'field_name': 'item_profile', 'relationship_to_old': 'revise', 'value': {'材质': '精钢'}, 'evidence': evidence}]}]}
        return json.dumps(result, ensure_ascii=False), {'finish_reason': 'stop'}

    def run_fixture(self, root, name, snapshot, floor=0, messages=None):
        chat = root / f'{name}.jsonl'; source = root / f'{name}-snapshot.json'; profiles = root / 'profiles.json'
        chat.write_text(json.dumps({'mes': '甲检查旧剑，确认尚可使用。' if floor == 0 else '甲继续检查旧剑，辨认出材质为精钢。', 'extra': {'nexus_source_floor': floor, 'nexus_source_version': f'version-{floor}'}}), encoding='utf-8')
        if messages:
            chat.write_text('\n'.join(json.dumps({'mes': text, 'extra': {'nexus_source_floor': i, 'nexus_source_version': f'version-{i}'}}) for i,text in enumerate(messages)), encoding='utf-8')
        source.write_text(json.dumps(snapshot), encoding='utf-8')
        profiles.write_text(json.dumps({prefix: {'transport': 'chat_completions', 'options': {}} for prefix in ['map', 'event', 'create', 'update', 'relation']}), encoding='utf-8')
        args = pipeline.parse_args([str(chat), '--endpoint', 'https://unused.invalid/v1/chat/completions', '--model', 'fixture-model', '--output', str(root / f'{name}.md'), '--snapshot', str(source), '--profiles', str(profiles), '--cache-dir', str(root / 'cache'), '--batch-size', '1', '--batches', '4'])
        return pipeline.run_probe(args)

    def test_two_jobs_reuse_identity_preserve_manual_fields_and_resume_without_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPENCODE_API_KEY': 'fixture-secret', 'NEXUS_EXTRACTION_OFFLINE': '1'}), patch.object(transport, 'call_model', side_effect=self.fixture_reply) as request:
            root = Path(directory)
            first = self.run_fixture(root, 'first', {'entities': [], 'state': None, 'fingerprint': 'empty'})
            self.assertEqual(first['status'], 'completed', first['batches'])
            item = next(e for e in first['final_network'] if e['type'] == 'item')
            item['components']['item_profile']['data']['人工备注'] = '保留此项'
            snapshot = {'entities': first['final_network'], 'state': first['checkpoint_state'], 'fingerprint': 'second'}
            second = self.run_fixture(root, 'second', snapshot, 1)
            self.assertEqual(second['status'], 'completed', [(name, task.get('fatal_error'), [a.get('validation_errors') for a in task.get('attempts',[])]) for batch in second['batches'] for name, task in batch.get('tasks',{}).items() if not task.get('ok')])
            items = [e for e in second['final_network'] if e['type'] == 'item']
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['id'], item['id'])
            self.assertEqual(items[0]['components']['item_profile']['data'], {'材质': '精钢', '形制': '长剑', '人工备注': '保留此项'})
            calls = request.call_count
            resumed = self.run_fixture(root, 'second', snapshot, 1)
            self.assertEqual(resumed['status'], 'completed')
            self.assertEqual(request.call_count, calls)
            self.assertEqual(resumed['final_network'], second['final_network'])
            self.assertEqual(len(resumed['checkpoint_state']['source_records']), 2)

    def test_failed_later_batch_resumes_only_the_unfinished_task(self):
        messages = ['甲检查旧剑，确认尚可使用。', '甲继续检查旧剑，辨认出材质为精钢。']
        calls = []
        failing = True
        def reply(*args, **kwargs):
            system, user = args[3:5]
            calls.append((system, user))
            if failing and system == v2.EVENT_CONTENT_SYSTEM_PROMPT and '精钢' in user:
                raise transport.ChatCompletionTransportError('fixture truncated', metadata={'retryable': False})
            return self.fixture_reply(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPENCODE_API_KEY': 'fixture-secret', 'NEXUS_EXTRACTION_OFFLINE': '1'}), patch.object(transport, 'call_model', side_effect=reply):
            root = Path(directory); snapshot = {'entities': [], 'state': None, 'fingerprint': 'empty'}
            failed = self.run_fixture(root, 'failed', snapshot, messages=messages)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['checkpoint_round_end'], 1)
            previous_count = len(calls); failing = False
            recovered = self.run_fixture(root, 'failed', snapshot, messages=messages)
            self.assertEqual(recovered['status'], 'completed')
            self.assertEqual(len(calls) - previous_count, 1)
            self.assertEqual(calls[-1][0], v2.EVENT_CONTENT_SYSTEM_PROMPT)
            self.assertEqual(len(recovered['checkpoint_state']['source_records']), 2)


if __name__ == '__main__': unittest.main()
