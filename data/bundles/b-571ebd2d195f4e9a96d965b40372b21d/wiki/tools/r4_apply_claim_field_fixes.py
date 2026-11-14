# -*- coding: utf-8 -*-
"""r4_apply_claim_field_fixes.py —— 按 `04-主张层字段契约.md` 核修四条主张的字段与引文。

核修对象（2026-11-14）：
  W-GRID-08  引文只覆盖第24条五项禁止行为中的三项 → 补（二）必要信息、（三）技术要求两条引文
  W-GRID-10  `conditions.project_type` 只列两类户用，但 claim_text 的「其他类型须开展接入系统设计」
             与 note「基础资料义务适用于所有类型」覆盖四类 → 改为 `any`，分层写进 note；补核心义务引文
  W-TIME-04  claim_text 里「（国能新能〔2013〕433 号体系）」是原文没有的等价表述 → 补官方解读第3段
             （433 号即《暂行办法》）+ 第23段（同时废止 + 仍按原政策执行）两条引文，文字不动
  W-TIME-05  无字段问题，只登记（问73 原文直接回答「仍按原政策执行」，末句归纳可接受）

不改：`claim_text`、`claim_status`、`knowledge_kind`、`temporal_scope`、`00`–`04` 任何契约项。
幂等：重复运行不叠加引文。写回前备份到 `.backup/`，写回后逐项回读。
用法：python tools\\r4_apply_claim_field_fixes.py [--dry-run]
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

MEASURE = "PV-NAT-2025-007-ATTACH"
EXPLAIN = "PV-EXPLAIN-20250123"

ADD_REFS = {
    "W-GRID-08": [
        # 第24条（二）：拒绝提供必要信息
        (MEASURE, "第24条",
         "拒绝向项目投资主体提供接入电网须知晓的配电网络的接入位置、可用容量、实际使用容量、出线方式、可用间隔数量等必要信息"),
        # 第24条（三）：技术要求不得高于国家和行业标准
        (MEASURE, "第24条", "接入适用的技术要求高于国家和行业技术标准、规范"),
    ],
    "W-GRID-10": [
        # 第27条：其他类型须开展接入系统设计（claim_text 的核心义务，此前无引文）
        (MEASURE, "第27条", "其他类型的分布式光伏发电项目应当开展接入系统设计工作"),
    ],
    "W-TIME-04": [
        # 解读第3段：433 号即《暂行办法》，支撑 claim_text 里的括号等价
        (EXPLAIN, "第3段",
         "国家能源局出台《分布式光伏发电项目管理暂行办法》（国能新能〔2013〕433号，以下简称《暂行办法》）"),
        # 解读第23段：同时废止 + 仍按原有政策执行，同一段里两件事都在
        (EXPLAIN, "第23段",
         "《分布式光伏发电项目管理暂行办法》同时废止。对于本办法发布之日前已备案且于2025年5月1日前并网投产的分布式光伏发电项目，仍按原有政策执行"),
    ],
}

NOTES = {
    "W-GRID-08": ("第二十四条列五项禁止行为（含「其他违反电网公平开放的行为」兜底项），"
                  "本条（一）（二）（三）（四）各引一处原文；2026-11-14 补齐（二）（三）两条引文"),
    "W-GRID-10": ("分层：免费提供接入系统相关方案只适用于自然人户用；其他类型（含一般工商业、大型工商业）"
                  "须开展接入系统设计；基础资料提供与书面告知义务不区分类型；集中汇流仅「鼓励」非自然人户用"),
}

TIME04_TS_NOTE = ("含当日依据问答第 72 问；问答 72 同时明确「并网」指全容量并网；"
                  "「原有政策」即被同时废止的《分布式光伏发电项目管理暂行办法》（国能新能〔2013〕433 号），见解读第3、23段")


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

    for cid, refs in ADD_REFS.items():
        c = by[cid]
        have = {r["quote"] for r in c["source_refs"]}
        added = 0
        for sid, loc, quote in refs:
            if quote in have:
                continue
            c["source_refs"].append({"source_id": sid, "locator": loc, "quote": quote})
            added += 1
        log.append("%s 引文 %d → %d（本轮 +%d）" % (cid, len(c["source_refs"]) - added, len(c["source_refs"]), added))

    for cid, note in NOTES.items():
        if by[cid]["conditions"].get("note") != note:
            by[cid]["conditions"]["note"] = note
            log.append("%s conditions.note 更新" % cid)

    g10 = by["W-GRID-10"]
    if g10["conditions"].get("project_type") != "any":
        g10["conditions"]["project_type"] = "any"
        log.append("W-GRID-10 conditions.project_type → any（原只列两类户用，与 claim_text/note 矛盾）")

    if by["W-TIME-04"]["temporal_scope"].get("note") != TIME04_TS_NOTE:
        by["W-TIME-04"]["temporal_scope"]["note"] = TIME04_TS_NOTE
        log.append("W-TIME-04 temporal_scope.note 注明 433 号出处")

    if not args.dry_run:
        backup = os.path.join(ROOT, ".backup")
        os.makedirs(backup, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        shutil.copy(CLAIMS, os.path.join(backup, "claims-%s-r4.jsonl" % stamp))
        save(CLAIMS, rows)
        log.append("claims.jsonl 已写盘（备份 claims-%s-r4.jsonl）" % stamp)

    print("改动 %d 项：" % len(log))
    for i, x in enumerate(log, 1):
        print("  %2d. %s" % (i, x))
    if args.dry_run:
        print("（dry-run，未写盘）")
        return 0

    back = {r["claim_id"]: r for r in load(CLAIMS)}
    ok = (back["W-GRID-10"]["conditions"]["project_type"] == "any"
          and len(back["W-GRID-08"]["source_refs"]) == 5
          and len(back["W-GRID-10"]["source_refs"]) == 4
          and len(back["W-TIME-04"]["source_refs"]) == 4
          and len(back["W-TIME-05"]["source_refs"]) == 2
          and "433" in back["W-TIME-04"]["temporal_scope"]["note"])
    print("回读校验：%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
