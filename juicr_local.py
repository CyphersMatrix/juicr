"""JUICR local comparison workbench. Run with Python 3.11+; no packages required."""
import argparse
import base64
from contextlib import contextmanager
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parent
FIELDS = {'temperature': (0, 2), 'top_p': (0, 1), 'top_k': (0, 100000),
          'min_p': (0, 1), 'repeat_penalty': (0, 2),
          'presence_penalty': (-2, 2), 'frequency_penalty': (-2, 2)}
TASKS = ('writing', 'extraction', 'coding')
TEST_FIELDS = ('content', 'context', 'instructions', 'criteria', 'task', 'file_type', 'repeats', 'seed', 'timeout')
PARAM_FIELDS = ('settings', 'writing_output_tokens', 'extraction_output_tokens', 'coding_output_tokens')
CODING_DEFAULT_TOKENS = 4096
# file_type -> (label, language, filename, description used in the instruction sent to the model)
FILE_TYPES = {
    'python_snippet': ('Python snippet', 'python', 'snippet.py', 'a short Python code snippet (a function or a few related statements, not a full program)'),
    'python_script': ('Python script', 'python', 'script.py', 'one complete, runnable Python script that uses only the standard library unless the task says otherwise'),
    'html_app': ('HTML single-file app', 'html', 'app.html', 'one self-contained HTML file with all CSS and JavaScript inline and no external resources'),
    'json': ('JSON configuration', 'json', 'config.json', 'one valid JSON configuration file'),
    'yaml': ('YAML configuration', 'yaml', 'config.yaml', 'one valid YAML configuration file'),
}
FENCE_ALIASES = {'python': {'python', 'py', 'python3'}, 'html': {'html', 'htm'}, 'json': {'json', 'jsonc'}, 'yaml': {'yaml', 'yml'}}


def coding_directive(file_type):
    label, language, _, what = FILE_TYPES[file_type]
    return (f'Produce {what}. Reply with only that file inside a single fenced code block tagged ```{language}. '
            'Write no explanation or other text outside the code block.')


def extract_code(text, language):
    """Pull the one file out of a model reply. Never executes anything."""
    blocks = [(m.group(1).lower(), m.group(2)) for m in re.finditer(r'```([\w+-]*)[^\n]*\n(.*?)```', text, re.S)]
    if blocks:
        wanted = FENCE_ALIASES[language]
        chosen = next((b for b in blocks if b[0] in wanted), blocks[0])
        return {'text': chosen[1].strip('\n'), 'source': 'fenced code block', 'extra_blocks': len(blocks) - 1}
    opened = re.search(r'```[\w+-]*[^\n]*\n', text)
    if opened:  # reply was cut off before the closing fence
        return {'text': text[opened.end():].strip('\n'), 'source': 'unclosed code fence (reply may be truncated)', 'extra_blocks': 0}
    return {'text': text.strip('\n'), 'source': 'whole reply (no code fence)', 'extra_blocks': 0}


