# -*- coding: utf-8 -*-
"""wiki_retrieval.py —— 检索与取证据公共层（分词 / BM25 / 上下文组装 / 引用校验）。

来源（2026-11-12）：分词回退策略、BM25、同节补充上下文、引用 token 规则、prompt 骨架，
移植自 `E:\\RAG\\源码\\rag_core.py`（同一作者项目，RAG 侧已在 27 题上验证过）。

**复制而非 import**：两个项目独立演进，一边改动不应影响另一边。移植时按本项目口径改了四处：
  1. 语料从 policy chunk 改为 claim / normalized 单元，`section` 改用章号或来源划分；
  2. 邻居补充只在同一 `section` 内、且位置相邻时发生，与 RAG 侧规则一致；
  3. 引用校验的已知 id 集合由调用方传入（claim_id 或 `source_id#locator`）；
  4. prompt 里的话术按本项目改写，去掉 RAG 专用表述。

依赖：只用标准库；`jieba` 存在则用词级分词，否则退回字符二元组（与移植前行为一致）。
"""
import math
import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:  # 可选依赖：没有 jieba 也能跑
    import jieba  # type: ignore
    _HAS_JIEBA = True
except Exception:  # noqa: BLE001
    jieba = None
    _HAS_JIEBA = False

if _HAS_JIEBA:  # 静默加载日志，避免污染命令输出
    try:
        jieba.setLogLevel(60)
    except Exception:  # noqa: BLE001
        pass

# 只保留「有信息量」的 token：中文、英文、数字；纯标点丢掉
_KEEP_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def tokenize(text):
    """中文分词：jieba 可用时词级，否则字符二元组。纯标点 token 丢弃。"""
    text = text or ""
    if _HAS_JIEBA:
        toks = [t.strip().lower() for t in jieba.lcut(text)]
    else:
        flat = re.sub(r"\s+", "", text).lower()
        toks = [flat[i:i + 2] for i in range(len(flat) - 1)]
    return [t for t in toks if t and _KEEP_RE.search(t)]


class BM25(object):
    """标准 BM25（k1=1.5, b=0.75）。语料规模在千级以内，直接全量打分即可。"""

    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        # index_text 用于检索（例如把「第12条」这类定位词拼进去），展示仍用 text
        self.tokens = [tokenize(d.get("index_text") or d.get("text", "")) for d in docs]
        self.lens = [len(t) for t in self.tokens]
        self.N = len(docs)
        self.avgdl = (sum(self.lens) / float(self.N)) if self.N else 0.0
        self.tf = [Counter(t) for t in self.tokens]
        self.df = Counter()
        for toks in self.tokens:
            for term in set(toks):
                self.df[term] += 1
        self._idf_cache = {}

    def idf(self, term):
        if term not in self._idf_cache:
            n = self.df.get(term, 0)
            self._idf_cache[term] = math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))
        return self._idf_cache[term]

    def scores(self, query, top_k=None):
        """返回 [(score, doc), ...]，按分数降序；只保留分数 > 0 的。"""
        q = tokenize(query)
        if not q or not self.N:
            return []
        out = []
        for i in range(self.N):
            s = 0.0
            tf = self.tf[i]
            dl = self.lens[i] or 1
            norm = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                s += self.idf(term) * f * (self.k1 + 1) / (f + norm)
            if s > 0:
                out.append((s, self.docs[i]))
        out.sort(key=lambda x: (-x[0], str(x[1].get("id"))))
        return out[:top_k] if top_k else out


def build_index(docs):
    return BM25(docs)


