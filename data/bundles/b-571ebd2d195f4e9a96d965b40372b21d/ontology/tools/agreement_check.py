# -*- coding: utf-8 -*-
"""比较作者标注（questions_v2.jsonl）与第二标注（labels_reviewer2.jsonl）。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "eval" / "retrieval" / "questions_v2.jsonl"
R = ROOT / "eval" / "retrieval" / "labels_reviewer2.jsonl"
OUT = ROOT / "eval" / "retrieval" / "agreement.json"


def load(path):
    return {json.loads(l)["question_id"]: json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


mine = load(Q)
rev = load(R)
rows = []
exact = jac_sum = cov_sum = 0.0
n = 0
for qid, r in rev.items():
    a = set(mine[qid]["gold"])
    b = set(r["locator_ids"])
    inter = a & b
    union = a | b
    n += 1
    exact += 1 if a == b else 0
    jac_sum += len(inter) / len(union) if union else 1.0
    cov_sum += len(inter) / len(a) if a else 1.0
    rows.append({
        "question_id": qid,
        "author_gold": sorted(a),
        "reviewer_gold": sorted(b),
        "intersection": sorted(inter),
        "exact_match": a == b,
        "jaccard": round(len(inter) / len(union), 4) if union else 1.0,
        "author_coverage_by_reviewer": round(len(inter) / len(a), 4) if a else 1.0,
        "reviewer_confidence": r.get("confidence"),
    })

summary = {
    "n": n,
    "exact_match_count": f"{int(exact)}/{n}",
    "exact_match_rate": round(exact / n, 4),
    "mean_jaccard": round(jac_sum / n, 4),
    "mean_author_coverage_by_reviewer": round(cov_sum / n, 4),
    "rows": rows,
}
OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: summary[k] for k in summary if k != "rows"}, ensure_ascii=False, indent=2))
for row in rows:
    print(row["question_id"], row["exact_match"], row["jaccard"],
          "author:", ",".join(x.split("#")[1] for x in row["author_gold"]),
          "| reviewer:", ",".join(x.split("#")[1] for x in row["reviewer_gold"]))
