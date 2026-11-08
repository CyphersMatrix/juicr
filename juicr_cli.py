"""JSON command-line client for the running local JUICR workbench (stdlib only)."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from juicr_local import NoRedirect, validate


class JSONParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({'error': message, 'help': 'Use --help for command syntax.'}), file=sys.stderr)
        self.exit(2)


class Client:
    def __init__(self, server):
        u = urlsplit(server)
        if (u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost') or
                u.username or u.password or u.query or u.fragment or u.path not in ('', '/')):
            raise ValueError('--server must be a loopback HTTP URL, e.g. http://127.0.0.1:8811')
        self.server = server.rstrip('/')
        self.opener = build_opener(ProxyHandler({}), NoRedirect)
        self.token = None

    def request(self, path, data=None):
        headers = {'Accept': 'application/json'}
        if data is not None:
            if self.token is None:
                self.token = self.request('/api/state')['token']
            headers.update({'Content-Type': 'application/json', 'X-JUICR-Token': self.token})
        raw = json.dumps(data).encode() if data is not None else None
        try:
            with self.opener.open(Request(self.server + path, data=raw, headers=headers), timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                message = json.load(exc).get('error', f'JUICR HTTP {exc.code}')
            except (ValueError, AttributeError):
                message = f'JUICR HTTP {exc.code}; restart JUICR if its CLI routes are unavailable'
            finally:
                exc.close()
            raise ValueError(message) from None
        except (URLError, OSError):
            raise ValueError('Could not reach JUICR. Start python juicr_local.py --no-browser, or check --server.') from None

    def item(self, item_id):
        return self.request('/api/item/' + quote(item_id, safe=''))


def parser():
    p = JSONParser(prog='python juicr_local.py cli', description=__doc__,
                   epilog='Start JUICR first. Output is JSON; credentials stay in the server.')
    p.add_argument('--server', default='http://127.0.0.1:8811', help='Running local JUICR URL')
    groups = p.add_subparsers(dest='group', required=True)
    tests = groups.add_parser('tests', help='List, inspect, create or run reusable tests').add_subparsers(dest='action', required=True)
    ls = tests.add_parser('list')
    ls.add_argument('--all-versions', action='store_true')
    ls.add_argument('--search', default='')
    show = tests.add_parser('show')
    show.add_argument('id', help='Exact test revision ID')
    create = tests.add_parser('create', help='Create a saved test; never generates output')
    create.add_argument('--file', help='UTF-8 JSON draft (same schema as tests show -> draft); use - for stdin')
    create.add_argument('--previous', help='Test ID to revise; old versions are retained')
    create.add_argument('--title')
    create.add_argument('--content-file', help='UTF-8 source material')
    create.add_argument('--instructions', help='What the model should do')
    create.add_argument('--context-file')
    create.add_argument('--criteria', default='')
    create.add_argument('--task', choices=['writing', 'extraction', 'coding'], default='writing')
    create.add_argument('--file-type', choices=['python_snippet', 'python_script', 'html_app', 'json', 'yaml'], help='Required for --task coding')
    create.add_argument('--coding-output-tokens', type=int, default=4096)
    create.add_argument('--settings', default='{}', help='JSON sampling object, e.g. {"temperature":0.7}')
    create.add_argument('--writing-output-tokens', type=int, default=1024)
    create.add_argument('--extraction-output-tokens', type=int, default=512)
    create.add_argument('--repeats', type=int, default=1)
    create.add_argument('--seed', type=int, default=0)
    create.add_argument('--timeout', type=int, default=120)
    run = tests.add_parser('run', help='Run an exact saved revision against one provider/model')
    run.add_argument('id', help='Exact test revision ID')
    target_options(run, model=True)
    run.add_argument('--dry-run', action='store_true', help='Return frozen inputs and allowance without running or tracking')
    run.add_argument('--no-wait', action='store_true', help='Return the run ID immediately')
    run.add_argument('--wait-timeout', type=float, default=600, help='Seconds to wait; expiry leaves the run active (exit 3)')
    models = groups.add_parser('models', help='Discover available models from configured endpoints').add_subparsers(dest='action', required=True)
    ml = models.add_parser('list', help='Read catalogs only; no load or generation')
    target_options(ml)
    ml.add_argument('--search', default='')
    runs = groups.add_parser('runs').add_subparsers(dest='action', required=True)
    rs = runs.add_parser('show'); rs.add_argument('id')
    cancel = runs.add_parser('cancel', help='Stop queued work for this run; in-flight usage may be unknown')
    cancel.add_argument('id')
    results = groups.add_parser('results').add_subparsers(dest='action', required=True)
    results.add_parser('list')
    compare = results.add_parser('compare', help='Compare two to six saved result IDs without generation')
    compare.add_argument('ids', nargs='+')
    return p


def target_options(p, model=False):
    p.add_argument('--provider', choices=['demo', 'lemonade', 'openrouter'], required=model,
                   help='Omit to list catalogs for every provider' if not model else 'Service to run against')
    if model:
        p.add_argument('--model', required=True, help='Exact model ID from models list')
    p.add_argument('--url', help='Lemonade API base URL; defaults to Connections')
    p.add_argument('--endpoint', help='Optional pinned OpenRouter provider slug (run only)')


def create_draft(a):
    if a.file:
        if a.title or a.content_file or a.instructions or a.context_file or a.criteria or a.settings != '{}':
            raise ValueError('Use --file alone for draft fields; edit the JSON to change its parameters')
        supplied = json.load(sys.stdin) if a.file == '-' else json.loads(Path(a.file).read_text(encoding='utf-8-sig'))
        # Reject accidental option overrides instead of silently ignoring them.
        defaults = dict(task='writing', writing_output_tokens=1024, extraction_output_tokens=512, coding_output_tokens=4096,
                        file_type=None, repeats=1, seed=0, timeout=120)
        if any(getattr(a, k) != v for k, v in defaults.items()):
            raise ValueError('Use --file alone for draft fields; edit the JSON to change its parameters')
        return validate(supplied)
    if not a.title or not a.content_file or not a.instructions:
        raise ValueError('Provide --file, or --title, --content-file and --instructions')
    return validate(dict(title=a.title, content=Path(a.content_file).read_text(encoding='utf-8-sig'),
        instructions=a.instructions, context=Path(a.context_file).read_text(encoding='utf-8-sig') if a.context_file else '',
        criteria=a.criteria, task=a.task, **({'file_type': a.file_type} if a.task == 'coding' else {}), repeats=a.repeats, seed=a.seed, timeout=a.timeout,
        profiles=[dict(name='Test parameters', provider='demo', model='demo-model', settings=json.loads(a.settings),
                       writing_output_tokens=a.writing_output_tokens, extraction_output_tokens=a.extraction_output_tokens,
                       coding_output_tokens=a.coding_output_tokens)]))


def execute(a, c):
    if a.group == 'tests':
        if a.action == 'list':
            rows = c.request('/api/tests')['tests']
            if not a.all_versions:
                latest = {}
                for row in rows:
                    if row['revision'] > latest.get(row['family_id'], {}).get('revision', 0):
                        latest[row['family_id']] = row
                rows = list(latest.values())
            return {'tests': [r for r in rows if a.search.casefold() in r['title'].casefold()]}, 0
        if a.action == 'create':
            d = create_draft(a)
            return c.request('/api/tests/save', {'draft': d, 'previous': a.previous}), 0
        test = c.item(a.id)
        if test.get('kind') != 'test_definition':
            raise ValueError('Expected a saved test revision ID')
        if a.action == 'show':
            return test, 0
        if not 0 < a.wait_timeout <= 86400:
            raise ValueError('--wait-timeout must be greater than 0 and at most 86400 seconds')
        if a.url and a.provider != 'lemonade' or a.endpoint and a.provider != 'openrouter':
            raise ValueError('--url applies to Lemonade; --endpoint applies to OpenRouter')
        d = test['draft']
        profile = d['profiles'][0]
        for key in ('url', 'endpoint'):
            profile.pop(key, None)
        profile.update(provider=a.provider, model=a.model, name=a.model)
        if a.provider == 'lemonade':
            profile['url'] = a.url or c.request('/api/state')['lemonade_url']
        if a.endpoint:
            profile['endpoint'] = a.endpoint
        d['test_id'] = test['id']
        d = validate(d)
        allowance = {'requests': d['repeats'], 'output_tokens': d['repeats'] * profile[d['task'] + '_output_tokens'],
                     'note': 'Input tokens and dollars are outside this allowance; in-flight usage may be unknown.'}
        if a.dry_run:
            return {'dry_run': True, 'draft': d, 'allowance': allowance}, 0
        rid = c.request('/api/run', d)['id']
        if a.no_wait:
            return {'id': rid, 'submitted': True, 'allowance': allowance}, 0
        end = time.monotonic() + a.wait_timeout
        while True:
            run = c.item(rid)
            if run['status'] != 'running':
                return run, 0 if run['status'] == 'completed' else 1
            if time.monotonic() >= end:
                return {'id': rid, 'status': 'wait_timeout', 'run_continues': True,
                        'note': 'Use runs show or runs cancel with this ID. No retry was made.'}, 3
            time.sleep(min(.2, max(0, end - time.monotonic())))
    if a.group == 'models':
        if a.endpoint or a.url and a.provider != 'lemonade':
            raise ValueError('Catalog --url requires --provider lemonade; --endpoint is for test runs only')
        state = c.request('/api/state')
        catalogs = []
        failed = False
        for provider in [a.provider] if a.provider else ['demo', 'lemonade', 'openrouter']:
            connection = {'provider': provider}
            if provider == 'lemonade':
                connection['url'] = a.url or state['lemonade_url']
            try:
                rows = c.request('/api/models', connection)['models']
                rows = [r for r in rows if a.search.casefold() in (r['id'] + ' ' + r['name']).casefold()]
                catalogs.append(dict(connection, status='available', models=rows))
            except ValueError as exc:
                failed = True
                catalogs.append(dict(connection, status='unavailable', models=[], error=str(exc)))
        return {'catalogs': catalogs, 'note': 'Catalog availability does not mean a model is loaded or inference is verified.'}, int(failed)
    if a.group == 'runs':
        if a.action == 'show':
            run = c.item(a.id)
            if 'jobs' not in run:
                raise ValueError('Expected a run ID')
            return run, 0
        return c.request('/api/cancel', {'id': a.id}), 0
    if a.action == 'list':
        return c.request('/api/results'), 0
    return c.request('/api/compare', {'ids': a.ids}), 0


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        result, code = execute(a, Client(a.server))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return code
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({'error': 'CLI interrupted. Any submitted run continues; inspect results or use runs cancel.'}), file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
