"""复用原BM25打分；只在显式选择的冻结片段上计算联合IDF。"""
import hashlib
import logging
from pathlib import Path
import jieba
import rag_core

REPO = Path(__file__).resolve().parents[1]
CORE_SHA256 = "E2BF2FDEB9270E96C46CD0D4558AB909E203C4D1DEEEB3ECFE969B891A53AD4C"
CONFIG = {"method": "BM25", "k1": 1.5, "b": 0.75, "tokenizer": "jieba-0.42.1",
          "idf_scope": "joint_requested_collections", "tie_break": "chunk_id_ascending",
          "query_expansion": False, "core_sha256": CORE_SHA256}
_tokenizer = None


def configuration():
    return {**CONFIG, "dictionary_sha256": hashlib.sha256((Path(jieba.__file__).parent / "dict.txt").read_bytes()).hexdigest()}


def tokenizer():
    global _tokenizer
    if _tokenizer is None:
        if jieba.__version__ != "0.42.1":
            raise ValueError("分词器版本变更，需重新登记配置")
        cache = REPO / ".run/v2/tokenizer"
        cache.mkdir(parents=True, exist_ok=True)
        jieba.setLogLevel(logging.WARNING)
        _tokenizer = jieba.Tokenizer()
        _tokenizer.tmp_dir = str(cache)
        _tokenizer.cache_file = "jieba-s1.cache"
    return _tokenizer


def search(snapshot, query, collections, top_k=5):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("请输入非空问题")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k须为1至20的整数")
    if not isinstance(collections, list) or not collections or len(collections) != len(set(collections)) or any(c not in {"rag_notes", "agent_tools"} for c in collections):
        raise ValueError("须显式选择一个集合；跨库时显式选择两个集合")
    if hashlib.sha256((REPO / "rag_core.py").read_bytes()).hexdigest().upper() != CORE_SHA256:
        raise ValueError("复用BM25源码身份改变，拒绝以旧配置继续查询")
    selected = sorted((c for c in snapshot["chunks"] if c["collection"] in collections), key=lambda c: c["chunk_id"])
    if not selected:
        return []
    cut = tokenizer().lcut
    docs = [{**c, "tokens": [t for t in cut(c["search_text"]) if t.strip()]} for c in selected]
    model = rag_core.BM25(docs)
    query_tokens = [t for t in cut(query) if t.strip()]
    scored = [(model._score(query_tokens, d), d) for d in docs]
    scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
    return [{"rank": i, "score": score, **{k: v for k, v in chunk.items() if k != "tokens"},
             "preview_revision": snapshot["preview_revision"]}
            for i, (score, chunk) in enumerate((x for x in scored if x[0] > 0), 1) if i <= top_k]
