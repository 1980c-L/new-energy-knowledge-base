# -*- coding: utf-8 -*-
"""r3_apply_c3_fixes.py —— 落实 C3 语义复审 F01–F04 对主张层与评估层的修订。

依据：reviews/C3-2026-11-11-Codex-语义复审.md（结论：有条件达到）
  F01 日期示例写反（页面层改，另简化「必须拼接」措辞）
  F02 电压被当互斥分档、「原则上」被当硬上限（W-DEF-08 收窄、W-DEF-06 条件注明）
  F03 模式主张独立消费时丢「同一用地红线」；MODE-04 省级归因超出原文；MX-09 后果归因无据
  F04 问59 具体扩建情形被外推为普遍规则（W-REG-09 限定题设）
  登记项：CON-03 措辞、CON-04 命题收窄
幂等：重复运行不会叠加。写回前备份到 .backup/。
用法：python tools\\r3_apply_c3_fixes.py [--dry-run]
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
REF = os.path.join(ROOT, "eval", "reference.jsonl")
ANS = os.path.join(ROOT, "eval", "results", "answers.jsonl")

REDLINE = "用户与发电项目须位于同一用地红线范围内（第五条最后一款）"


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

    # ---------- F02：DEF-08 收窄为「现有材料不足」，不再用硬上限做排除证明 ----------
    c = by["W-DEF-08"]
    c["claim_text"] = ("第四条对大型工商业只列举两档（35 千伏且原则上不超过 20 兆瓦、110（66）千伏且原则上不超过 50 兆瓦）；"
                       "对超出该原则性规模的项目（如 35 千伏接入、20 兆瓦以上），现有材料不足以确认如何归类——"
                       "既不能据此判定其符合分布式（大型工商业），也不能判定为集中式。")
    c["conditions"]["note"] = "本条是「现有材料不足」的声明，不是由硬上限作出的排除证明；处置需省级细则或官方答复"
    c["derivation"]["steps"] = [
        "第四条对大型工商业列举两档电压与对应容量，两档都用「原则上不超过」，保留例外空间",
        "「原则上」意味着超出该规模并非当然被排除，故超出部分的归类在第四条内没有确定答案",
        "问答 6 对「10 千伏、8 兆瓦」给出具体否定答复，但只适用于该具体情形，不能推广为所有电压/容量组合的排除规则",
    ]
    c["derivation"]["note"] = "标 unknown：只声明现有材料不足以判定，不据此肯定或否定其分布式身份"
    log.append("W-DEF-08 收窄（不再用硬上限作排除证明）")

    # ---------- F02（建议项）：DEF-06 条件注明专线供电的法人条件 ----------
    c = by["W-DEF-06"]
    c["conditions"]["note"] = "两档之外无第三档（见 W-DEF-08）；专线供电情形的法人条件见 W-REG-08"
    log.append("W-DEF-06 conditions.note 指向 REG-08")

    # ---------- F01：TIME-03 措辞不再制造「必须拼接」的证据依赖 ----------
    c = by["W-TIME-03"]
    c["conditions"]["note"] = "通知落款（1 月 17 日）用于对比；发布日由问答 71 直接给出，不靠拼接推定"
    c["derivation"]["steps"][2] = "正文第43条只写「自发布之日起施行」，未写日期；问答 71 直接明确发布日为 2025 年 1 月 23 日"
    c["derivation"]["note"] = "日期差异本身成立；页面示例的方向按 C3 F01 修正（见页面「实务后果」块）"
    log.append("W-TIME-03 措辞简化（发布日由问71直接给出）")

    # ---------- F03：矩阵格补「同一用地红线」，MX-11 例外措辞改准 ----------
    for cid in ("W-MX-02", "W-MX-05", "W-MX-08", "W-MX-11", "W-MX-12"):
        c = by[cid]
        c["conditions"]["exception"] = REDLINE
        if cid == "W-MX-11":
            c["claim_text"] = ("大型工商业 × 全部自发自用：可选，且为原则性首选模式（条文用「原则上」；"
                               "除现货连续运行地区的余电上网外，其他例外情形现有材料未确认）。")
        if cid == "W-MX-12":
            c["conditions"]["note"] = "矩阵格 12/12；具体省份是否连续运行须查该省状态"
        log.append("%s 补红线条件" % cid)

    # ---------- F03：MODE-04 删掉超出第五条/问11 的省级归因 ----------
    c = by["W-MODE-04"]
    c["conditions"]["exception"] = ("「原则上」表示存在例外空间；除「电力现货市场连续运行地区的余电上网」这一已明确路径外，"
                                    "其他例外情形现有材料未确认")
    log.append("W-MODE-04 删除「省级细则可另定例外」的归因")

    # ---------- F03：MX-09 删掉「后果由省级细则规定」 ----------
    c = by["W-MX-09"]
    c["claim_text"] = ("一般工商业 × 自发自用余电上网：可选，但年自发自用电量比例由各省级能源主管部门确定；"
                       "未达标的后果本项目未核实，需另查相关规定。")
    c["conditions"]["exception"] = "比例数值依省而定；未达标后果未核实"
    log.append("W-MX-09 删掉无据的后果归因")

    # ---------- F04：REG-09 限定到问59 题设 ----------
    c = by["W-REG-09"]
    c["claim_text"] = ("问答 59 所述情形：35 千伏公共连接点、原 6 兆瓦余电上网、增建 14 兆瓦后按大型工商业管理的项目，"
                       "先期建成的部分上网模式须同步调整为全部自发自用。其他规模变更情形如何归类与选择模式，"
                       "须结合第五条及有关规定另行判断，本条不作断言。")
    # project_type 须用枚举值（S6 强校验），题设细节放 exception
    c["conditions"]["project_type"] = "大型工商业"
    c["conditions"]["exception"] = ("本条限定于问答 59 的题设情形（35 千伏公共连接点、原 6 兆瓦余电上网、增建 14 兆瓦后按大型工商业管理），"
                                    "不得外推为所有扩建情形")
    c["conditions"]["note"] = "提取范围与问59 题设一致；一般不适用于未讨论的扩建场景"
    log.append("W-REG-09 限定题设范围")

    # ---------- 登记项：CON-03 / CON-04 措辞 ----------
    c = by["W-CON-03"]
    c["claim_text"] = ("分布式光伏发电项目无需取得发电类电力业务许可证；备案、并网等其他手续仍须按办法办理。")
    log.append("W-CON-03 措辞改为不可截取")

    c = by["W-CON-04"]
    c["claim_text"] = ("备案容量按交流侧容量计，因此组件（直流侧）容量高于备案交流容量，不等于交流侧超备案；"
                       "交流侧容量不得大于备案容量，容配比应科学合理确定。")
    c["derivation"]["note"] = ("容配比条款是这条的落点；本推导只说明「直流高于备案 ≠ 交流超备案」，"
                              "不主张任意容配比设计均获准")
    log.append("W-CON-04 命题收窄")

    if not args.dry_run:
        backup = os.path.join(ROOT, ".backup")
        os.makedirs(backup, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        shutil.copy(CLAIMS, os.path.join(backup, "claims-%s.jsonl" % stamp))
        save(CLAIMS, rows)
        log.append("claims.jsonl 已写盘（备份 claims-%s.jsonl）" % stamp)

    # ---------- 评估层：三题措辞 ----------
    refs, ans = load(REF), load(ANS)
    for r in refs:
        if r["case_id"] == "W-A-01":
            if "380 伏不专属于自然人户用" not in r["reference_answer"]:
                r["reference_answer"] += ("另：电压只是分类条件之一，须与投资主体、建设场所、容量一并判断；"
                                          "380 伏不专属于自然人户用。超出第四条原则性规模的项目（如 35 千伏接入、20 兆瓦以上），"
                                          "现有材料不足以确认归类。")
        elif r["case_id"] == "W-A-02":
            if "问答 65" not in r["reference_answer"]:
                r["reference_answer"] = r["reference_answer"].replace(
                    "投运后可按第五条变更上网模式**一次**",
                    "投运后可按第五条变更上网模式**一次**（触发情形是用户负荷或自身经营状况变化导致原方式无法满足办法要求，问答 65）")
        elif r["case_id"] == "W-A-04":
            r["reference_answer"] = ("指 2025 年 1 月 23 日，即向社会公布之日（问答 71 直接明确，且含当日）；"
                                     "文件落款 2025 年 1 月 17 日是印发日期，用于对比，不是发布日。"
                                     "证据：第四十三条「自发布之日起施行」＋问答 71。")
        r["independent_review"] = "changed_by_executor_after_C3"
    for a in ans:
        if a["case_id"] == "W-A-01":
            a["answer"] = ("四类，无第五类：自然人户用（≤380 伏，办法未设容量上限）、非自然人户用（≤10(20) 千伏、≤6 兆瓦，"
                           "措辞是「不超过」）、一般工商业（≤10(20) 千伏、原则上≤6 兆瓦）、大型工商业（35 千伏且原则上≤20 兆瓦，"
                           "或 110(66) 千伏且原则上≤50 兆瓦）。四类的建筑物及其附属场所均须位于同一用地红线内。"
                           "注意：电压只是条件之一，须与投资主体、场所、容量一并判断，380 伏不专属于自然人户用；"
                           "超出原则性规模的项目（如 35 千伏接入、20 兆瓦以上）现有材料不足以确认归类，不判定为分布式或集中式。")
        elif a["case_id"] == "W-A-02":
            if "触发情形为负荷" not in a["answer"]:
                a["answer"] = a["answer"].replace(
                    "投运后可按第五条变更一次，同时办备案变更、告知备案机关，并重签并网调度协议与购售电合同。",
                    "投运后可按第五条变更一次（触发情形为负荷或经营状况变化导致原方式无法满足办法要求），同时办备案变更、"
                    "告知备案机关，并重签并网调度协议与购售电合同。")
        elif a["case_id"] == "W-A-04":
            a["answer"] = ("指 2025 年 1 月 23 日，即向社会公布之日（问答 71 直接明确，且 1 月 23 日当日备案也算「发布之日前」）；"
                           "文件落款 1 月 17 日是签发日期，用于对比，不是发布日，两者差 6 天，1 月 18–22 日备案的项目归属因此不同。")
        a["review_status"] = "author_non_blind_adjusted_after_C3"

    if not args.dry_run:
        shutil.copy(REF, os.path.join(backup, "reference-%s.jsonl" % stamp))
        shutil.copy(ANS, os.path.join(backup, "answers-%s.jsonl" % stamp))
        save(REF, refs)
        save(ANS, ans)
        log.append("eval/reference.jsonl 与 eval/results/answers.jsonl 已写盘")

    print("改动 %d 项：" % len(log))
    for i, x in enumerate(log, 1):
        print("  %2d. %s" % (i, x))
    if args.dry_run:
        print("（dry-run，未写盘）")
        return 0

    back = {r["claim_id"]: r for r in load(CLAIMS)}
    ok = (back["W-REG-09"]["conditions"]["exception"].startswith("本条限定于")
          and back["W-REG-09"]["conditions"]["project_type"] == "大型工商业"
          and back["W-MX-02"]["conditions"]["exception"] == REDLINE
          and "现有材料不足以确认如何归类" in back["W-DEF-08"]["claim_text"]
          and "原则上" in back["W-DEF-08"]["claim_text"])
    print("回读校验：%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
