"""Reproduce the supplied 18-topic/28-query experiment independently."""
import json
import re
import sqlite3
import time
from pathlib import Path
import jieba
from app.metrics import percentiles


def tokens(text):
    result=[]
    for piece in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+",text.casefold()):
        result.extend([piece] if re.fullmatch(r"[a-z0-9]+",piece) else [w.strip() for w in jieba.cut(piece) if len(w.strip())>1])
    return result


def evaluate():
    fixture=json.loads(Path(__file__).with_name("external-fixture.json").read_text(encoding="utf-8"))
    db=sqlite3.connect(":memory:")
    db.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY,title TEXT,content TEXT)")
    db.execute("CREATE VIRTUAL TABLE words USING fts5(search_tokens,tokenize='unicode61')")
    db.execute("CREATE VIRTUAL TABLE tri USING fts5(content,tokenize='trigram')")
    gold={}
    for i,(identifier,title,content) in enumerate(fixture["CORPUS"],1):
        gold[identifier]=i
        db.execute("INSERT INTO chunks VALUES(?,?,?)",(i,title,content))
        db.execute("INSERT INTO words(rowid,search_tokens) VALUES(?,?)",(i," ".join(tokens(title+" "+content))))
        db.execute("INSERT INTO tri(rowid,content) VALUES(?,?)",(i,content))
    def like(query):
        terms=list(dict.fromkeys(tokens(query)))[:12]
        scored=[(i,sum(t in (title+content).casefold() for t in terms)) for i,title,content in db.execute("SELECT * FROM chunks")]
        return [i for i,score in sorted(scored,key=lambda x:(-x[1],x[0])) if score][:5]
    def match(query,trigram=False):
        terms=list(dict.fromkeys(tokens(query)))[:12]
        if trigram:
            terms=[t for t in terms if len(t)>=3]
        expression=" OR ".join('"'+t.replace('"','')+'"' for t in terms)
        if not expression:
            return like(query) if trigram else []
        table="tri" if trigram else "words"
        return [r[0] for r in db.execute(f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table}) LIMIT 5",(expression,))]
    results={}
    for name,fn in {"fts5_words":match,"like":like,"trigram":lambda q:match(q,True)}.items():
        rows=[]
        for query,expected in fixture["QUERIES"]:
            start=time.perf_counter()
            ids=fn(query)
            rows.append({"query":query,"expected":gold[expected],"ids":ids,"latency_ms":(time.perf_counter()-start)*1000})
        n=len(rows)
        results[name]={"cases":n,**{f"hit_at_{k}":sum(r["expected"] in r["ids"][:k] for r in rows)/n for k in (1,3,5)},
            "mrr_at_5":sum(1/(r["ids"].index(r["expected"])+1) if r["expected"] in r["ids"] else 0 for r in rows)/n,
            "zero_retrieval":sum(not r["ids"] for r in rows),"latency_ms":percentiles([r["latency_ms"] for r in rows]),"raw":rows}
    db.close()
    return {"corpus":18,"queries":28,"scope":"isolated in-memory lexical comparison, no vectors", "results":results}


if __name__=="__main__":
    print(json.dumps(evaluate(),ensure_ascii=False,indent=2))
