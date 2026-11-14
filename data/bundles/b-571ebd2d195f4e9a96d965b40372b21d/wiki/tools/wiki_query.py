# -*- coding: utf-8 -*-
"""wiki_query.py —— 问答入口的规则层：路由 + 双时点 + 取证据 + 组上下文 + 出 prompt。

职责边界（`09` §3.3 / §8.1）：
  本工具**不生成自然语言答案**，只做可测的那一半——
    1. 判断该走 L1（已编译结论）还是 L2（原文片段）；
    2. 强制时点：没给 `policy_as_of` 就**拒绝 L1、降级 L2** 并标注（不是报错停服）；
    3. 用 `temporal_scope` + `current_effectiveness` 过滤候选；
    4. 检索、组装上下文（真实命中 vs 同节补充分开）、校验引用；
    5. 输出结构化结果与可直接喂给模型的 prompt。

生成层（模型）只负责在给定材料内组织语言，**不得改变本工具给出的时点、依据与降级**。

用法：
    python tools\\wiki_query.py --question "分布式光伏怎么分类" --policy-as-of 2025-12-31
    python tools\\wiki_query.py --question "第十二条原文" --policy-as-of 2025-12-31 --json
    python tools\\wiki_query.py --question "..." --policy-as-of today --prompt-only

退出码：0 正常；2 参数非法（时点格式）；3 内部错误（如缺 claims 文件）。
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_retrieval as wr  # noqa: E402
import wiki_tool as wt  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 最近一次取数日期：问题时点晚于此，库可能缺少该时点之后的变化
LATEST_RETRIEVAL = "2026-11-11"

# 问「原文」的标记：命中就走 L2
L2_MARKERS = ("原文", "原话", "第几条", "哪一条", "条款", "怎么写的", "怎么写", "第几问", "逐字")
# 问「结论」的标记：仅用于在无候选时解释原因
L1_MARKERS = ("能不能", "是否", "需要", "按哪", "如何分类", "怎么备案", "什么条件", "要不要", "可以吗", "多久")

MAX_L1_CANDIDATES = 3
# 进入结论层的最低相对分：低于 0.6×第一名的一律不展示，避免把顺带命中当依据
L1_MIN_RATIO = 0.6

# unknown 主张的展示标记（与 claim_status 取值一致，用于消费方不必回看 claim_status）
UNKNOWN_KIND = "unknown"
# 置顶闸门：unknown 主张的分数达到第一名的这个比例才置顶。
# 0.6（L1_MIN_RATIO）只是「可以展示」的底线；置顶会让回答以「官方未明确」开头，属强动作，
# 要求它与最佳候选接近同等相关。实测（2026-11-13，14 道评测题 + 4 个探针问题）：
# 进入已选中集合的 unknown 相对分为 1.00 / 1.00 / 0.96 / 0.93 / 0.79，
# 0.79 与 0.93 之间是空档，取 0.85 落在空档中央；低于闸门的仍照常展示，只是不抢首位。
UNKNOWN_HOIST_MIN_RATIO = 0.85

# 问题里能识别的项目类型词（长词优先，避免「工商业」误吃「大型工商业」）
TYPE_WORDS = ("自然人户用", "非自然人户用", "一般工商业", "大型工商业")
# 问题里能识别的主题词：命中才认为这是「可结论层回答」的问题
TOPIC_WORDS = ("备案", "上网模式", "分类", "类型", "条件", "时限", "并网", "建档立卡",
               "许可证", "容量", "电压", "千伏", "兆瓦", "千瓦", "装机", "修订", "废止",
               "全额上网", "余电上网", "自发自用", "投资主体")

# 结论层不覆盖的主题：命中即降级原文层，禁止据结论层作答（`09` §3.3 的拒答前置）。
# 措辞是「结论层不覆盖」而非「本库不覆盖」：第 36 条原文就规定绿证核发，只是没建主张，
# 说「本库不覆盖」会与原文层事实冲突（2026-11-13 修正，见 `10-覆盖与不覆盖清单.md` §4）。
OUT_OF_SCOPE_WORDS = ("补贴", "电价", "上网电价", "土地", "税收", "绿证", "碳交易",
                      "消纳责任", "储能配置", "贷款", "融资", "保险",
                      "地级市", "县级市", "省级细则", "地方细则")


def detect_signals(question):
    """从问题里抽项目类型与主题词。纯规则，可测、可解释。"""
    types = set()
    if "非自然人户用" in question:
        types.add("非自然人户用")
    if "自然人户用" in question or ("户用" in question and "非自然人户用" not in question):
        types.add("自然人户用")
    if "大型工商业" in question:
        types.add("大型工商业")
    if "一般工商业" in question or ("工商业" in question and "大型工商业" not in question):
        types.add("一般工商业")
    topics = [t for t in TOPIC_WORDS if t in question]
    return types, topics


def claim_types(c):
    v = (c.get("conditions") or {}).get("project_type")
    if isinstance(v, list):
        return set(v)
    if isinstance(v, str):
        return {v}
    return set()


def cond_match(c, types):
    if not types:
        return True
    ct = claim_types(c)
    return (not ct) or ("any" in ct) or bool(ct & types)


def is_unknown(c):
    return (c.get("claim_status") or "") == UNKNOWN_KIND


def hoist_unknown(picked):
    """把已选中集合里的 unknown 主张提到最前，组内保持分数序（稳定分组）。

    两道限制，避免「为了置顶把无关空白拉进来」：
      1. 只在**已选中**集合内重排——unknown 想进来，仍须先过 L1_MIN_RATIO 与 top-k；
      2. 只有分数 ≥ UNKNOWN_HOIST_MIN_RATIO×第一名时才置顶，否则照常按分数排（仍标 unknown）。
    """
    if not picked:
        return picked
    top = picked[0][0]
    lead = {i for i, (s, c) in enumerate(picked)
            if is_unknown(c) and s >= UNKNOWN_HOIST_MIN_RATIO * top}
    return ([x for i, x in enumerate(picked) if i in lead]
            + [x for i, x in enumerate(picked) if i not in lead])


def parse_date(text, field):
    if text is None:
        return None
    t = str(text).strip()
    if t.lower() in ("today", "now"):
        return datetime.now().date().isoformat()
    if not wt.ISO_DATE_RE.match(t):
        raise SystemExit("参数 %s 不是 ISO 日期（YYYY-MM-DD 或 today）：%r" % (field, text))
    try:
        datetime.strptime(t, "%Y-%m-%d")
    except ValueError:
        raise SystemExit("参数 %s 不是有效日期（如 2025-13-40）：%r" % (field, text))
    return t


def l1_candidates(proj, question, policy_as_of, claim_idx, types):
    """L1 候选：落在该时点内、未被标记取代、且条件与问题识别出的类型相容。"""
    out = []
    for score, d in claim_idx.scores(question):
        c = proj.claim_by_id.get(d["id"])
        if not c:
            continue
        ts = c.get("temporal_scope") or {}
        if ts.get("current_effectiveness") == "checked_superseded":
            continue  # 已声明作废：即使区间没收窄也不进结论层
        # 只按「规则自身有效期」过滤；event_deadline（项目并网/备案截止）不参与版本过滤，
        # 否则 2025-12-31 提问「5 月 1 日前并网的存量项目」会漏掉 W-TIME-04/05（173 号 F1a）
        if not wt.ts_rule_within(ts, wt.iso_date(policy_as_of)):
            continue
        if not cond_match(c, types):
            continue
        out.append((score, c))
    return out


def decide_layer(question, policy_as_of, candidates, types, topics):
    """返回 (layer, reason)。规则全部显式，便于回归测试。

    为什么不用「候选条数/分数比」判太宽：实测过，60 条短 claim 上大量低分命中，
    第 4 名与第 1 名的分数比经常 ≥70%，会把正常问题一律赶去原文层。
    改成看**问题里有没有可识别的条件或主题词**——没有信号，说明问题太泛，走原文层更稳。
    """
    if policy_as_of is None:
        return "L2", "未指定 policy_as_of：拒绝走结论层，降级原文层"
    hit_l2 = [m for m in L2_MARKERS if m in question]
    if hit_l2:
        return "L2", "问题在问原文措辞（命中 %s）" % "/".join(hit_l2[:2])
    hit_oos = [w for w in OUT_OF_SCOPE_WORDS if w in question]
    if hit_oos:
        return "L2", ("问题涉及结论层不覆盖的主题（%s）：降级原文层；"
                      "原文层可能仍有相关条文，不得据结论层作答" % "/".join(hit_oos[:2]))
    if not types and not topics:
        return "L2", "问题里没有可识别的项目类型或主题词，走原文层更稳"
    if not candidates:
        return "L2", "结论层在该时点没有条件相容的候选主张"
    sig = []
    if types:
        sig.append("项目类型=" + "/".join(sorted(types)))
    if topics:
        sig.append("主题=" + "/".join(topics[:3]))
    return "L1", "命中信号（%s），结论层有 %d 条相容候选" % ("；".join(sig), len(candidates))


def claim_payload(c, score=None):
    ts = c.get("temporal_scope") or {}
    return {
        "claim_id": c.get("claim_id"),
        "score": round(score, 4) if score is not None else None,
        "claim_text": c.get("claim_text"),
        "claim_status": c.get("claim_status"),
        # 显式标记，消费方（生成层/前端）不必再回看 claim_status 的取值表
        "answer_kind": UNKNOWN_KIND if is_unknown(c) else "supported",
        "conditions": c.get("conditions"),
        "temporal_scope": {
            "start": ts.get("start"), "end": ts.get("end"),
            "boundary_inclusion": ts.get("boundary_inclusion"),
            "current_effectiveness": ts.get("current_effectiveness"),
            "start_role": ts.get("start_role"), "end_role": ts.get("end_role"),
            "policy_as_of": ts.get("policy_as_of"), "retrieved_at": ts.get("retrieved_at"),
        },
        "evidence": [{"source_id": r.get("source_id"), "locator": r.get("locator"),
                      "quote": r.get("quote")}
                     for r in (c.get("source_refs") or []) if isinstance(r, dict)],
    }


def build(question, policy_as_of, event_as_of, forced_layer, top_k, max_chars):
    proj = wt.Project(ROOT)
    proj.load_sources()
    proj.load_claims()

    claim_docs = [d for d in wt.build_wiki_docs(proj, "wiki") if d["kind"] == "claim"]
    claim_idx = wr.build_index(claim_docs)
    # 只有 role=build 的来源可作为答题依据；corroborate（仅交叉校验）与 eval_only（仅评测）不参与
    build_ids = {s["source_id"] for s in proj.sources if s.get("role") == "build"}
    raw_docs = [d for d in wt.build_wiki_docs(proj, "raw") if d.get("source_id") in build_ids]
    raw_idx = wr.build_index(raw_docs) if raw_docs else None

    warnings = []
    if policy_as_of and policy_as_of > LATEST_RETRIEVAL:
        warnings.append(
            "问题时点 %s 晚于最近取数日 %s：库内可能缺少该时点之后的变化（含政策是否换版），"
            "结论不具备该时点的充分性。" % (policy_as_of, LATEST_RETRIEVAL))
    if event_as_of and policy_as_of and event_as_of > policy_as_of:
        warnings.append("event_as_of（%s）晚于 policy_as_of（%s）：请确认问的是"
                        "「按哪版政策」还是「何时发生的事」。" % (event_as_of, policy_as_of))

    types, topics = detect_signals(question)
    candidates = (l1_candidates(proj, question, policy_as_of, claim_idx, types)
                  if policy_as_of else [])
    layer, reason = decide_layer(question, policy_as_of, candidates, types, topics)
    if forced_layer:
        if forced_layer == "L1" and policy_as_of is None:
            # 强制只能「收紧」不能「绕过」时点闸门（09 §8.1 裁决）
            reason = "调用方强制 L1，但未指定 policy_as_of：按裁决拒绝，仍走原文层"
        else:
            layer, reason = forced_layer, "调用方强制指定 %s" % forced_layer

    result = {
        "question": question,
        "policy_as_of": policy_as_of,
        "event_as_of": event_as_of or policy_as_of,
        "route": {
            "layer": layer,
            "reason": reason,
            "l1_rejected": None if policy_as_of else "未指定 policy_as_of",
            "forced": bool(forced_layer),
            "l1_candidate_count": len(candidates),
            "signals": {"types": sorted(types), "topics": topics},
            "stale_risk": bool(policy_as_of and policy_as_of > LATEST_RETRIEVAL),
            "unknown_claims": [],
            "unknown_hoisted": False,
        },
        "answer_kind": None,
        "claims": [],
        "contexts": [],
        "counts": {},
        "refs_known": 0,
        "warnings": warnings,
    }

    if layer == "L1":
        top_score = candidates[0][0] if candidates else 0.0
        picked = [x for x in candidates if x[0] >= L1_MIN_RATIO * top_score][
            :min(top_k, MAX_L1_CANDIDATES)]
        before_lead = picked[0][1].get("claim_id") if picked else None
        picked = hoist_unknown(picked)
        after_lead = picked[0][1].get("claim_id") if picked else None
        result["claims"] = [claim_payload(c, s) for s, c in picked]
        result["answer_kind"] = (UNKNOWN_KIND if picked and is_unknown(picked[0][1])
                                 else ("supported" if picked else None))
        result["route"]["unknown_claims"] = [c.get("claim_id") for _, c in picked if is_unknown(c)]
        result["route"]["unknown_hoisted"] = bool(before_lead and before_lead != after_lead)
        known = {c.get("claim_id") for c in proj.claims}
        # L1 的上下文直接由选中的 claim 构成，标 origin=hit（它们就是命中）
        result["contexts"] = [
            {"id": c.get("claim_id"), "origin": "hit", "rank": i + 1,
             "section": c.get("page_path"), "text": c.get("claim_text") or "",
             "answer_kind": UNKNOWN_KIND if is_unknown(c) else "supported"}
            for i, (_, c) in enumerate(picked)]
        result["counts"] = {"hit": len(picked), "neighbor": 0}
        result["refs_known"] = len(known)
    else:
        if raw_idx is None:
            result["warnings"].append("normalized/ 为空，无法走原文层")
            result["exit"] = 3
            return result
        hits = raw_idx.scores(question, top_k=top_k)
        ctx = wr.assemble_context(hits, raw_docs, radius=1, max_chars=max_chars)
        known = {d["id"] for d in raw_docs} | {c.get("claim_id") for c in proj.claims}
        result["contexts"] = [
            {"id": d.get("id"), "origin": d.get("origin"), "rank": d.get("rank"),
             "section": d.get("section"), "text": d.get("text")}
            for d in ctx["contexts"]]
        result["counts"] = {"hit": ctx["hit_count"], "neighbor": ctx["neighbor_count"],
                            "hit_chars": ctx["hit_chars"], "total_chars": ctx["total_chars"]}
        result["refs_known"] = len(known)

    result["prompt"] = wr.build_prompt(question, result["contexts"], layer=layer)
    return result


def render(result):
    lines = ["问题：%s" % result["question"],
             "时点：policy_as_of=%s，event_as_of=%s" % (result["policy_as_of"], result["event_as_of"]),
             "路由：%s —— %s" % (result["route"]["layer"], result["route"]["reason"])]
    if result["route"]["l1_rejected"]:
        lines.append("注意：%s，结论层已拒绝，下面的材料全部来自原文层。" % result["route"]["l1_rejected"])
    if result.get("answer_kind") == UNKNOWN_KIND:
        lines.append("官方未明确：结论层命中的是「官方未明确」类主张（%s）。回答须先说明官方未明确，"
                     "不得用邻近档位、其他情形或常识类推。"
                     % "、".join(result["route"].get("unknown_claims") or []))
    for w in result["warnings"]:
        lines.append("警告：%s" % w)
    if result["claims"]:
        lines.append("")
        lines.append("依据（结论层）：")
        for c in result["claims"]:
            ts = c["temporal_scope"]
            mark = "（官方未明确）" if c.get("answer_kind") == UNKNOWN_KIND else ""
            lines.append("  [%s]%s %s" % (c["claim_id"], mark, (c["claim_text"] or "")[:70]))
            lines.append("      适用：%s ~ %s（含端点：%s，效力：%s）" % (
                ts["start"] or "开放", ts["end"] or "开放",
                ts["boundary_inclusion"], ts["current_effectiveness"]))
            for side in ("start", "end"):
                if ts.get("%s_role" % side) == "event_deadline":
                    lines.append("      注意：%s 是**项目事件截止日**（%s），不是规则失效日；"
                                 "不满足该事件时点的项目不适用本条。" % (ts.get(side), side))
            for e in c["evidence"]:
                lines.append("      出处：%s %s" % (e["source_id"], e["locator"]))
    if result["contexts"]:
        lines.append("")
        lines.append("上下文（命中 %d，同节补充 %d）：" % (
            result["counts"].get("hit", 0), result["counts"].get("neighbor", 0)))
        for d in result["contexts"]:
            tag = "" if d["origin"] == "hit" else "［补充］"
            lines.append("  %s%s %s" % (tag, d["id"], (d["text"] or "")[:60].replace("\n", " ")))
    lines.append("")
    lines.append("引用可核对的 id 共 %d 个；生成层须只在上面的材料内作答。" % result["refs_known"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wiki 问答入口（规则层，不含模型生成）")
    ap.add_argument("--question", required=True)
    ap.add_argument("--policy-as-of", dest="policy_as_of", default=None,
                    help="问的是哪一版政策；不给则拒绝 L1、降级 L2。可写 today")
    ap.add_argument("--event-as-of", dest="event_as_of", default=None,
                    help="事实/行为发生的时间，默认同 policy_as_of")
    ap.add_argument("--layer", choices=["auto", "L1", "L2"], default="auto")
    ap.add_argument("--top-k", dest="top_k", type=int, default=5)
    ap.add_argument("--max-chars", dest="max_chars", type=int, default=6000)
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--prompt-only", dest="prompt_only", action="store_true")
    args = ap.parse_args(argv)

    try:
        policy_as_of = parse_date(args.policy_as_of, "--policy-as-of")
        event_as_of = parse_date(args.event_as_of, "--event-as-of")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    forced = None if args.layer == "auto" else args.layer
    result = build(args.question, policy_as_of, event_as_of, forced, args.top_k, args.max_chars)
    if result.get("exit") == 3:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    if args.prompt_only:
        print(result["prompt"])
    elif args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
