# -*- coding: utf-8 -*-
"""r1_apply_c1_revisions.py —— 落实 C1 裁决（2026-11-11）对主张层的修订。

依据：reviews/C1-2026-11-11-Codex-六问裁决.md
  Q2  → W-TIME-04 / W-TIME-05：5 月 1 日边界改为「含当日」，并写入「全容量并网」；补引用问答 72
  Q6  → 新增 W-DEF-10（文件结构）、W-REG-10（第40条授权省级制定实施细则）
  顺带 → W-MODE-07 删除无依据的「投运后」「全生命周期」；W-MODE-08 的 note 改准层级

做法：按 claim_id 逐条改字段，写回前备份到 `.backup/`，写回后回读校验；
      幂等：已应用的条目跳过（检测 marker），重复运行不会叠加。
用法：
    python tools\\r1_apply_c1_revisions.py [--dry-run]
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
CLAIMS = os.path.join(ROOT, "claims.jsonl")
TS = {"policy_as_of": "2025-12-31", "retrieved_at": "2026-11-11", "start": None, "end": None,
      "boundary_inclusion": "none", "current_effectiveness": "not_checked", "note": ""}

Q72_INCL = ("2025 年5 月1 日前（含当日）并网是指分布式光伏发 电项目全容量并网")
Q72_PART = ("对于部分容量并网的，不能按照原政策执行")


def ref(source_id, locator, quote):
    return {"source_id": source_id, "locator": locator, "quote": quote}


def build_new_claims():
    return [
        {
            "claim_id": "W-DEF-10",
            "page_path": "wiki/topics/项目分类.md",
            "claim_text": "本办法共 7 章 43 条；第七章「附则」为第 41–43 条（第41条离网型项目备案、第42条解释权、第43条施行与过渡）。",
            "source_refs": [
                ref("PV-NAT-2025-007-ATTACH", "全文结构", "共 7 章 43 条"),
                ref("PV-NAT-2025-007-ATTACH", "第41条", "参照本办法开展离网型分布式光伏发电的备案管理"),
            ],
            "conditions": {"project_type": "any", "region": "national",
                           "note": "文件结构信息：仅章数、条数、章节归属，不承载政策义务或时间规则"},
            "temporal_scope": dict(TS),
            "claim_status": "supported",
            "knowledge_kind": "extracted",
        },
        {
            "claim_id": "W-REG-10",
            "page_path": "wiki/topics/备案与时间衔接.md",
            "claim_text": "各省级能源主管部门可根据本办法，会同国家能源局派出机构制定适应本省（自治区、直辖市）实际的实施细则。",
            "source_refs": [
                ref("PV-NAT-2025-007-ATTACH", "第40条",
                    "各省级能源主管部门可根据本办法，会同国家能源局派出机构制定适应本省（自治区、直辖市）实际的实施细则。"),
            ],
            "conditions": {"region": "provincial_variable", "subject": "省级能源主管部门",
                           "note": "只授权制定细则，不能据此推断某省已有细则或具体办理天数"},
            "temporal_scope": dict(TS),
            "claim_status": "supported",
            "knowledge_kind": "extracted",
        },
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="落实 C1 裁决对 claims.jsonl 的修订")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = [json.loads(x) for x in open(CLAIMS, encoding="utf-8") if x.strip()]
    by_id = {r["claim_id"]: r for r in rows}
    changes = []

    # ---- Q2：W-TIME-04 ----
    c = by_id.get("W-TIME-04")
    if c:
        before = (c["claim_text"], c["temporal_scope"].get("boundary_inclusion"), c["temporal_scope"].get("note"))
        c["claim_text"] = ("过渡条款：发布之日前已备案、且于 2025 年 5 月 1 日前（含当日）全容量并网投产的项目，"
                           "仍按原政策（国能新能〔2013〕433 号体系）执行；仅部分容量并网的不适用。")
        c["temporal_scope"]["end"] = "2025-05-01"
        c["temporal_scope"]["boundary_inclusion"] = "end"
        c["temporal_scope"]["note"] = "含当日依据问答第 72 问；问答 72 同时明确「并网」指全容量并网"
        if not any(r.get("locator") == "问72" for r in c["source_refs"]):
            c["source_refs"].append(ref("PV-FAQ-20250411", "问72", Q72_INCL))
        changes.append(("W-TIME-04", before, (c["claim_text"], c["temporal_scope"]["boundary_inclusion"],
                                              c["temporal_scope"]["note"])))

    # ---- Q2：W-TIME-05 ----
    c = by_id.get("W-TIME-05")
    if c:
        before = (c["claim_text"], c["temporal_scope"].get("boundary_inclusion"), c["temporal_scope"].get("note"))
        c["claim_text"] = ("发布之日前已备案、发布之日后做备案变更、但仍在 2025 年 5 月 1 日前（含当日）全容量并网投产的项目，"
                           "仍按原政策执行——中间做过备案变更不影响过渡资格。")
        c["temporal_scope"]["end"] = "2025-05-01"
        c["temporal_scope"]["boundary_inclusion"] = "end"
        c["temporal_scope"]["note"] = "边界同 W-TIME-04（含当日、全容量并网）；问答 72 明确部分容量并网不按原政策"
        if not any(r.get("locator") == "问72" for r in c["source_refs"]):
            c["source_refs"].append(ref("PV-FAQ-20250411", "问72", Q72_PART))
        changes.append(("W-TIME-05", before, (c["claim_text"], c["temporal_scope"]["boundary_inclusion"],
                                              c["temporal_scope"]["note"])))

    # ---- 顺带：W-MODE-07 去掉无据限定 ----
    c = by_id.get("W-MODE-07")
    if c:
        before = c["claim_text"]
        c["claim_text"] = ("项目投资主体可根据电力用户负荷、自身经营状况等情况，按照第五条规定变更上网模式一次，"
                           "同时进行备案变更并告知备案机关，电网企业协助做好接网调整。")
        c["conditions"]["note"] = ("「一次」是硬上限；出处在第三十五条第二款，原文不含「投运后」「全生命周期」限定；"
                                   "重签协议与合同见 W-MODE-09")
        changes.append(("W-MODE-07", before, c["claim_text"]))

    # ---- 顺带：W-MODE-08 出处层级改准 ----
    c = by_id.get("W-MODE-08")
    if c:
        before = c["conditions"].get("note")
        c["conditions"]["note"] = ("第三十五条本身即提「根据电力用户负荷、自身经营状况等情况」；"
                                   "问答 65 进一步说明「发生变化，导致上网方式无法满足《管理办法》要求」的情形")
        changes.append(("W-MODE-08", before, c["conditions"]["note"]))

    # ---- Q6：新增两条 ----
    added = []
    for nc in build_new_claims():
        if nc["claim_id"] not in by_id:
            rows.append(nc)
            added.append(nc["claim_id"])

    print("将修改 %d 条，新增 %d 条（现有 %d 条）" % (len(changes), len(added), len(rows)))
    for cid, b, a in changes:
        print("-- %s\n   旧: %s\n   新: %s" % (cid, b, a))
    print("新增：%s" % (added or "无"))

    if args.dry_run:
        print("（dry-run，未写盘）")
        return 0

    backup_dir = os.path.join(ROOT, ".backup")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    shutil.copy(CLAIMS, os.path.join(backup_dir, "claims-%s.jsonl" % stamp))

    with open(CLAIMS, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    back = [json.loads(x) for x in open(CLAIMS, encoding="utf-8") if x.strip()]
    ok = (len(back) == len(rows)
          and all(x["claim_id"] in {y["claim_id"] for y in back} for x in rows)
          and next(x for x in back if x["claim_id"] == "W-TIME-04")["temporal_scope"]["boundary_inclusion"] == "end"
          and next(x for x in back if x["claim_id"] == "W-TIME-04")["temporal_scope"]["end"] == "2025-05-01")
    print("回读校验：%s（%d 条，备份 .backup/claims-%s.jsonl）" % ("通过" if ok else "失败", len(back), stamp))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
