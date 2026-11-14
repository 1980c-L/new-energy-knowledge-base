# -*- coding: utf-8 -*-
"""A 阶段第 7 步：固定首批评估题与原文参照。

题目的 evidence 一律从 normalized 层按键取真实文本（不手写引文），
避免参照与原文不一致；reference 标注「作者准备 / 非盲 / 待独立审查」。
"""
import os, json, datetime, re

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
NORM = os.path.join(ROOT, "normalized")
EV = os.path.join(ROOT, "eval")
os.makedirs(os.path.join(EV, "results"), exist_ok=True)

struct = [json.loads(x) for x in open(os.path.join(NORM, "PV-NAT-2025-007__measure.structure.jsonl"), encoding="utf-8")]
qa = [json.loads(x) for x in open(os.path.join(NORM, "PV-FAQ-20250411__faq2025.qa.jsonl"), encoding="utf-8")]
expl = [json.loads(x) for x in open(os.path.join(NORM, "PV-EXPLAIN-20250123__explain.paras.jsonl"), encoding="utf-8")]
case = [json.loads(x) for x in open(os.path.join(NORM, "PV-CASE-20250402__case.paras.jsonl"), encoding="utf-8")]

ART = {x["article_no"]: x for x in struct if x["unit"] == "article"}
Q = {x["q_no"]: x for x in qa}

def art(n, limit=260):
    a = ART[n]
    t = re.sub(r"\s+", " ", a["text"]).strip()
    return {"source_id": "PV-NAT-2025-007-ATTACH", "locator": f"第{n}条",
            "chapter": f"第{a['chapter_no']}章 {a['chapter_title']}",
            "quote": t[:limit] + ("…" if len(t) > limit else "")}

def faq(n, limit=260):
    x = Q[n]
    t = re.sub(r"\s+", " ", x["answer"]).strip()
    return {"source_id": "PV-FAQ-20250411", "locator": f"问{n}",
            "print_page": x["print_pages"][0],
            "quote": t[:limit] + ("…" if len(t) > limit else "")}

def para(idx, limit=260):
    p = expl[idx - 1]
    t = re.sub(r"\s+", " ", p["text"]).strip()
    return {"source_id": "PV-EXPLAIN-20250123", "locator": f"第{idx}段",
            "quote": t[:limit] + ("…" if len(t) > limit else "")}

CASES = [
    dict(case_id="W-A-01", type="lookup", policy_as_of="2025-12-31",
         question="《分布式光伏发电开发建设管理办法》把分布式光伏发电分为哪几种类型？各自的接入电压等级和容量上限是多少？",
         evidence=[art(4, 900)]),
    dict(case_id="W-A-02", type="condition", policy_as_of="2025-12-31",
         question="分布式光伏发电的上网模式有哪几种？自然人户用和大型工商业分别能选哪些？投运后还能改吗？",
         evidence=[art(5, 900), art(35, 500)]),
    dict(case_id="W-A-03", type="lookup", policy_as_of="2025-12-31",
         question="《管理办法》一共几章几条？第七章包含哪几条？",
         evidence=[{"source_id": "PV-NAT-2025-007-ATTACH", "locator": "全文结构",
                    "quote": f"共 {len([x for x in struct if x['unit']=='chapter'])} 章 "
                             f"{len(ART)} 条；第七章 附则 为第 41–43 条"}]),
    dict(case_id="W-A-04", type="synthesis", policy_as_of="2025-12-31",
         question="《管理办法》里的“发布之日”具体指哪一天？为什么不是文件落款的那一天？",
         evidence=[faq(71, 400), art(43, 400)]),
    dict(case_id="W-A-05", type="condition", policy_as_of="2025-12-31",
         question="大型工商业分布式光伏，投资主体（备案主体）与用电主体是否必须一致？",
         evidence=[faq(13, 500), art(4, 900)]),
    dict(case_id="W-A-06", type="condition", policy_as_of="2025-12-31",
         question="《管理办法》第四条说的“附属场所”，包不包括与建筑物在同一用地红线范围内的闲置空地？",
         evidence=[faq(14, 300), art(4, 900)]),
    dict(case_id="W-A-07", type="condition", policy_as_of="2025-12-31",
         question="接入系统电压等级为 35 千伏的项目，电网企业应当在多少个工作日内出具接入方案答复意见？50 千伏的呢？",
         evidence=[art(28, 600)]),
    dict(case_id="W-A-08", type="temporal", policy_as_of="2025-12-31",
         question="2025 年 5 月 1 日前已经并网投产的存量项目，按哪套规则执行？",
         evidence=[art(43, 500)]),
    dict(case_id="W-A-09", type="temporal", policy_as_of="2025-12-31",
         question="一个项目在办法发布之日前已备案，发布之后又做了备案变更，但仍能在 2025 年 5 月 1 日前并网投产，按新规还是老规？",
         evidence=[faq(73, 400), art(43, 500)]),
    dict(case_id="W-A-10", type="unknown", policy_as_of="2026-11-11",
         question="到 2026 年 11 月，《分布式光伏发电开发建设管理办法》是否仍然完全有效、有没有被修订？",
         evidence=[art(43, 300)],
         expected_answer_note="办法第四十三条自述有效期五年，但本轮未核查此后是否修订/废止，正确回答应明确 current_effectiveness: not_checked，不得断言“仍完全有效”"),
    dict(case_id="W-A-11", type="out_of_scope", policy_as_of="2025-12-31",
         question="某某市（地级市）分布式光伏备案的具体办理时限是几个工作日？",
         evidence=[art(40, 300)],
         expected_answer_note="国家办法不规定地市级具体时限，只授权省级制定实施细则；应回答“本办法未规定，需查该省实施细则”，不得编造数字"),
    dict(case_id="W-A-12", type="out_of_scope", policy_as_of="2025-12-31",
         question="自然人户用分布式光伏现在还有国家补贴吗？每度补多少钱？",
         evidence=[para(5, 400)],
         expected_answer_note="办法与问答均不规定补贴标准；解读提到已进入平价无补贴市场化阶段，属官方解读表述而非条款，不能当条款引用，也不应给出具体补贴金额"),
]

