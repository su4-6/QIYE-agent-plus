import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.assistance import validate_plan
from app.config import settings
from app.llm import generate_support_plan, last_generation


class ContextualSupportTest(unittest.TestCase):
    def test_unknown_sources_and_unsafe_steps_cannot_be_published(self):
        catalog={'7:1':{'chunk_id':7,'text':'放入平整纸张。'}}
        for text, ids in [('放入纸张。',['9:1']),('关闭防火墙。',['7:1'])]:
            with self.assertRaises(ValueError):
                validate_plan({'decision':'advise','steps':[{'text':text,'source_ids':ids}],
                               'check_result':'检查能否打印。'},catalog)

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
