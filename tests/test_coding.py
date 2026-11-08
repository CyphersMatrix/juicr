"""Coding task: one-file output, parse-only checks, frozen test fields. Inference is injected or simulated."""
import copy
import json
import tempfile
import unittest

from juicr_local import (Workbench, validate, request_body, extract_code, check_code, build_file,
                         FILE_TYPES, CODING_DEFAULT_TOKENS)
from tests.test_local import draft, wait


def coding_draft(file_type='python_script', **extra):
    d = draft()
    d['profiles'] = d['profiles'][:1]
    d['profiles'][0].pop('coding_output_tokens', None)
    d.update(task='coding', file_type=file_type, repeats=1, **extra)
    return d


class CodingTests(unittest.TestCase):
    def test_validation_requires_a_known_file_type_and_defaults_the_token_cap(self):
        d = coding_draft()
        v = validate(d)
        self.assertEqual(v['file_type'], 'python_script')
        self.assertEqual(v['profiles'][0]['coding_output_tokens'], CODING_DEFAULT_TOKENS)
        for bad in (None, 'rust', ''):
            with self.assertRaises(ValueError):
                validate(dict(d, file_type=bad))
        self.assertNotIn('file_type', validate(dict(draft(), file_type='json')))  # ignored for other tasks
        with self.assertRaises(ValueError):
            validate(dict(d, profiles=[dict(d['profiles'][0], coding_output_tokens=0)]))

    def test_request_asks_for_one_fenced_file_and_uses_the_coding_cap(self):
        d = validate(coding_draft('yaml', criteria='Use two spaces.'))
        d['profiles'][0]['coding_output_tokens'] = 3000
        body = request_body(d, d['profiles'][0], 5)
        system = body['messages'][0]['content']
        self.assertEqual(body['max_tokens'], 3000)
        self.assertIn('Requirements:\nUse two spaces.', system)
        self.assertIn('YAML configuration file', system)
        self.assertIn('```yaml', system)
        self.assertNotIn('fenced code block', request_body(validate(draft()), draft()['profiles'][0], 5)['messages'][0]['content'])

    def test_extraction_picks_the_matching_block_and_flags_odd_replies(self):
        text = 'Intro\n```json\n{"a": 1}\n```\nand\n```python\nprint(1)\n```'
        got = extract_code(text, 'python')
        self.assertEqual((got['text'], got['extra_blocks'], got['source']), ('print(1)', 1, 'fenced code block'))
        self.assertEqual(extract_code('```\nx = 1\n```', 'python')['text'], 'x = 1')
        cut = extract_code('```python\ndef f():\n    return 1', 'python')
        self.assertIn('truncated', cut['source']); self.assertEqual(cut['text'], 'def f():\n    return 1')
        self.assertIn('no code fence', extract_code('x = 1', 'python')['source'])

    def test_checks_are_parse_only_and_never_a_score(self):
        self.assertEqual(check_code('x = 1\n', 'python')['status'], 'valid')
        bad = check_code('def f(:\n', 'python')
        self.assertEqual(bad['status'], 'invalid'); self.assertIn('line 1', bad['detail'])
        # Parsing must not execute: this would raise or write if it ran.
        self.assertEqual(check_code('raise SystemExit(3)\nopen("never-created.txt", "w")\n', 'python')['status'], 'valid')
        self.assertEqual(check_code('{"a": [1, 2]}', 'json')['status'], 'valid')
        self.assertIn('line 1', check_code('{"a": }', 'json')['detail'])
        self.assertEqual(check_code('<!doctype html><html><body><p>Hi<div><b>x</b></div></body></html>', 'html')['status'], 'valid')
        self.assertEqual(check_code('<div><span>x</div>', 'html')['status'], 'invalid')
        self.assertEqual(check_code('plain text', 'html')['status'], 'invalid')
        self.assertEqual(check_code('   ', 'python')['status'], 'invalid')
        self.assertEqual(check_code('a: 1', 'yaml')['status'], 'not_checked')
        for key, (_, language, filename, _) in FILE_TYPES.items():
            self.assertIn(build_file('```' + language + '\nx\n```', key)['filename'], filename)

    def test_run_stores_a_separate_file_result_for_each_type_without_executing(self):
        replies = {'python_snippet': '```python\nprint("hi")\n```', 'python_script': '```py\nraise SystemExit(9)\n```',
                   'html_app': '```html\n<!doctype html><title>t</title><p>x</p>\n```', 'json': '```json\n{"k": 1}\n```',
                   'yaml': '```yaml\nk: 1\n```'}
        for file_type, reply in replies.items():
            def fake(d, p, seed):
                return ({'choices': [{'message': {'content': reply}, 'finish_reason': 'stop'}]}, request_body(d, p, seed))
            with tempfile.TemporaryDirectory() as tmp:
                w = Workbench(tmp, fake); rid = w.start(coding_draft(file_type)); wait(w)
                run = w.get(rid)
                self.assertEqual(run['status'], 'completed')
                f = run['results'][0]['file']
                self.assertEqual(f['file_type'], file_type)
                self.assertEqual(f['text'] + '\n', reply.split('\n', 1)[1].rsplit('\n```', 1)[0] + '\n')
                self.assertEqual(f['validation']['status'], 'not_checked' if file_type == 'yaml' else 'valid')
                self.assertEqual(w.saved_results()[0]['file_type'], file_type)

    def test_demo_provider_produces_a_clearly_simulated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp); rid = w.start(coding_draft('json')); wait(w)
            f = w.get(rid)['results'][0]['file']
            self.assertEqual((f['text'], f['validation']['status']), ('{"simulated": true}', 'valid'))

    def test_writing_results_have_no_file_and_failed_calls_have_none(self):
        def boom(d, p, seed):
            raise OSError('x')
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp); rid = w.start(draft()); wait(w)
            self.assertTrue(all('file' not in r for r in w.get(rid)['results']))
            w2 = Workbench(tmp + '/b', boom); rid = w2.start(coding_draft()); wait(w2)
            self.assertNotIn('file', w2.get(rid)['results'][0])

    def test_file_type_is_frozen_per_test_version_and_flagged_in_comparisons(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp)
            test = w.save_test(coding_draft('json'))
            self.assertEqual(test['draft']['file_type'], 'json')
            changed = copy.deepcopy(test['draft']); changed['file_type'] = 'yaml'; changed['test_id'] = test['id']
            with self.assertRaises(ValueError):
                w.start(changed)
            ok = copy.deepcopy(test['draft']); ok['test_id'] = test['id']
            w.start(ok); wait(w)
            w.start(dict(coding_draft('yaml'), title='other')); wait(w)
            ids = [r['id'] for r in w.saved_results()]
            self.assertIn('file_type', w.compare_results(ids)['differences'])

    def test_older_saved_tests_without_a_coding_cap_still_run_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp)
            test = w.save_test(draft() | {'profiles': draft()['profiles'][:1]})
            stored = w.get(test['id'])
            for p in stored['draft']['profiles']:
                p.pop('coding_output_tokens')  # simulate a record written before this feature
            w.save(test['id'], 'test', stored)
            d = json.loads(json.dumps(stored['draft'])); d['test_id'] = test['id']
            w.start(d); wait(w)
            self.assertEqual(w.saved_results()[0]['status'], 'done')


if __name__ == '__main__':
    unittest.main()
