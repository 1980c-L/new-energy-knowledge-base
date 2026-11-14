# -*- coding: utf-8 -*-
"""BM25 vs EmbeddingGemma2 同语料同题对照（检索候选层，不进入 rules/assess）。

用法（项目根）：
  python tools/retrieval_eval.py                 # BM25 + 向量（若环境可用）
  python tools/retrieval_eval.py --no-embedding  # 只跑 BM25
输出：eval/retrieval/results.json（每次覆盖）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = ROOT / "normalized"
OUT_DIR = ROOT / "eval" / "retrieval"
QUESTIONS = OUT_DIR / "questions.jsonl"
MODEL_DIR = Path(os.environ.get("EMBEDDINGGEMMA_DIR", r"E:\hf-cache\models\embeddinggemma-2"))
QUERY_PREFIX = "task: search result | query: "
DOC_PREFIX = "title: {title} | text: {text}"

# 语料：条款 + 问答 + 解读小节（不含 metadata/link/page 导航记录）
CORPUS_FILES = [
    ("clause", "PV-NAT-2025-007-ATTACH.jsonl"),
    ("qa", "PV-FAQ-20250411.jsonl"),
    ("section", "PV-EXPLAIN-20250123.jsonl"),
]


def norm_text(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s or "")


def tokens(text: str) -> list[str]:
    """中文按字符 bigram，英文/数字按词；保留条款号。"""
    text = norm_text(text)
    toks: list[str] = []
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    toks.extend(re.findall(r"[A-Za-z0-9]+(?:\.[0-9]+)?", text))
    return toks


def load_corpus() -> list[dict]:
    docs = []
    for kind, fname in CORPUS_FILES:
        path = NORMALIZED / fname
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("kind") != kind:
                continue
            text = rec.get("text") or ""
            title = rec.get("section") or rec.get("chapter") or rec.get("value") or ""
            docs.append({
                "locator_id": rec["locator_id"],
                "kind": kind,
                "title": title,
                "text": text,
            })
    return docs


def load_questions() -> list[dict]:
    qs = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            qs.append(json.loads(line))
    return qs


class BM25:
    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.doc_tokens = [tokens(d["text"]) for d in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avg_len = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.tf = [Counter(t) for t in self.doc_tokens]
        df = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        self.idf = {t: math.log(1 + (len(docs) - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        q_toks = tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q_toks:
                if t not in tf:
                    continue
                f = tf[t]
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.max_avg())
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            scores.append((i, s))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    def max_avg(self) -> float:
        return self.avg_len or 1.0


def load_embedder():
    import torch
    from transformers import AutoModel, AutoTokenizer

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model = AutoModel.from_pretrained(str(MODEL_DIR), local_files_only=True, dtype=torch.float32)
    model.eval()

    def encode(texts: list[str]) -> "torch.Tensor":
        batch = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            out = model(**batch)
        hidden = out.last_hidden_state
        mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        emb = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb

    return tok, encode


def evaluate(rankings: dict[str, list[str]], questions: list[dict], k: int = 5) -> dict:
    """rankings: question_id -> top-k locator ids"""
    rows = []
    rr_sum = 0.0
    hit1 = hit5 = 0
    for q in questions:
        gold = q["gold_locator"]
        ranked = rankings.get(q["question_id"], [])
        try:
            rank = ranked.index(gold) + 1
        except ValueError:
            rank = 0
        rr_sum += 1.0 / rank if rank else 0.0
        hit1 += 1 if rank == 1 else 0
        hit5 += 1 if 0 < rank <= k else 0
        rows.append({"question_id": q["question_id"], "rank": rank, "top1": ranked[:1], "top5": ranked[:k]})
    n = max(len(questions), 1)
    return {
        "n": len(questions),
        "recall_at_1": round(hit1 / n, 4),
        "recall_at_5": round(hit5 / n, 4),
        "mrr": round(rr_sum / n, 4),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embedding", action="store_true")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    docs = load_corpus()
    questions = load_questions()
    print(f"corpus: {len(docs)} units; questions: {len(questions)}")

    bm25 = BM25(docs)
    bm_rank = {}
    for q in questions:
        hits = bm25.search(q["question"], top_k=args.top_k)
        bm_rank[q["question_id"]] = [docs[i]["locator_id"] for i, _ in hits]
    bm = evaluate(bm_rank, questions, args.top_k)
    print(f"BM25      R@1={bm['recall_at_1']} R@5={bm['recall_at_5']} MRR={bm['mrr']}")

    result = {
        "batch": "pv-national-2025-v1",
        "corpus_units": len(docs),
        "corpus_by_kind": dict(Counter(d["kind"] for d in docs)),
        "questions": len(questions),
        "top_k": args.top_k,
        "bm25": bm,
        "embedding": None,
        "note": "向量只作候选召回对照，不进入 rules/assess，也不作为主张真假判据。",
    }

    if not args.no_embedding:
        try:
            tok, encode = load_embedder()
            import torch
            doc_texts = [DOC_PREFIX.format(title=d["title"] or "none", text=d["text"]) for d in docs]
            with torch.no_grad():
                doc_emb = encode(doc_texts)
                q_emb = encode([QUERY_PREFIX + q["question"] for q in questions])
            sims = q_emb @ doc_emb.T
            emb_rank = {}
            for qi, q in enumerate(questions):
                idx = torch.topk(sims[qi], k=args.top_k).indices.tolist()
                emb_rank[q["question_id"]] = [docs[i]["locator_id"] for i in idx]
            em = evaluate(emb_rank, questions, args.top_k)
            print(f"Embedding R@1={em['recall_at_1']} R@5={em['recall_at_5']} MRR={em['mrr']}")
            result["embedding"] = {
                "model": "google/embeddinggemma-2",
                "model_dir": str(MODEL_DIR),
                "revision": "f9972ac22e696a105c482202646979e33e4b5a1e1",
                "dim": int(doc_emb.shape[1]),
                "normalized": True,
                "dtype": "float32",
                "query_prefix": QUERY_PREFIX,
                "doc_prefix": DOC_PREFIX,
                **em,
            }
        except Exception as exc:  # 环境不可用时仍保留 BM25 结果
            print(f"[WARN] embedding unavailable: {type(exc).__name__}: {exc}")
            result["embedding_error"] = f"{type(exc).__name__}: {exc}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written {out} ({out.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
