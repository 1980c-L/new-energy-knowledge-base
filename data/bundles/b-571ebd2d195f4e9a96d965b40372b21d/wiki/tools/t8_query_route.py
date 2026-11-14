# -*- coding: utf-8 -*-
"""t8_query_route.py —— 查询层路由回归（15 组）。

钉住的是「判断」而不是「文采」：该走结论层还是原文层、缺时点是否拒绝、
库外主题是否降级、时点过滤是否真的生效、unknown 主张是否置顶并显式标记。
生成层的行为不在这里测。

注意「U1 / U2」两组的区别：U1（50 千伏）的 W-GRID-05 本来就是 BM25 第一名，
**不置顶也能过**，它验的是标记；真正验置顶机制的是 U2（35 千伏 30 兆瓦），
那里的 unknown 主张原本排第 2。两组一起看，才不会「假通过」。

用法：python tools\\t8_query_route.py
退出码：0 全部通过；1 有断言失败。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_query as wq  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = [
    {
        "name": "缺 policy_as_of → 拒绝 L1",
        "question": "分布式光伏分为哪几类",
        "policy": None,
        "expect": {"layer": "L2", "l1_rejected": True},
    },
    {
        "name": "强制 L1 也不能绕过时点闸门",
        "question": "分布式光伏分为哪几类",
        "policy": None,
        "force": "L1",
        "expect": {"layer": "L2", "l1_rejected": True, "no_claims": True},
    },
    {
        "name": "问原文措辞 → L2",
        "question": "第十二条原文",
        "policy": "2025-12-31",
        "expect": {"layer": "L2"},
    },
    {
        "name": "库外主题（补贴）→ L2",
        "question": "自然人户用分布式光伏现在还有国家补贴吗",
        "policy": "2025-12-31",
        "expect": {"layer": "L2", "no_claims": True},
    },
    {
        "name": "库外主题（地级市）→ L2",
        "question": "某某市（地级市）分布式光伏备案的办理时限",
        "policy": "2025-12-31",
        "expect": {"layer": "L2"},
    },
    {
        "name": "上网模式问题 → L1 且命中 W-MX-07",
        "question": "一般工商业分布式光伏可以全额上网吗",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "has_claim": "W-MX-07"},
    },
    {
        "name": "未知档位问题 → L1 且候选含 unknown 主张",
        "question": "35千伏30兆瓦的项目按哪一档管理",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "has_claim": "W-DEF-08"},
    },
    {
        "name": "时点晚于取数日 → stale_risk",
        "question": "一般工商业分布式光伏可以全额上网吗",
        "policy": "2027-06-01",
        "expect": {"stale_risk": True},
    },
    {
        "name": "对照：该时点下确实能命中 W-DEF-02",
        "question": "分布式光伏备案分为哪几类",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "has_claim": "W-DEF-02"},
    },
    {
        "name": "时点早于办法生效 → 不得命中生效后的主张",
        "question": "分布式光伏备案分为哪几类",
        "policy": "2025-01-01",
        "expect": {"not_claim": "W-DEF-02"},
    },
    {
        "name": "强制 L2 → L2 且标记 forced",
        "question": "一般工商业分布式光伏可以全额上网吗",
        "policy": "2025-12-31",
        "force": "L2",
        "expect": {"layer": "L2", "forced": True},
    },
    {
        "name": "U1 50 千伏时限 → L1、首位 W-GRID-05、标 unknown",
        "question": "50千伏接入方案答复时限",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "first_claim": "W-GRID-05", "answer_kind": "unknown",
                   "render_has": "官方未明确"},
    },
    {
        "name": "U2 35 千伏 30 兆瓦（unknown 原本排第 2）→ 置顶到首位",
        "question": "35千伏30兆瓦的项目按哪一档管理",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "first_claim": "W-DEF-08", "answer_kind": "unknown",
                   "unknown_hoisted": True},
    },
    {
        "name": "U3 对照：普通问题时点问题 → 不被 unknown 抢首位",
        "question": "分布式光伏备案分为哪几类",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "answer_kind": "supported", "no_unknown": True,
                   "render_not_has": "官方未明确"},
    },
    {
        "name": "U4 是否修订 → L1、首位 W-TIME-06、标 unknown",
        "question": "《分布式光伏发电开发建设管理办法》是否被修订过，现在还有效吗",
        "policy": "2026-11-11",
        "expect": {"layer": "L1", "first_claim": "W-TIME-06", "answer_kind": "unknown"},
    },
]


def run_case(c):
    result = wq.build(c["question"], c["policy"], None, c.get("force"), 5, 6000)
    exp = c["expect"]
    ids = [x["claim_id"] for x in result["claims"]]
    errs = []

    if "layer" in exp and result["route"]["layer"] != exp["layer"]:
        errs.append("层=%s，期望 %s" % (result["route"]["layer"], exp["layer"]))
    if exp.get("l1_rejected") and not result["route"]["l1_rejected"]:
        errs.append("l1_rejected 为空")
    if exp.get("no_claims") and ids:
        errs.append("本不该有结论层依据，实际给出 %s" % ids)
    if "has_claim" in exp and exp["has_claim"] not in ids:
        errs.append("缺 %s（实际 %s）" % (exp["has_claim"], ids))
    if "not_claim" in exp and exp["not_claim"] in ids:
        errs.append("不该出现 %s" % exp["not_claim"])
    if "stale_risk" in exp and result["route"]["stale_risk"] != exp["stale_risk"]:
        errs.append("stale_risk=%s，期望 %s" % (result["route"]["stale_risk"], exp["stale_risk"]))
    if "forced" in exp and result["route"]["forced"] != exp["forced"]:
        errs.append("forced=%s，期望 %s" % (result["route"]["forced"], exp["forced"]))
    if "first_claim" in exp:
        got = ids[0] if ids else None
        if got != exp["first_claim"]:
            errs.append("首位=%s，期望 %s（实际顺序 %s）" % (got, exp["first_claim"], ids))
    if "answer_kind" in exp and result.get("answer_kind") != exp["answer_kind"]:
        errs.append("answer_kind=%s，期望 %s" % (result.get("answer_kind"), exp["answer_kind"]))
    if "no_unknown" in exp and result["route"].get("unknown_claims"):
        errs.append("不该有 unknown 主张，实际 %s" % result["route"]["unknown_claims"])
    if "unknown_hoisted" in exp and result["route"].get("unknown_hoisted") != exp["unknown_hoisted"]:
        errs.append("unknown_hoisted=%s，期望 %s"
                    % (result["route"].get("unknown_hoisted"), exp["unknown_hoisted"]))
    if "render_has" in exp or "render_not_has" in exp:
        text = wq.render(result)
        if "render_has" in exp and exp["render_has"] not in text:
            errs.append("render 缺「%s」" % exp["render_has"])
        if "render_not_has" in exp and exp["render_not_has"] in text:
            errs.append("render 不该出现「%s」" % exp["render_not_has"])
    return errs, result, ids


def gate_checks():
    """置顶闸门的单测（纯函数，不依赖语料）。

    端到端用例只覆盖到「命中就置顶」，闸门的**下半边**（相关度不够就不抢首位）
    在现有语料里没有自然样本，故直接对 `hoist_unknown` 注入合成候选。
    """
    sup = (10.0, {"claim_id": "S1", "claim_status": "supported"})
    unk_hi = (9.0, {"claim_id": "U-hi", "claim_status": "unknown"})   # 0.90 ≥ 0.85
    unk_lo = (8.0, {"claim_id": "U-lo", "claim_status": "unknown"})   # 0.80 < 0.85
    out = []
    got_hi = [x[1]["claim_id"] for x in wq.hoist_unknown([sup, unk_hi])]
    out.append(("G1 相关度达标（0.90）→ 置顶", got_hi == ["U-hi", "S1"], got_hi))
    got_lo = [x[1]["claim_id"] for x in wq.hoist_unknown([sup, unk_lo])]
    out.append(("G2 相关度不足（0.80）→ 保持分数序", got_lo == ["S1", "U-lo"], got_lo))
    got_empty = wq.hoist_unknown([])
    out.append(("G3 空集合不报错", got_empty == [], got_empty))
    return out


def main():
    total_fail = 0
    total = 0
    for c in CASES:
        total += 1
        errs, result, ids = run_case(c)
        ok = not errs
        if not ok:
            total_fail += 1
        print("%-6s %s" % ("PASS" if ok else "FAIL", c["name"]))
        print("       层=%s 依据=%s answer_kind=%s 置顶=%s" % (
            result["route"]["layer"], ids or "-", result.get("answer_kind"),
            result["route"].get("unknown_hoisted")))
        print("       理由=%s" % result["route"]["reason"])
        for e in errs:
            print("       ✗ %s" % e)
    print()
    for name, ok, got in gate_checks():
        total += 1
        if not ok:
            total_fail += 1
        print("%-6s %s   实际顺序=%s" % ("PASS" if ok else "FAIL", name, got))
    print()
    print("%d/%d PASS；ALL_PASS=%s" % (total - total_fail, total, total_fail == 0))
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
