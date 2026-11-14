"""Frozen BM25 and local vector-ranking reuse. No provider generation or grading input."""
import hashlib
import json
import math
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
PLAN=ROOT.parent/'项目资料/发展规划'
EVIDENCE=PLAN/'165-证据-第三四层收口'
SNAPSHOT=ROOT/'tools/dify_public/public-snapshot.json'
SNAPSHOT_SHA256='2c3a69ffa44ad2523e51329c60d1aff8f6b8121c4a3ecb8ef23c8fa4bbb83ad2'
MODEL_REVISION='f9972ac22e696a105c482202646979e33e4b5a1e'
OLD=PLAN/'162-证据-EmbeddingGemma2本地检索'


def read(path):return json.loads(Path(path).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def query_key(query):return hashlib.sha256(query.encode('utf-8')).hexdigest()


def save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


class LocalRetriever:
    def __init__(self,snapshot_path=SNAPSHOT,vector_paths=None):
        if sha(snapshot_path)!=SNAPSHOT_SHA256:raise ValueError('snapshot_identity_mismatch')
        self.snapshot=read(snapshot_path)
        self.chunks=sorted(self.snapshot['chunks'],key=lambda c:c['chunk_id'])
        self.ids={c['chunk_id'] for c in self.chunks}
        self.vectors={}
        self.docs=None
        self.tokenizer=None
        self.model=None
        for path in vector_paths or []:self.add_vectors(path)

    def add_vectors(self,path):
        data=read(path)
        if data['snapshot_sha256']!=SNAPSHOT_SHA256 or data['model_revision']!=MODEL_REVISION:
            raise ValueError('vector_identity_mismatch')
        for row in data['queries']:
            query=row['question'];ranks=row['ranking']
            if len(ranks)!=len(self.ids) or {r['chunk_id'] for r in ranks}!=self.ids:
                raise ValueError('vector_chunk_mapping_mismatch')
            if any(not isinstance(r['score'],(int,float)) or not math.isfinite(r['score']) for r in ranks):
                raise ValueError('nonfinite_vector_score')
            if ranks!=sorted(ranks,key=lambda r:(-r['score'],r['chunk_id'])):raise ValueError('vector_rank_order_mismatch')
            key=query_key(query)
            if key in self.vectors and self.vectors[key]['ranking']!=ranks:raise ValueError('vector_query_conflict')
            self.vectors[key]=row

    def _bm25(self):
        if self.model is not None:return
        import jieba
        sys.path.insert(0,str(ROOT))
        import rag_core
        from v2.retrieve import CORE_SHA256
        if sha(ROOT/'rag_core.py').upper()!=CORE_SHA256 or jieba.__version__!='0.42.1':
            raise ValueError('bm25_implementation_drift')
        self.tokenizer=jieba.Tokenizer()
        cache=EVIDENCE/'runtime-cache'
        cache.mkdir(exist_ok=True)
        self.tokenizer.tmp_dir=str(cache);self.tokenizer.cache_file='jieba.cache'
        self.docs=[dict(c,tokens=[t for t in self.tokenizer.lcut(c['search_text']) if t.strip()]) for c in self.chunks]
        self.model=rag_core.BM25(self.docs)

    def retrieve(self,query,method='A',combined=None,depth=60):
        if not isinstance(query,str) or not query.strip():raise ValueError('empty_query')
        if method not in ['A','D','V'] or type(depth) is not int or not 1<=depth<=505:raise ValueError('invalid_route')
        start=time.perf_counter()
        if method=='V':
            row=self.vectors.get(query_key(query))
            if row is None:raise ValueError('vector_query_not_encoded')
            ranks=row['ranking'][:depth]
            return dict(ranking=ranks,method=method,query=query,cache_lookup_seconds=time.perf_counter()-start,
                        query_encode_seconds=row.get('encode_seconds'),query_encoding_cached=True,
                        answerability_threshold_calibrated=False)
        self._bm25()
        search_query=combined if method=='D' and combined else query
        if not isinstance(search_query,str) or not search_query.strip():raise ValueError('empty_combined_query')
        tokens=[t for t in self.tokenizer.lcut(search_query) if t.strip()]
        scores=[(self.model._score(tokens,d),d['chunk_id']) for d in self.docs]
        scores.sort(key=lambda r:(-r[0],r[1]))
        return dict(ranking=[dict(score=s,chunk_id=i) for s,i in scores if s>0][:depth],method=method,
                    query=search_query,retrieval_seconds=time.perf_counter()-start,query_encoding_cached=False)


def export_old_vectors(out):
    vectors=read(OLD/'vectors.json')
    queries=read(OLD/'queries.json')
    save(out,dict(snapshot_sha256=SNAPSHOT_SHA256,model_revision=MODEL_REVISION,
        source_sha256=sha(OLD/'vectors.json'),queries=[dict(question=q['question'],ranking=vectors['rows'][q['id']]['ranking'],
            encode_seconds=vectors['rows'][q['id']]['timings'][0]['encode_seconds']) for q in queries]))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--export-old',type=Path,required=True)
    export_old_vectors(p.parse_args().export_old)