# ---------------- 上下文组装 ----------------
def assemble_context(hits, docs, radius=1, max_chars=6000):
    """把检索命中组装成生成用上下文，并按需补**同一 section 内相邻**的片段。

    移植自 RAG `rag_core.assemble_context`，铁律不变：
      - 真实命中集合与顺序**原样保留**，本函数不新增、不删除、不重排命中；
      - 只补同一 section 内、位置相邻的片段；
      - 补充片段带 `origin="neighbor"`，展示与评测都不得当命中看待；
      - `max_chars` 只约束补充片段，真实命中永远完整保留。
    """
    if not hits:
        return {"contexts": [], "hits": [], "neighbors": [], "neighbor_count": 0,
                "hit_count": 0, "hit_chars": 0, "total_chars": 0, "max_chars": max_chars}
    position = {d.get("id"): i for i, d in enumerate(docs)}
    hit_ids = {d.get("id") for _, d in hits}

    groups = {}
    for rank, (score, d) in enumerate(hits, 1):
        key = (d.get("source_id") or d.get("page_path") or "", d.get("section") or "")
        g = groups.setdefault(key, {"best_rank": rank, "items": {}})
        g["best_rank"] = min(g["best_rank"], rank)
        pos = position.get(d.get("id"), len(docs))
        g["items"][pos] = dict(d, score=round(score, 4), rank=rank, origin="hit")

    for rank, (score, d) in enumerate(hits, 1):
        pos = position.get(d.get("id"))
        if pos is None:
            continue
        key = (d.get("source_id") or d.get("page_path") or "", d.get("section") or "")
        g = groups[key]
        for j in range(max(0, pos - radius), min(len(docs), pos + radius + 1)):
            nd = docs[j]
            if nd.get("id") in hit_ids or j in g["items"]:
                continue
            if (nd.get("source_id") or nd.get("page_path")) != (d.get("source_id") or d.get("page_path")):
                continue
            if (nd.get("section") or "") != (d.get("section") or ""):
                continue
            g["items"][j] = dict(nd, score=None, rank=rank, origin="neighbor")

    ordered = []
    for key in sorted(groups, key=lambda k: (groups[k]["best_rank"], k)):
        for pos in sorted(groups[key]["items"]):
            ordered.append(groups[key]["items"][pos])

    hit_chars = sum(len(e.get("text") or "") for e in ordered if e["origin"] == "hit")
    total = hit_chars
    kept = []
    for e in ordered:
        if e["origin"] == "hit":
            kept.append(e)
            continue
        n = len(e.get("text") or "")
        if total + n > max_chars:
            continue
        total += n
        kept.append(e)

    return {
        "contexts": kept,
        "hits": [e for e in kept if e["origin"] == "hit"],
        "neighbors": [e for e in kept if e["origin"] == "neighbor"],
        "hit_count": sum(1 for e in kept if e["origin"] == "hit"),
        "neighbor_count": sum(1 for e in kept if e["origin"] == "neighbor"),
        "hit_chars": hit_chars,
        "total_chars": total,
        "max_chars": max_chars,
    }


# ---------------- 引用校验 ----------------
# 与 RAG 侧同一条规则：`[id]`，右方括号后不能紧接 `(`（排除 markdown 链接文字）
_REF_TOKEN_RE = re.compile(r"\[([^\]\n]{1,120})\](?!\()")
_REF_PREFIX_RE = re.compile(r"^(?:来源|出处)\s*[:：]?\s*")


def normalize_ref_token(raw):
    text = (raw or "").strip().strip("`").strip()
    return _REF_PREFIX_RE.sub("", text).strip().strip("`").strip()


def extract_refs(answer, known_ids):
    """从回答里提取引用 id：按首次出现去重，只保留能与已知 id 对上的。"""
    known = set(known_ids)
    out = []
    for m in _REF_TOKEN_RE.finditer(answer or ""):
        rid = normalize_ref_token(m.group(1))
        if rid in known and rid not in out:
            out.append(rid)
    return out


def count_ref_occurrences(answer):
    return len(_REF_TOKEN_RE.findall(answer or ""))


# ---------------- prompt 骨架 ----------------
def build_prompt(question, contexts, corpus_name="新能源政策 Wiki", layer=None):
    """只依据给定材料作答；材料不足必须说「未找到」，不许补。"""
    blocks = []
    for d in contexts:
        tag = "" if d.get("origin") == "hit" else "（同节补充，非独立命中）"
        if d.get("answer_kind") == "unknown":
            tag += "（官方未明确：本条是证据边界声明）"
        ref = d.get("id") or ""
        where = d.get("section") or ""
        head = "[%s]%s%s " % (ref, ("《%s》" % where) if where else "", tag)
        text = (d.get("text") or "").strip()
        blocks.append(head + text)
    refs = "\n\n".join(blocks)
    layer_note = ""
    if layer == "L1":
        layer_note = (
            "下面的材料是已编译的结论（每条带 policy_as_of 与适用条件）。"
            "请按材料给出的条件如实回答；条件不覆盖问题情形时，说明「现有材料不足以确认」，不要外推。"
            "标了「官方未明确」的材料是证据边界声明：须如实回答官方未明确，"
            "不得用邻近档位、其他情形或常识类推。\n"
        )
    elif layer == "L2":
        layer_note = (
            "下面的材料是政策原文片段。请引用原话回答，不要改写条文措辞，也不要把原文片段当成结论外推。\n"
        )
    return (
        "你是「%s」的资料问答助手。请【只依据】下面【资料】回答，并标注引用（如 [W-REG-07]）。\n"
        "%s"
        "标了「同节补充」的片段只用于补全同一处列举，可以引用，但不要说成另一次独立检索。\n"
        "资料没有覆盖的内容，回答「现有材料不足以确认」；不要编造，也不要用常识补条文。\n\n"
        "【资料】\n%s\n\n【问题】\n%s\n\n【回答】" % (corpus_name, layer_note, refs, question)
    )
