"""Versioned service-request policy, separate from RAG answer quality.

Approval authorizes a request in the demo; it never installs software, grants
access, allocates stock or reports delivery. Unknown conditions require IT.
"""
import json
from datetime import datetime, timezone
from app.database import get_connection

DEMO_POLICY = {
    'enabled': True, 'software': ['7-Zip', 'Visual Studio Code'],
    'equipment': ['键盘', '鼠标', '显示器'], 'max_loan_days': 7,
    'denied_items': ['破解软件', '盗版软件'],
}


def seed_demo_policy(db):
    # INSERT only once. Never reactivate or overwrite an administrator revision.
    if not db.execute("SELECT 1 FROM service_policies WHERE tenant_id='demo'").fetchone():
        db.execute('INSERT INTO service_policies(tenant_id,version,policy_json,operator,created_at) VALUES(?,?,?,?,?)',
                   ('demo',1,json.dumps(DEMO_POLICY,ensure_ascii=False),'demo-bootstrap',datetime.now(timezone.utc).isoformat()))


def get_policy(tenant):
    with get_connection() as db:
        row=db.execute('SELECT * FROM service_policies WHERE tenant_id=? ORDER BY version DESC LIMIT 1',(tenant,)).fetchone()
    if not row:
        return {'version':0,'enabled':False,'software':[],'equipment':[],'max_loan_days':1,'denied_items':[]}
    return {**json.loads(row['policy_json']),'version':row['version']}


def publish_policy(tenant, expected_version, policy, operator):
    with get_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        version=db.execute('SELECT COALESCE(MAX(version),0) FROM service_policies WHERE tenant_id=?',(tenant,)).fetchone()[0]
        if version!=expected_version:raise LookupError('规则已更新，请重新加载后发布')
        db.execute('INSERT INTO service_policies(tenant_id,version,policy_json,operator,created_at) VALUES(?,?,?,?,?)',
                   (tenant,version+1,json.dumps(policy,ensure_ascii=False),operator,datetime.now(timezone.utc).isoformat()))
    return get_policy(tenant)


def evaluate_request(tenant, request):
    policy=get_policy(tenant)
    item=request['item'].strip()
    decision='manual'; reason='申请不在当前自动审批范围内，由 IT 审核。'
    if not policy['enabled']:
        reason='自动审批已关闭，申请交给 IT 审核。'
    elif item.casefold() in {s.casefold() for s in policy['denied_items']}:
        decision='reject';reason='申请项目在当前规则的禁用清单中。请使用合规的替代方案。'
    elif request['requires_privilege']:
        reason='涉及额外权限，自动审批不适用，交给 IT 核对授权。'
    elif request['service_type']=='software_install':
        if item.casefold() in {s.casefold() for s in policy['software']} and request['company_device']:
            decision='approve';reason='申请的软件在允许清单中，用于公司设备且未申请额外权限。'
        elif not request['company_device']:
            reason='个人设备不在自动审批范围内，请由 IT 确认。'
    elif item in policy['equipment'] and request['loan_days']<=policy['max_loan_days']:
        decision='approve';reason=f"申请设备在允许清单中，借用期限不超过 {policy['max_loan_days']} 天，未申请额外权限。"
    elif request['service_type']=='equipment_loan' and request['loan_days']>policy['max_loan_days']:
        reason=f"借用期限超过自动审批的 {policy['max_loan_days']} 天范围，交给 IT 审核。"
    return {'kind':'service_request','decision':decision,'reason':reason,'policy_version':policy['version'],
            'policy_snapshot':policy,'submitted_request':request}
