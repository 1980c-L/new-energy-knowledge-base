# -*- coding: utf-8 -*-
"""r2_apply_c1_eval.py —— 落实 C1 裁决 Q2 对评估层（题 / 参照 / 答案）的修订。

依据：reviews/C1-2026-11-11-Codex-六问裁决.md
  Q2：5 月 1 日边界「含当日」，且「并网」指全容量并网；部分容量并网不能按原政策执行
  并要求「Q2 至少核对『5 月 1 日当天全容量并网』与『仅部分容量并网』两种答案及导出正文」

做法：按 case_id 改参照与答案，新增 W-A-13 / W-A-14 两题；写回前备份到 .backup/；幂等。
用法：python tools\\r2_apply_c1_eval.py [--dry-run]
退出码：0 成功；1 校验失败。
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "eval", "cases.jsonl")
REF = os.path.join(ROOT, "eval", "reference.jsonl")
ANS = os.path.join(ROOT, "eval", "results", "answers.jsonl")

Q72_INCL = "2025 年5 月1 日前（含当日）并网是指分布式光伏发 电项目全容量并网。"
Q72_PART = "对于部分容量并网的，不能按照原政策执行。"
ART43 = ("本办法自发布之日起施行，有效期五年。《分布式光伏发电项目管理暂行办法》"
         "（国能新能〔2013〕433号）同时废止。对于本办法发布之日前已备案且于2025年5月1日前并网投产的"
         "分布式光伏发电项目，仍按原有政策执行。")


def load(path):
    return [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]


def save(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def ev(source_id, locator, quote, **kw):
    d = {"source_id": source_id, "locator": locator}
    d.update(kw)
    d["quote"] = quote
    return d


NEW_CASES = [
    {"case_id": "W-A-13",
     "question": "某分布式光伏项目在办法发布之日前已备案，2025 年 5 月 1 日当天完成全容量并网，按新规还是原政策？",
     "type": "temporal", "policy_as_of": "2025-12-31",
     "expected_evidence_groups": ["第43条", "问72"], "source_kind": "official_public",
     "note": "考「含当日」：问答 72 问明确 5 月 1 日前（含当日）并网指全容量并网，故当天全容量并网仍在过渡范围内",
     "batch": "pv-national-2025-v1"},
    {"case_id": "W-A-14",
     "question": "某分布式光伏项目在办法发布之日前已备案，2025 年 5 月 1 日当天只有部分容量并网，其余容量 6 月才并网，能否按原政策执行？",
     "type": "condition", "policy_as_of": "2025-12-31",
     "expected_evidence_groups": ["问72"], "source_kind": "official_public",
     "note": "考「必须全容量」：问答 72 问明确部分容量并网不能按原政策执行；不得只答「含当日」",
     "batch": "pv-national-2025-v1"},
]

NEW_REFS = {
    "W-A-13": {
        "case_id": "W-A-13",
        "reference_answer": ("按原政策执行。问答 72 问明确「2025 年 5 月 1 日前（含当日）并网是指分布式光伏发电项目"
                            "全容量并网」；当天完成全容量并网即在过渡范围内，前提是发布之日前已备案（第四十三条）。"),
        "evidence": [ev("PV-FAQ-20250411", "问72", Q72_INCL, print_page=32),
                     ev("PV-NAT-2025-007-ATTACH", "第43条", ART43, chapter="第7章 附则")],
        "prepared_by": "CodeBuddy（作者自建，非独立审查者）",
        "basis": "normalized 层原文（sources.jsonl 中 pv-national-2025-v1 批次）",
        "blind_status": "non_blind",
        "independent_review": "pending",
    },
    "W-A-14": {
        "case_id": "W-A-14",
        "reference_answer": ("不能按原政策执行。问答 72 问原文：「2025 年 5 月 1 日前（含当日）并网是指分布式光伏发电项目"
                            "全容量并网。对于部分容量并网的，不能按照原政策执行。」当天只部分容量并网不属于过渡范围。"),
        "evidence": [ev("PV-FAQ-20250411", "问72", Q72_PART, print_page=32)],
        "prepared_by": "CodeBuddy（作者自建，非独立审查者）",
        "basis": "normalized 层原文（sources.jsonl 中 pv-national-2025-v1 批次）",
        "blind_status": "non_blind",
        "independent_review": "pending",
    },
}

NEW_ANS = {
    "W-A-13": {
        "case_id": "W-A-13", "layer": "wiki",
        "answer": ("按原政策执行：问答 72 问明确「2025 年 5 月 1 日前（含当日）并网是指全容量并网」，"
                   "当天完成全容量并网仍在过渡范围内；另一条件「发布之日前已备案」本题已满足。"),
        "cited_claim_ids": ["W-TIME-04", "W-TIME-02"],
        "raw_evidence": [], "must_include": ["含当日", "全容量"],
        "note": "C1 Q2 修正后新增：验证「含当日」这一侧",
    },
    "W-A-14": {
        "case_id": "W-A-14", "layer": "wiki",
        "answer": ("不能按原政策执行：问答 72 问明确「5 月 1 日前（含当日）并网」指全容量并网，"
                   "对于部分容量并网的不能按原政策执行。当天只部分容量并网、其余 6 月并网，不满足过渡条件。"),
        "cited_claim_ids": ["W-TIME-04", "W-TIME-05"],
        "raw_evidence": [], "must_include": ["部分容量", "不能按原政策"],
        "note": "C1 Q2 修正后新增：验证「必须全容量」这一侧",
    },
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="落实 C1 Q2 对评估层的修订")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(ROOT, ".backup")
    os.makedirs(backup, exist_ok=True)

    cases, refs, answers = load(CASES), load(REF), load(ANS)
    changes = []

    # ---- 参照：W-A-08 / W-A-09 补「含当日、全容量」----
    for r in refs:
        if r["case_id"] == "W-A-08":
            old = r["reference_answer"]
            r["reference_answer"] = ("按原有政策执行：发布之日前已备案、且于 2025 年 5 月 1 日前（含当日）全容量并网投产的"
                                     "分布式光伏发电项目，仍按原有政策执行（第四十三条原文用「原有政策」）。"
                                     "问答 72 问明确「含当日」，且「并网」指全容量并网，部分容量并网不适用。")
            if not any(e.get("locator") == "问72" for e in r["evidence"]):
                r["evidence"].append(ev("PV-FAQ-20250411", "问72", Q72_INCL, print_page=32))
            changes.append(("reference/W-A-08", old, r["reference_answer"]))
        elif r["case_id"] == "W-A-09":
            old = r["reference_answer"]
            r["reference_answer"] = ("仍按原政策执行（问答 73 问：发布之日前已备案、发布之日后做过备案变更、"
                                     "且于 2025 年 5 月 1 日前并网投产的项目）。边界与 W-A-08 一致："
                                     "「5 月 1 日前」含当日，且指全容量并网（问答 72 问）。")
            if not any(e.get("locator") == "问72" for e in r["evidence"]):
                r["evidence"].append(ev("PV-FAQ-20250411", "问72", Q72_PART, print_page=32))
            changes.append(("reference/W-A-09", old, r["reference_answer"]))

    # ---- 答案：W-A-08 去掉「按不含记录并标待确认」----
    for a in answers:
        if a["case_id"] == "W-A-08":
            old = a["answer"]
            a["answer"] = ("按原有政策（国能新能〔2013〕433 号体系）执行：须同时满足「发布之日前已备案」与"
                           "「2025 年 5 月 1 日前（含当日）全容量并网投产」。两处边界均已由官方问答定死："
                           "「发布之日前」含 1 月 23 日当日（问答 71），「5 月 1 日前」含当日且指全容量并网（问答 72）；"
                           "仅部分容量并网不能按原政策执行。")
            a["must_include"] = ["含当日", "全容量"]
            a["note"] = "C1 Q2 修正：原「按不含记录并标待确认」已删除"
            changes.append(("answer/W-A-08", old, a["answer"]))

    # ---- 新增两题 ----
    added = []
    for c in NEW_CASES:
        if not any(x["case_id"] == c["case_id"] for x in cases):
            cases.append(c)
            added.append(c["case_id"])
    for cid, r in NEW_REFS.items():
        if not any(x["case_id"] == cid for x in refs):
            refs.append(r)
    for cid, a in NEW_ANS.items():
        if not any(x["case_id"] == cid for x in answers):
            answers.append(a)

    print("改动 %d 处，新增题 %s；题 %d / 参照 %d / 答案 %d" % (
        len(changes), added or "无", len(cases), len(refs), len(answers)))
    for key, b, a in changes:
        print("-- %s\n   旧: %s\n   新: %s" % (key, b, a))
    if args.dry_run:
        print("（dry-run，未写盘）")
        return 0

    for path in (CASES, REF, ANS):
        shutil.copy(path, os.path.join(backup, "%s-%s" % (os.path.basename(path), stamp)))
    save(CASES, cases)
    save(REF, refs)
    save(ANS, answers)

    back = load(REF)
    w8 = next(x for x in back if x["case_id"] == "W-A-08")
    ok = (len(load(CASES)) == len(cases) and len(back) == len(refs)
          and any(e.get("locator") == "问72" for e in w8["evidence"])
          and any(x["case_id"] == "W-A-14" for x in load(ANS)))
    print("回读校验：%s（备份在 .backup/*-%s）" % ("通过" if ok else "失败", stamp))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
