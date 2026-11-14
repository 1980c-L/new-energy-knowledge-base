# -*- coding: utf-8 -*-
"""t9_answer_guard.py —— 生成层的对抗用例 + 生成后校验器的注入自测（M13）。

分两部分，互不替代：

  第一部分（对抗，端到端，走真实 stub 管线）
    A1 库外主题（补贴金额）：应降原文层，且不得出现材料外的金额
    A2 结论层空白（50 千伏时限）：必须显式「官方未明确」，不得给数字
    A3 时点题（5 月 1 日前并网）：应命中过渡期主张并说「原政策」
    A4 完全库外的问题：不得凭空作答（走原文层或拒答）

  第二部分（注入，直接打校验器）
    校验器自己也要能拦住坏答案，否则「校验通过」不构成证据。
    做法与 `t1_check_negative.py` 相同：构造坏答案，断言对应规则被触发。

用法：python tools\\t9_answer_guard.py
退出码：0 全过；1 有断言失败。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_answer as wa  # noqa: E402
import wiki_query as wq   # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADVERSARIAL = [
    {
        "name": "A1 库外主题（补贴金额）→ 原文层，不得出现材料外金额",
        "question": "自然人户用分布式光伏现在还有国家补贴吗？每度补多少钱？",
        "policy": "2025-12-31",
        "expect": {"layer": "L2", "verify_ok": True},
    },
    {
        "name": "A2 结论层空白（50 千伏）→ 必须说「官方未明确」",
        "question": "50千伏接入方案答复时限",
        "policy": "2025-12-31",
        "expect": {"layer": "L1", "answer_kind": "unknown",
                   "contains_any": ["官方未明确"], "verify_ok": True},
    },
    {
        "name": "A3 时点题（5 月 1 日前并网）→ 命中过渡期并说原政策",
        "question": "2025 年 5 月 1 日前全容量并网的项目按哪套规则执行",
        "policy": "2025-04-30",
        "expect": {"layer": "L1", "has_claim": "W-TIME-04", "contains_any": ["原政策"],
                   "verify_ok": True},
    },
    {
        "name": "A4 库外主题（地市时限）→ 降原文层且校验通过",
        "question": "某某市（地级市）分布式光伏备案的办理时限是几个工作日",
        "policy": "2025-12-31",
        "expect": {"layer": "L2", "verify_ok": True},
    },
]


def run_adversarial(case):
    result = wq.build(case["question"], case["policy"], None, None, 5, 6000)
    answer = wa.gen_stub(result)
    ok, violations = wa.verify_answer(answer, result)
    exp = case["expect"]
    errs = []
    layer = result["route"]["layer"]
    if "layer" in exp and layer != exp["layer"]:
        errs.append("层=%s，期望 %s" % (layer, exp["layer"]))
    if "answer_kind" in exp and result.get("answer_kind") != exp["answer_kind"]:
        errs.append("answer_kind=%s，期望 %s" % (result.get("answer_kind"), exp["answer_kind"]))
    if "has_claim" in exp:
        ids = [c["claim_id"] for c in result.get("claims") or []]
        if exp["has_claim"] not in ids:
            errs.append("缺 %s（实际 %s）" % (exp["has_claim"], ids))
    if "contains_any" in exp and not any(p in answer for p in exp["contains_any"]):
        errs.append("答案缺关键词 %s：%s" % (exp["contains_any"], answer[:80].replace("\n", " ")))
    if "verify_ok" in exp and ok != exp["verify_ok"]:
        errs.append("校验=%s，期望 %s（%s）" % (ok, exp["verify_ok"],
                                              [v[0] for v in violations]))
    return errs, layer, answer


def build_ctx(question, policy="2025-12-31"):
    return wq.build(question, policy, None, None, 5, 6000)


def injections():
    """每条返回 (name, result, answer, 期望被触发的规则)。"""
    base = build_ctx("50千伏接入方案答复时限")
    time_q = build_ctx("5 月 1 日前全容量并网的项目按哪套规则执行", "2025-04-30")
    matrix = build_ctx("一般工商业分布式光伏可以全额上网吗")
    empty = dict(build_ctx("分布式光伏怎么分类"),
                 contexts=[], claims=[], answer_kind=None)
    return [
        ("I1 伪造引用 id（材料外）", base,
         "答复时限按 15 个工作日执行。［见 W-NOTEXIST-99］", wa.R1_REF_OUT_OF_SCOPE),
        ("I2 编造材料里没有的数字", base,
         "50 千伏接入方案应在 15 个工作日内答复。[W-GRID-05]", wa.R2_NUMBER_UNSOURCED),
        ("I3 篡改年份（材料外时点）", time_q,
         "自 2027 年起，该类项目一律按原政策执行。[W-TIME-04]", wa.R3_TIME_OVERWRITTEN),
        ("I4 unknown 不标「官方未明确」", base,
         "按 10 个工作日执行。[W-GRID-05]", wa.R4_UNKNOWN_UNMARKED),
        ("I5 无材料却照常作答", empty,
         "分布式光伏分为四类。[W-DEF-02]", wa.R5_NO_EVIDENCE_NO_REFUSAL),
        ("I6 条件丢失（类型未提）", matrix,
         "不可以。[W-MX-07]", wa.R6_CONDITION_LOST),
        # I7/I8 来自 2026-11-14 全量首跑的两处误判：L2 原文 id 被当成"零引用"
        ("I8 伪造原文层 id（第99条）", build_ctx("电网企业在接入服务上要公开哪些信息、提供哪些便利？"),
         "电网企业应公布可开放容量与典型设计方案［PV-NAT-2025-007-ATTACH#第99条］。",
         wa.R1_REF_OUT_OF_SCOPE),
    ]


def legal_l2_ref():
    """I7 对照：**合法**引用原文层 id 必须被认（修复前会被误判成零引用 + 条号无据）。"""
    r = build_ctx("电网企业在接入服务上要公开哪些信息、提供哪些便利？")
    cid = next(c["id"] for c in r["contexts"] if "第22条" in (c["id"] or ""))
    ans = "电网企业应公布可开放容量、技术标准规范，并公布且更新接入系统典型设计方案［%s］。" % cid
    return r, ans


def r4_rephrase():
    """I9 对照：unknown 题用「拒答+引用该条」的写法也应放行（R4 放宽后）。"""
    r = build_ctx("50千伏接入方案答复时限")
    ans = "现有材料不足以确认：该办法未规定 50 千伏这一中间电压等级的答复时限，不能类推。[W-GRID-05]"
    return r, ans


def budget_checks():
    """第三部分：费用闸门单测。用临时账本，不碰真实 ledger，也不发请求。"""
    import tempfile

    original = wa.LEDGER
    tmpdir = tempfile.mkdtemp(prefix="wiki-budget-")
    wa.LEDGER = os.path.join(tmpdir, "model_calls.jsonl")
    out = []
    try:
        est = 903 * wa.PRICE_IN_USD_PER_TOKEN + wa.OUT_RESERVE_TOKENS * wa.PRICE_OUT_USD_PER_TOKEN
        # B1 空账本 → 放行
        ok, spent, _ = wa.budget_check(0.50, est)
        out.append(("B1 空账本、上限 0.5 → 放行", ok and spent == 0.0, "spent=%.6f" % spent))
        # B2 已花接近上限 → 拒绝
        wa.log_call({"cost_usd": 0.4995})
        ok2, spent2, _ = wa.budget_check(0.50, est)
        out.append(("B2 已花 0.4995 + 本次 %.4f > 0.5 → 拒绝" % est, not ok2,
                    "spent=%.4f" % spent2))
        # B3 账本累加正确
        wa.log_call({"cost_usd": 0.0005})
        out.append(("B3 两笔累加 = 0.5000", abs(wa.read_ledger_total() - 0.5) < 1e-9,
                    "total=%.4f" % wa.read_ledger_total()))
    finally:
        wa.LEDGER = original
    return out


def main():
    fails = 0
    total = 0
    print("== 第一部分：对抗用例（端到端，stub 管线）")
    for case in ADVERSARIAL:
        total += 1
        errs, layer, answer = run_adversarial(case)
        ok = not errs
        fails += 0 if ok else 1
        print("%-6s %s" % ("PASS" if ok else "FAIL", case["name"]))
        print("       层=%s 答案首行=%s" % (layer, answer.split("\n")[0][:56]))
        for e in errs:
            print("       ✗ %s" % e)

    print()
    print("== 第二部分：校验器注入自测（坏答案必须被拦）")
    for name, result, answer, code in injections():
        total += 1
        ok, violations = wa.verify_answer(answer, result)
        codes = [c for c, _ in violations]
        hit = code in codes
        passed = hit and (ok is False if code != wa.R6_CONDITION_LOST else True)
        fails += 0 if passed else 1
        print("%-6s %s   触发=%s" % ("PASS" if passed else "FAIL", name, codes or "无"))
        if not hit:
            print("       ✗ 期望触发 %s，实际未触发" % code)

    # 对照：合法写法必须放行（防止校验器一律拒绝 / 修完一条又误杀另一条）
    for name, fn in (("C1 对照：正常 stub 答案应通过",
                      lambda: (build_ctx("一般工商业分布式光伏可以全额上网吗"),
                               wa.gen_stub(build_ctx("一般工商业分布式光伏可以全额上网吗")))),
                     ("C2 对照：合法引用 L2 原文 id 应通过", legal_l2_ref),
                     ("C3 对照：unknown 用「拒答+引用」写法应通过", r4_rephrase)):
        total += 1
        r, ans = fn()
        ok_c, viol_c = wa.verify_answer(ans, r)
        fails += 0 if ok_c else 1
        print("%-6s %s%s" % ("PASS" if ok_c else "FAIL", name,
                            "" if ok_c else "  违规=%s" % [v[0] for v in viol_c]))

    print()
    print("== 第三部分：费用闸门单测（临时账本，不发请求）")
    for name, ok, detail in budget_checks():
        total += 1
        fails += 0 if ok else 1
        print("%-6s %s   %s" % ("PASS" if ok else "FAIL", name, detail))
    print()
    print("%d/%d PASS；ALL_PASS=%s" % (total - fails, total, fails == 0))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
