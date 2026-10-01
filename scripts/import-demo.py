"""Explicit import of simulated SOPs, preserving administrator documents."""
import argparse
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def import_simulated(tenant="demo", *, vectors=True):
    from app.database import init_database, get_connection
    from app.knowledge import import_document
    from app.resources import resource_path
    init_database()
    topics = json.loads(resource_path("simulated_sops.json").read_text(encoding="utf-8"))["topics"]
    count = 0
    for topic in topics:
        title = f"模拟SOP {topic['id']} {topic['title']}"
        with get_connection() as db:
            existing = db.execute("SELECT 1 FROM knowledge_documents WHERE tenant_id=? AND title=? AND active=1", (tenant, title)).fetchone()
        if existing:
            continue
        content = f"# {topic['id']} {topic['title']}\n\n适用场景：{topic['symptoms']}。处理流程：{topic['steps']}。边界：本资料为模拟操作规程；实际操作需遵循所在企业授权，生产、敏感数据及权限变更转人工确认。"
        count += import_document(tenant, title, "md", content, with_embedding=vectors)["chunks"]
    supplement=resource_path('employee_self_help.md')
    if supplement.exists():
        with get_connection() as db:
            existing=db.execute('SELECT 1 FROM knowledge_documents WHERE tenant_id=? AND title=? AND active=1',
                                (tenant,'员工自助支持 T24 电脑蓝屏')).fetchone()
        if not existing:
            count+=import_document(tenant,'员工自助支持 T24 电脑蓝屏','md',supplement.read_text(encoding='utf-8'),with_embedding=vectors)['chunks']
    printer=resource_path('printer_self_help.md')
    if printer.exists():
        title='员工自助支持 T19 打印机缺纸'
        content=printer.read_text(encoding='utf-8')
        with get_connection() as db:
            existing=db.execute('SELECT 1 FROM knowledge_documents WHERE tenant_id=? AND title=? AND active=1',(tenant,title)).fetchone()
        if not existing:
            count+=import_document(tenant,title,'md',content,with_embedding=vectors)['chunks']
        # Correct only our exact v1 import's mistaken topic label; retain the
        # old document/chunks for historical citations and preserve admin edits.
        with get_connection() as db:
            legacy=db.execute('SELECT id FROM knowledge_documents WHERE tenant_id=? AND title=? AND version=1 AND sha256=? AND active=1',
                (tenant,'员工自助支持 T05 打印机缺纸',hashlib.sha256(content.encode()).hexdigest())).fetchone()
            if legacy:
                db.execute('UPDATE knowledge_documents SET active=0 WHERE id=?',(legacy['id'],))
                db.execute('UPDATE knowledge_chunks SET active=0 WHERE document_id=?',(legacy['id'],))
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--without-vectors", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"new_chunks": import_simulated(vectors=not args.without_vectors)}, ensure_ascii=False))
