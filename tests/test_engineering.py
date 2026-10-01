import importlib.util
import json
import sqlite3
import struct
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.config import settings
from app.database import get_connection, init_database
from app.evidence import evidence_score
from app.answer_validation import align_answer, sentence_catalog, render_selection
from app.knowledge import chunk_sections, import_document, retrieve
from app.llm import generate_grounded_answer, validate_citations
from app.main import app
from app.metrics import retrieval_metrics
from app.repository import approve_ticket, save_ticket
from app.retrieval_health import vector_health
from app.security import create_session, use_quota
from evaluation.run import ROOT, calibrate, isolated_directory, routing


class EngineeringTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(vars(settings))
        self.temp = isolated_directory()
        self.directory = self.temp.__enter__()
        for key,value in {"database_url":str(self.directory/"test.db"),"embedding_provider":"disabled",
                          "llm_provider":"disabled","app_env":"test","session_secret":"s"*48}.items():
            object.__setattr__(settings,key,value)
        init_database()

    def tearDown(self):
        self.temp.__exit__(None,None,None)
        for key,value in self.original.items():
            object.__setattr__(settings,key,value)

    def document(self,tenant="demo"):
        import_document(tenant,"测试打印机指南","md","# 设备\n## 打印机\n\n打印机显示缺纸时检查纸盒尺寸。记录型号后检查传感器。",with_embedding=False)
        return retrieve("打印机缺纸","demo",mode="bm25")["hits"]

    def ticket(self,tenant="demo",identifier="TEST-1",retrieval=None):
        save_ticket({"ticket_id":identifier,"tenant_id":tenant,"access_token_hash":"hash",
            "title":"模拟测试工单","description":"本条工单用于自动化工程测试","requester":"test",
            "category":"综合咨询","priority":"低","risk_level":"低风险","status":"待人工处理",
            "needs_human_approval":True,"answer":"人工处理","answer_source":"test",
            "confidence":.3,"citations":[],"retrieval":retrieval or {},"handoff_reason":"insufficient_evidence"})

    def test_sentence_boundaries_and_heading_context(self):
        sentences=["检查打印机纸盒尺寸与耗材情况。","核对驱动版本并记录设备型号。","记录错误时间并联系服务台。"]*5
        chunks=chunk_sections("# 设备\n## 打印机\n\n"+"".join(sentences),80)
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(path=="设备 / 打印机" and content.endswith("。") and len(content)<=80 for path,content in chunks))
        self.assertEqual("".join(content.split("\n",1)[1] for _,content in chunks),"".join(sentences))

    def test_long_sentence_fallback_loses_no_text(self):
        text="长"*901
        chunks=chunk_sections(text,100)
        self.assertEqual("".join(c for _,c in chunks),text)
        self.assertTrue(all(len(c)<=100 for _,c in chunks))

    def test_stable_keys_and_versioned_documents(self):
        first=self.document()[0]
        second=self.document()[0]
        self.assertEqual(first["chunk_key"],second["chunk_key"])
        self.assertEqual(second["version"],2)
        self.assertNotEqual(first["document_id"],second["document_id"])

    def test_health_disabled_without_embedding_call(self):
        with patch("app.knowledge.embed",side_effect=AssertionError("health must not call embed")):
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").json()["vector"]["state"],"disabled")
                self.assertEqual(client.get("/health/live").json(),{"status":"alive"})

    def test_vector_missing_and_model_mismatch(self):
        self.document()
        object.__setattr__(settings,"embedding_provider","local")
        self.assertEqual(vector_health()["state"],"index_not_ready")
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?,embedding_model='old-model'",(struct.pack("512f",*([1.0]*512)),))
        self.assertEqual(vector_health()["state"],"model_mismatch")

    def test_existing_index_does_not_claim_unchecked_runtime(self):
        self.document()
        object.__setattr__(settings,"embedding_provider","local")
        vector=struct.pack("512f",*([1.0]*512))
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?,embedding_model=?",(vector,settings.embedding_model))
        with patch("app.knowledge.embed",side_effect=AssertionError("health must not infer")):
            self.assertEqual(vector_health()["state"],"index_ready_unverified")
        with patch("app.knowledge.embed",return_value=vector):
            retrieve("打印机缺纸","demo",mode="hybrid")
        self.assertEqual(vector_health()["state"],"ready")

    def test_database_health_failure_is_503_without_error_details(self):
        with TestClient(app) as client, patch("app.main.get_connection",side_effect=sqlite3.OperationalError("secret-path")):
            response=client.get("/health")
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()["database"],"unavailable")
        self.assertNotIn("secret-path",response.text)

    def test_vector_runtime_failure_is_observable_and_recovers(self):
        self.document()
        object.__setattr__(settings,"embedding_provider","local")
        vector=struct.pack("512f",*([1.0]*512))
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?,embedding_model=?",(vector,settings.embedding_model))
        with patch("app.knowledge.embed",side_effect=RuntimeError("secret must not appear")), self.assertLogs("app.knowledge",level="WARNING") as logs:
            result=retrieve("打印机缺纸","demo",mode="hybrid")
        self.assertEqual(result["vector_state"],"runtime_failed")
        self.assertEqual(vector_health()["state"],"runtime_failed")
        self.assertNotIn("secret must not appear","".join(logs.output))
        self.assertTrue(result["hits"])
        with patch("app.knowledge.embed",return_value=vector):
            self.assertEqual(retrieve("打印机缺纸","demo",mode="hybrid")["vector_state"],"ready")
        self.assertEqual(vector_health()["state"],"ready")

    def test_extension_load_failure_degrades_health(self):
        import sqlite_vec
        object.__setattr__(settings,"embedding_provider","local")
        with patch.object(sqlite_vec,"load",side_effect=sqlite3.OperationalError("blocked")):
            self.assertEqual(vector_health()["state"],"runtime_failed")

    def test_fourth_citation_is_valid(self):
        for i in range(4):
            import_document("demo",f"打印机{i}","txt",f"打印机缺纸指南第{i}段。检查纸盒尺寸与记录型号。",with_embedding=False)
        hits=retrieve("打印机缺纸","demo",mode="bm25")["hits"][:4]
        self.assertEqual(len(hits),4)
        self.assertEqual(validate_citations([hits[3]["id"]],hits),[hits[3]["id"]])

    def test_fourth_citation_returned_to_ticket_view(self):
        from app.agent import answer, decide
        for i in range(4):
            import_document("demo",f"打印机{i}","txt",f"打印机缺纸指南第{i}段。检查纸盒尺寸与记录型号。",with_embedding=False)
        result=retrieve("打印机缺纸","demo",mode="bm25")
        state={"title":"打印机缺纸","description":"纸盒有纸仍提示缺纸","tenant_id":"demo",
            "category":"设备","risk_level":"低风险","allow_llm":True,"retrieval":result}
        state.update(decide(state))
        fourth=result["hits"][3]["id"]
        with patch("app.agent.generate_grounded_answer",return_value=("来自第四条资料的建议",[fourth])):
            returned=answer(state)
        self.assertEqual([c["chunk_id"] for c in returned["citations"]],[fourth])

    def test_mixed_valid_and_invalid_citations_are_rejected(self):
        hits=self.document()
        with self.assertRaises(ValueError):
            validate_citations([hits[0]["id"],99999],hits)
        with self.assertRaises(ValueError):
            validate_citations([str(hits[0]["id"])],hits)

    def test_cross_tenant_and_stale_citation_rejected(self):
        hits=self.document()
        fake=[{**hits[0],"tenant_id":"other"}]
        with self.assertRaises(ValueError):
            validate_citations([hits[0]["id"]],fake)
        self.document()
        with self.assertRaises(ValueError):
            validate_citations([hits[0]["id"]],hits)

    def test_wrong_tenant_context_is_not_sent_to_model(self):
        hits=self.document()
        object.__setattr__(settings,"llm_provider","mimo")
        object.__setattr__(settings,"mimo_api_key","test-only")
        with patch("app.llm.OpenAI") as factory:
            answer,_=generate_grounded_answer(title="打印机故障",description="模拟工单",category="设备",
                risk_level="低风险",hits=hits,allow_llm=True,tenant_id="other")
        self.assertIsNone(answer)
        factory.assert_not_called()

    def test_smoke_script_uses_real_isolated_citation(self):
        spec=importlib.util.spec_from_file_location("test_smoke",ROOT/"scripts/check-llm.py")
        smoke=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        object.__setattr__(settings,"llm_provider","mimo")
        object.__setattr__(settings,"mimo_api_key","test-only")
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"selected_sentence_ids":["1:0"]}'),finish_reason="stop")],usage=None)
        with patch("app.llm.OpenAI") as factory:
            factory.return_value.chat.completions.create.return_value=response
            smoke.main()
        self.assertEqual(settings.database_url,str(self.directory/"test.db"))
        self.assertEqual(factory.return_value.chat.completions.create.call_count,1)

    def test_sentence_alignment_requires_a_complete_source_sentence(self):
        hits=self.document()
        citation=hits[0]["id"]
        result=align_answer("打印机显示缺纸时检查纸盒尺寸。",[citation],hits)
        self.assertTrue(result["passed"])
        self.assertEqual(result["sentences"][0]["chunk_id"],citation)
        self.assertEqual(result["sentences"][0]["version"],hits[0]["version"])

    def test_new_numeric_policy_and_operation_are_rejected(self):
        hits=self.document()
        for text in ("等待15–30分钟自动解锁。","重启打印服务器。",
                     "打印机显示缺纸时检查纸盒尺寸。然后卸载驱动。"):
            self.assertFalse(align_answer(text,[hits[0]["id"]],hits)["passed"])

    def test_alignment_cannot_strip_a_negation_or_use_uncited_source(self):
        hits=[{"id":1,"version":1,"content":"禁止删除数据库记录。"},
              {"id":2,"version":1,"content":"核对系统时间。"}]
        self.assertFalse(align_answer("删除数据库记录。",[1],hits)["passed"])
        self.assertFalse(align_answer("核对系统时间。",[1],hits)["passed"])
        self.assertFalse(align_answer("",[1],hits)["passed"])

    def test_alignment_failure_is_persistable_human_handoff(self):
        from app.agent import answer
        hits=self.document()
        object.__setattr__(settings,"llm_provider","mimo")
        object.__setattr__(settings,"mimo_api_key","test-only")
        content=json.dumps({"selected_sentence_ids":["not-a-retrieved-sentence"]})
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason="stop")],usage=None)
        with patch("app.llm.OpenAI") as factory:
            factory.return_value.chat.completions.create.return_value=response
            result=answer({"title":"打印机缺纸","description":"打印机故障","category":"设备",
                "risk_level":"低风险","tenant_id":"demo","allow_llm":True,
                "retrieval":{"hits":hits},"citations":[{"chunk_id":hits[0]["id"]}]})
        self.assertEqual(result["handoff_reason"],"source_selection_failed")
        self.assertEqual(result["status"],"待人工处理")
        self.assertFalse(result["retrieval"]["answer_validation"]["passed"])

    def test_selector_renders_source_and_ignores_freeform_invention(self):
        hits=self.document()
        catalog=sentence_catalog(hits)
        selected=next(iter(catalog))
        object.__setattr__(settings,"llm_provider","mimo")
        object.__setattr__(settings,"mimo_api_key","test-only")
        content=json.dumps({"selected_sentence_ids":[selected],"answer":"等待30分钟并关闭全部安全软件。"})
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason="stop")],usage=None)
        with patch("app.llm.OpenAI") as factory:
            factory.return_value.chat.completions.create.return_value=response
            answer,cited=generate_grounded_answer(title="打印机缺纸",description="设备故障",category="设备",
                risk_level="低风险",hits=hits,allow_llm=True,tenant_id="demo")
        self.assertEqual(answer,catalog[selected]["text"])
        self.assertEqual(cited,[hits[0]["id"]])
        self.assertNotIn("30分钟",answer)

    def test_selector_rejects_one_invalid_id_or_nonstring_atomically(self):
        catalog=sentence_catalog(self.document())
        valid=next(iter(catalog))
        for selected in ([valid,"missing"],[valid,True],[],[valid]*9,"not-a-list"):
            answer,cited,validation=render_selection(selected,catalog)
            self.assertIsNone(answer)
            self.assertFalse(cited)
            self.assertFalse(validation["passed"])

    def test_selector_keeps_negation_and_version_metadata(self):
        hit={"id":1,"title":"审批规则","version":3,"chunk_key":"stable","content":"禁止删除数据库记录。"}
        catalog=sentence_catalog([hit])
        answer,cited,validation=render_selection(["1:0"],catalog)
        self.assertEqual(answer,"禁止删除数据库记录。")
        self.assertEqual(validation["sentences"][0]["version"],3)
        self.assertEqual(validation["sentences"][0]["chunk_key"],"stable")

    def test_shadow_features_do_not_change_retrieval_ranking(self):
        self.document()
        basic=retrieve("打印机缺纸","demo",mode="bm25")
        features=retrieve("打印机缺纸","demo",mode="bm25",collect_features=True)
        self.assertEqual([h["id"] for h in basic["hits"]],[h["id"] for h in features["hits"]])
        self.assertEqual(features["features"]["has_vector"],0)
        self.assertEqual(features["features"]["has_bm25"],1)

    def test_vector_failure_falls_back_to_calibrated_bm25(self):
        self.document()
        object.__setattr__(settings,"embedding_provider","local")
        vector=struct.pack("512f",*([1.0]*512))
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?,embedding_model=?",(vector,settings.embedding_model))
        with patch("app.knowledge.embed",side_effect=RuntimeError("simulated failure")):
            result=retrieve("打印机缺纸","demo",mode="vector")
        self.assertTrue(result["hits"])
        self.assertTrue(result["vector_fallback"])
        self.assertEqual(result["vector_state"],"runtime_failed")
        self.assertEqual(result["evidence_mode"],"bm25")

    def test_vector_uses_its_own_threshold_and_no_business_rerank(self):
        self.document()
        object.__setattr__(settings,"embedding_provider","local")
        vector=struct.pack("512f",*([1.0]*512))
        with get_connection() as db:
            db.execute("UPDATE knowledge_chunks SET embedding=?,embedding_model=?",(vector,settings.embedding_model))
        with patch("app.knowledge.embed",return_value=vector), patch("app.knowledge.threshold",return_value=.5) as threshold:
            result=retrieve("打印机缺纸","demo",mode="vector")
        self.assertEqual({call.args for call in threshold.call_args_list},{("vector",)})
        self.assertEqual(result["evidence_mode"],"vector")
        self.assertFalse(result["vector_fallback"])
        self.assertAlmostEqual(result["hits"][0]["score"],1/61)
        from app.main import health
        with patch("app.main.policy",return_value={"default_mode":"vector"}):
            self.assertEqual(health()["retrieval"],"vector")

    def test_timeout_has_no_retry_or_secret_log(self):
        from openai import APITimeoutError
        import httpx
        hits=self.document()
        object.__setattr__(settings,"llm_provider","mimo")
        object.__setattr__(settings,"mimo_api_key","fake-test-only")
        with patch("app.llm.OpenAI") as factory:
            factory.return_value.chat.completions.create.side_effect=APITimeoutError(request=httpx.Request("POST","https://example.invalid"))
            answer,cited=generate_grounded_answer(title="打印机故障",description="打印机缺纸",category="设备",risk_level="低风险",hits=hits,allow_llm=True)
        self.assertIsNone(answer)
        self.assertFalse(cited)
        self.assertEqual(factory.call_args.kwargs["max_retries"],0)
        self.assertEqual(factory.return_value.chat.completions.create.call_count,1)

    def test_evidence_boundary_is_single_predicate(self):
        with patch("app.knowledge.threshold",return_value=1.0), patch("app.knowledge.automation_enabled",return_value=True):
            self.document()
            result=retrieve("打印机","demo",mode="bm25")
            self.assertEqual(result["evidence_score"],1)
            self.assertTrue(result["sufficient"])
        with patch("app.knowledge.threshold",return_value=1.01):
            self.assertFalse(retrieve("打印机","demo",mode="bm25")["sufficient"])
        self.assertEqual(evidence_score([], ["打印机"]),0)

    def test_calibration_fails_closed_and_never_reads_test_rows(self):
        rows=[{"split":"calibration","kind":"answerable","hit":True,"evidence_score":.8,"top_correct":True}]*20
        rows += [{"split":"test","kind":"insufficient","hit":True,"evidence_score":1,"top_correct":False}]*100
        self.assertEqual(calibrate(rows)["threshold"],.8)
        self.assertEqual(calibrate(rows[:19])["threshold"],1.01)

    def test_failed_release_gate_prevents_generation(self):
        self.document()
        with patch("app.knowledge.threshold",return_value=0), patch("app.knowledge.automation_enabled",return_value=False):
            result=retrieve("打印机缺纸","demo",mode="bm25")
        self.assertTrue(result["calibrated_sufficient"])
        self.assertFalse(result["sufficient"])

    def test_backup_restore_and_service_restart(self):
        self.ticket()
        with get_connection() as db:
            db.execute("PRAGMA user_version=0")
        init_database()
        backup=next((self.directory/"backups").glob("*.db"))
        restored=self.directory/"restored.db"
        with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(restored)) as target:
            source.backup(target)
        object.__setattr__(settings,"database_url",str(restored))
        for _ in range(2):
            with TestClient(app) as client:
                self.assertEqual(client.get("/health/live").status_code,200)
                self.assertEqual(retrieval_metrics("demo",30)["ticket_totals"]["total"],1)

    def test_rate_limit_hour_and_day_expiry(self):
        instant=172801
        with patch("app.security.time.time",return_value=instant):
            self.assertTrue(use_quota("ticket-hour","ip",1,3600))
            self.assertTrue(use_quota("llm-day","demo",1,86400))
        with patch("app.security.time.time",return_value=instant+3600):
            self.assertTrue(use_quota("ticket-hour","ip",1,3600))
            self.assertFalse(use_quota("llm-day","demo",1,86400))
        with get_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0],2)

    def test_legacy_rate_migration_and_online_backup(self):
        with get_connection() as db:
            db.execute("PRAGMA user_version=0")
            now=int(time.time())
            db.execute("INSERT INTO rate_limits VALUES('llm-day:old',?,1,NULL)",(str(now//86400),))
            db.execute("INSERT INTO rate_limits VALUES('ticket-hour:old',?,1,NULL)",(str(now//3600-2),))
        self.ticket()
        init_database()
        backups=list((self.directory/"backups").glob("*.db"))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],1)
        with get_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0],1)
        init_database()
        self.assertEqual(len(list((self.directory/"backups").glob("*.db"))),1)

    def test_actual_old_schema_migration_preserves_data(self):
        old=self.directory/"old.db"
        with sqlite3.connect(old) as db:
            db.execute("""CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY,title TEXT,description TEXT,requester TEXT,category TEXT,
                priority TEXT,risk_level TEXT,status TEXT,needs_human_approval INTEGER,answer TEXT,created_at TEXT,updated_at TEXT)""")
            db.execute("INSERT INTO tickets VALUES('OLD','旧工单','原内容','员工','综合','低','低风险','待人工处理',1,'原答案','2026','2026')")
        db.close()
        object.__setattr__(settings,"database_url",str(old))
        init_database()
        with get_connection() as db:
            self.assertEqual(db.execute("SELECT answer FROM tickets WHERE ticket_id='OLD'").fetchone()[0],"原答案")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],0)

    def test_seed_survives_an_empty_mounted_data_volume(self):
        from app.knowledge import seed_demo
        packaged=self.directory/"resources"
        packaged.mkdir()
        (self.directory/"data").mkdir()
        (packaged/"knowledge_base.txt").write_text("打印机模拟规程。检查设备型号和纸盒尺寸，再联系服务台记录错误信息。",encoding="utf-8")
        with patch("app.resources.PROJECT_ROOT",self.directory):
            seed_demo()
        with get_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0],1)

    def test_concurrent_approval_has_one_winner(self):
        self.ticket()
        def approve(_):
            try:
                approve_ticket("TEST-1","demo",True,"审批员","测试")
                return "ok"
            except LookupError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(approve,range(2))),["conflict","ok"])
        with get_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='人工审批'").fetchone()[0],1)

    def test_admin_metrics_tenant_window_and_legacy_denominator(self):
        result={"schema_version":3,"mode":"hybrid","hits":[],"sufficient":False,"latency_ms":20,"vector_state":"runtime_failed","vector_reason":"Timeout"}
        self.ticket(retrieval=result)
        self.ticket(identifier="LEGACY")
        self.ticket(tenant="other",identifier="OTHER",retrieval=result)
        data=retrieval_metrics("demo",7)
        self.assertEqual(data["sample_count"],1)
        self.assertEqual(data["legacy_record_count"],1)
        self.assertEqual(data["zero_retrieval_rate"],1)
        self.assertEqual(data["ticket_totals"]["total"],2)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/admin/retrieval-metrics").status_code,403)
            session,_=create_session()
            client.cookies.set("ticket_session",session)
            self.assertEqual(client.get("/api/v1/admin/retrieval-metrics?days=2").status_code,422)
            self.assertEqual(client.get("/api/v1/admin/retrieval-metrics").json()["sample_count"],1)

    def test_release_gate_is_not_counted_as_missing_evidence(self):
        result={"schema_version":3,"mode":"bm25","hits":[{}],"sufficient":False,
            "calibrated_sufficient":True,"latency_ms":1,"vector_state":"not_used"}
        self.ticket(retrieval=result)
        with get_connection() as db:
            db.execute("UPDATE tickets SET handoff_reason='evaluation_gate'")
        metrics=retrieval_metrics("demo",7)
        self.assertEqual(metrics["insufficient_evidence_rate"],0)
        self.assertEqual(metrics["evaluation_gate_count"],1)
        self.assertEqual(metrics["human_handoff_rate"],1)

    def test_request_id_persisted_through_http_and_audit(self):
        with TestClient(app) as client:
            response=client.post("/api/v1/tickets",json={"title":"打印机无法打印","description":"打印机显示缺纸但纸盒里有纸"})
        data=response.json()
        self.assertEqual(data["request_id"],response.headers["X-Request-ID"])
        with get_connection() as db:
            row=db.execute("SELECT retrieval_json FROM tickets WHERE ticket_id=?",(data["ticket_id"],)).fetchone()
            audit=db.execute("SELECT detail FROM audit_logs WHERE ticket_id=?",(data["ticket_id"],)).fetchone()
        self.assertEqual(json.loads(row[0])["request_id"],data["request_id"])
        self.assertEqual(json.loads(audit[0])["request_id"],data["request_id"])

    def test_bm25_quality_regression_on_frozen_queries(self):
        spec=importlib.util.spec_from_file_location("test_import",ROOT/"scripts/import-demo.py")
        importer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(importer)
        importer.import_simulated(vectors=False)
        cases=json.loads((ROOT/"evaluation/benchmark-cases.json").read_text(encoding="utf-8"))["cases"]
        selected=[c for c in cases if c["split"]=="test" and c["kind"]=="answerable"]
        hits=0
        for case in selected:
            result=retrieve(case["query"],"demo",3,mode="bm25")
            hits += any(h["title"].split()[1] in case["expected_topics"] for h in result["hits"])
        # Frozen first measurement: 51/60; explicit 2-case tolerance for platform
        # tokenization differences. This does not assert generated-answer quality.
        self.assertGreaterEqual(hits,49)


if __name__=="__main__":
    unittest.main()
