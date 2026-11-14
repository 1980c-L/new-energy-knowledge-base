# -*- coding: utf-8 -*-
"""t3_eval_run.py —— 12 题实跑（M6）。

两部分，互不替代：

A 检索实跑（纯机器，可复跑）
  用 wiki_tool 同一套词法打分：先在页面正文上取 top-N 页，再在 claim_text 上取 top-K 条，
  合并成「只用 Wiki 层能取到的证据池」，检查它是否覆盖 reference.jsonl 的证据组。
  这一部分不受作者主观影响，回答的是「Wiki 的检索入口够不够用」。

B 答案对照（作者按 Wiki 作答，机器只查引用与覆盖面）
  读 eval/results/answers.jsonl，检查：
    - 引用的 claim_id 都存在；
    - 额外引用的 raw 证据能定位到 normalized 单元；
    - 引用证据（claim 的 source_refs + raw 证据）覆盖 reference 的证据组；
    - 没有引用 eval_only 来源；
    - claim_status=unknown 的题（eval case type=unknown）至少引用一条 unknown 主张；
    - answers 里声明的必含表述真的出现在答案文本里（字面检查，不判断语义）。
  这一部分回答的是「按 Wiki 能不能把题答全」，**作者与出题者同一人（non_blind），不等于独立审查**。

用法：
    python tools\\t3_eval_run.py [--top-pages 2] [--top-claims 8]
输出：
    eval/results/retrieval-run.json   检索实跑明细
    eval/results/summary.json         两部分汇总 + 缺口清单
退出码：0 两部分全过；1 有缺口或不合规项。
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wiki_tool as wt  # noqa: E402
import wiki_retrieval as wr  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(HERE)


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def claim_refs(proj, ids):
    """claim_id 集合 → {(source_id, locator)}，含 derivation 的 basis_source_refs。"""
    keys = set()
    for cid in ids:
        c = proj.claim_by_id.get(cid) or {}
        for r in list(c.get("source_refs") or []) + list((c.get("derivation") or {}).get("basis_source_refs") or []):
            if isinstance(r, dict) and "source_id" in r and "locator" in r:
                keys.add((r["source_id"], r["locator"]))
    return keys


def expected_keys(ref):
    return {(e["source_id"], e["locator"]) for e in ref.get("evidence") or []}


def build_indices(proj):
    """页面索引与主张索引各建一次（BM25 需要全语料统计，不能逐文档打分）。"""
    page_docs, claim_docs = [], []
    for p in wt.PAGES:
        path = os.path.join(proj.root, p.replace("/", os.sep))
        if os.path.exists(path):
            page_docs.append({"id": p, "text": wt.read_text(path), "page_path": p, "section": p})
    for c in proj.claims:
        claim_docs.append({"id": c.get("claim_id"), "text": c.get("claim_text") or "",
                           "page_path": c.get("page_path"), "section": c.get("page_path")})
    return wr.build_index(page_docs), wr.build_index(claim_docs)


def retrieve(proj, query, page_idx, claim_idx, top_pages, top_claims):
    """Wiki 层检索：页面正文 top-N + 主张文本 top-K（BM25，与 search 同一引擎）。"""
    pages = [d["id"] for _, d in page_idx.scores(query, top_k=top_pages)]
    claim_hits = [(s, d["id"]) for s, d in claim_idx.scores(query, top_k=top_claims)]
    ids = [c.get("claim_id") for c in proj.claims if c.get("page_path") in pages]
    for _, cid in claim_hits:
        if cid not in ids:
            ids.append(cid)
    return pages, ids, [cid for _, cid in claim_hits]


def first_evidence_rank(proj, query, claim_idx, need):
    """按 BM25 全量排名，找第一条能提供标准证据的主张；返回名次或 None。"""
    if not need:
        return None
    for rank, (_score, d) in enumerate(claim_idx.scores(query), 1):
        if claim_refs(proj, [d["id"]]) & need:
            return rank
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="12 题实跑（检索 + 答案对照）")
    ap.add_argument("--top-pages", dest="top_pages", type=int, default=2)
    ap.add_argument("--top-claims", dest="top_claims", type=int, default=8)
    args = ap.parse_args(argv)

    proj = wt.Project(ROOT)
    proj.load_sources()
    proj.load_claims()

    cases = load_jsonl(os.path.join(ROOT, "eval", "cases.jsonl"))
    refs = {r["case_id"]: r for r in load_jsonl(os.path.join(ROOT, "eval", "reference.jsonl"))}
    answers = {a["case_id"]: a for a in load_jsonl(os.path.join(ROOT, "eval", "results", "answers.jsonl"))}

    page_idx, claim_idx = build_indices(proj)
    retrieval, failures = [], []
    for case in cases:
        cid = case["case_id"]
        ref = refs.get(cid) or {}
        need = expected_keys(ref)
        pages, pool, top_claims = retrieve(proj, case["question"], page_idx, claim_idx,
                                           args.top_pages, args.top_claims)
        got = claim_refs(proj, pool)
        missing = sorted(need - got)
        ok = not missing
        rank = first_evidence_rank(proj, case["question"], claim_idx, need)
        retrieval.append({
            "case_id": cid, "type": case.get("type"), "question": case["question"],
            "pages_hit": pages, "pool_claims": len(pool), "top_claims": top_claims,
            "evidence_expected": sorted(need), "evidence_missing": missing, "covered": ok,
            "first_rank": rank,
        })
        if not ok:
            failures.append({"part": "retrieval", "case_id": cid,
                             "detail": "Wiki 层证据池未覆盖：%s" % missing})

    answer_report, cover_ok = [], 0
    for case in cases:
        cid = case["case_id"]
        ref = refs.get(cid) or {}
        need = expected_keys(ref)
        a = answers.get(cid)
        rec = {"case_id": cid, "type": case.get("type")}
        if not a:
            rec.update({"present": False})
            failures.append({"part": "answer", "case_id": cid, "detail": "缺答案记录"})
            answer_report.append(rec)
            continue
        bad_ids = [x for x in a.get("cited_claim_ids") or [] if x not in proj.claim_by_id]
        raw = a.get("raw_evidence") or []
        raw_bad = []
        for r in raw:
            ok_res, why = proj.resolve(r.get("source_id"), r.get("locator"))
            if not ok_res:
                raw_bad.append({"ref": r, "why": why})
        eval_only = [(r.get("source_id"), r.get("locator")) for r in raw
                     if (proj.source_by_id.get(r.get("source_id")) or {}).get("role") == "eval_only"]
        eval_only += [(sid, loc) for sid, loc in claim_refs(proj, a.get("cited_claim_ids") or [])
                      if (proj.source_by_id.get(sid) or {}).get("role") == "eval_only"]
        got = claim_refs(proj, a.get("cited_claim_ids") or []) | {(r.get("source_id"), r.get("locator")) for r in raw}
        missing = sorted(need - got)
        unknown_ok = True
        if case.get("type") == "unknown":
            unknown_ok = any((proj.claim_by_id.get(x) or {}).get("claim_status") == "unknown"
                             for x in a.get("cited_claim_ids") or [])
        phrases = a.get("must_include") or []
        phrase_ok = all(p in (a.get("answer") or "") for p in phrases)
        ok = not (bad_ids or raw_bad or eval_only or missing or not unknown_ok or not phrase_ok)
        rec.update({
            "present": True, "covered": not missing, "evidence_missing": missing,
            "bad_claim_ids": bad_ids, "bad_raw_evidence": raw_bad, "eval_only_used": eval_only,
            "unknown_claim_cited": unknown_ok, "phrases_ok": phrase_ok,
            "layer": a.get("layer"), "ok": ok,
        })
        answer_report.append(rec)
        cover_ok += 1 if not missing else 0
        if not ok:
            failures.append({"part": "answer", "case_id": cid,
                             "detail": {"missing": missing, "bad_claim_ids": bad_ids,
                                        "bad_raw": raw_bad, "eval_only": eval_only,
                                        "unknown_claim_cited": unknown_ok, "phrases_ok": phrase_ok}})

    os.makedirs(os.path.join(ROOT, "eval", "results"), exist_ok=True)
    run_path = os.path.join(ROOT, "eval", "results", "retrieval-run.json")
    with open(run_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                             "top_pages": args.top_pages, "top_claims": args.top_claims,
                             "cases": retrieval}, ensure_ascii=False, indent=2) + "\n")

    # 检索排名指标（2026-11-12 新增，口径与 RAG 侧 04_eval 一致：拒答题单列不计分）
    SCORED_TYPES = {"lookup", "condition", "temporal", "synthesis", "unknown"}
    scored = [r for r in retrieval if r.get("type") in SCORED_TYPES]
    ranks = [r["first_rank"] for r in scored if r.get("first_rank")]
    metrics = {
        "engine": "BM25(k1=1.5,b=0.75)；分词=%s" % ("jieba" if wr._HAS_JIEBA else "字符二元组"),
        "scored_cases": len(scored),
        "excluded_out_of_scope": [r["case_id"] for r in retrieval if r.get("type") == "out_of_scope"],
        "hit_at_1": round(sum(1 for x in ranks if x <= 1) / float(len(scored)), 4) if scored else 0.0,
        "hit_at_3": round(sum(1 for x in ranks if x <= 3) / float(len(scored)), 4) if scored else 0.0,
        "hit_at_5": round(sum(1 for x in ranks if x <= 5) / float(len(scored)), 4) if scored else 0.0,
        "mrr": round(sum(1.0 / x for x in ranks) / float(len(scored)), 4) if scored else 0.0,
        "no_evidence_found": [r["case_id"] for r in scored if not r.get("first_rank")],
        "rank_detail": [{"case_id": r["case_id"], "type": r.get("type"),
                         "first_rank": r.get("first_rank")} for r in retrieval],
    }

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cases": len(cases),
        "retrieval_covered": sum(1 for r in retrieval if r["covered"]),
        "answer_covered": cover_ok,
        "answers_present": len(answers),
        "non_blind": True,
        "reviewer": "CodeBuddy（作者本人，出题与作答同一人；不等于独立审查）",
        "independent_review": "pending",
        "failures": failures,
        "retrieval_metrics": metrics,
        "retrieval_detail": [{"case_id": r["case_id"], "covered": r["covered"],
                              "missing": r["evidence_missing"]} for r in retrieval],
        "answer_detail": answer_report,
        "exit_code": 0 if not failures else 1,
    }
    sum_path = os.path.join(ROOT, "eval", "results", "summary.json")
    with open(sum_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    for r in retrieval:
        print("%s %-14s 检索覆盖=%s%s" % (r["case_id"], r["type"], "是" if r["covered"] else "否",
                                        "" if r["covered"] else "  缺 %s" % r["evidence_missing"]))
    for r in answer_report:
        if r.get("present"):
            print("%s %-14s 答案合规=%s%s" % (r["case_id"], r["type"], "是" if r.get("ok") else "否",
                                            "" if r.get("ok") else "  缺 %s" % r.get("evidence_missing")))
        else:
            print("%s %-14s 答案缺失" % (r["case_id"], r["type"]))
    print("检索覆盖 %d/%d；答案覆盖 %d/%d；缺口 %d 项" % (
        summary["retrieval_covered"], len(cases), cover_ok, len(cases), len(failures)))
    print("排名指标（%s，计分 %d 题，超出范围 %d 题单列）：Hit@1=%.4f Hit@3=%.4f Hit@5=%.4f MRR=%.4f" % (
        "BM25", metrics["scored_cases"], len(metrics["excluded_out_of_scope"]),
        metrics["hit_at_1"], metrics["hit_at_3"], metrics["hit_at_5"], metrics["mrr"]))
    if metrics["no_evidence_found"]:
        print("未找到标准证据的题：%s" % metrics["no_evidence_found"])
    print("明细：eval/results/retrieval-run.json、eval/results/summary.json")
    return summary["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
