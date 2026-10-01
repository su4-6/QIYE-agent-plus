"""Reproducible benchmark: every run uses an isolated temporary database."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
import shutil
from contextlib import contextmanager
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings
from app.database import get_connection, init_database
from app.knowledge import retrieve
from app.metrics import percentiles
from app.retrieval_health import vector_health
from app.tools import evaluate_priority, evaluate_risk_level
from evaluation.legacy import retrieve_context

DATA = ROOT / "evaluation/benchmark-cases.json"
CORPUS = ROOT / "data/simulated_sops.json"
VARIANTS = {
    "bm25": {"mode":"bm25", "rerank":False},
    "vector": {"mode":"vector", "rerank":False},
    "hybrid_rrf": {"mode":"hybrid", "rerank":False},
    "hybrid": {"mode":"hybrid"},
    "hybrid_no_rewrite": {"mode":"hybrid", "rewrite":False},
    "hybrid_no_title": {"mode":"hybrid", "title_signal":False},
}


def write(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")


@contextmanager
def isolated_directory():
    parent = (ROOT / "data").resolve()
    directory = parent / ("eval-temp-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        if not directory.resolve().is_relative_to(parent) or directory.parent != parent:
            raise RuntimeError("temporary cleanup path outside workspace")
        shutil.rmtree(directory)


def calibrate(rows):
    eligible=[r for r in rows if r["split"]=="calibration" and r["kind"]!="high_risk"]
    best={"threshold":1.01,"accepted":0,"precision":None,"reason":"insufficient_calibration_evidence"}
    for value in sorted({r["evidence_score"] for r in eligible},reverse=True):
        accepted=[r for r in eligible if r["hit"] and r["evidence_score"]>=value]
        correct=sum(r["kind"]=="answerable" and r["top_correct"] for r in accepted)
        precision=correct/len(accepted) if accepted else 0
        if len(accepted)>=20 and precision>=.95 and len(accepted)>best["accepted"]:
            best={"threshold":value,"accepted":len(accepted),"precision":precision,"reason":"calibrated"}
    best["eligible"]=len(eligible)
    return best


def routing(rows, threshold):
    matrix={"tp":0,"fp":0,"tn":0,"fn":0}
    for r in rows:
        if r["split"]!="test":
            continue
        gold=r["kind"]!="answerable" or not r["top_correct"]
        predicted=r["risk_human"] or not r["hit"] or r["evidence_score"]<threshold
        matrix["tp" if gold and predicted else "fp" if predicted else "fn" if gold else "tn"]+=1
    tp,fp,tn,fn=(matrix[k] for k in ("tp","fp","tn","fn"))
    return {"confusion_matrix":matrix,"human_precision":tp/(tp+fp) if tp+fp else None,
            "human_recall":tp/(tp+fn) if tp+fn else None,"auto_coverage":(tn+fn)/(tp+fp+tn+fn)}


def summarize(rows):
    results={}
    for split in ("calibration","test"):
        subset=[r for r in rows if r["split"]==split]
        positives=[r for r in subset if r["kind"]=="answerable"]
        n=len(positives)
        results[split]={"answerable_cases":n,
            **{f"hit_at_{k}":sum(bool(set(r["expected_topics"]) & set(r["topics"][:k])) for r in positives)/n for k in (1,3,5)},
            "mrr_at_5":sum(next((1/(i+1) for i,t in enumerate(r["topics"][:5]) if t in r["expected_topics"]),0) for r in positives)/n,
            "recall_at_3":sum(len(set(r["topics"][:3]) & set(r["expected_topics"]))/len(r["expected_topics"]) for r in positives)/n,
            "zero_retrieval_count":sum(not r["hit"] for r in subset),"all_cases":len(subset),
            "zero_retrieval_rate":sum(not r["hit"] for r in subset)/len(subset),
            "vector_degraded_count":sum(r.get("vector_state") in {"runtime_failed","model_mismatch","index_not_ready"} for r in subset),
            "latency_ms":percentiles([r["latency_ms"] for r in subset])}
    return results


def evaluate(output: Path, *, publish_policy=False, live=False, performance=False, reuse_generation=False):
    cases=json.loads(DATA.read_text(encoding="utf-8"))["cases"]
    assert len(cases)==180 and all(sum(c["split"]==s for c in cases)==90 for s in ("calibration","test"))
    keys=("database_url","embedding_provider","llm_provider","app_env","max_public_hourly","max_llm_daily")
    before={k:getattr(settings,k) for k in keys}
    output.mkdir(parents=True,exist_ok=True)
    try:
        with isolated_directory() as temp:
            for key,value in {"database_url":str(Path(temp)/"benchmark.db"),"embedding_provider":"local","llm_provider":"disabled"}.items():
                object.__setattr__(settings,key,value)
            init_database()
            spec=importlib.util.spec_from_file_location("demo_import",ROOT/"scripts/import-demo.py")
            importer=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(importer)
            start=time.perf_counter()
            importer.import_simulated()
            indexing_seconds=time.perf_counter()-start
            health=vector_health()
            if health["coverage_ratio"]!=1 or health["state"]!="ready":
                raise RuntimeError("真实向量实验未完成：有效向量覆盖率未达到100%")
            with get_connection() as db:
                chunks=[dict(r) for r in db.execute("SELECT id,content,title FROM knowledge_chunks WHERE active=1 ORDER BY id")]
            corpus=[r["content"] for r in chunks]
            content_topics={r["content"]:r["title"].split()[1] for r in chunks}
            retrieve(cases[0]["query"],"demo",mode="hybrid")
            variants,raw={},{}
            for name,options in {"legacy_keyword":{},**VARIANTS}.items():
                rows=[]
                for case in cases:
                    start=time.perf_counter()
                    if name=="legacy_keyword":
                        hits=retrieve_context(case["query"],5,corpus=corpus)
                        topics=[content_topics[h] for h in hits]
                        extra={"evidence_score":0,"vector_state":"not_used"}
                    else:
                        result=retrieve(case["query"],"demo",5,**options)
                        topics=[h["title"].split()[1] for h in result["hits"]]
                        extra={k:result[k] for k in ("evidence_score","vector_state","vector_reason","bm25_ids","vector_ids")}
                    elapsed_ms=(time.perf_counter()-start)*1000
                    priority=evaluate_priority(case["query"],case["query"])
                    risk=evaluate_risk_level(case["query"],case["query"],priority)
                    rows.append({**case,**extra,"topics":topics,"hit":bool(topics),
                        "top_correct":bool(topics and topics[0] in case["expected_topics"]),
                        "risk_human":risk in {"中风险","高风险"},"latency_ms":elapsed_ms})
                raw[name]=rows
                variants[name]=summarize(rows)
            calibrations={m:calibrate(raw[m]) for m in ("bm25","hybrid")}
            thresholds={m:r["threshold"] for m,r in calibrations.items()}
            default="hybrid" if variants["hybrid"]["test"]["hit_at_3"]>=variants["bm25"]["test"]["hit_at_3"] else "bm25"
            policy_data={"version":1,"default_mode":default,"thresholds":thresholds,"calibration":calibrations,
                "cases_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),"corpus_sha256":hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
                "selection_rule":"maximize coverage at precision>=.95 and accepted>=20; higher threshold breaks ties"}
            release_gate={}
            for mode in thresholds:
                decision=routing(raw[mode],thresholds[mode])["confusion_matrix"]
                accepted=decision["tn"]+decision["fn"]
                precision=decision["tn"]/accepted if accepted else 0
                release_gate[mode]={"accepted":accepted,"precision":precision,
                    "passed":accepted>=20 and precision>=.95,
                    "reason":"held_out_precision_below_95pct" if precision<.95 else "insufficient_accepted_samples" if accepted<20 else "passed"}
            policy_data["release_gate"]=release_gate
            write(output/"policy.json",policy_data)
            if publish_policy:
                write(ROOT/"evaluation/policy.json",policy_data)
            summary={"environment":{"python":sys.version,"platform":platform.platform(),"processor":platform.processor(),
                "logical_cpus":os.cpu_count(),"commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
                "working_tree":subprocess.check_output(["git","status","--short"],cwd=ROOT,text=True).strip(),
                "model":health["model"],"model_threads":2,"utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())},
                "dataset":{"topics":60,"chunks":len(chunks),"cases":180,"split":"90 calibration / 90 test","label_source":"AI-assisted synthetic"},
                "indexing_seconds":indexing_seconds,"vector_health":health,"variants":variants,"policy":policy_data,
                "routing":{m:routing(raw[m],thresholds[m]) for m in thresholds},
                "limitations":["Local simulated corpus and AI-assisted labels; not production performance","Citation validity is not semantic faithfulness","Latency excludes initial model warmup"]}
            summary["environment"]["packages"]={name:importlib.metadata.version(name) for name in ("fastembed","sqlite-vec","jieba","langgraph","fastapi","openai","numpy","onnxruntime")}
            summary["environment"]["source_sha256_normalized_lf"]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_text(encoding="utf-8").replace("\r\n","\n").encode()).hexdigest() for folder in ("app","evaluation","scripts") for p in (ROOT/folder).glob("*.py")}
            write(output/"retrieval-raw.json",raw)
            write(output/"summary.json",summary)
            if live or reuse_generation:
                summary["generation"]=live_generation(raw[default],default,thresholds[default],output,resume_only=reuse_generation)
                write(output/"summary.json",summary)
            if performance:
                from evaluation.performance import benchmark
                object.__setattr__(settings,"app_env","test")
                object.__setattr__(settings,"max_public_hourly",10000)
                summary["performance"]=benchmark(cases,output)
            write(output/"summary.json",summary)
            write(output/"manifest.json",{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file() and p.name!="manifest.json"})
            print(json.dumps({"output":str(output),"default_mode":default,"thresholds":thresholds,
                "test_hit_at_3":{n:v["test"]["hit_at_3"] for n,v in variants.items()}},ensure_ascii=False,indent=2))
            return summary
    finally:
        for key,value in before.items():
            object.__setattr__(settings,key,value)


def live_generation(rows,mode,threshold,output,*,resume_only=False):
    from app.llm import generate_grounded_answer,last_generation
    if not resume_only and not settings.mimo_api_key.strip():
        raise RuntimeError("MIMO_API_KEY 未配置；真实生成实验未完成")
    object.__setattr__(settings,"llm_provider","disabled" if resume_only else "mimo")
    selected=[r for r in rows if r["split"]=="test" and r["kind"]=="answerable" and not r["risk_human"] and r["hit"] and r["evidence_score"]>=threshold][:30]
    previous=output/"generation-raw.json"
    runs=json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else []
    if len(runs)>30 or [r["case_id"] for r in runs] != [r["id"] for r in selected[:len(runs)]]:
        raise RuntimeError("已有生成记录与本次候选不匹配，请使用新的输出目录；不会重复调用")
    if resume_only and len(runs)!=len(selected):
        raise RuntimeError("仅复用模式缺少已完成记录，不会调用模型")
    ledger_path=output/"generation-ledger.json"
    ledger=json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {
        "model":settings.mimo_model,"cases_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "corpus_sha256":hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "attempt_ids":[r["case_id"] for r in runs],"call_limit":30,"reserved_output_per_call":1000}
    if ledger["model"]!=settings.mimo_model or ledger["cases_sha256"]!=hashlib.sha256(DATA.read_bytes()).hexdigest() or ledger["corpus_sha256"]!=hashlib.sha256(CORPUS.read_bytes()).hexdigest():
        raise RuntimeError("生成账本的模型或数据版本不匹配；不会再次调用")
    write(ledger_path,ledger)
    for row in selected:
        if row["id"] in {r["case_id"] for r in runs}:
            continue
        if row["id"] in ledger["attempt_ids"] or len(ledger["attempt_ids"])>=30:
            continue  # interrupted/ambiguous requests consume a slot, never retry
        ledger["attempt_ids"].append(row["id"])
        write(ledger_path,ledger)
        result=retrieve(row["query"],"demo",mode=mode,rerank=mode!="bm25")
        start=time.perf_counter()
        answer,citations=generate_grounded_answer(title=row["query"],description=row["query"],category="模拟工单",risk_level="低风险",hits=result["hits"][:4],allow_llm=True,tenant_id="demo")
        detail=last_generation.get()
        runs.append({"case_id":row["id"],"query":row["query"],"answer":answer,"citations":citations,
            "evidence":[{k:h[k] for k in ("id","title","content","version")} for h in result["hits"][:4]],
            "elapsed_ms":(time.perf_counter()-start)*1000,**detail,
            "semantic_review":{"reviewer":"AI review pending","verdict":"pending"}})
        write(output/"generation-raw.json",runs)
        if sum(r.get("usage",{}).get("completion_tokens",0) for r in runs)>30000:
            raise RuntimeError("模型输出预算异常，停止后续请求")
    object.__setattr__(settings,"llm_provider","disabled")
    n=len(runs)
    review_path=output/"semantic-review.json"
    review=json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else None
    if review and review.get("generation_raw_sha256")!=hashlib.sha256(previous.read_bytes()).hexdigest():
        review=None
    return {"calls":len(ledger["attempt_ids"]),"completed_records":n,"call_limit":30,"max_output_tokens_per_call":1000,
        "model":settings.mimo_model,"thinking":"disabled","retries":0,
        "selection":"first 30 held-out low-risk queries passing calibrated evidence threshold; generator experiment bypasses release gate, not automatic production answers",
        "completion_tokens":sum(r.get("usage",{}).get("completion_tokens",0) for r in runs),
        "prompt_tokens":sum(r.get("usage",{}).get("prompt_tokens",0) for r in runs),
        "structured_success_rate":sum(bool(r.get("structured")) for r in runs)/n if n else None,
        "citation_valid_rate":sum(bool(r.get("citations_valid")) for r in runs)/n if n else None,
        "answer_success_rate":sum(bool(r["answer"]) for r in runs)/n if n else None,
        "latency_ms":percentiles([r["elapsed_ms"] for r in runs]),
        "semantic_review":review["summary"] if review else "pending AI review; not included in citation validity"}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=ROOT/"evaluation/results/latest")
    parser.add_argument("--publish-policy",action="store_true")
    parser.add_argument("--live-mimo",action="store_true")
    parser.add_argument("--performance",action="store_true")
    parser.add_argument("--reuse-generation",action="store_true",help="reuse completed results without any LLM requests")
    args=parser.parse_args()
    evaluate(args.output,publish_policy=args.publish_policy,live=args.live_mimo,performance=args.performance,reuse_generation=args.reuse_generation)
