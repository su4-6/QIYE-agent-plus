import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from openai import AuthenticationError

from app.config import settings
from app.database import get_connection, init_database
from app.llm import generate_support_plan, is_llm_enabled, llm_status
from app.main import app
from app.model_api import (ModelApiInput, PROVIDERS, public_configuration, resolve_connection,
                           restore_default, save_configuration, test_configuration, validate_endpoint,
                           ModelConnection, generation_options)
from app.security import create_session, password_hash


class ModelApiTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(vars(settings))
        self.directory = tempfile.TemporaryDirectory(dir='work')
        for name, value in {'database_url': str(Path(self.directory.name)/'test.db'), 'app_env': 'test',
                            'embedding_provider': 'disabled', 'llm_provider': 'mimo',
                            'mimo_api_key': 'dedicated-fake-environment-key', 'session_secret': 'test-session-secret-'+'s'*40,
                            'admin_password_hash': password_hash('model-test-password'), 'admin_username': 'admin',
                            'model_api_allowed_base_urls': '', 'max_llm_daily': 100,
                            'low_risk_assistance': True}.items():
            object.__setattr__(settings, name, value)
        init_database()
        self.client = TestClient(app)
        token, self.csrf = create_session()
        self.client.cookies.set('ticket_session', token)
        self.headers = {'X-CSRF-Token': self.csrf}

    def tearDown(self):
        self.client.close()
        for key, value in self.original.items():
            object.__setattr__(settings, key, value)
        self.directory.cleanup()

    def payload(self, version=0, provider='deepseek', **kwargs):
        return ModelApiInput(expected_version=version, provider=provider, base_url=PROVIDERS[provider]['base_url'],
                             model=PROVIDERS[provider]['model'], api_key='fake-deepseek-key', **kwargs)

    def test_encrypted_persistence_metadata_and_restore_environment(self):
        configuration = save_configuration('demo', 'admin', self.payload())
        self.assertEqual(configuration['version'], 1)
        self.assertNotIn('fake-deepseek-key', json.dumps(configuration))
        self.assertNotIn('key_ciphertext', json.dumps(configuration))
        self.assertEqual(resolve_connection().api_key, 'fake-deepseek-key')
        with get_connection() as db:
            ciphertext = db.execute('SELECT key_ciphertext FROM model_api_profiles').fetchone()[0]
            self.assertNotIn('fake-deepseek-key', ciphertext)
        init_database()  # restart/migration must preserve active connection
        self.assertEqual(llm_status()['provider'], 'deepseek')
        restored = restore_default('demo', 'admin', 1)
        self.assertEqual(restored['version'], 2)
        self.assertEqual(restored['source'], 'environment')
        self.assertEqual(resolve_connection().api_key, 'dedicated-fake-environment-key')
        self.assertEqual(resolve_connection().model, settings.mimo_model)

    def test_saved_profiles_reuse_only_their_own_keys_and_preserve_audit(self):
        save_configuration('demo', 'admin', self.payload())
        save_configuration('demo', 'admin', ModelApiInput(expected_version=1, provider='mimo',
            base_url=PROVIDERS['mimo']['base_url'], model='mimo-v2.5-pro'))
        save_configuration('demo', 'admin', ModelApiInput(expected_version=2, provider='deepseek',
            base_url=PROVIDERS['deepseek']['base_url'], model='deepseek-chat'))
        self.assertEqual(resolve_connection().api_key, 'fake-deepseek-key')
        with get_connection() as db:
            rows = db.execute('SELECT * FROM model_api_audit ORDER BY id').fetchall()
        self.assertEqual([r['version'] for r in rows], [1, 2, 3])
        self.assertNotIn('fake-deepseek-key', json.dumps([dict(r) for r in rows]))

    def test_changed_custom_endpoint_requires_new_key(self):
        object.__setattr__(settings, 'model_api_allowed_base_urls', 'https://models.one.example/v1,https://models.two.example/v1')
        save_configuration('demo', 'admin', ModelApiInput(expected_version=0, provider='compatible',
            base_url='https://models.one.example/v1', model='test-model', api_key='custom-test-key'))
        with self.assertRaisesRegex(ValueError, '新的 Key'):
            save_configuration('demo', 'admin', ModelApiInput(expected_version=1, provider='compatible',
                base_url='https://models.two.example/v1', model='test-model'))
        self.assertEqual(resolve_connection().base_url, 'https://models.one.example/v1')

    def test_siliconflow_china_requires_own_key_and_preserves_mimo_profile(self):
        save_configuration('demo', 'admin', ModelApiInput(expected_version=0, provider='mimo',
            base_url=PROVIDERS['mimo']['base_url'], model='mimo-v2.6-flash'))
        candidate=ModelApiInput(expected_version=1,provider='siliconflow_cn',
            base_url=PROVIDERS['siliconflow_cn']['base_url'],model='Qwen/Qwen3-8B')
        with self.assertRaisesRegex(ValueError,'尚未配置 Key'):
            save_configuration('demo','admin',candidate)
        self.assertEqual(resolve_connection().provider,'mimo')
        candidate=ModelApiInput(expected_version=1,provider='siliconflow_cn',
            base_url=PROVIDERS['siliconflow_cn']['base_url'],model='Qwen/Qwen3-8B',api_key='local-siliconflow-fake-key')
        saved=save_configuration('demo','admin',candidate)
        self.assertEqual(resolve_connection().api_key,'local-siliconflow-fake-key')
        self.assertEqual(resolve_connection().driver,'siliconflow')
        self.assertNotIn('local-siliconflow-fake-key',json.dumps(saved))
        save_configuration('demo','admin',ModelApiInput(expected_version=2,provider='mimo',
            base_url=PROVIDERS['mimo']['base_url'],model='mimo-v2.6-flash'))
        self.assertEqual(resolve_connection().api_key,'dedicated-fake-environment-key')

    def test_siliconflow_china_address_binding_and_parameter_scope(self):
        china=PROVIDERS['siliconflow_cn']['base_url']
        for url in ['https://api.siliconflow.com/v1',china+'/chat/completions',
                    'https://api.siliconflow.cn.evil.example/v1']:
            with self.assertRaises(ValueError):validate_endpoint('siliconflow_cn',url)
        for provider in ['siliconflow_cn','compatible']:
            connection=ModelConnection(provider,china,'Qwen/Qwen3-8B','fake')
            self.assertEqual(generation_options(connection,400),{'max_tokens':400,'extra_body':{'enable_thinking':False}})
        self.assertEqual(generation_options(ModelConnection('siliconflow_cn',china,'THUDM/GLM-4-9B-0414','fake'),400),{'max_tokens':400})
        self.assertEqual(generation_options(ModelConnection('deepseek',PROVIDERS['deepseek']['base_url'],'Qwen/Qwen3-8B','fake'),400),{'max_tokens':400})

    def test_siliconflow_probe_disables_thinking_without_activating_candidate(self):
        payload=ModelApiInput(expected_version=0,provider='siliconflow_cn',
            base_url=PROVIDERS['siliconflow_cn']['base_url'],model='Qwen/Qwen3-8B',api_key='local-siliconflow-fake-key')
        good=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        with patch('app.model_api.create_client') as factory:
            client=factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value=good
            self.assertTrue(test_configuration('demo',payload)['passed'])
            arguments=client.chat.completions.create.call_args.kwargs
        self.assertEqual(arguments['extra_body'],{'enable_thinking':False})
        self.assertEqual(arguments['max_tokens'],256)
        self.assertEqual(public_configuration('demo')['version'],0)
        self.assertEqual(resolve_connection().provider,'mimo')

    def test_stale_version_cannot_overwrite_or_restore(self):
        save_configuration('demo', 'admin', self.payload())
        with self.assertRaises(LookupError):
            save_configuration('demo', 'another-admin', self.payload())
        with self.assertRaises(LookupError):
            restore_default('demo', 'another-admin', 0)
        self.assertEqual(resolve_connection().version, 1)

    def test_ssrf_and_provider_key_redirection_rejected_before_network(self):
        addresses = ['http://api.deepseek.com/v1', 'https://127.0.0.1/v1', 'https://169.254.169.254/latest',
                     'https://api.deepseek.com.evil.example/v1', 'https://api.deepseek.com/v1?x=1',
                     'https://u:p@api.deepseek.com/v1', 'https://api.deepseek.com:8443/v1',
                     'https://api.deepseek.com/%2e%2e/v1', 'https://api.deepseek.com/v1/redirect',
                     'https://api.openai.com/v1']
        for address in addresses:
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_endpoint('deepseek', address)
        with self.assertRaises(ValueError):
            validate_endpoint('compatible', 'https://attacker.example/v1')

    def test_malformed_secret_not_reflected_in_validation_response(self):
        raw = self.payload().model_dump(mode='json')
        raw['api_key'] = 'fake-key-secret WITH SPACE'
        response = self.client.put('/api/v1/admin/model-api', json=raw, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(raw['api_key'], response.text)
        self.assertNotIn('input', response.text)

    def test_unauthenticated_employee_and_csrf_cannot_manage_or_test(self):
        raw = {**self.payload().model_dump(mode='json'), 'api_key': 'fake-key'}
        with patch('app.main.test_configuration') as network:
            self.assertEqual(self.client.put('/api/v1/admin/model-api', json=raw).status_code, 403)
            self.assertEqual(self.client.post('/api/v1/admin/model-api/test', json=raw).status_code, 403)
            self.assertEqual(self.client.post('/api/v1/admin/model-api/restore', json={'expected_version':0}).status_code, 403)
            self.client.cookies.clear()
            self.assertEqual(self.client.get('/api/v1/admin/model-api').status_code, 403)
            self.assertEqual(self.client.put('/api/v1/admin/model-api', json=raw, headers=self.headers).status_code, 403)
            network.assert_not_called()

    def test_failed_connection_test_leaves_active_configuration_unchanged(self):
        raw = {**self.payload().model_dump(mode='json'), 'api_key': 'fake-key'}
        with patch('app.main.test_configuration', side_effect=ValueError('供应商拒绝了 API Key')):
            response = self.client.put('/api/v1/admin/model-api', json=raw, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(resolve_connection().source, 'environment')
        self.assertEqual(public_configuration('demo')['version'], 0)

    def test_switch_route_and_connection_test_do_not_echo_credentials(self):
        raw = {**self.payload().model_dump(mode='json'), 'api_key': 'fake-key'}
        with patch('app.main.test_configuration', return_value={'passed':True}) as check:
            tested = self.client.post('/api/v1/admin/model-api/test', json=raw, headers=self.headers)
            self.assertEqual(tested.status_code, 200)
            self.assertEqual(public_configuration('demo')['version'], 0)
            saved = self.client.put('/api/v1/admin/model-api', json=raw, headers=self.headers)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(check.call_count, 2)
        self.assertEqual(saved.json()['active']['provider'], 'deepseek')
        self.assertNotIn('fake-key', saved.text)
        self.assertEqual(self.client.get('/health').json()['llm_provider'], 'deepseek')

    def test_structured_probe_and_paid_call_quota(self):
        good = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
        with patch('app.model_api.create_client') as factory:
            client = factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value = good
            self.assertTrue(test_configuration('demo', self.payload())['passed'])
            args = client.chat.completions.create.call_args.kwargs
            self.assertEqual(args['model'], 'deepseek-chat')
            self.assertEqual(args['response_format'], {'type':'json_object'})
            client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='普通文本'))])
            with self.assertRaises(ValueError):
                test_configuration('demo', self.payload())
        with patch('app.security.use_quota', return_value=False), patch('app.model_api.create_client') as factory:
            with self.assertRaises(OverflowError):
                test_configuration('demo', self.payload())
            factory.assert_not_called()

    def test_upstream_error_body_cannot_disclose_key(self):
        response = httpx.Response(401, request=httpx.Request('POST', 'https://api.deepseek.com/v1/chat/completions'))
        error = AuthenticationError('fake-deepseek-key was invalid', response=response, body={'key':'fake-deepseek-key'})
        with patch('app.model_api.create_client') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.side_effect = error
            with self.assertRaises(ValueError) as caught:
                test_configuration('demo', self.payload())
        self.assertNotIn('fake-deepseek-key', str(caught.exception))

    def test_tenant_scoped_profiles_and_encryption_binding(self):
        save_configuration('demo', 'admin', self.payload())
        self.assertEqual(resolve_connection('other').source, 'environment')
        with get_connection() as db:
            db.execute("UPDATE model_api_profiles SET base_url='https://api.openai.com/v1'")
        with self.assertRaises(ValueError):
            resolve_connection()
        self.assertFalse(is_llm_enabled())
        restore_default('demo', 'admin', 1)
        self.assertTrue(is_llm_enabled())

    def test_mid_request_switch_keeps_draft_and_review_on_one_connection(self):
        save_configuration('demo', 'admin', self.payload())
        draft = {'decision':'advise', 'steps':[{'text':'纸盒为空时放入平整纸张。','source_ids':['7:1']}],
                 'check_result':'检查能否恢复打印。'}
        outputs = [draft, {'passed':True}]
        calls=[]
        def respond(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                restore_default('demo', 'admin', 1)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(outputs.pop(0))))])
        with patch('app.llm.OpenAI') as factory, patch('app.llm.validate_citations',return_value=[7]), patch('app.security.use_quota',return_value=True):
            factory.return_value.chat.completions.create.side_effect=respond
            text,citations = generate_support_plan('打印机缺纸','纸盒是空的。','硬件与办公设备',
                {'7:1':{'chunk_id':7,'text':'纸盒为空时放入平整纸张。'}},[], 'demo', [])
            self.assertEqual(factory.call_args.kwargs['api_key'], 'fake-deepseek-key')
        self.assertTrue(text)
        self.assertEqual(citations, [7])
        self.assertEqual([r['model'] for r in calls], ['deepseek-chat', 'deepseek-chat'])
        self.assertEqual(resolve_connection().provider, 'mimo')

    def test_existing_version7_database_is_backed_up_and_tickets_preserved(self):
        with get_connection() as db:
            db.execute('PRAGMA user_version=7')
            columns = [r['name'] for r in db.execute('PRAGMA table_info(tickets)')]
            snapshot = db.execute('SELECT * FROM tickets').fetchall()
        init_database()
        with get_connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 8)
            self.assertEqual([r['name'] for r in db.execute('PRAGMA table_info(tickets)')], columns)
            self.assertEqual(db.execute('SELECT * FROM tickets').fetchall(), snapshot)
        self.assertTrue(list((Path(self.directory.name)/'backups').glob('*.db')))


if __name__ == '__main__':
    unittest.main()
