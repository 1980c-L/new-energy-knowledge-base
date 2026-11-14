# -*- coding: utf-8 -*-
"""r5_apply_173_fixes.py —— 落实 173 号窄复验的 F1a、F2 两项未关闭问题。

依据：`E:\\RAG\\项目资料\\发展规划\\173-证据-准入修复窄复验\\wiki\\independent-review.md`
  F1a（未关闭）`W-TIME-04/05` 的 `end=2025-05-01` 是项目并网截止，被 `policy_as_of` 当规则失效日过滤
      → 按 `04` §七 新增的端点角色，标 `end_role=event_deadline`；查询层改用 `ts_rule_within`
      （同类的 `W-TIME-02/03` 一并标，避免只修一半）
  F2（未关闭）`W-GRID-08` 丢了第24条（三）的「保证电网和设备安全运行的必要技术要求」例外
      → 改写 `claim_text`、写入 `conditions.exception`、引文换成含例外的整句

不改：任何其他主张；`claim_status`、`knowledge_kind`、日期端点本身、`boundary_inclusion`。
幂等：重复运行不叠加。写回前备份 `.backup/`，写回后逐项回读。
用法：python tools\\r5_apply_173_fixes.py [--dry-run]
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

EVENT_DEADLINE_CLAIMS = ["W-TIME-02", "W-TIME-03", "W-TIME-04", "W-TIME-05"]

GRID08_TEXT = (
    "电网企业须公平无歧视提供接入服务：不得无正当理由拒绝或拖延接入、"
    "不得拒绝提供接入位置与可用容量等必要信息、不得违规收取不合理服务费用；"
    "接入适用的技术要求不得高于国家和行业技术标准、规范，"
    "但保证电网和设备安全运行所必需的必要技术要求除外。"
)
GRID08_EXCEPTION = ("保证电网和设备安全运行所必需的必要技术要求，可以高于国家和行业技术标准、规范"
                    "（第24条（三）明文的例外，不是「原则上」的软化）")
GRID08_NOTE = ("第二十四条列五项禁止行为（含「其他违反电网公平开放的行为」兜底项）；（一）（二）（三）（四）各引一处原文；"
               "（三）项带安全必要技术例外，不得写成绝对禁止")
GRID08_OLD_QUOTE = "接入适用的技术要求高于国家和行业技术标准、规范"
GRID08_NEW_QUOTE = ("对符合国家要求建设的发电设施，除保证电网和设备安全运行的必要技术要求外，"
                    "接入适用的技术要求高于国家和行业技术标准、规范")


def load(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def save(p, rows):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = load(CLAIMS)
    by = {r["claim_id"]: r for r in rows}
    log = []

    for cid in EVENT_DEADLINE_CLAIMS:
        ts = by[cid]["temporal_scope"]
        if ts.get("end_role") != "event_deadline":
            ts["end_role"] = "event_deadline"
            log.append("%s temporal_scope.end_role=event_deadline（end=%s 是项目事件截止）" % (cid, ts.get("end")))

    c = by["W-GRID-08"]
    if c["claim_text"] != GRID08_TEXT:
        c["claim_text"] = GRID08_TEXT
        log.append("W-GRID-08 claim_text 写入安全必要技术例外")
    if c["conditions"].get("exception") != GRID08_EXCEPTION:
        c["conditions"]["exception"] = GRID08_EXCEPTION
        log.append("W-GRID-08 conditions.exception 补例外（原为 null）")
    if c["conditions"].get("note") != GRID08_NOTE:
        c["conditions"]["note"] = GRID08_NOTE
        log.append("W-GRID-08 conditions.note 改准（写明（三）项有例外）")
    for r in c["source_refs"]:
        if r.get("quote") == GRID08_OLD_QUOTE:
            r["quote"] = GRID08_NEW_QUOTE
            log.append("W-GRID-08 引文（三）换成含例外的整句")

    if not args.dry_run:
        backup = os.path.join(ROOT, ".backup")
        os.makedirs(backup, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        shutil.copy(CLAIMS, os.path.join(backup, "claims-%s-r5.jsonl" % stamp))
        save(CLAIMS, rows)
        log.append("claims.jsonl 已写盘（备份 claims-%s-r5.jsonl）" % stamp)

    print("改动 %d 项：" % len(log))
    for i, x in enumerate(log, 1):
        print("  %2d. %s" % (i, x))
    if args.dry_run:
        print("（dry-run，未写盘）")
        return 0

    back = {r["claim_id"]: r for r in load(CLAIMS)}
    ok = (all((back[cid]["temporal_scope"].get("end_role") == "event_deadline")
              for cid in EVENT_DEADLINE_CLAIMS)
          and "安全运行所必需的必要技术要求除外" in back["W-GRID-08"]["claim_text"]
          and back["W-GRID-08"]["conditions"]["exception"] == GRID08_EXCEPTION
          and any(r.get("quote") == GRID08_NEW_QUOTE for r in back["W-GRID-08"]["source_refs"]))
    print("回读校验：%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
