import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import settings
from app.main import app
from app.database import get_connection,init_database
from app.approvals import get_policy,evaluate_request,publish_policy
from app.security import password_hash
from evaluation.run import isolated_directory


class ServiceApprovalTest(unittest.TestCase):
    def setUp(self):
        self.old=dict(vars(settings));self.temp=isolated_directory();self.root=self.temp.__enter__()
        for k,v in {'database_url':str(self.root/'tickets.db'),'embedding_provider':'disabled','llm_provider':'disabled',
                    'app_env':'test','session_secret':'s'*48,'max_public_hourly':100,
                    'admin_password_hash':password_hash('approval-test-password')}.items():object.__setattr__(settings,k,v)
        self.c=TestClient(app);self.c.__enter__()
        self.admin=self.c.post('/api/v1/admin/login',json={'password':'approval-test-password'}).json()['csrf_token']
        self.emp=self.c.post('/api/v1/employee/register',json={'username':'employee1','password':'employee-test-password','display_name':'演示员工'}).json()['csrf_token']

    def tearDown(self):
        self.c.__exit__(None,None,None)
        for k,v in self.old.items():object.__setattr__(settings,k,v)
        self.temp.__exit__(None,None,None)

    def submit(self,item='7-Zip',kind='software_install',days=1,**kw):
        service={'service_type':kind,'item':item,'company_device':True,'requires_privilege':False,'loan_days':days}
        service.update(kw.pop('fields',{}))
        data={'title':'办公服务申请','description':'模拟申请：用于日常办公，期望明天使用。','request_kind':'service','service_request':service}
        data.update(kw)
        with patch('app.agent.generate_grounded_answer',side_effect=AssertionError('Service policy must not call RAG generation')):
            r=self.c.post('/api/v1/tickets',json=data,headers={'X-CSRF-Token':self.emp})
        self.assertEqual(r.status_code,201,r.text);return r.json()

    def work(self,t,action,body=''):
        return self.c.post('/api/v1/admin/tickets/'+t['ticket_id']+'/work',json={'action':action,'body':body,'expected_version':t['workflow_version']},headers={'X-CSRF-Token':self.admin})

    def test_allowed_software_approval_fulfillment_confirmation_and_ownership(self):
        t=self.submit();self.assertTrue(t['approval_passed']);self.assertFalse(t['needs_human_approval'])
        self.assertEqual(t['status'],'审批通过，待人工执行');self.assertEqual(t['request_kind'],'service')
        self.assertIn('尚未交付或安装',t['public_answer']);id=t['ticket_id'];token=t['access_token']
        self.assertEqual(self.c.post('/api/v1/tickets/'+id+'/actions',json={'action':'resolve','expected_version':t['workflow_version']},headers={'X-CSRF-Token':self.emp}).status_code,409)
        t=self.work(t,'start').json();self.assertEqual(t['status'],'处理中')
        t=self.work(t,'resolve','已按批准范围完成模拟安装，请确认。').json();self.assertEqual(t['status'],'待员工确认')
        t=self.c.post('/api/v1/tickets/'+id+'/actions',json={'action':'resolve','expected_version':t['workflow_version']},headers={'X-CSRF-Token':self.emp}).json()
        self.assertEqual(t['status'],'已解决')
        logs=self.c.get('/api/v1/admin/tickets/'+id+'/audit-logs').json()
        rule=next(x for x in logs if x['action']=='服务申请自动决策')['detail']
        self.assertEqual(rule['decision'],'approve');self.assertEqual(rule['policy_version'],1)
        self.assertEqual(rule['submitted_request']['item'],'7-Zip')
        self.c.post('/api/v1/employee/logout',headers={'X-CSRF-Token':self.emp})
        self.c.post('/api/v1/employee/register',json={'username':'employee2','password':'employee-test-password','display_name':'演示员工'})
        self.assertEqual(self.c.get('/api/v1/employee/tickets').json()['total'],0)
        self.assertEqual(self.c.get('/api/v1/tickets/'+id,headers={'X-Ticket-Token':token}).status_code,404)

    def test_equipment_term_and_unknown_software_require_manual_review(self):
        self.assertTrue(self.submit('鼠标','equipment_loan',7)['approval_passed'])
        for t in [self.submit('鼠标','equipment_loan',8),self.submit('未在清单中的软件')]:
            self.assertEqual(t['status'],'待人工处理');self.assertFalse(t['approval_passed'])
            self.assertEqual(self.work(t,'start').status_code,409)
            self.assertEqual(self.work(t,'resolve','不能跳过审批').status_code,409)
            response=self.work(t,'request_info','请补充用途和使用时间')
            self.assertEqual(response.status_code,200)
            t=self.c.post('/api/v1/tickets/'+t['ticket_id']+'/messages',json={'body':'补充：项目组需要办公使用。'},headers={'X-CSRF-Token':self.emp}).json()
            self.assertEqual(t['status'],'待人工处理')
            self.assertEqual(self.c.post('/api/v1/admin/tickets/'+t['ticket_id']+'/approval',json={'approved':True,'comment':'已核对用途'},headers={'X-CSRF-Token':self.admin}).status_code,200)

    def test_explicit_denial_and_privilege_context_are_not_auto_approved(self):
        self.assertEqual(self.submit('破解软件')['status'],'审批拒绝')
        self.assertEqual(self.submit(fields={'requires_privilege':True})['status'],'待人工处理')
        self.assertEqual(self.submit(description='申请安装7-Zip，还要生产数据库管理员权限。')['status'],'待人工处理')
        self.assertEqual(self.submit(fields={'company_device':False})['status'],'待人工处理')

    def test_policy_publication_csrf_conflict_disable_and_persistent_history(self):
        before=self.submit();policy=get_policy('demo');data={k:v for k,v in policy.items() if k!='version'}
        data.update(expected_version=1,enabled=False)
        self.assertEqual(self.c.put('/api/v1/admin/service-policy',json=data).status_code,403)
        r=self.c.put('/api/v1/admin/service-policy',json=data,headers={'X-CSRF-Token':self.admin})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['version'],2)
        self.assertEqual(self.c.put('/api/v1/admin/service-policy',json=data,headers={'X-CSRF-Token':self.admin}).status_code,409)
        self.assertEqual(self.submit()['status'],'待人工处理')
        init_database();self.assertFalse(get_policy('demo')['enabled'])
        self.assertTrue(self.c.get('/api/v1/tickets/'+before['ticket_id']).json()['approval_passed'])
        with get_connection() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM service_policies WHERE tenant_id='demo'").fetchone()[0],2)

    def test_employee_scope_change_cannot_silently_expand_approved_request(self):
        t=self.submit();id=t['ticket_id']
        t=self.c.post('/api/v1/tickets/'+id+'/messages',json={'body':'还希望增加管理员权限。'},headers={'X-CSRF-Token':self.emp}).json()
        self.assertEqual(t['status'],'待人工处理');self.assertFalse(t['approval_passed']);self.assertFalse(t['can_retry_ai'])
        self.assertEqual(t['handoff_reason'],'service_scope_update')
        self.assertEqual(self.work(t,'start').status_code,409)
        t=self.c.post('/api/v1/admin/tickets/'+id+'/approval',json={'approved':True,'comment':'复核模拟范围'},headers={'X-CSRF-Token':self.admin}).json()
        self.assertTrue(t['approval_passed'])
        t=self.work(t,'resolve','仅按复核范围处理').json()
        t=self.c.post('/api/v1/tickets/'+id+'/actions',json={'action':'resolve','expected_version':t['workflow_version']},headers={'X-CSRF-Token':self.emp}).json()
        t=self.c.post('/api/v1/tickets/'+id+'/actions',json={'action':'reopen','expected_version':t['workflow_version']},headers={'X-CSRF-Token':self.emp}).json()
        self.assertFalse(t['approval_passed']);self.assertEqual(self.work(t,'start').status_code,409)

    def test_no_policy_cross_tenant_and_reject_invalid_or_unauthenticated_requests(self):
        request={'service_type':'software_install','item':'7-Zip','company_device':True,'requires_privilege':False,'loan_days':1}
        self.assertEqual(evaluate_request('different-tenant',request)['decision'],'manual')
        self.assertEqual(get_policy('different-tenant')['version'],0)
        self.c.post('/api/v1/employee/logout',headers={'X-CSRF-Token':self.emp})
        r=self.c.post('/api/v1/tickets',json={'title':'服务申请','description':'用于办公的软件安装申请','request_kind':'service','service_request':request})
        self.assertEqual(r.status_code,401)
        r=self.c.post('/api/v1/tickets',json={'title':'服务申请','description':'用于办公的软件安装申请','request_kind':'service'})
        self.assertEqual(r.status_code,422)
