# -*- coding: utf-8 -*-
"""t7_succession_drill.py —— 演替演练（B 类变更），只在临时副本上做。

与 t4_update_drill.py 的分工：
  t4  演练「补主张」（A 类之外的新增），只看结构错与 chunk 差异；
  t7  演练「演替」：旧 claim 收窄区间 + 新 claim 承接，重点验三道闸门——
      ① check 有没有「同条件 + 区间重叠/空洞 + 结论相反」的校验；
      ② 页面正文是否跟着改（P4 只比对 claim_id 字面，不看语义）；
      ③ 查询/导出层有没有时点过滤，还是新旧两条一起给出。

用法：
    python tools\\t7_succession_drill.py [--spec drills\\2026-11-12-演替演练.json]
退出码：0 演练执行完成（结果是否符合预期由报告判读）；1 复制或执行失败。

红线：只在 tempfile.mkdtemp() 副本内写；正式 raw/、normalized/、claims.jsonl、
sources.jsonl、wiki/、exports/ 只读。假想条文前缀 DRILL-，正文含「演练虚构」。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "wiki_tool.py")
DEFAULT_SPEC = os.path.join(ROOT, "drills", "2026-11-12-演替演练.json")

# 报告模板里要判读的两组「同一问题的两个版本」
CONFLICT_PAIRS = [("W-REG-07", "W-REG-11"), ("W-GRID-02", "W-GRID-07")]


def make_clone():
    d = tempfile.mkdtemp(prefix="wt_succ_")
    for name in ("claims.jsonl", "sources.jsonl"):
        shutil.copy(os.path.join(ROOT, name), d)
    for name in ("normalized", "wiki"):
        shutil.copytree(os.path.join(ROOT, name), os.path.join(d, name))
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    # 假想条文一起带进副本，供 DRILL 来源的 text_path 解析
    drill_dir = os.path.join(ROOT, "drills")
    if os.path.isdir(drill_dir):
        shutil.copytree(drill_dir, os.path.join(d, "drills"))
    return d


def run(args, root):
    p = subprocess.run([sys.executable, TOOL, "--root", root] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"_raw": (p.stdout or "")[-600:], "_err": (p.stderr or "")[-600:]}


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


def within(ts, asof):
    """时点过滤原型（09 §3.4）：start/end 为 null 表示该侧开放。

    这个函数不写进 wiki_tool.py，只用于演练里量「补上闸门能不能拦住」。
    """
    start, end = ts.get("start"), ts.get("end")
    incl = ts.get("boundary_inclusion") or "none"
    if start is None and end is None:
        return True
    if start is not None:
        if asof < start:
            return False
        if asof == start and incl not in ("start", "both"):
            return False
    if end is not None:
        if asof > end:
            return False
        if asof == end and incl not in ("end", "both"):
            return False
    return True


def cond_key(c):
    cond = c.get("conditions") or {}
    return json.dumps({k: cond.get(k) for k in ("project_type", "region", "subject")},
                      ensure_ascii=False, sort_keys=True)


def overlap_pairs(claims, asof):
    """理想闸门 A：同一问题的两个版本（新旧 claim）在同一时点同时命中 → 报警。"""
    hits = []
    for a, b in CONFLICT_PAIRS:
        ca = next((x for x in claims if x.get("claim_id") == a), None)
        cb = next((x for x in claims if x.get("claim_id") == b), None)
        if not ca or not cb:
            continue
        if within(ca.get("temporal_scope") or {}, asof) and within(cb.get("temporal_scope") or {}, asof):
            hits.append({"as_of": asof, "claim_ids": [a, b]})
    return hits


def apply_scenario(clone, spec, sc):
    """在副本上执行一次演替，返回变更摘要。"""
    claims_path = os.path.join(clone, "claims.jsonl")
    sources_path = os.path.join(clone, "sources.jsonl")

    raw_lines = read_lines(claims_path)
    by_id = {}
    for i, ln in enumerate(raw_lines):
        by_id[json.loads(ln)["claim_id"]] = i

    changed = []
    for rule in sc.get("narrow") or []:
        cid = rule["claim_id"]
        if cid not in by_id:
            continue
        idx = by_id[cid]
        c = json.loads(raw_lines[idx])
        ts = c.get("temporal_scope") or {}
        before = json.dumps(ts, ensure_ascii=False, sort_keys=True)
        if rule.get("end") is not None:
            ts["end"] = rule["end"]
        if rule.get("boundary_inclusion") is not None:
            ts["boundary_inclusion"] = rule["boundary_inclusion"]
        if rule.get("current_effectiveness"):
            ts["current_effectiveness"] = rule["current_effectiveness"]
        if rule.get("superseded_by"):
            c["superseded_by"] = rule["superseded_by"]
        ts["note"] = (ts.get("note") or "") + "｜演练：按假想新规收窄区间"
        c["temporal_scope"] = ts
        raw_lines[idx] = json.dumps(c, ensure_ascii=False)
        changed.append({"claim_id": cid, "before": before,
                        "after": json.dumps(ts, ensure_ascii=False, sort_keys=True)})

    rewritten = []
    for rw in sc.get("rewrite") or []:
        cid = rw["claim_id"]
        idx = by_id.get(cid)
        if idx is None:
            continue
        c = json.loads(raw_lines[idx])
        before_text = c.get("claim_text")
        c["claim_text"] = rw["claim_text"]
        ts = c.get("temporal_scope") or {}
        ts["note"] = (ts.get("note") or "") + "｜演练：" + rw.get("note", "")
        if rw.get("current_effectiveness"):
            ts["current_effectiveness"] = rw["current_effectiveness"]
        c["temporal_scope"] = ts
        raw_lines[idx] = json.dumps(c, ensure_ascii=False)
        rewritten.append({"claim_id": cid, "before": before_text, "after": rw["claim_text"]})

    added = []
    if sc.get("add_new_claims"):
        for nc in spec["new_claims"]:
            raw_lines.append(json.dumps(nc, ensure_ascii=False))
            added.append(nc["claim_id"])

    # 原样写回：未改动行的字符串逐字节保留，便于核对「其余 claim 字节不变」
    with open(claims_path, "w", encoding="utf-8", newline="\n") as fh:
        for ln in raw_lines:
            fh.write(ln + "\n")

    if spec["drill_source"]["source_id"] not in {
            json.loads(x)["source_id"] for x in read_lines(sources_path) if x.strip()}:
        with open(sources_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(spec["drill_source"], ensure_ascii=False) + "\n")

    page_changed = False
    if sc.get("edit_page"):
        pe = spec["page_edit"]
        p = os.path.join(clone, pe["page"].replace("/", os.sep))
        text = open(p, encoding="utf-8").read()
        if pe["anchor"] in text and "W-REG-11" not in text:
            text = text.replace(pe["anchor"], pe["anchor"] + pe["insert"], 1)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            page_changed = True

    return {"narrowed": changed, "rewritten": rewritten, "added": added,
            "page_changed": page_changed}


def scenario_run(spec, sc):
    d = make_clone()
    try:
        diff = apply_scenario(d, spec, sc)

        rc_check, rep = run(["check"], d)
        rc_export, exp = run(["export"], d)
        rc_search, srch = run(["search", "--query", spec["probe_query"], "--mode", "wiki",
                               "--top-k", "8"], d)

        claims = [json.loads(x) for x in read_lines(os.path.join(d, "claims.jsonl"))]
        chunks = [json.loads(x) for x in read_lines(os.path.join(d, "exports", "wiki_chunks.jsonl"))]
        page_text = open(os.path.join(d, spec["page_edit"]["page"].replace("/", os.sep)),
                         encoding="utf-8").read()
        clone_lines = read_lines(os.path.join(d, "claims.jsonl"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ① check 报告
    struct = rep.get("structure_errors") or []
    loc = rep.get("locator_errors") or []
    p4 = [e for e in struct if e.get("code") == "P4"]

    # ② 页面与 claim 是否自相矛盾（F01 型漏改）
    page_has_old = "一个月内" in page_text
    page_has_new = "十五个工作日" in page_text
    page_contradiction = page_has_old and page_has_new
    # 跨层冲突：页面仍是旧表述、而 claim_text 已改成新表述。
    # 这一类比「页面内并存两句」更隐蔽——C1 就是它，而 page_contradiction 看不到。
    claim_new_ids = [c["claim_id"] for c in claims if c["claim_id"] in spec["probe_claims"]
                     and ("十五个工作日" in (c.get("claim_text") or "")
                          or "5 个工作日" in (c.get("claim_text") or ""))]
    page_vs_claim_conflict = page_has_old and bool(claim_new_ids)

    # ③ 导出层：同一问题两种时限是否同时进入下游
    ch_old = [c["chunk_id"] for c in chunks if "一个月内" in (c.get("text") or "")]
    ch_new = [c["chunk_id"] for c in chunks if "十五个工作日" in (c.get("text") or "")]

    # ④ 时点过滤原型：理想闸门能不能分开
    asof_hits = {}
    overlaps = []
    for asof in spec["probe_dates"]:
        hits = [c["claim_id"] for c in claims if within(c.get("temporal_scope") or {}, asof)
                and c["claim_id"] in spec["probe_claims"]]
        asof_hits[asof] = hits
        overlaps.extend(overlap_pairs(claims, asof))

    # ⑤ 检索层：同一问题是否同时召回新旧
    search_claims = []
    for r in (srch.get("results") or []):
        for cid in r.get("claim_ids") or []:
            if cid in spec["probe_claims"] and cid not in search_claims:
                search_claims.append(cid)

    # ⑥ 未改动 claim 是否逐字节保留（V2 的「其余不动」）
    base_map = {json.loads(x)["claim_id"]: x for x in read_lines(os.path.join(ROOT, "claims.jsonl"))}
    touched = ({c["claim_id"] for c in diff["narrowed"]}
               | {c["claim_id"] for c in diff["rewritten"]}
               | set(diff["added"]))
    untouched = [ln for ln in clone_lines if json.loads(ln)["claim_id"] not in touched]
    untouched_ok = all(ln == base_map.get(json.loads(ln)["claim_id"]) for ln in untouched)
    untouched_count = len(untouched)

    return {
        "label": sc["label"],
        "expect_note": sc.get("expect_note"),
        "narrowed": diff["narrowed"],
        "rewritten": diff["rewritten"],
        "added": diff["added"],
        "page_changed": diff["page_changed"],
        "check_exit": rc_check,
        "structure_error_codes": sorted({e.get("code") for e in struct}),
        "p4_missing_page_refs": [{"page": e.get("page"), "claim_id": e.get("claim_id")} for e in p4],
        "locator_error_codes": sorted({e.get("code") for e in loc}),
        "page_has_old_text": page_has_old,
        "page_has_new_text": page_has_new,
        "page_contradiction": page_contradiction,
        "claim_text_has_new": claim_new_ids,
        "page_vs_claim_conflict": page_vs_claim_conflict,
        "export_exit": rc_export,
        "export_chunks": exp.get("chunks"),
        "export_claim_chunks": exp.get("claim_chunks"),
        "export_readback_ok": exp.get("readback_ok"),
        "export_old_time_chunks": ch_old,
        "export_new_time_chunks": ch_new,
        "asof_hits": asof_hits,
        "ideal_gate_overlaps": overlaps,
        "search_hits": search_claims,
        "untouched_claims_identical": untouched_ok,
        "untouched_claims_count": untouched_count,
        "touched_claim_ids": sorted(touched),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="演替演练（B 类变更，临时副本）")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--out", default=None, help="证据 JSON 输出路径")
    args = ap.parse_args(argv)

    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)

    base_claims = read_lines(os.path.join(ROOT, "claims.jsonl"))
    base_chunks = read_lines(os.path.join(ROOT, "exports", "wiki_chunks.jsonl"))

    results = [scenario_run(spec, sc) for sc in spec["scenarios"]]

    evidence = {
        "drill_id": spec["drill_id"],
        "title": spec["title"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "临时副本；正式数据未改",
        "base": {"claims": len(base_claims), "chunks": len(base_chunks)},
        "probe_dates": spec["probe_dates"],
        "probe_claims": spec["probe_claims"],
        "conflict_pairs": CONFLICT_PAIRS,
        "scenarios": results,
        "verdict": "见 reviews/D-2026-11-12-01-演替演练.md",
    }

    out_path = args.out or os.path.join(ROOT, "reviews", "%s-证据.json" % spec["drill_id"])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

    for r in results:
        print("== %s" % r["label"])
        print("   check exit=%s 结构错=%s 定位错=%s P4=%s" % (
            r["check_exit"], ",".join(r["structure_error_codes"]) or "无",
            ",".join(r["locator_error_codes"]) or "无",
            [x["claim_id"] for x in r["p4_missing_page_refs"]] or "无"))
        print("   页面：旧表述=%s 新表述=%s 页内矛盾=%s 跨层矛盾(页面旧/claim新)=%s" % (
            r["page_has_old_text"], r["page_has_new_text"], r["page_contradiction"],
            r["page_vs_claim_conflict"]))
        print("   导出：%s chunk（claim %s）旧时限=%s 新时限=%s" % (
            r["export_chunks"], r["export_claim_chunks"],
            r["export_old_time_chunks"] or "无", r["export_new_time_chunks"] or "无"))
        print("   时点命中：%s" % json.dumps(r["asof_hits"], ensure_ascii=False))
        print("   理想闸门重叠：%s" % (r["ideal_gate_overlaps"] or "无"))
        print("   检索同时召回：%s" % (r["search_hits"] or "无"))
        if r["rewritten"]:
            print("   直接改写文本：%s" % [(x["claim_id"], x["after"][:24]) for x in r["rewritten"]])
    print("证据：%s" % os.path.relpath(out_path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
