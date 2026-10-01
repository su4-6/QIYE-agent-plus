import json,tempfile,unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import settings
from app.main import app
from app.database import get_connection
from app.knowledge import import_document
from app.security import password_hash
from app.tools import create_it_ticket
from evaluation.run import isolated_directory


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.original=dict(vars(settings));self.temp=isolated_directory();self.directory=self.temp.__enter__()
        for key,value in {'database_url':str(self.directory/'tickets.db'),'embedding_provider':'disabled',
                          'llm_provider':'disabled','app_env':'test','session_secret':'x'*48,
                          'admin_password_hash':password_hash('workflow-test-password'),'max_public_hourly':100}.items():
            object.__setattr__(settings,key,value)
        self.client=TestClient(app);self.client.__enter__()
        self.admin_csrf=self.client.post('/api/v1/admin/login',json={'password':'workflow-test-password'}).json()['csrf_token']

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for key,value in self.original.items():object.__setattr__(settings,key,value)
        self.temp.__exit__(None,None,None)

    def create(self,high=False,auto=True,client=None,headers=None):
        client=client or self.client
        data={'title':'打印机缺纸','description':'打印机一直提示缺纸，纸盒里有纸，重新放纸后仍提示缺纸。','requester':'员工'}
        if high:data={'title':'生产数据库权限','description':'需要修改生产系统权限并访问敏感数据。','requester':'员工'}
        if auto and not high:
            # Fixture for workflow testing only, not a production gate bypass.
            def advice(state):
                return {**state,'ticket_id':create_it_ticket(),'category':'硬件与办公设备','priority':'低','risk_level':'低风险',
                        'status':'已给出处理建议','needs_human_approval':False,'answer':'模拟已校验的排查建议，用于流程测试。',
                        'answer_source':'逐句引用对齐的检索答复','citations':[],'retrieval':{},'confidence':0}
            with patch('app.main.ticket_graph.invoke',side_effect=advice):
                r=client.post('/api/v1/tickets',json=data,headers=headers or {})
        else:r=client.post('/api/v1/tickets',json=data,headers=headers or {})
        self.assertEqual(r.status_code,201,r.text);return r.json()

    def action(self,t,action):return self.client.post('/api/v1/tickets/'+t['ticket_id']+'/actions',headers={'X-Ticket-Token':t['access_token']},json={'action':action,'expected_version':t['workflow_version']})
    def work(self,t,action,body=''):
        return self.client.post('/api/v1/admin/tickets/'+t['ticket_id']+'/work',headers={'X-CSRF-Token':self.admin_csrf},json={'action':action,'body':body,'expected_version':t['workflow_version']})

    def test_original_agent_gate_remains_and_draft_is_not_employee_reply(self):
        with patch('app.agent.generate_grounded_answer',side_effect=AssertionError('Gate must not call model')) as model:
            t=self.create(auto=False)
        self.assertEqual(t['status'],'待人工处理');self.assertTrue(t['needs_human_approval']);model.assert_not_called()
        self.assertNotIn('质量门槛',t['public_answer']);self.assertNotIn('审批前',t['public_answer'])
        self.assertEqual(t['answer_source'],'人工接管前知识库资料')

    def test_employee_admin_complete_lifecycle(self):
        original=self.create();r=self.action(original,'escalate');self.assertEqual(r.status_code,200)
        t=r.json();t['access_token']=original['access_token'];self.assertEqual(t['status'],'待人工处理')
        r=self.work(t,'start');self.assertEqual(r.status_code,200);t.update({k:v for k,v in r.json().items() if k!='access_token'})
        r=self.work(t,'request_info','请提供打印机型号和屏幕提示。');self.assertEqual(r.status_code,200);t.update({k:v for k,v in r.json().items() if k!='access_token'})
        r=self.client.post('/api/v1/tickets/'+t['ticket_id']+'/messages',headers={'X-Ticket-Token':t['access_token']},json={'body':'型号为演示打印机，屏幕显示缺纸。'})
        self.assertEqual(r.status_code,200);t.update({k:v for k,v in r.json().items() if k!='access_token'});self.assertEqual(t['status'],'处理中')
        r=self.work(t,'resolve','请尝试重新打印，并确认现在是否正常。');self.assertEqual(r.status_code,200);t.update({k:v for k,v in r.json().items() if k!='access_token'})
        self.assertEqual(t['status'],'待员工确认');r=self.action(t,'resolve');self.assertEqual(r.status_code,200);t.update({k:v for k,v in r.json().items() if k!='access_token'})
        self.assertEqual(t['status'],'已解决');self.assertEqual(self.work(t,'reply','多余回复').status_code,409)
        r=self.action(t,'reopen');self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'待人工处理')
        self.assertEqual([m['actor'] for m in r.json()['messages']],['employee','admin','employee','admin','employee'])

    def test_tokens_csrf_and_duplicate_transitions(self):
        t=self.create();path='/api/v1/tickets/'+t['ticket_id']
        self.assertEqual(self.client.post(path+'/messages',json={'body':'新的补充内容'}).status_code,404)
        self.assertEqual(self.client.post('/api/v1/admin/tickets/'+t['ticket_id']+'/work',json={'action':'reply','body':'回复','expected_version':0}).status_code,403)
        self.assertEqual(self.action(t,'resolve').status_code,200);self.assertEqual(self.action(t,'resolve').status_code,409)

    def test_sensitive_ticket_cannot_bypass_approval(self):
        t=self.create(high=True);self.assertEqual(t['status'],'待人工处理')
        self.assertEqual(self.work(t,'start').status_code,409);self.assertEqual(self.work(t,'resolve','已经处理').status_code,409)
        self.assertEqual(self.work(t,'request_info','提供账户').status_code,409)
        self.assertNotIn('质量门槛',t['public_answer']);self.assertNotIn('评分',t['public_answer'])

    def test_employee_account_ownership_not_display_name_or_token(self):
        alice=TestClient(app);bob=TestClient(app)
        a=alice.post('/api/v1/employee/register',json={'username':'alice','display_name':'同名员工','password':'alice-password'});self.assertEqual(a.status_code,201)
        b=bob.post('/api/v1/employee/register',json={'username':'bob','display_name':'同名员工','password':'bob-password'});self.assertEqual(b.status_code,201)
        t=self.create(auto=False,client=alice,headers={'X-CSRF-Token':a.json()['csrf_token']})
        self.assertEqual(t['requester'],'同名员工')
        self.assertEqual(alice.get('/api/v1/employee/tickets').json()['total'],1)
        self.assertEqual(bob.get('/api/v1/employee/tickets').json()['total'],0)
        path='/api/v1/tickets/'+t['ticket_id'];stolen={'X-Ticket-Token':t['access_token'],'X-CSRF-Token':b.json()['csrf_token']}
        self.assertEqual(bob.get(path,headers=stolen).status_code,404)
        self.assertEqual(bob.post(path+'/messages',headers=stolen,json={'body':'越权补充'}).status_code,404)
        self.assertEqual(TestClient(app).get(path,headers=stolen).status_code,404)
        self.assertEqual(alice.get(path).status_code,200)
        self.assertEqual(alice.post(path+'/messages',json={'body':'缺少CSRF'}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/admin/tickets').json()['total'],1)

    def test_employee_session_logout_relogin_and_auth_separation(self):
        client=TestClient(app)
        self.assertEqual(client.get('/api/v1/employee/tickets').status_code,401)
        r=client.post('/api/v1/employee/register',json={'username':'employee1','display_name':'演示员工','password':'employee-password'});self.assertEqual(r.status_code,201)
        csrf=r.json()['csrf_token'];self.assertIn('HttpOnly',r.headers['set-cookie'])
        self.assertEqual(client.get('/api/v1/admin/tickets').status_code,403)
        t=self.create(auto=False,client=client,headers={'X-CSRF-Token':csrf})
        self.assertEqual(client.post('/api/v1/employee/logout').status_code,403)
        self.assertEqual(client.post('/api/v1/employee/logout',headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(client.get('/api/v1/employee/tickets').status_code,401)
        self.assertEqual(client.post('/api/v1/employee/login',json={'username':'employee1','password':'incorrect-password'}).status_code,401)
        self.assertEqual(client.post('/api/v1/employee/login',json={'username':'employee1','password':'employee-password'}).status_code,200)
        self.assertEqual(client.get('/api/v1/employee/tickets').json()['items'][0]['ticket_id'],t['ticket_id'])

    def test_saved_data_survives_schema_restart(self):
        t=self.create();from app.database import init_database
        init_database();r=self.client.get('/api/v1/tickets/'+t['ticket_id'],headers={'X-Ticket-Token':t['access_token']})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['public_answer'],t['public_answer'])

    def test_real_graph_clarification_then_same_ticket_grounded_advice(self):
        for k,v in {'low_risk_assistance':True,'llm_provider':'mimo','mimo_api_key':'test-only'}.items():object.__setattr__(settings,k,v)
        import_document('demo','打印机自助支持','md','打印机显示缺纸时先确认纸盒是否有纸。纸张充足时，检查纸张尺寸是否与纸盒设置一致。',with_embedding=False)
        responses=[]
        def model_response(**kwargs):
            prompt=kwargs['messages'][1]['content']
            if '独立审查员' in prompt:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'passed':True,'reason':'supported'})),finish_reason='stop')],usage=None)
            catalog=json.loads(prompt.split('当前数据：\n')[1])['sources']
            if not responses:
                data={'decision':'clarify','understanding':'需要确认纸盒是否有纸。','steps':[],
                      'questions':['纸盒中是否已经放入纸张？'],'check_result':'','handoff_reason':''}
            else:
                selected=next(k for k,v in catalog.items() if '纸张尺寸' in v['text'])
                data={'decision':'advise','understanding':'已确认纸盒里有纸。',
                      'steps':[{'text':'请核对纸张尺寸与纸盒设置是否一致。','source_ids':[selected]}],
                      'questions':[],'check_result':'调整后观察缺纸提示是否消失。','handoff_reason':''}
            responses.append(data)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data,ensure_ascii=False)),finish_reason='stop')],usage=None)
        with patch('app.llm.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect=model_response
            r=self.client.post('/api/v1/tickets',json={'title':'打印机提示缺纸','description':'打印机屏幕一直提示缺纸，无法打印文件。','requester':'员工'})
            self.assertEqual(r.status_code,201);t=r.json();self.assertEqual(t['status'],'等待补充信息（AI）')
            r=self.client.post('/api/v1/tickets/'+t['ticket_id']+'/messages',headers={'X-Ticket-Token':t['access_token']},json={'body':'纸盒里有纸，但尺寸可能不对。'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['ticket_id'],t['ticket_id'])
        self.assertEqual(r.json()['status'],'已给出处理建议');self.assertIn('纸张尺寸',r.json()['public_answer'])
        self.assertTrue(r.json()['citations']);self.assertEqual(len(responses),2)
        self.assertEqual(self.client.get('/api/v1/admin/tickets').json()['total'],1)
        from app.evidence import policy
        self.assertFalse(policy()['release_gate']['vector']['passed'])

    def test_low_risk_assistance_does_not_call_model_for_sensitive_request(self):
        for k,v in {'low_risk_assistance':True,'llm_provider':'mimo','mimo_api_key':'test-only'}.items():object.__setattr__(settings,k,v)
        with patch('app.llm.OpenAI') as factory:t=self.create(high=True)
        self.assertEqual(t['status'],'待人工处理');factory.assert_not_called()
        self.assertIn('接管原因',t['handoff_summary'])

    def test_business_urgency_does_not_force_ordinary_faults_to_manual(self):
        for k,v in {'low_risk_assistance':True,'auto_approve_low_risk':True,'llm_provider':'mimo','mimo_api_key':'test-only'}.items():object.__setattr__(settings,k,v)
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            'decision':'clarify','understanding':'需要确认当前故障提示。',
            'questions':['当前界面出现了什么错误提示？']},ensure_ascii=False)))])
        with patch('app.llm.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=response
            for title in ['打印机缺纸影响工作','邮箱收不到邮件','VPN连接超时']:
                r=self.client.post('/api/v1/tickets',json={'title':title,'description':'今天办公时出现故障，之前一直正常，请帮我排查。','requester':'员工'})
                self.assertEqual(r.status_code,201,r.text);t=r.json()
                self.assertEqual(t['priority'],'中');self.assertEqual(t['risk_level'],'低风险')
                self.assertEqual(t['status'],'等待补充信息（AI）');self.assertFalse(t['needs_human_approval'])
                with get_connection() as db:
                    audit=db.execute('SELECT action FROM audit_logs WHERE ticket_id=?',(t['ticket_id'],)).fetchall()
                self.assertIn('只读自助自动放行',[x['action'] for x in audit])
            self.assertEqual(factory.return_value.chat.completions.create.call_count,3)

    def test_automatic_self_service_switch_is_effective(self):
        for k,v in {'low_risk_assistance':True,'auto_approve_low_risk':False,'llm_provider':'mimo','mimo_api_key':'test-only'}.items():object.__setattr__(settings,k,v)
        with patch('app.llm.OpenAI') as factory:t=self.create(auto=False)
        factory.assert_not_called();self.assertEqual(t['status'],'待人工处理')
        self.assertEqual(t['handoff_reason'],'automatic_support_disabled')

    def test_old_urgent_fault_can_retry_but_deliberate_handoff_cannot_loop(self):
        r=self.client.post('/api/v1/tickets',json={'title':'VPN连接超时','description':'今天办公网络VPN无法连接，公网可以正常上网。','requester':'员工'})
        self.assertEqual(r.status_code,201);t=r.json();self.assertEqual(t['risk_level'],'中风险')
        object.__setattr__(settings,'low_risk_assistance',True)
        from app.repository import get_ticket
        from app.workflow import change_ticket
        self.assertTrue(get_ticket(t['ticket_id'],'demo')['can_retry_ai'])
        result=change_ticket(t['ticket_id'],'demo','employee','retry_ai',expected_version=t['workflow_version'])
        self.assertEqual(result['status'],'AI处理中')
        with get_connection() as db:
            db.execute("UPDATE tickets SET status='待人工处理',handoff_reason='clarification_limit' WHERE ticket_id=?",(t['ticket_id'],))
        self.assertFalse(get_ticket(t['ticket_id'],'demo')['can_retry_ai'])
        with self.assertRaises(LookupError):change_ticket(t['ticket_id'],'demo','employee','retry_ai')

    def test_printer_topic_correction_retains_history_and_admin_edits(self):
        import runpy
        importer=runpy.run_path(str(Path('scripts/import-demo.py')))['import_simulated']
        content=Path('data/printer_self_help.md').read_text(encoding='utf-8')
        legacy_title='员工自助支持 T05 打印机缺纸'
        old=import_document('correction',legacy_title,'md',content,with_embedding=False)
        admin=import_document('admin-preserve',legacy_title,'md','管理员自定义排查资料：纸盒中已经有纸，不要重复装纸，设备信息需核验。',with_embedding=False)
        importer('correction',vectors=False);importer('admin-preserve',vectors=False)
        with get_connection() as db:
            self.assertEqual(db.execute('SELECT active FROM knowledge_documents WHERE id=?',(old['document_id'],)).fetchone()['active'],0)
            self.assertEqual(db.execute('SELECT active FROM knowledge_documents WHERE id=?',(admin['document_id'],)).fetchone()['active'],1)
            self.assertTrue(db.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id='correction' AND title='员工自助支持 T19 打印机缺纸' AND active=1").fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM knowledge_chunks WHERE document_id=?',(old['document_id'],)).fetchone())

    def test_employee_followup_preserves_chronology_without_ai_risk_contamination(self):
        t=self.create()
        warning='请放入纸张。无需修改生产数据库权限。'
        with get_connection() as db:
            db.execute('UPDATE tickets SET public_answer=?,answer_source=? WHERE ticket_id=?',
                       (warning,'结合上下文的AI排查建议',t['ticket_id']))
        object.__setattr__(settings,'low_risk_assistance',True)
        captured=[]
        def next_reply(state):
            captured.append(state)
            return {'status':'等待补充信息（AI）','answer':'纸盒装纸后有什么提示？',
                    'public_answer':'纸盒装纸后有什么提示？','answer_source':'AI澄清问题','citations':[],
                    'retrieval':{},'needs_human_approval':False,'risk_level':'低风险'}
        with patch('app.agent.ticket_graph.invoke',side_effect=next_reply):
            r=self.client.post('/api/v1/tickets/'+t['ticket_id']+'/messages',
                headers={'X-Ticket-Token':t['access_token']},json={'body':'已经放入纸张，但仍报错。'})
        self.assertEqual(r.status_code,200,r.text)
        state=captured[0]
        self.assertNotIn('生产数据库权限',state['description'])
        self.assertEqual(state['initial_description'],t['description'])
        self.assertEqual(state['conversation'][-1],{'role':'employee','text':'已经放入纸张，但仍报错。'})
        self.assertTrue(any(x['role']=='ai' and warning in x['text'] for x in state['conversation']))

    def test_legacy_ai_retry_keeps_ticket_and_rejects_sensitive_or_assigned(self):
        from app.workflow import change_ticket
        from app.repository import continue_assistance
        t=self.create(auto=False)
        object.__setattr__(settings,'low_risk_assistance',True)
        changed=change_ticket(t['ticket_id'],'demo','employee','retry_ai',expected_version=t['workflow_version'])
        self.assertEqual(changed['status'],'AI处理中')
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            'decision':'clarify','understanding':'需要确认设备当前提示。','steps':[],
            'questions':['打印机屏幕显示了什么报错？'],'check_result':'','handoff_reason':''},ensure_ascii=False)),finish_reason='stop')],usage=None)
        object.__setattr__(settings,'llm_provider','mimo');object.__setattr__(settings,'mimo_api_key','test-only')
        with patch('app.llm.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect=[response,SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'passed':True,'reason':'supported'})),finish_reason='stop')],usage=None)]
            result=continue_assistance(t['ticket_id'],'demo')
        self.assertEqual(result['ticket_id'],t['ticket_id']);self.assertEqual(result['status'],'等待补充信息（AI）')
        self.assertEqual([m['actor'] for m in result['messages']],['ai'])
        high=self.create(high=True)
        with self.assertRaises(LookupError):change_ticket(high['ticket_id'],'demo','employee','retry_ai')
        assigned=self.create(auto=False)
        change_ticket(assigned['ticket_id'],'demo','admin','start',operator='admin')
        with self.assertRaises(LookupError):change_ticket(assigned['ticket_id'],'demo','employee','retry_ai')

    def test_admin_username_required_and_identity_is_server_owned(self):
        self.assertEqual(self.client.post('/api/v1/admin/login',json={'username':'wrong-admin','password':'workflow-test-password'}).status_code,401)
        me=self.client.get('/api/v1/admin/me');self.assertEqual(me.status_code,200);self.assertEqual(me.json()['username'],'admin')
        t=self.create(high=True)
        r=self.client.post('/api/v1/admin/tickets/'+t['ticket_id']+'/approval',headers={'X-CSRF-Token':self.admin_csrf},json={'approved':True,'operator':'伪造姓名','comment':'模拟审批'})
        self.assertEqual(r.status_code,200)
        audit=self.client.get('/api/v1/admin/tickets/'+t['ticket_id']+'/audit-logs').json()
        self.assertEqual(audit[-1]['operator'],'admin')

    def test_unsafe_model_clarification_is_not_sent_to_employee(self):
        for k,v in {'low_risk_assistance':True,'llm_provider':'mimo','mimo_api_key':'test-only'}.items():object.__setattr__(settings,k,v)
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"decision":"clarify","understanding":"需确认账户情况","questions":["请把账号密码发给我？"]}'),finish_reason='stop')],usage=None)
        with patch('app.llm.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=response
            t=self.create(auto=False)
        self.assertEqual(t['status'],'待人工处理');self.assertNotIn('账号密码',t['public_answer'])

    def test_blue_screen_uses_original_gate_and_admin_filters(self):
        r=self.client.post('/api/v1/tickets',json={'title':'电脑蓝屏','description':'电脑开机后不断蓝屏，屏幕显示停止代码。','requester':'测试员工'})
        self.assertEqual(r.status_code,201);self.assertEqual(r.json()['answer_source'],'人工接管前知识库资料')
        self.assertNotIn('评分',r.json()['public_answer'])
        result=self.client.get('/api/v1/admin/tickets',params={'keyword':'电脑蓝屏','status':'待人工处理'}).json()
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['ticket_id'],r.json()['ticket_id'])
        icon=self.client.get('/favicon.svg');self.assertEqual(icon.status_code,200);self.assertIn('image/svg+xml',icon.headers['content-type'])
        for page in ['/', '/admin']:
            self.assertIn('/favicon.svg?v=20261001-2',self.client.get(page).text)


if __name__=='__main__':unittest.main()