cases_rows, ref_rows = [], []
for c in CASES:
    cases_rows.append({
        "case_id": c["case_id"], "question": c["question"], "type": c["type"],
        "policy_as_of": c["policy_as_of"],
        "expected_evidence_groups": [e["locator"] for e in c["evidence"]],
        "source_kind": "official_public" if c["type"] != "synthesis" else "official_public",
        "note": c.get("expected_answer_note", ""),
        "batch": "pv-national-2025-v1",
    })

with open(os.path.join(EV, "cases.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
    for r in cases_rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

# 参照答案（作者按原文准备，尚未经独立审查 —— 明确标注）
ANSWERS = {
    "W-A-01": "四类（第四条）：①自然人户用——自然人利用自有住宅、庭院投资建设，接入电压等级不超过 380 伏；②非自然人户用——非自然人利用居民住宅、庭院，不超过 10(20) 千伏、总装机不超过 6 兆瓦；③一般工商业——利用党政机关、学校、医院、市政、文化体育、交通场站等公共机构及工商业厂房等建筑物及其附属场所，不超过 10(20) 千伏、总装机原则上不超过 6 兆瓦；④大型工商业——利用建筑物及其附属场所，接入用户侧电网或与用户开展专线供电，35 千伏且原则上不超过 20 兆瓦，或 110(66) 千伏且原则上不超过 50 兆瓦。以上各类型的建筑物及其附属场所应位于同一用地红线范围内。",
    "W-A-02": "模式共三种：全额上网、全部自发自用、自发自用余电上网（第五条）。但可按类型选择的范围不同：户用两类（自然人户用、非自然人户用）三种都可选；一般工商业只能选全部自发自用或自发自用余电上网（余电上网的年自发自用电量比例由各省级能源主管部门确定）；大型工商业原则上只能选全部自发自用，只有电力现货市场连续运行地区才可采用自发自用余电上网参与现货市场。另外，涉及自发自用的，用户与项目须位于同一用地红线范围内。投运后可按第五条变更上网模式**一次**，同时办理备案变更并告知备案机关，重新签订并网调度协议和购售电合同（第三十五条）。",
    "W-A-03": "7 章 43 条；第七章「附则」为第 41–43 条（离网型备案、解释权、施行与过渡）。",
    "W-A-04": "指 2025 年 1 月 23 日，即向社会公布之日；文件落款 2025 年 1 月 17 日是印发日期。办法第四十三条只说“自发布之日起施行”，正文本身不含具体日期，日期需由通知落款与问答第 71 问共同确定。",
    "W-A-05": "分两种情形（问答第 13 问）：与用户开展**专线供电**（不直接接入公共电网）的大型工商业分布式光伏，用户与发电项目投资方必须为同一法人主体；而**接入用户侧电网、且与公共电网有连接点**的大型工商业分布式光伏，用户与发电项目投资方可以不是同一法人主体。不能笼统答“必须一致”或“不必一致”。",
    "W-A-06": "可以（问答第 14 问）：当构筑物和闲置空地与建筑物在同一用地红线范围内时，可按“附属场所”的相关要求建设分布式光伏发电项目。",
    "W-A-07": "35 千伏及以下为 10 个工作日内出具答复意见；110 千伏（66 千伏）为 20 个工作日（第二十八条）。办法未规定 50 千伏这一档，属未覆盖情形，不得类推。",
    "W-A-08": "按原有政策执行：办法发布之日前已备案、且于 2025 年 5 月 1 日前并网投产的分布式光伏发电项目，仍按原有政策执行（第四十三条原文用“原有政策”）。",
    "W-A-09": "仍按原政策执行（问答第 73 问：发布之日前已备案、发布之日后做过备案变更、且于 2025 年 5 月 1 日前并网投产的项目）。",
    "W-A-10": "不能断言。办法第四十三条自述“自发布之日起施行，有效期五年”，但本轮未核查 2025-01-23 之后是否修订或废止，正确口径是 current_effectiveness: not_checked。",
    "W-A-11": "本办法未规定地市级办理时限；第四十条只授权各省级能源主管部门制定实施细则，需查该省细则。",
    "W-A-12": "办法与问答均未规定补贴标准。解读第 5 段（“从发展环境看……”）提到包括分布式光伏在内的新能源已全面进入平价无补贴市场化的发展阶段，这是官方解读表述、不是条款，不能据此给出具体补贴金额。",
}
for c in CASES:
    ref_rows.append({
        "case_id": c["case_id"],
        "reference_answer": ANSWERS[c["case_id"]],
        "evidence": c["evidence"],
        "prepared_by": "CodeBuddy（作者自建，非独立审查者）",
        "prepared_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "basis": "normalized 层原文（sources.jsonl 中 pv-national-2025-v1 批次）",
        "blind_status": "non_blind",
        "independent_review": "pending",
    })
with open(os.path.join(EV, "reference.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
    for r in ref_rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"cases.jsonl: {len(cases_rows)} 题")
from collections import Counter
print("题型分布:", dict(Counter(r["type"] for r in cases_rows)))
print(f"reference.jsonl: {len(ref_rows)} 条（blind_status=non_blind, independent_review=pending）")
for r in ref_rows[:3]:
    print(f"  {r['case_id']} 证据 {[e['locator'] for e in r['evidence']]}")
    print(f"      {r['evidence'][0]['quote'][:90]}")
