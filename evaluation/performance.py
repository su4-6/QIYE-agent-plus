"""Real local HTTP: POST + authenticated GET, no LLM calls."""
import ctypes
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import uvicorn
from app.metrics import percentiles


def process_memory():
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_=[("cb",ctypes.c_ulong),("PageFaultCount",ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ("PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage")]
        counters=Counters()
        counters.cb=ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(counters),counters.cb):
            raise RuntimeError("process memory sampling failed")
        return counters.WorkingSetSize/1024**2
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024


def benchmark(cases,output):
    from app.main import app
    listener=socket.socket()
    listener.bind(("127.0.0.1",0))
    listener.listen(128)
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level="error",lifespan="off"))
    thread=threading.Thread(target=server.run,kwargs={"sockets":[listener]},daemon=True)
    thread.start()
    deadline=time.monotonic()+10
    while not server.started:
        if time.monotonic()>deadline:
            raise RuntimeError("benchmark HTTP service did not start")
        time.sleep(.05)
    baseline=process_memory()
    stop=threading.Event()
    samples=[]
    def monitor():
        while not stop.wait(.02):
            samples.append(process_memory())
    sampler=threading.Thread(target=monitor,daemon=True)
    sampler.start()
    results={}
    selected=[c for c in cases if c["kind"]=="answerable" and c["split"]=="test"]
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}",timeout=60,trust_env=False) as client:
            for concurrency in (1,5,10):
                def submit(i):
                    query=selected[i%len(selected)]["query"]
                    start=time.perf_counter()
                    try:
                        response=client.post("/api/v1/tickets",json={"title":query,"description":query,"requester":"模拟压测"})
                    except httpx.HTTPError as exc:
                        return {"index":i,"status":0,"verified":False,"error_type":type(exc).__name__,"elapsed_ms":(time.perf_counter()-start)*1000}
                    valid=False
                    if response.status_code==201:
                        ticket=response.json()
                        try:
                            fetched=client.get(f"/api/v1/tickets/{ticket['ticket_id']}",headers={"X-Ticket-Token":ticket["access_token"]})
                            valid=fetched.status_code==200 and fetched.json()["ticket_id"]==ticket["ticket_id"] and bool(ticket.get("request_id"))
                        except httpx.HTTPError:
                            valid=False
                    return {"index":i,"status":response.status_code,"verified":valid,"elapsed_ms":(time.perf_counter()-start)*1000}
                start=time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    rows=list(pool.map(submit,range(100)))
                elapsed=time.perf_counter()-start
                results[str(concurrency)]={"requests":100,"successes":sum(r["verified"] for r in rows),
                    "elapsed_seconds":elapsed,"throughput_workflows_per_second":100/elapsed,
                    "latency_ms":percentiles([r["elapsed_ms"] for r in rows])}
                (output/f"http-concurrency-{concurrency}.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")
    finally:
        stop.set()
        sampler.join()
        server.should_exit=True
        thread.join(10)
        listener.close()
    return {"mode":"real local HTTP, LLM disabled; POST + authenticated GET workflow","concurrency":results,
        "baseline_rss_mib":baseline,"sampled_peak_rss_mib":max(samples,default=baseline),
        "memory_scope":"whole benchmark process including BGE model and evaluation data; 20ms sampling"}
