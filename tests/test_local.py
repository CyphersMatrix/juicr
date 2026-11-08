"""Local workbench tests: all inference is injected, never networked to a provider."""
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from io import BytesIO
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from juicr_local import Workbench, Connections, validate, request_body, make_server, list_models


def draft():
    p = dict(name='A', provider='demo', model='fake', settings={'temperature': .7},
             writing_output_tokens=100, extraction_output_tokens=50)
    return dict(title='Example', content='Identical source', instructions='Extract facts', task='writing',
                repeats=2, seed=17, timeout=5, profiles=[p, dict(copy.deepcopy(p), name='B')])


def wait(w):
    end = time.monotonic() + 5
    while w.active and time.monotonic() < end:
        time.sleep(.01)
    if w.active:
        raise AssertionError('Worker did not finish')


def delete(w, request):
    preview=w.delete_preview(request)
    return w.delete_items(dict(request,expected=preview['token']))


class LocalTests(unittest.TestCase):
    def test_delete_result_keeps_stable_ids_hides_payload_and_undo_survives_restart(self):
        def fake(d,p,seed):
            return {'choices':[{'message':{'content':f'unique-answer-{seed}'}}]},request_body(d,p,seed)
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp,fake);d=draft();d['profiles']=d['profiles'][:1];d['repeats']=3
            test=w.save_test(d);target=w.track_model(d['profiles'][0]);rid=w.start(w.load_test(test['id'],target['id']));wait(w)
            original=w.get(rid);ids=[row['id'] for row in w.saved_results()]
            deleted_text=original['results'][1]['text']
            batch=delete(w,{'kind':'results','ids':[ids[1]]})
            self.assertEqual([row['id'] for row in w.saved_results()],[ids[0],ids[2]])
            self.assertNotIn(deleted_text,json.dumps(w.get(rid)))
            self.assertTrue(w.get(rid)['results'][1]['deleted'])
            with self.assertRaises(ValueError):w.compare_results([ids[0],ids[1]])
            self.assertEqual(len(w.compare_results([ids[0],ids[2]])['entries']),2)
            self.assertEqual(len(w.coverage()['cells'][0]['results']),2)
            restored=Workbench(tmp,fake)
            self.assertEqual(restored.state()['last_deletion']['id'],batch['undo_id'])
            self.assertEqual(len(restored.saved_results()),2)
            restored.undo_delete(batch['undo_id'])
            self.assertEqual(restored.get(rid),original)
            self.assertEqual([r['id'] for r in restored.saved_results()],ids)

    def test_delete_test_all_versions_keep_or_delete_results_and_preserve_other_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp);d=draft();d['profiles']=d['profiles'][:1];d['repeats']=1
            first=w.save_test(d);target=w.track_model(d['profiles'][0]);runs=[]
            runs.append(w.start(w.load_test(first['id'],target['id'])));wait(w)
            d['content']='Changed test version';second=w.save_test(d,first['id'])
            runs.append(w.start(w.load_test(second['id'],target['id'])));wait(w)
            other=w.save_test(dict(d,title='Unrelated test'))
            w.start(w.load_test(other['id'],target['id']));wait(w)
            w.start(d);wait(w) # Same title without a saved-test link must not be guessed into the cascade.
            old_rows=w.saved_results()
            plan=w.delete_preview({'kind':'test','id':second['id']})
            self.assertEqual(plan['versions'],2);self.assertEqual(plan['related_results'],2);self.assertEqual(plan['result_count'],0)
            kept=delete(w,{'kind':'test','id':second['id']})
            self.assertEqual([t['id'] for t in w.test_definitions()],[other['id']])
            self.assertEqual(w.saved_results(),old_rows)
            with self.assertRaises(ValueError):w.load_test(first['id'],target['id'])
            w.undo_delete(kept['undo_id'])
            cascade=delete(w,{'kind':'test','id':first['id'],'include_results':True})
            self.assertEqual(len(w.saved_results()),2)
            self.assertEqual([t['id'] for t in w.test_definitions()],[other['id']])
            for rid in runs:
                with self.assertRaises(ValueError):w.get(rid)
                self.assertNotIn(rid,[i['id'] for i in w.state()['items']])
            w.undo_delete(cascade['undo_id'])
            self.assertEqual(w.saved_results(),old_rows)
            self.assertEqual(w.get(first['id']),first);self.assertEqual(w.get(second['id']),second)

    def test_deletion_batches_do_not_restore_earlier_deleted_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp);d=draft();d['profiles']=d['profiles'][:1]
            test=w.save_test(d);target=w.track_model(d['profiles'][0]);w.start(w.load_test(test['id'],target['id']));wait(w)
            ids=[r['id'] for r in w.saved_results()]
            first=delete(w,{'kind':'results','ids':[ids[0]]})
            second=delete(w,{'kind':'test','id':test['id'],'include_results':True})
            w.undo_delete(second['undo_id'])
            self.assertEqual([r['id'] for r in w.saved_results()],[ids[1]])
            w.undo_delete(first['undo_id'])
            self.assertEqual([r['id'] for r in w.saved_results()],ids)

    def test_delete_guard_stale_preview_and_invalid_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp);d=draft();d['profiles']=d['profiles'][:1];d['repeats']=1
            test=w.save_test(d);target=w.track_model(d['profiles'][0]);loaded=w.load_test(test['id'],target['id'])
            w.start(loaded);wait(w)
            request={'kind':'test','id':test['id'],'include_results':True}
            preview=w.delete_preview(request)
            w.start(loaded);wait(w)
            with self.assertRaises(ValueError):w.delete_items(dict(request,expected=preview['token']))
            self.assertEqual(len(w.saved_results()),2)
            w.active='running'
            with self.assertRaises(ValueError):delete(w,request)
            w.active=None
            result_id=w.saved_results()[0]['id']
            for invalid in [None,{}, {'kind':'test','id':[]}, {'kind':'results','ids':[]},
                            {'kind':'results','ids':[result_id,result_id]}, {'kind':'results','ids':['missing:0']},
                            {'kind':'test','id':test['id'],'include_results':'yes'}]:
                with self.assertRaises(ValueError):w.delete_preview(invalid)
            batch=delete(w,request)
            w.active='running'
            with self.assertRaises(ValueError):w.undo_delete(batch['undo_id'])
            w.active=None;w.undo_delete(batch['undo_id'])
            with self.assertRaises(ValueError):w.undo_delete(batch['undo_id'])

    def test_reusable_definition_coverage_quick_load_and_revision_history(self):
        calls=[]
        def fake(d,p,seed):
            calls.append(request_body(d,p,seed))
            return {'choices':[{'message':{'content':'Synthetic response'},'finish_reason':'stop'}]},calls[-1]
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp,fake);d=draft();d['profiles']=d['profiles'][:1];d['repeats']=1
            d.update(context='Saved reference context',criteria='Use only supplied facts')
            test=w.save_test(d)
            first=w.track_model(d['profiles'][0])
            other=copy.deepcopy(d['profiles'][0]);other['model']='another-model';other['settings']={'temperature':1.5}
            second=w.track_model(other)
            self.assertEqual([c['status'] for c in w.coverage()['cells']],['not_run','not_run'])
            loaded=w.load_test(test['id'],second['id'])
            self.assertEqual(len(calls),0)
            self.assertEqual(loaded['profiles'][0]['model'],'another-model')
            self.assertEqual(loaded['profiles'][0]['settings'],d['profiles'][0]['settings'])
            self.assertEqual(loaded['context'],d['context']);self.assertEqual(loaded['criteria'],d['criteria'])
            w.start(loaded);wait(w);w.start(loaded);wait(w)
            cell=next(c for c in w.coverage()['cells'] if c['model_id']==second['id'])
            self.assertEqual(cell['status'],'results');self.assertEqual(len(cell['results']),2)
            self.assertEqual(w.coverage()['cells'][0]['status'],'not_run')
            self.assertIn('Requirements:',calls[0]['messages'][0]['content'])
            self.assertEqual(calls[0]['messages'][1]['content'],'Context:\nSaved reference context')
            original=copy.deepcopy(test)
            changed=copy.deepcopy(loaded);changed['context']='Different context'
            with self.assertRaises(ValueError):w.start(changed)
            changed=copy.deepcopy(loaded);changed['profiles'][0]['settings']['temperature']=.9
            with self.assertRaises(ValueError):w.start(changed)
            revision=w.save_test(changed,test['id'])
            self.assertEqual(revision['revision'],2);self.assertEqual(revision['family_id'],test['family_id'])
            self.assertEqual(w.get(test['id']),original)
            self.assertTrue(all(c['status']=='not_run' for c in w.coverage()['cells'] if c['test_id']==revision['id']))
            restored=Workbench(tmp,fake)
            self.assertEqual(len(restored.test_definitions()),2)
            compare=restored.compare_results(cell['results'])
            self.assertTrue(compare['same_input']);self.assertEqual(len(calls),2)

    def test_coverage_distinguishes_failed_attempts_provider_identity_and_legacy_runs(self):
        def failed(d,p,s):raise ValueError('Synthetic failure')
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp,failed);d=draft();d['profiles']=d['profiles'][:1];d['repeats']=1
            legacy=w.start(d);wait(w)
            test=w.save_test(d)
            target=w.track_model(d['profiles'][0])
            other=copy.deepcopy(d['profiles'][0]);other.update(provider='openrouter',endpoint='pinned-provider')
            cloud=w.track_model(other)
            self.assertNotEqual(target['id'],cloud['id'])
            loaded=w.load_test(test['id'],target['id']);w.start(loaded);wait(w)
            cell=next(c for c in w.coverage()['cells'] if c['model_id']==target['id'])
            self.assertEqual(cell['attempts'],1) # Unlinked older run does not count.
            self.assertEqual(cell['status'],'errors_only');self.assertEqual(cell['results'],[])
            self.assertEqual(next(c for c in w.coverage()['cells'] if c['model_id']==cloud['id'])['status'],'not_run')
            w.caller=lambda d,p,s:({'choices':[{'message':{'content':'ok'}}]},request_body(d,p,s))
            new=w.load_test(test['id'],cloud['id']);self.assertEqual(new['profiles'][0]['endpoint'],'pinned-provider')
            with self.assertRaises(ValueError):w.load_test(test['id'],'missing-model')
            with self.assertRaises(ValueError):w.load_test(legacy,target['id'])

    def test_single_model_results_accumulate_and_compare_without_inference(self):
        calls=[]
        def fake(d,p,seed):
            calls.append(seed)
            return {'choices':[{'message':{'content':f'Answer {len(calls)}'},'finish_reason':'stop'}]},request_body(d,p,seed)
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp,fake)
            d=draft();d['profiles']=d['profiles'][:1];d['repeats']=1
            first=w.start(d);wait(w)
            first_run=w.get(first)
            self.assertEqual(first_run['request_limit'],1)
            self.assertEqual(len(first_run['results']),1)
            first_result=w.saved_results()[0]['id']
            d['profiles'][0]['settings']['temperature']=.2
            second=w.start(d);wait(w)
            restored=Workbench(tmp,fake)
            rows=restored.saved_results()
            self.assertEqual(len(rows),2)
            self.assertEqual(w.get(first),first_run)
            comparison=restored.compare_results([first_result,rows[0]['id']])
            self.assertTrue(comparison['same_input'])
            self.assertEqual([e['result']['text'] for e in comparison['entries']],['Answer 1','Answer 2'])
            self.assertEqual([e['profile']['settings']['temperature'] for e in comparison['entries']],[.7,.2])
            self.assertEqual(len(calls),2)
            d['content']='Changed content';d['instructions']='Changed instructions';d['task']='extraction'
            restored.start(d);wait(restored)
            comparison=restored.compare_results([first_result,restored.saved_results()[0]['id']])
            self.assertFalse(comparison['same_input'])
            self.assertEqual(comparison['differences'],['content','instructions','task'])
            self.assertEqual(len(calls),3)

    def test_saved_result_comparison_rejects_missing_duplicate_or_invalid_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workbench(tmp);d=draft();d['profiles']=d['profiles'][:1]
            rid=w.start(d);wait(w)
            a,b=[row['id'] for row in w.saved_results()]
            for ids in [None,[],[a],[a,a],[a,1],[a,'missing:0'],[a,rid+':-1'],[a,rid+':999'],[a,'invalid']]:
                with self.assertRaises(ValueError):w.compare_results(ids)
            self.assertEqual(len(w.compare_results([b,a])['entries']),2)
            d['profiles']=[]
            with self.assertRaises(ValueError):validate(d)

    def test_session_keys_not_persisted_returned_or_forwarded_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'LEMONADE_API_KEY':'', 'OPENROUTER_API_KEY':''}):
            w = Workbench(tmp)
            status = w.connections.update({'provider':'lemonade', 'key':'session-secret', 'remember':False})
            self.assertEqual(status['lemonade'], {'configured':True,'source':'session'})
            self.assertEqual(w.connections.key({'provider':'lemonade','url':w.connections.url}), 'session-secret')
            self.assertEqual(w.connections.key({'provider':'lemonade','url':'http://other.test/v1'}), '')
            self.assertNotIn('session-secret', json.dumps(w.state()))
            self.assertNotIn('session-secret', w.connections.path.read_text())
            self.assertFalse(Connections(tmp).status()['lemonade']['configured'])
            w.connections.update({'provider':'lemonade','key':''})
            self.assertEqual(w.connections.key({'provider':'lemonade'}), 'session-secret')
            with self.assertRaises(ValueError):
                w.connections.update({'provider':'lemonade','url':'http://other.test/v1'})
            w.connections.update({'provider':'lemonade','remove':True})
            self.assertFalse(w.connections.status()['lemonade']['configured'])

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_remembered_keys_encrypted_and_reloaded_without_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY':''}):
            c = Connections(tmp)
            c.update({'provider':'openrouter','key':'remembered-private-key','remember':True})
            self.assertNotIn('remembered-private-key', c.path.read_text())
            restored = Connections(tmp)
            self.assertEqual(restored.key({'provider':'openrouter'}), 'remembered-private-key')
            self.assertEqual(restored.status()['openrouter']['source'], 'saved')
            restored.update({'provider':'openrouter','remove':True})
            self.assertFalse(Connections(tmp).status()['openrouter']['configured'])

    def test_inference_uses_configured_session_key_without_exporting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp)
            w.connections.update({'provider':'openrouter','key':'inference-private-key'})
            d = draft(); d['profiles'][0]['provider'] = 'openrouter'
            with patch('juicr_local.infer', return_value=({}, {})) as infer_mock:
                w.caller(d,d['profiles'][0],17)
                self.assertEqual(infer_mock.call_args.kwargs['key'], 'inference-private-key')
            self.assertNotIn('inference-private-key', json.dumps(d))

    def test_catalog_get_normalizes_models_and_keeps_keys_server_side(self):
        payload = {'data': [{'id': 'b', 'name': 'Beta'}, {'id': 'a', 'name': 'Alpha'},
                            {'id': 'a', 'name': 'Alpha'}, {'id': None}, 'invalid']}
        with patch.dict('os.environ', {'LEMONADE_API_KEY': 'private-key'}), patch('juicr_local.build_opener') as opener:
            opener.return_value.open.return_value = BytesIO(json.dumps(payload).encode())
            models = list_models({'provider': 'lemonade', 'url': 'http://localhost:13305/api/v1/'})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'http://localhost:13305/api/v1/models')
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer private-key')
            self.assertEqual([m['id'] for m in models], ['a', 'b'])
            self.assertNotIn('private-key', json.dumps(models))
        with patch('juicr_local.build_opener') as opener:
            opener.return_value.open.return_value = BytesIO(b'{"data": []}')
            self.assertEqual(list_models({'provider': 'openrouter'}), [])
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url,
                             'https://openrouter.ai/api/v1/models')

    def test_catalog_errors_and_invalid_connections(self):
        with patch('juicr_local.build_opener') as opener:
            for raw in [b'not json', b'{"error": "private-key"}']:
                opener.return_value.open.return_value = BytesIO(raw)
                with self.assertRaises(ValueError) as e: list_models({'provider': 'openrouter'})
                self.assertNotIn('private-key', str(e.exception))
            error = HTTPError('https://example.test', 401, 'secret', {}, BytesIO(b'private-key'))
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(ValueError, 'HTTP 401'): list_models({'provider': 'openrouter'})
        for connection in [None, {'provider': 'bad'}, {'provider':'lemonade','url':'http://user:key@localhost/v1'}]:
            with self.assertRaises(ValueError): list_models(connection)
        self.assertEqual(len(list_models({'provider': 'demo'})), 2)

    def test_translation_and_task_caps(self):
        d = draft(); p = d['profiles'][0]
        p.update(provider='openrouter', endpoint='provider-slug')
        p['settings']['repeat_penalty'] = 1.1
        b = request_body(d, p, 17)
        self.assertEqual(b['repetition_penalty'], 1.1)
        self.assertNotIn('repeat_penalty', b)
        self.assertEqual(b['max_tokens'], 100)
        self.assertEqual(b['provider'], dict(require_parameters=True, allow_fallbacks=False, only=['provider-slug']))
        d['task'] = 'extraction'
        self.assertEqual(request_body(d, p, 17)['max_tokens'], 50)

    def test_validation_rejects_invalid_and_unknown_controls(self):
        for val in [True, float('nan'), 3, '0.7']:
            d = draft(); d['profiles'][0]['settings']['temperature'] = val
            with self.assertRaises(ValueError): validate(d)
        d = draft(); d['profiles'][0]['settings']['ignored_field'] = 2
        with self.assertRaises(ValueError): validate(d)

    def test_exact_shared_content_paired_seeds_and_durable_results(self):
        calls = []
        def fake(d, p, seed):
            b = request_body(d, p, seed); calls.append(b)
            return {'choices': [{'message': {'content': '<script>untrusted</script>'}, 'finish_reason': 'length'}],
                    'usage': {'prompt_tokens': 7, 'completion_tokens': 100, 'cost': .01}, 'provider': 'fake'}, b
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp, fake); rid = w.start(draft()); wait(w)
            run = Workbench(tmp).get(rid)
            self.assertEqual(run['status'], 'completed')
            self.assertEqual(len(calls), 4)
            self.assertEqual(run['output_token_ceiling'], 400)
            self.assertEqual([b['messages'] for b in calls], [calls[0]['messages']]*4)
            self.assertEqual(sorted(b['seed'] for b in calls), [17, 17, 18, 18])
            self.assertEqual(run['results'][0]['finish_reason'], 'length')

    def test_cancel_stops_queue_and_does_not_retry_active(self):
        started, release = threading.Event(), threading.Event()
        def fake(d, p, seed):
            started.set(); release.wait(3)
            raise OSError('private-key-must-not-be-saved')
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp, fake); rid = w.start(draft())
            self.assertTrue(started.wait(2))
            with self.assertRaises(ValueError): w.start(draft())
            w.cancel.set(); release.set(); wait(w)
            run = w.get(rid)
            self.assertEqual(run['status'], 'cancelled')
            self.assertEqual(len(run['results']), 1)
            self.assertIsNone(run['results'][0]['usage'])
            self.assertNotIn('private-key', json.dumps(run))

    def test_restart_marks_interrupted_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Workbench(tmp)
            w.save('old', 'run', {'title': 'Old', 'status': 'running'})
            self.assertEqual(Workbench(tmp).get('old')['status'], 'interrupted')

    def test_local_server_guard_and_save_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetched = []
            def fetch_catalog(connection):
                fetched.append(connection)
                return [{'id': 'fake', 'name': 'Fake model'}]
            w = Workbench(tmp); server = make_server(w, 0, model_fetcher=fetch_catalog)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/api/state') as r: token = json.load(r)['token']
                with urlopen(base+'/api/results') as r:self.assertEqual(json.load(r)['results'],[])
                with urlopen(base+'/api/coverage') as r:self.assertEqual(json.load(r)['tests'],[])
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/compare',data=b'{"ids":[]}'))
                self.assertEqual(error.exception.code,403);error.exception.close()
                body = json.dumps(draft()).encode()
                for headers in [ {'Content-Type':'application/json'},
                    {'X-JUICR-Token':token, 'Origin':'https://external.example'},
                    {'X-JUICR-Token':token, 'Host':'external.example'} ]:
                    with self.assertRaises(HTTPError) as e:
                        urlopen(Request(base+'/api/save', data=body, headers=headers))
                    self.assertEqual(e.exception.code, 403)
                    e.exception.close()
                with urlopen(Request(base+'/api/save', data=body, headers={'X-JUICR-Token':token})) as r:
                    rid = json.load(r)['id']
                self.assertEqual(w.get(rid)['content'], 'Identical source')
                definition=draft();definition['profiles']=definition['profiles'][:1]
                test_body=json.dumps({'draft':definition}).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/tests/save',data=test_body))
                self.assertEqual(error.exception.code,403);error.exception.close()
                def post(path,data):
                    with urlopen(Request(base+path,data=json.dumps(data).encode(),headers={'X-JUICR-Token':token})) as r:return json.load(r)
                test=post('/api/tests/save',{'draft':definition})
                target=post('/api/models/track',{'profile':definition['profiles'][0]})
                loaded=post('/api/tests/load',{'test_id':test['id'],'model_id':target['id']})
                self.assertEqual(loaded['test_id'],test['id']);self.assertIsNone(w.active)
                for path in ['/api/delete-preview','/api/delete','/api/undo-delete']:
                    with self.assertRaises(HTTPError) as error:urlopen(Request(base+path,data=b'{}'))
                    self.assertEqual(error.exception.code,403);error.exception.close()
                request={'kind':'test','id':test['id']}
                preview=post('/api/delete-preview',request)
                self.assertNotIn('targets',preview)
                batch=post('/api/delete',dict(request,expected=preview['token']))
                self.assertEqual(w.test_definitions(),[])
                post('/api/undo-delete',{'id':batch['undo_id']})
                self.assertEqual(w.test_definitions()[0]['id'],test['id'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/models', data=b'{"provider":"demo"}'))
                error.exception.close()
                self.assertEqual(fetched, [])
                with urlopen(Request(base+'/api/models', data=b'{"provider":"demo"}',
                                     headers={'X-JUICR-Token':token})) as r:
                    self.assertEqual(json.load(r)['models'][0]['id'], 'fake')
                self.assertEqual(fetched, [{'provider': 'demo'}])
                self.assertIsNone(w.active)
                key_body = json.dumps({'provider':'openrouter', 'key':'api-test-key'}).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/connections', data=key_body))
                self.assertEqual(error.exception.code, 403); error.exception.close()
                with urlopen(Request(base+'/api/connections', data=key_body, headers={'X-JUICR-Token':token})) as r:
                    raw = r.read()
                    self.assertNotIn(b'api-test-key', raw)
                    self.assertTrue(json.loads(raw)['openrouter']['configured'])
                with urlopen(Request(base+'/api/test-connection', data=b'{"provider":"openrouter"}',
                                     headers={'X-JUICR-Token':token})) as r:
                    self.assertEqual(json.load(r)['count'], 1)
                w.active = 'fake-running-comparison'
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/connections', data=key_body, headers={'X-JUICR-Token':token}))
                self.assertEqual(error.exception.code, 400); error.exception.close()
                w.active = None
                with urlopen(base) as r:
                    self.assertIn(b'<title>JUICR', r.read())
                with urlopen(base+'/local_appearance.js') as r:
                    self.assertTrue(r.headers['Content-Type'].startswith('text/javascript'))
                    self.assertIn(b'juicr.appearance.v1',r.read())
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