class _TagCheck(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
    OPTIONAL = {'p', 'li', 'dt', 'dd', 'tr', 'td', 'th', 'thead', 'tbody', 'tfoot', 'option', 'optgroup', 'colgroup', 'html', 'head', 'body'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.has_html = [], [], False

    def handle_starttag(self, tag, attrs):
        self.has_html = True
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.has_html = True

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                skipped = self.stack.pop()
                if skipped not in self.OPTIONAL:
                    self.problems.append(f'<{skipped}> not closed')
            self.stack.pop()
        else:
            self.problems.append(f'stray </{tag}>')


def check_code(text, language):
    """Parse-only check; informational, not a quality score. Generated code is never run."""
    if not text.strip():
        return {'status': 'invalid', 'detail': 'The file is empty.'}
    try:
        if language == 'python':
            ast.parse(text)
            return {'status': 'valid', 'detail': 'Python syntax parses (the code was not run).'}
        if language == 'json':
            json.loads(text)
            return {'status': 'valid', 'detail': 'Valid JSON.'}
        if language == 'html':
            parser = _TagCheck()
            parser.feed(text)
            parser.close()
            problems = parser.problems + [f'<{t}> not closed' for t in parser.stack if t not in parser.OPTIONAL]
            if not parser.has_html:
                return {'status': 'invalid', 'detail': 'No HTML tags found.'}
            if problems:
                return {'status': 'invalid', 'detail': 'Tag structure problems: ' + '; '.join(problems[:5])}
            return {'status': 'valid', 'detail': 'HTML tags are balanced (the page was not rendered or run).'}
    except SyntaxError as e:
        return {'status': 'invalid', 'detail': f'Python syntax error on line {e.lineno}: {e.msg}'}
    except json.JSONDecodeError as e:
        return {'status': 'invalid', 'detail': f'JSON error at line {e.lineno}, column {e.colno}: {e.msg}'}
    except Exception:
        return {'status': 'invalid', 'detail': 'The file could not be parsed.'}
    return {'status': 'not_checked', 'detail': 'Not checked: Python has no built-in YAML parser and JUICR uses only the standard library.'}


def build_file(text, file_type):
    label, language, filename, _ = FILE_TYPES[file_type]
    found = extract_code(text or '', language)
    return {'file_type': file_type, 'label': label, 'language': language, 'filename': filename,
            'text': found['text'], 'source': found['source'], 'extra_blocks': found['extra_blocks'],
            'validation': check_code(found['text'], language)}


def output_cap(d, p):
    return p.get(d['task'] + '_output_tokens', CODING_DEFAULT_TOKENS)


def model_identity(profile):
    identity = {k: profile.get(k, '') for k in ('provider', 'model')}
    if identity['provider'] == 'lemonade':
        identity['url'] = profile['url'].rstrip('/')
    if identity['provider'] == 'openrouter':
        identity['endpoint'] = profile.get('endpoint', '')
    return identity


def model_id(profile):
    return hashlib.sha256(json.dumps(model_identity(profile), sort_keys=True).encode()).hexdigest()[:20]


def number(value, name, lo, hi, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a number')
    if not lo <= value <= hi or (integer and int(value) != value):
        raise ValueError(f'{name} must be {"an integer " if integer else ""}between {lo} and {hi}')
    return int(value) if integer else value


def validate(draft):
    if not isinstance(draft, dict):
        raise ValueError('Expected a comparison object')
    d = json.loads(json.dumps(draft))
    for key in ('context', 'criteria'):
        d.setdefault(key, '')
        if not isinstance(d[key], str) or len(d[key]) > 100000:
            raise ValueError(f'{key} must be text under 100,000 characters')
    for key in ('title', 'content', 'instructions'):
        if not isinstance(d.get(key), str) or not d[key].strip():
            raise ValueError(f'{key} is required')
        if len(d[key]) > 100000:
            raise ValueError(f'{key} is too long')
    if d.get('task') not in TASKS:
        raise ValueError('Choose writing, extraction or coding')
    if d['task'] == 'coding':
        if d.get('file_type') not in FILE_TYPES:
            raise ValueError('Choose the kind of file the coding test should produce')
    else:
        d.pop('file_type', None)
    d['repeats'] = number(d.get('repeats'), 'repeats', 1, 20, True)
    d['seed'] = number(d.get('seed'), 'seed', 0, 2147483647, True)
    d['timeout'] = number(d.get('timeout'), 'timeout', 5, 600, True)
    if not isinstance(d.get('profiles'), list) or not 1 <= len(d['profiles']) <= 6:
        raise ValueError('Use one to six model configurations')
    for p in d['profiles']:
        if not isinstance(p, dict) or p.get('provider') not in ('demo', 'lemonade', 'openrouter'):
            raise ValueError('Unknown provider')
        for k in ('name', 'model'):
            if not isinstance(p.get(k), str) or not p[k].strip() or len(p[k]) > 250:
                raise ValueError(f'Each column needs a {k}')
        if not isinstance(p.get('settings'), dict) or set(p['settings']) - FIELDS.keys():
            raise ValueError('Unknown sampling setting')
        for k, v in p['settings'].items():
            number(v, k, *FIELDS[k], integer=k == 'top_k')
        for k in ('writing_output_tokens', 'extraction_output_tokens', 'coding_output_tokens'):
            p[k] = number(p.get(k, CODING_DEFAULT_TOKENS if k == 'coding_output_tokens' else None), k, 1, 32768, True)
        if p['provider'] == 'lemonade':
            u = urlsplit(p.get('url', ''))
            if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('Lemonade needs an HTTP API base URL without credentials')
        if p.get('endpoint') and (not isinstance(p['endpoint'], str) or len(p['endpoint']) > 150):
            raise ValueError('Invalid OpenRouter provider name')
    count = len(d['profiles']) * d['repeats']
    if count > 100:
        raise ValueError('A comparison is limited to 100 requests')
    return d


def request_body(d, p, seed):
    body = {'model': p['model'], 'messages': [
        {'role': 'system', 'content': d['instructions']},
        {'role': 'user', 'content': d['content']}],
        'seed': seed, 'max_tokens': output_cap(d, p), 'stream': False,
        **p['settings']}
    if d.get('criteria'):
        body['messages'][0]['content'] += '\n\nRequirements:\n' + d['criteria']
    if d['task'] == 'coding':
        body['messages'][0]['content'] += '\n\n' + coding_directive(d['file_type'])
    if d.get('context'):
        body['messages'].insert(1, {'role': 'user', 'content': 'Context:\n' + d['context']})
    if p['provider'] == 'openrouter':
        if 'repeat_penalty' in body:
            body['repetition_penalty'] = body.pop('repeat_penalty')
        body['provider'] = {'require_parameters': True, 'allow_fallbacks': False}
        if p.get('endpoint'):
            body['provider']['only'] = [p['endpoint']]
    return body


class RequestAbandoned(Exception):
    """Stop was pressed while a provider request was still in flight."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def infer(d, p, seed, key=None):
    body = request_body(d, p, seed)
    if p['provider'] == 'demo':
        reply = '[Simulated response — no inference]\n\n' + d['content']
        if d['task'] == 'coding':
            language = FILE_TYPES[d['file_type']][1]
            sample = {'python': 'print("Simulated example: no model was used")', 'json': '{"simulated": true}',
                      'yaml': 'simulated: true', 'html': '<!doctype html>\n<title>Simulated</title>\n<p>No model was used.</p>'}[language]
            reply = f'```{language}\n{sample}\n```'
        return {'choices': [{'message': {'content': reply,
                    'reasoning_content': ''}, 'finish_reason': 'stop'}],
                'model': p['model'], 'usage': None, 'provider': 'demo'}, body
    if key is None:
        key = os.environ.get('OPENROUTER_API_KEY' if p['provider'] == 'openrouter' else 'LEMONADE_API_KEY', '')
    if p['provider'] == 'openrouter' and not key:
        raise ValueError('Add your OpenRouter API key in Connections')
    url = ('https://openrouter.ai/api/v1' if p['provider'] == 'openrouter' else p['url'].rstrip('/'))
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = Request(url + '/chat/completions', data=json.dumps(body).encode(), headers=headers)
    try:
        with build_opener(NoRedirect).open(req, timeout=d['timeout']) as r:
            raw = r.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError('Provider response exceeded 8 MB')
            result = json.loads(raw)
        if not isinstance(result, dict) or result.get('error') or not result.get('choices'):
            raise ValueError('Provider returned an error or no choices; usage may be unknown')
        return result, body
    except HTTPError as e:
        code = e.code
        e.close()
        raise ValueError(f'Provider HTTP {code}; check model, supported settings, key and credits') from None
    except (URLError, OSError):
        raise ValueError('Provider connection failed or timed out; usage is unknown') from None


class Workbench:
    def __init__(self, directory, caller=infer):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = directory / 'workbench.db'
        self.connections = Connections(directory)
        self.lock = threading.RLock()
        self.active = None
        self.cancel = threading.Event()
        self.caller = (lambda d, p, seed: infer(d, p, seed, key=self.connections.key(p))) if caller is infer else caller
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT, data TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS deletion_batch(id TEXT PRIMARY KEY, description TEXT, created REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS deleted(kind TEXT, target TEXT, batch TEXT, PRIMARY KEY(kind,target))')
            # Results of an active run are appended here one row at a time; the run record is rewritten once at the end.
            c.execute('CREATE TABLE IF NOT EXISTS run_result(run_id TEXT, idx INTEGER, data TEXT, PRIMARY KEY(run_id,idx))')
            rows = c.execute("SELECT id,data FROM records WHERE kind='run'").fetchall()
            for rid, raw in rows:
                run = self.hydrate(c, rid, json.loads(raw))
                if run['status'] == 'running':
                    run['status'] = 'interrupted'
                    c.execute('DELETE FROM run_result WHERE run_id=?', (rid,))
                    run['note'] = 'Application stopped. In-flight usage may be unknown; no automatic retry.'
                    c.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(run), rid))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def hydrate(c, rid, run):
        """Attach incrementally stored results to a run that has not been finalized yet."""
        if run.get('status') == 'running':
            run['results'] = [json.loads(r[0]) for r in
                              c.execute('SELECT data FROM run_result WHERE run_id=? ORDER BY idx', (rid,))]
        return run

    def save(self, rid, kind, data):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (rid, kind, json.dumps(data)))

    def get(self, rid):
        with self.connect() as c:
            row = c.execute('SELECT kind,data FROM records WHERE id=?', (rid,)).fetchone()
            hidden = set(c.execute('SELECT kind,target FROM deleted'))
        if row is None or (row[0], rid) in hidden:
            raise ValueError('Saved item not found')
        value = json.loads(row[1])
        if row[0] == 'run':
            with self.connect() as c:
                self.hydrate(c, rid, value)
        if row[0] == 'run' and value.get('results'):
            # Keep response indices stable while omitting deleted response payloads.
            value['results'] = [{'deleted':True,'column':r['column'],'repeat':r['repeat']}
                                if ('result', f'{rid}:{i}') in hidden else r
                                for i,r in enumerate(value['results'])]
            if all(r.get('deleted') for r in value['results']):
                raise ValueError('Saved item not found')
        return value

    def delete_preview(self, data):
        if not isinstance(data, dict):
            raise ValueError('Choose a test or saved results to delete')
        results = self.saved_results()
        targets, family = [], None
        if data.get('kind') == 'test':
            if not isinstance(data.get('id'),str):
                raise ValueError('Choose a saved test to delete')
            test = self.get(data.get('id'))
            if test.get('kind') != 'test_definition':
                raise ValueError('Test not found')
            family = test['family_id']
            versions = [t for t in self.test_definitions() if t['family_id'] == family]
            test_ids = {t['id'] for t in versions}
            related = [r for r in results if r['test_id'] in test_ids]
            if type(data.get('include_results', False)) is not bool:
                raise ValueError('Choose whether to delete the test results')
            targets = [('test', t['id']) for t in versions]
            if data.get('include_results'):
                targets.extend(('result', r['id']) for r in related)
            title, version_count, related_count = test['title'], len(versions), len(related)
        elif data.get('kind') == 'results':
            ids = data.get('ids')
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 100 or
                    any(not isinstance(rid,str) for rid in ids) or len(set(ids)) != len(ids)):
                raise ValueError('Select one to 100 distinct saved results')
            known = {r['id'] for r in results}
            if any(rid not in known for rid in ids):
                raise ValueError('A selected result no longer exists; refresh the list')
            targets = [('result', rid) for rid in ids]
            title, version_count, related_count = 'Selected results', 0, len(ids)
        else:
            raise ValueError('Choose a test or saved results to delete')
        token = hashlib.sha256(json.dumps(sorted(targets)).encode()).hexdigest()
        return {'kind':data['kind'], 'title':title, 'family_id':family,
                'versions':version_count, 'related_results':related_count,
                'result_count':sum(k=='result' for k,_ in targets), 'token':token,
                'targets':targets}

    def delete_items(self, data):
        with self.lock:
            if self.active:
                raise ValueError('Wait for the running test to finish before deleting items')
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                plan = self.delete_preview(data)
                if not secrets.compare_digest(str(data.get('expected','')),plan['token']):
                    raise ValueError('The items changed. Review the deletion again before confirming')
                batch = secrets.token_hex(12)
                if plan['kind']=='test':
                    description = f'Deleted test “{plan["title"]}” and its {plan["versions"]} saved version(s). '
                    description += (f'Also deleted {plan["result_count"]} saved answer(s).' if data.get('include_results')
                                    else f'Kept {plan["related_results"]} saved answer(s).')
                else:
                    count = plan['result_count']
                    description = f'Deleted {count} saved answer{"s" if count!=1 else ""}.'
                c.execute('INSERT INTO deletion_batch VALUES(?,?,?)',(batch,description,time.time()))
                c.executemany('INSERT INTO deleted VALUES(?,?,?)',[(kind,target,batch) for kind,target in plan['targets']])
            return {'undo_id':batch, 'description':description, 'family_id':plan['family_id']}

    def undo_delete(self, batch):
        if not isinstance(batch,str):
            raise ValueError('Deletion not found')
        with self.lock:
            if self.active:
                raise ValueError('Wait for the running test to finish before restoring items')
            with self.connect() as c:
                if not c.execute('SELECT 1 FROM deleted WHERE batch=?',(batch,)).fetchone():
                    raise ValueError('Deletion already undone or not found')
                c.execute('DELETE FROM deleted WHERE batch=?',(batch,))
            return {'ok':True}

    def state(self):
        with self.connect() as c:
            rows = c.execute("SELECT id,kind,data FROM records WHERE kind IN ('draft','run') ORDER BY rowid DESC").fetchall()
            hidden = set(c.execute('SELECT kind,target FROM deleted'))
            undo = c.execute('SELECT id,description FROM deletion_batch WHERE EXISTS '
                             '(SELECT 1 FROM deleted WHERE batch=deletion_batch.id) ORDER BY created DESC LIMIT 1').fetchone()
        rows = [(i,k,raw) for i,k,raw in rows if not (k=='run' and (rs:=json.loads(raw).get('results')) and
                    all(('result',f'{i}:{index}') in hidden for index in range(len(rs))))]
        return {'items': [{'id': i, 'kind': k, 'title': self.label(k, v := json.loads(raw)),
                           'status': v.get('status'), 'created': v.get('created')}
                          for i, k, raw in rows], 'active': self.active,
                'openrouter_ready': self.connections.status()['openrouter']['configured'],
                'lemonade_url': self.connections.url, 'connections': self.connections.status(),
                'last_deletion':{'id':undo[0],'description':undo[1]} if undo else None}

    @staticmethod
    def label(kind, value):
        """History label; generic draft titles get a content snippet so entries can be told apart."""
        title = value['title']
        if kind == 'draft' and title.strip() in ('', 'Untitled comparison'):
            snippet = ' '.join(str(value.get('content', '')).split())[:40]
            models = ', '.join(sorted({p.get('model', '') for p in value.get('profiles', []) if p.get('model')}))
            title = 'Untitled setup' + (f' · {models}' if models else '') + (f' · “{snippet}”' if snippet else '')
        return title

    def save_test(self, draft, previous=None):
        d = validate(draft)
        if len(d['profiles']) != 1:
            raise ValueError('Use one model configuration when saving a reusable test; its parameters apply to every target model')
        d.pop('test_id', None)
        with self.lock:
            rid = secrets.token_hex(8)
            family, revision = rid, 1
            if previous:
                old = self.get(previous)
                if old.get('kind') != 'test_definition':
                    raise ValueError('Previous test definition not found')
                family = old['family_id']
                revision = max(t['revision'] for t in self.test_definitions() if t['family_id'] == family) + 1
            record = {'kind': 'test_definition', 'id': rid, 'family_id': family, 'revision': revision,
                      'title': d['title'], 'created': time.time(), 'draft': d}
            self.save(rid, 'test', record)
            self.track_model(d['profiles'][0])
            return record

    def test_definitions(self):
        with self.connect() as c:
            return [json.loads(row[0]) for row in c.execute("SELECT data FROM records WHERE kind='test' AND NOT EXISTS "
                    "(SELECT 1 FROM deleted WHERE kind='test' AND target=records.id) ORDER BY rowid DESC")]

    def track_model(self, profile):
        # Validate through the same model/settings contract without dispatching requests.
        validate({'title':'Tracked model','content':'x','instructions':'x','task':'writing',
                  'repeats':1,'seed':0,'timeout':5,'profiles':[profile]})
        identity = model_identity(profile)
        record = {'id': model_id(profile), 'title': profile['model'], **identity}
        self.save('model_' + record['id'], 'model', record)
        return record

    def load_test(self, test_id, target_id):
        test = self.get(test_id)
        if test.get('kind') != 'test_definition':
            raise ValueError('Test definition not found')
        try:
            target = self.get('model_' + target_id)
        except ValueError:
            old = next((r['profile'] for r in self.saved_results() if model_id(r['profile']) == target_id), None)
            if old is None:
                raise ValueError('Tracked model not found') from None
            target = {'id': target_id, **model_identity(old)}
        if target.get('id') != target_id:
            raise ValueError('Tracked model not found')
        d = json.loads(json.dumps(test['draft']))
        p = d['profiles'][0]
        for key in ('provider', 'model', 'url', 'endpoint'):
            p.pop(key, None)
        p.update({k: target[k] for k in ('provider', 'model', 'url', 'endpoint') if k in target})
        p['name'] = target['model']
        d['test_id'] = test_id
        return validate(d)

    def coverage(self):
        tests = self.test_definitions()
        with self.connect() as c:
            models = [json.loads(row[0]) for row in c.execute("SELECT data FROM records WHERE kind='model' ORDER BY rowid")]
        # Include models from older runs without claiming those runs used a saved test definition.
        results = self.saved_results()
        known = {m['id'] for m in models}
        for row in results:
            mid = model_id(row['profile'])
            if mid not in known:
                models.append({'id': mid, 'title': row['profile']['model'], **model_identity(row['profile'])})
                known.add(mid)
        cells = []
        for test in tests:
            for target in models:
                rows = [r for r in results if r.get('test_id') == test['id'] and model_id(r['profile']) == target['id']]
                successful = [r for r in rows if r['status'] == 'done']
                cells.append({'test_id':test['id'], 'model_id':target['id'], 'attempts':len(rows),
                    'results': [r['id'] for r in successful], 'all_results':[r['id'] for r in rows],
                    'status':'results' if successful else 'errors_only' if rows else 'not_run'})
        return {'tests':tests, 'models':models, 'cells':cells}

    def saved_results(self):
        """One stable selectable entry per recorded response, including failed attempts."""
        loaded = []
        with self.connect() as c:
            records = c.execute("SELECT id,data FROM records WHERE kind='run' ORDER BY rowid DESC").fetchall()
            hidden = {row[0] for row in c.execute("SELECT target FROM deleted WHERE kind='result'")}
            for rid, raw in records:
                run = self.hydrate(c, rid, json.loads(raw))
                loaded.append((rid, run))
        items = []
        for rid, run in loaded:
            for index, result in enumerate(run.get('results', [])):
                if f'{rid}:{index}' in hidden:
                    continue
                p = run['draft']['profiles'][result['column']]
                items.append({'id': f'{rid}:{index}', 'run_id': rid, 'title': run['title'],
                    'created': run['created'], 'profile': p, 'task': run['draft']['task'], 'file_type': run['draft'].get('file_type'),
                    'repeat': result['repeat'], 'seed': result['seed'], 'status': result['status'],
                    'seconds': result['seconds'], 'finish_reason': result.get('finish_reason'),
                    'usage': result.get('usage'), 'test_id':run['draft'].get('test_id')})
        return items

    def compare_results(self, ids):
        if (not isinstance(ids, list) or not 2 <= len(ids) <= 6 or
                any(not isinstance(rid, str) for rid in ids) or len(set(ids)) != len(ids)):
            raise ValueError('Select two to six distinct saved results')
        entries = []
        for result_id in ids:
            try:
                rid, index_text = result_id.rsplit(':', 1)
                index = int(index_text)
                run = self.get(rid)
                if index < 0 or run.get('id') != rid:
                    raise ValueError('Invalid saved result')
                result = run['results'][index]
                if result.get('deleted'):
                    raise ValueError('Saved result not found')
                d = run['draft']
                entries.append({'id': result_id, 'run_id': rid, 'title': run['title'],
                    'created': run['created'], 'profile': d['profiles'][result['column']],
                    'task': d['task'], 'file_type': d.get('file_type'), 'instructions': d['instructions'], 'content': d['content'],
                    'context':d.get('context',''), 'criteria':d.get('criteria',''), 'test_id':d.get('test_id'),
                    'result': result})
            except (IndexError, KeyError, ValueError):
                raise ValueError('Saved result not found') from None
        differences = [name for name in ('content', 'instructions', 'task', 'file_type', 'context', 'criteria')
                       if any(e[name] != entries[0][name] for e in entries[1:])]
        return {'kind': 'saved_result_comparison', 'entries': entries, 'differences': differences,
                'same_input': not differences}

    def start(self, draft):
        d = validate(draft)
        if d.get('test_id'):
            definition = self.get(d['test_id'])
            if definition.get('kind') != 'test_definition':
                raise ValueError('Test definition not found')
            template = definition['draft']
            if (any(d.get(k) != template.get(k) for k in TEST_FIELDS) or
                    any(any(p[k] != template['profiles'][0].get(k, CODING_DEFAULT_TOKENS) for k in PARAM_FIELDS) for p in d['profiles'])):
                raise ValueError('Test content or parameters changed; save a new test revision before linking these results')
        with self.lock:
            if self.active:
                raise ValueError('A test is already running')
            if d.get('test_id'):
                self.get(d['test_id']) # Recheck deletion after taking the worker/deletion lock.
            rid = secrets.token_hex(8)
            for p in d['profiles']:
                self.track_model(p)
            jobs = [(i, n, d['seed'] + n) for n in range(d['repeats']) for i in range(len(d['profiles']))]
            random.Random(d['seed']).shuffle(jobs)
            run = {'id': rid, 'title': d['title'], 'created': time.time(), 'status': 'running',
                   'draft': d, 'content_hash': hashlib.sha256(d['content'].encode()).hexdigest(),
                   'jobs': jobs, 'results': [], 'request_limit': len(jobs),
                   'output_token_ceiling': sum(output_cap(d, d['profiles'][i]) for i, _, _ in jobs)}
            self.cancel.clear()
            self.save(rid, 'run', run)
            self.active = rid
            threading.Thread(target=self.execute, args=(run,), daemon=True).start()
            return rid

    def call_cancellable(self, d, p, seed):
        """Run the provider call in a helper thread so Stop need not wait out a slow request."""
        box = {}

        def work():
            try:
                box['ok'] = self.caller(d, p, seed)
            except BaseException as exc:
                box['err'] = exc
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(0.1)
            if self.cancel.is_set() and thread.is_alive():
                raise RequestAbandoned()
        if 'err' in box:
            raise box['err']
        return box['ok']

    def execute(self, run):
        try:
            d = run['draft']
            for i, repeat, seed in run['jobs']:
                if self.cancel.is_set():
                    break
                p = d['profiles'][i]
                result = {'column': i, 'repeat': repeat, 'seed': seed, 'request': request_body(d, p, seed)}
                t0 = time.monotonic()
                abandoned = False
                try:
                    response, body = self.call_cancellable(d, p, seed)
                    choice = response['choices'][0]
                    message = choice.get('message', {})
                    result.update(status='done', text=message.get('content') or '',
                        reasoning=message.get('reasoning_content') or message.get('reasoning') or '',
                        finish_reason=choice.get('finish_reason'), usage=response.get('usage'),
                        model=response.get('model'), provider=response.get('provider'),
                        response_id=response.get('id'), request=body,
                        support='simulated' if p['provider'] == 'demo' else 'requested; effective behavior unverified')
                    if d['task'] == 'coding':
                        result['file'] = build_file(result['text'], d['file_type'])
                except RequestAbandoned:
                    abandoned = True
                    result.update(status='error', text='', usage=None,
                                  error='Stopped while this request was in flight. The provider may still finish it and '
                                        'charge for it; usage is unknown.')
                except Exception as exc:
                    # Never persist arbitrary transport exceptions, which may include credentials.
                    result.update(status='error', text='', usage=None,
                                  error=str(exc) if type(exc) is ValueError else 'Request failed; usage unknown')
                result['seconds'] = time.monotonic() - t0
                run['results'].append(result)
                with self.connect() as c:  # append one row; the full record is written once at the end
                    c.execute('INSERT OR REPLACE INTO run_result VALUES(?,?,?)',
                              (run['id'], len(run['results']) - 1, json.dumps(result)))
                if abandoned:
                    break
            run['status'] = 'cancelled' if self.cancel.is_set() else 'completed'
            if run['status'] == 'completed' and any(r['status'] == 'error' for r in run['results']):
                run['status'] = 'completed_with_errors'
        finally:
            if run['status'] == 'running':
                run['status'] = 'interrupted'
            with self.connect() as c:  # same transaction: final record and removal of the incremental rows
                c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (run['id'], 'run', json.dumps(run)))
                c.execute('DELETE FROM run_result WHERE run_id=?', (run['id'],))
            with self.lock:
                self.active = None


def list_models(connection, key=None):
    """Read the selected service's catalog only; never load a model or infer."""
    if not isinstance(connection, dict):
        raise ValueError('Expected a connection')
    provider = connection.get('provider')
    if provider == 'demo':
        return [{'id': 'demo-model', 'name': 'Demo model'},
                {'id': 'demo-alternative', 'name': 'Demo alternative'}]
    if provider == 'openrouter':
        base = 'https://openrouter.ai/api/v1'
        if key is None:
            key = os.environ.get('OPENROUTER_API_KEY', '')
    elif provider == 'lemonade':
        base = connection.get('url', '')
        if not isinstance(base, str):
            raise ValueError('Enter a Lemonade API base URL')
        u = urlsplit(base)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Lemonade needs an HTTP API base URL without credentials')
        if key is None:
            key = os.environ.get('LEMONADE_API_KEY', '')
    else:
        raise ValueError('Unknown provider')
    headers = {'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    try:
        req = Request(base.rstrip('/') + '/models', headers=headers)
        with build_opener(NoRedirect).open(req, timeout=15) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('Model catalog exceeded 8 MB')
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError('Endpoint did not return a model catalog')
        models = {}
        for row in payload['data']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
                continue
            name = row.get('name')
            models[row['id']] = {'id': row['id'], 'name': name if isinstance(name, str) and name else row['id']}
        return sorted(models.values(), key=lambda m: (m['name'].casefold(), m['id']))
    except HTTPError as e:
        code = e.code
        e.close()
        if code == 401:
            raise ValueError('Model catalog HTTP 401; add or update your API key in Connections') from None
        raise ValueError(f'Model catalog HTTP {code}; check the API base URL and key') from None
    except (URLError, OSError):
        raise ValueError('Could not reach the model catalog; check the endpoint and try Refresh') from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('Endpoint did not return a valid JSON model catalog') from None


def windows_protect(raw, decrypt=False):
    """User-scoped Windows DPAPI; never falls back to plaintext storage."""
    if os.name != 'nt':
        raise ValueError('Remembering keys is supported on Windows; use a session key on this system')
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows could not protect or unlock the saved API keys')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class Connections:
    def __init__(self, directory):
        self.path = Path(directory) / 'connections.json'
        self.lock = threading.RLock()
        self.url = os.environ.get('LEMONADE_BASE_URL', 'http://10.138.6.1:13305/api/v1').rstrip('/')
        self.saved, self.session = {}, {}
        if self.path.exists():
            try:
                record = json.loads(self.path.read_text(encoding='utf-8'))
                self.url = record['lemonade_url']
                if record.get('protected_keys'):
                    self.saved = json.loads(windows_protect(base64.b64decode(record['protected_keys']), True))
            except Exception:
                raise ValueError('Could not unlock Connections settings for this Windows user') from None

    def key(self, connection):
        provider = connection.get('provider')
        if provider not in ('lemonade', 'openrouter'):
            return ''
        with self.lock:
            if provider == 'lemonade' and connection.get('url', self.url).rstrip('/') != self.url:
                return ''  # Never send the configured key to another API base URL.
            return self.session.get(provider, self.saved.get(provider,
                os.environ.get('LEMONADE_API_KEY' if provider == 'lemonade' else 'OPENROUTER_API_KEY', '')))

    def status(self):
        with self.lock:
            result = {'lemonade_url': self.url, 'can_remember': os.name == 'nt'}
            for provider in ('lemonade', 'openrouter'):
                key = self.key({'provider': provider})
                source = 'session' if provider in self.session else 'saved' if provider in self.saved else 'environment'
                result[provider] = {'configured': bool(key), 'source': source if key else 'none'}
            return result

    def update(self, data):
        if not isinstance(data, dict) or data.get('provider') not in ('lemonade', 'openrouter'):
            raise ValueError('Choose Lemonade or OpenRouter')
        provider = data['provider']
        key = data.get('key', '')
        if not isinstance(key, str) or len(key) > 4096 or any(ord(ch) < 32 or ord(ch) > 126 for ch in key):
            raise ValueError('API key must be a single line of printable text')
        key = key.strip()
        with self.lock:
            url = self.url
            if provider == 'lemonade':
                url = data.get('url', self.url)
                u = urlsplit(url)
                if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                    raise ValueError('Enter a valid Lemonade API base URL without credentials')
                url = url.rstrip('/')
            saved, session = dict(self.saved), dict(self.session)
            if data.get('remove'):
                saved.pop(provider, None); session.pop(provider, None)
            elif key:
                if data.get('remember'):
                    if os.name != 'nt':
                        raise ValueError('Use a session key on this system')
                    saved[provider] = key; session.pop(provider, None)
                else:
                    session[provider] = key; saved.pop(provider, None)
            # Reject ambiguous endpoint changes that would forward an existing key elsewhere.
            if url != self.url and not key and not data.get('remove') and self.key({'provider': provider}):
                raise ValueError('Re-enter the key or remove it when changing the Lemonade endpoint')
            protected = base64.b64encode(windows_protect(json.dumps(saved).encode())).decode() if saved else ''
            temp = self.path.with_suffix('.tmp')
            try:
                temp.write_text(json.dumps({'lemonade_url': url, 'protected_keys': protected}), encoding='utf-8')
                os.replace(temp, self.path)
            except OSError:
                raise ValueError('Could not save Connections settings') from None
            self.url, self.saved, self.session = url, saved, session
            return self.status()


def make_server(workbench, port=8811, model_fetcher=None):
    token = secrets.token_urlsafe(32)
    if model_fetcher is None:
        model_fetcher = lambda connection: list_models(connection, key=workbench.connections.key(connection))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, mime='application/json'):
            raw = json.dumps(value).encode() if mime == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            host = self.headers.get('Host', '')
            valid = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
            return host in valid and (not self.headers.get('Origin') or self.headers['Origin'] == 'http://' + host)

        def do_GET(self):
            if not self.allowed():
                return self.reply({'error': 'Local access only'}, 403)
            path = urlsplit(self.path).path
            files = {'/': ('local_ui.html', 'text/html'), '/local_ui.js': ('local_ui.js', 'text/javascript'),
                     '/local_appearance.js': ('local_appearance.js', 'text/javascript'),
                     '/local_ui.css': ('local_ui.css', 'text/css')}
            if path in files:
                name, mime = files[path]
                return self.reply((ROOT / name).read_bytes(), mime=mime)
            if path == '/api/state':
                return self.reply(dict(workbench.state(), token=token))
            if path == '/api/results':
                return self.reply({'results': workbench.saved_results()})
            if path == '/api/tests':
                return self.reply({'tests': workbench.test_definitions()})
            if path == '/api/coverage':
                return self.reply(workbench.coverage())
            if path.startswith('/api/item/'):
                try:
                    return self.reply(workbench.get(path.rsplit('/', 1)[1]))
                except ValueError as e:
                    return self.reply({'error': str(e)}, 404)
            self.reply({'error': 'Not found'}, 404)

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-JUICR-Token', ''), token):
                return self.reply({'error': 'Local session required; refresh the page'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1000000:
                    raise ValueError('Request must be under 1 MB')
                d = json.loads(self.rfile.read(length))
                if self.path == '/api/delete-preview':
                    plan = workbench.delete_preview(d)
                    return self.reply({k:v for k,v in plan.items() if k!='targets'})
                if self.path == '/api/delete':
                    return self.reply(workbench.delete_items(d))
                if self.path == '/api/undo-delete':
                    return self.reply(workbench.undo_delete(d.get('id') if isinstance(d,dict) else None))
                if self.path == '/api/tests/save':
                    return self.reply(workbench.save_test(d['draft'], d.get('previous')))
                if self.path == '/api/models/track':
                    return self.reply(workbench.track_model(d['profile']))
                if self.path == '/api/tests/load':
                    # Preparation only; Run remains a separate explicit action.
                    return self.reply(workbench.load_test(d['test_id'], d['model_id']))
                if self.path == '/api/compare':
                    return self.reply(workbench.compare_results(d.get('ids') if isinstance(d, dict) else None))
                if self.path == '/api/connections':
                    with workbench.lock:
                        if workbench.active:
                            raise ValueError('Wait for the current comparison to finish before changing connections')
                        return self.reply(workbench.connections.update(d))
                if self.path == '/api/test-connection':
                    if not isinstance(d, dict) or d.get('provider') not in ('lemonade', 'openrouter'):
                        raise ValueError('Choose a connection')
                    connection = {'provider': d['provider'], 'url': workbench.connections.url}
                    return self.reply({'count': len(model_fetcher(connection))})
                if self.path == '/api/models':
                    return self.reply({'models': model_fetcher(d)})
                if self.path == '/api/save':
                    d = validate(d)
                    rid = secrets.token_hex(8)
                    workbench.save(rid, 'draft', d)
                    return self.reply({'id': rid})
                if self.path == '/api/run':
                    return self.reply({'id': workbench.start(d)})
                if self.path == '/api/cancel':
                    with workbench.lock:
                        if d.get('id') and d['id'] != workbench.active:
                            raise ValueError('That run is not active; no other run was stopped')
                        workbench.cancel.set()
                    return self.reply({'ok': True})
                return self.reply({'error': 'Not found'}, 404)
            except (ValueError, TypeError, KeyError) as e:
                self.reply({'error': str(e)}, 400)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'cli':
        from juicr_cli import main as cli_main
        return cli_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__, epilog='AI/automation commands: python juicr_local.py cli --help')
    parser.add_argument('--port', type=int, default=8811)
    parser.add_argument('--data', default=str(Path.home() / '.juicr-local'))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = make_server(Workbench(args.data), args.port)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'JUICR: {url}\nData: {args.data}\nPress Ctrl+C to stop.')
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
