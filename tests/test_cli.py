"""CLI integration against disposable loopback servers; never real providers."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import juicr_cli
from juicr_local import Workbench, make_server, request_body


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.catalogs = []
        def fake(d, p, seed):
            self.calls.append((d, p, seed))
            return {'choices': [{'message': {'content': 'simulated CLI answer'}}]}, request_body(d, p, seed)
        def catalog(connection):
            self.catalogs.append(connection)
            if connection['provider'] == 'openrouter':
                raise ValueError('Simulated catalog unavailable')
            return [{'id': 'alpha', 'name': 'Alpha model'}, {'id': 'beta', 'name': 'Beta model'}]
        self.w = Workbench(self.tmp.name, fake)
        self.server = make_server(self.w, 0, model_fetcher=catalog)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.source = Path(self.tmp.name) / 'source.txt'
        self.source.write_text('Fixed source content', encoding='utf-8')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = juicr_cli.main(['--server', self.url, *args])
        return code, json.loads(out.getvalue() or err.getvalue())

    def create(self, *extra):
        code, row = self.cli('tests', 'create', '--title', 'CLI test', '--content-file', str(self.source),
                             '--instructions', 'Summarize', '--settings', '{"temperature":0.37}', *extra)
        self.assertEqual(code, 0, row)
        return row

    def test_create_list_show_revise_and_preserve_frozen_parameters(self):
        first = self.create('--criteria', 'Keep facts')
        second = self.create('--previous', first['id'])
        code, rows = self.cli('tests', 'list')
        self.assertEqual([r['id'] for r in rows['tests']], [second['id']])
        self.assertEqual(len(self.cli('tests', 'list', '--all-versions')[1]['tests']), 2)
        self.assertEqual(self.cli('tests', 'list', '--search', 'missing')[1]['tests'], [])
        self.assertEqual(self.cli('tests', 'show', first['id'])[1], first)
        code, preview = self.cli('tests', 'run', first['id'], '--provider', 'lemonade', '--model', 'alpha', '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(preview['draft']['content'], 'Fixed source content')
        self.assertEqual(preview['draft']['profiles'][0]['settings'], {'temperature': .37})
        self.assertEqual(preview['draft']['criteria'], 'Keep facts')
        self.assertEqual(preview['allowance']['requests'], 1)
        self.assertEqual(preview['allowance']['output_tokens'], 1024)
        self.assertEqual(len(self.w.coverage()['models']), 1)  # No new target tracked by dry-run.
        self.assertFalse(self.calls)

    def test_json_file_stdin_and_validation(self):
        first = self.create()
        file = Path(self.tmp.name) / 'test.json'
        file.write_text(json.dumps(first['draft']), encoding='utf-8')
        self.assertEqual(self.cli('tests', 'create', '--file', str(file))[0], 0)
        with patch('sys.stdin', io.StringIO(json.dumps(first['draft']))):
            self.assertEqual(self.cli('tests', 'create', '--file', '-')[0], 0)
        self.assertEqual(self.cli('tests', 'create', '--file', str(file), '--repeats', '2')[0], 2)
        self.assertEqual(self.cli('tests', 'create', '--title', 'incomplete')[0], 2)
        file.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.cli('tests', 'create', '--file', str(file))[0], 2)
        self.assertFalse(self.calls)

    def test_connected_model_catalogs_search_and_partial_failure(self):
        code, result = self.cli('models', 'list')
        self.assertEqual(code, 1)
        self.assertEqual([c['status'] for c in result['catalogs']], ['available', 'available', 'unavailable'])
        code, result = self.cli('models', 'list', '--provider', 'lemonade', '--search', 'BETA', '--url', 'http://127.0.0.1:9999/v1')
        self.assertEqual(code, 0)
        self.assertEqual(result['catalogs'][0]['models'], [{'id': 'beta', 'name': 'Beta model'}])
        self.assertEqual(self.catalogs[-1]['url'], 'http://127.0.0.1:9999/v1')
        self.assertFalse(self.calls)
        self.assertNotIn('token', json.dumps(result))

    def test_provider_target_run_accumulates_results_and_compare_is_read_only(self):
        test = self.create()
        before = self.w.get(test['id'])
        for provider in ('lemonade', 'openrouter'):
            args = ['--endpoint', 'pinned'] if provider == 'openrouter' else ['--url', 'http://127.0.0.1:9999/v1']
            code, run = self.cli('tests', 'run', test['id'], '--provider', provider, '--model', 'alpha', *args)
            self.assertEqual(code, 0, run)
            self.assertEqual(run['status'], 'completed')
            self.assertEqual(run['draft']['test_id'], test['id'])
            self.assertEqual(run['draft']['profiles'][0]['provider'], provider)
            self.assertEqual(self.cli('runs', 'show', run['id'])[1], run)
        self.assertEqual(self.w.get(test['id']), before)
        rows = self.cli('results', 'list')[1]['results']
        self.assertEqual(len(rows), 2)
        code, compared = self.cli('results', 'compare', *[r['id'] for r in rows])
        self.assertEqual(code, 0)
        self.assertTrue(compared['same_input'])
        self.assertEqual(len(self.calls), 2)

    def test_timeout_and_scoped_cancel_never_stop_another_run(self):
        release = threading.Event()
        original = self.w.caller
        self.w.caller = lambda *args: (release.wait(5), original(*args))[1]
        test = self.create('--repeats', '2')
        try:
            code, pending = self.cli('tests', 'run', test['id'], '--provider', 'demo', '--model', 'alpha', '--wait-timeout', '.01')
            self.assertEqual(code, 3)
            self.assertTrue(pending['run_continues'])
            self.assertEqual(self.cli('runs', 'cancel', 'different-run')[0], 2)
            self.assertFalse(self.w.cancel.is_set())
            self.assertEqual(self.cli('runs', 'cancel', pending['id'])[0], 0)
            self.assertTrue(self.w.cancel.is_set())
        finally:
            release.set()
            deadline = time.monotonic() + 5
            while self.w.active and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertEqual(len(self.calls), 1)

    def test_no_wait_failure_exit_and_invalid_ids(self):
        test = self.create()
        code, submitted = self.cli('tests', 'run', test['id'], '--provider', 'demo', '--model', 'alpha', '--no-wait')
        self.assertEqual(code, 0)
        self.assertTrue(submitted['submitted'])
        while self.w.active:
            time.sleep(.01)
        def fail(*args):
            raise ValueError('Simulated failure')
        self.w.caller = fail
        code, run = self.cli('tests', 'run', test['id'], '--provider', 'demo', '--model', 'alpha')
        self.assertEqual(code, 1)
        self.assertEqual(run['status'], 'completed_with_errors')
        self.assertEqual(self.cli('tests', 'show', run['id'])[0], 2)
        self.assertEqual(self.cli('tests', 'run', 'missing', '--provider', 'demo', '--model', 'alpha')[0], 2)

    def test_both_entry_points_produce_json(self):
        root = Path(__file__).resolve().parents[1]
        for script, prefix in [('juicr_cli.py', []), ('juicr_local.py', ['cli'])]:
            run = subprocess.run([sys.executable, str(root / script), *prefix, '--server', self.url,
                                  'models', 'list', '--provider', 'demo'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(json.loads(run.stdout)['catalogs'][0]['models']), 2)

    def test_client_rejects_remote_servers_and_reports_unreachable(self):
        for url in ('https://127.0.0.1:8811', 'http://example.com', 'http://user:pass@localhost:8811',
                    self.url + '/private', self.url + '?key=secret'):
            with self.assertRaises(ValueError):
                juicr_cli.Client(url)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(juicr_cli.main(['--server', 'http://127.0.0.1:0', 'tests', 'list']), 2)


if __name__ == '__main__':
    unittest.main()
