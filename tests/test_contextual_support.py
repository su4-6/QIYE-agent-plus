import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.assistance import validate_plan
from app.config import settings
from app.llm import generate_support_plan, last_generation
from app.model_api import ModelConnection


class ContextualSupportTest(unittest.TestCase):
    def test_fast_draft_still_requires_independent_review(self):
        draft={'decision':'advise','understanding':'纸盒为空。','steps':[{'text':'放入平整纸张。','source_ids':['7:1']}],
               'check_result':'观察缺纸提示是否消失。'}
        outputs=[draft,{'passed':True,'reason':'操作依据与当前事实一致。'}]
        calls=[]
        def respond(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(outputs.pop(0),ensure_ascii=False)))])
        with patch('app.llm.OpenAI') as client, patch('app.llm.validate_citations',return_value=[7]), patch('app.security.use_quota',return_value=True):
            client.return_value.chat.completions.create.side_effect=respond
            text,citations=generate_support_plan('打印机缺纸','纸盒是空的。','硬件与办公设备',
                {'7:1':{'chunk_id':7,'text':'纸盒为空时放入平整纸张。'}},[],'demo',[],
                connection=ModelConnection('mimo','https://api.xiaomimimo.com/v1','mimo-v2.6-flash','test-only'))
        self.assertTrue(text);self.assertEqual(citations,[7]);self.assertEqual(len(calls),2)
        self.assertTrue(all(c['extra_body']['thinking']['type']=='disabled' for c in calls))
        self.assertEqual(calls[0]['max_completion_tokens'],1800)
        self.assertEqual(calls[1]['max_completion_tokens'],400)
        self.assertLessEqual(calls[0]['timeout'],18)
        self.assertLessEqual(calls[1]['timeout'],12)
        self.assertEqual([c['stage'] for c in last_generation.get()['model_calls']],['draft','review'])

    def test_expired_shared_budget_never_publishes_unreviewed_draft(self):
        draft={'decision':'advise','understanding':'纸盒为空。','steps':[{'text':'放入平整纸张。','source_ids':['7:1']}],
               'check_result':'观察缺纸提示是否消失。'}
        clock=[0.0]
        def respond(**kwargs):
            clock[0]=100.0
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(draft,ensure_ascii=False)))])
        with patch('app.llm.time.perf_counter',side_effect=lambda:clock[0]), patch('app.llm.OpenAI') as client, \
             patch('app.llm.validate_citations',return_value=[7]), patch('app.security.use_quota',return_value=True):
            client.return_value.chat.completions.create.side_effect=respond
            text,citations=generate_support_plan('打印机缺纸','纸盒是空的。','硬件与办公设备',
                {'7:1':{'chunk_id':7,'text':'纸盒为空时放入平整纸张。'}},[],'demo',[],
                connection=ModelConnection('mimo','https://api.xiaomimimo.com/v1','mimo-v2.6-flash','test-only'))
            self.assertEqual(client.return_value.chat.completions.create.call_count,1)
        self.assertIsNone(text);self.assertEqual(citations,[])
        self.assertFalse(last_generation.get()['answer_validation']['passed'])
        self.assertEqual(last_generation.get()['answer_validation']['reason'],'support_time_budget_exceeded')

    def test_unknown_sources_and_unsafe_steps_cannot_be_published(self):
        catalog={'7:1':{'chunk_id':7,'text':'放入平整纸张。'}}
        for text, ids in [('放入纸张。',['9:1']),('关闭防火墙。',['7:1'])]:
            with self.assertRaises(ValueError):
                validate_plan({'decision':'advise','steps':[{'text':text,'source_ids':ids}],
                               'check_result':'检查能否打印。'},catalog)

    def test_official_self_service_guidance_is_not_a_request_for_secrets(self):
        catalog={'7:1':{'chunk_id':7,'text':'普通员工使用官方门户自助重置密码。'}}
        result=validate_plan({'decision':'advise','steps':[{'text':'通过企业官方自助门户重置密码。','source_ids':['7:1']}],
                              'check_result':'在官方门户确认是否恢复登录。'},catalog)
        self.assertEqual(result['decision'],'advise')
        with self.assertRaises(ValueError):
            validate_plan({'decision':'clarify','questions':['请把账号密码发给我？']},catalog)

    def test_independent_rejection_does_not_publish_repeated_advice(self):
        old=dict(vars(settings))
        object.__setattr__(settings,'llm_provider','mimo')
        object.__setattr__(settings,'mimo_api_key','test-only')
        draft={'decision':'advise','understanding':'装纸后仍失败。',
               'steps':[{'text':'请再次装纸。','source_ids':['7:1']}],'check_result':'重试打印。'}
        rejection={'passed':False,'reason':'员工已经装纸并明确报告无效，不应重复。'}
        outputs=[draft,rejection,draft,rejection]
        prompts=[]
        def respond(**kwargs):
            prompts.append(kwargs['messages'][1]['content'])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(outputs.pop(0),ensure_ascii=False)))])
        try:
            with patch('app.llm.OpenAI') as client, patch('app.llm.validate_citations',return_value=[7]), patch('app.security.use_quota',return_value=True):
                client.return_value.chat.completions.create.side_effect=respond
                text, citations=generate_support_plan('打印机缺纸','已经放纸，重试仍失败。','硬件与办公设备',
                    {'7:1':{'chunk_id':7,'text':'纸盒为空时放入平整纸张。'}},[], 'demo',
                    [{'role':'ai','text':'请放入纸张。'},{'role':'employee','text':'已经放纸，重试仍失败。'}])
            self.assertIsNone(text);self.assertEqual(citations,[])
            self.assertFalse(last_generation.get()['answer_validation']['passed'])
            self.assertIn('已经放纸，重试仍失败。',prompts[0])
            self.assertEqual(len(prompts),4)
        finally:
            for key,value in old.items():object.__setattr__(settings,key,value)


if __name__=='__main__':unittest.main()
