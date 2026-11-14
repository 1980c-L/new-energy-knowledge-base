# -*- coding: utf-8 -*-
"""wiki_answer.py —— 生成层 + 生成后校验（M13）。

职责边界（`09` §3.3、`handoff/接续-2026-11-13-c.md` §三.4）：

  1. **生成**：只允许在 `wiki_query.build()` 给出的材料内组织语言；
  2. **强制引用**：答案里的 `[id]` 必须落在 `contexts` 的 id 集合内，编造的 id 不算引用；
  3. **无依据拒答**：材料为空、或问题落在结论层不覆盖的主题上，输出「现有材料不足以确认」；
  4. **生成后校验**：出稿后逐条查（引用越界 / 数字无据 / 时点被改 / unknown 未标 / 缺证未拒答 / 条件丢失），
     **不通过就退回，不返回答案**。

为什么校验不能后补（`07-语义护栏.md` §一 的两次实证）：`check` 只保证「结论挂了 claim、claim 能定位到原文」，
不保证「答案只用了材料」。模型一旦绕开材料凭记忆答，前面 71 条主张、22 组负向测试、18 组路由回归全部白做。

两种 provider：
  - `stub`（默认）：**不联网**，按规则从材料拼答案。作用是让「生成→校验」管线可跑、可测、可复跑；
  - `model`：真实调用，**必须** `--provider model --i-approve-cost` 双开关，且先跑 `--dry-run` 看请求数与估算 token。
    未授权时直接退出码 4，不发任何网络请求。（本轮未授权，因此从未真实调用。）

用法：
    python tools\\wiki_answer.py --question "..." --policy-as-of 2025-12-31
    python tools\\wiki_answer.py --question "..." --policy-as-of 2025-12-31 --json
    python tools\\wiki_answer.py --question "..." --provider model --dry-run      # 只算请求与 token
    python tools\\wiki_answer.py --question "..." --provider model --i-approve-cost # 需授权

退出码：0 答案通过校验；1 校验不通过（答案已退回，stdout 只给违规项）；
        2 参数非法；3 内部错误；4 未授权/未就绪（如缺 API key）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_query as wq  # noqa: E402
import wiki_retrieval as wr  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFUSE = "现有材料不足以确认"
UNKNOWN_PHRASES = ("官方未明确", "未规定", "办法未规定")

# 校验规则代号（与报告里的 code 对应）
R1_REF_OUT_OF_SCOPE = "R1"   # 引用了材料外的 id（或一个引用都没有）
R2_NUMBER_UNSOURCED = "R2"   # 出现材料里没有的数字 / 条号 / 日期
R3_TIME_OVERWRITTEN = "R3"   # 时点被改写（答案里的年份/日期不在材料与问题里）
R4_UNKNOWN_UNMARKED = "R4"   # 结论层首位是 unknown，但答案没说「官方未明确」
R5_NO_EVIDENCE_NO_REFUSAL = "R5"  # 材料为空/无命中，但答案没有拒答
R6_CONDITION_LOST = "R6"     # 主张带条件，答案里条件词全丢（警告级，不阻塞）

# ---- 计费与预算闸门 ----
# 单价来源：DeepSeek 官方定价页，2026-11-13 抓取。**一律按高峰价算**（不猜时段），宁可高估。
# thinking 默认开启，返回的 usage.completion_tokens 已含思考 token，按实际返回记账，不另加系数；
# 但**调用前**只能用预留值（output_tokens_reserved）估上限，故预留按 5× 取。
PRICE_IN_USD_PER_TOKEN = 0.30 / 1_000_000
PRICE_OUT_USD_PER_TOKEN = 1.20 / 1_000_000
DEFAULT_MAX_COST_USD = 0.50
OUT_RESERVE_TOKENS = 2000      # 单请求输出预留（thinking 开）
MAX_OUTPUT_TOKENS = 3000       # max_tokens 实际上限（给思考过程留够；400 实测会被吃光）
LEDGER = os.environ.get("WIKI_LEDGER") or os.path.join(ROOT, "logs", "model_calls.jsonl")

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_ART_RE = re.compile(r"第\s*\d+\s*条")
_DATE_RE = re.compile(r"\d{4}\s*[-年]\s*\d{1,2}\s*[-月]?\s*\d{0,2}\s*日?")
_YEAR_RE = re.compile(r"\d{4}\s*年")


# ---------------- 生成（stub） ----------------
def gen_stub(result):
    """离线生成：完全由材料拼装，不做材料外的润色。

    L1：逐条给出结论 + 条件 + 适用时点；unknown 首位时先声明「官方未明确」。
    L2：给出原文片段并指出出处单元；没有命中就拒答。
    """
    layer = result["route"]["layer"]
    claims = result.get("claims") or []
    ctxs = result.get("contexts") or []

    if layer == "L1":
        if not claims:
            return REFUSE
        head = ""
        if result.get("answer_kind") == wq.UNKNOWN_KIND:
            head = ("官方未明确：材料中命中的是「官方未明确」类主张（%s），以下不构成可执行结论。\n"
                    % "、".join("[%s]" % x for x in result["route"].get("unknown_claims") or []))
        lines = [head] if head else []
        for c in claims:
            ts = c.get("temporal_scope") or {}
            cond = c.get("conditions") or {}
            pt = cond.get("project_type")
            seg = "[%s] %s" % (c["claim_id"], c.get("claim_text") or "")
            if pt and pt != "any":
                seg += "（适用类型：%s）" % ("、".join(pt) if isinstance(pt, list) else pt)
            seg += "（适用时点：%s 起%s）" % (ts.get("start") or "该规定生效",
                                             "，止于 %s" % ts["end"] if ts.get("end") else "")
            lines.append("- " + seg)
        lines.append("")
        lines.append("以上均出自已编译结论，引用位置见对应 claim 的 source_refs；"
                     "材料之外的情形不作断言。")
        return "\n".join(lines)

    hits = [c for c in ctxs if c.get("origin") == "hit"]
    if not hits:
        return REFUSE
    lines = ["原文层材料如下（未作结论化改写）："]
    for c in hits:
        lines.append("- [%s] %s" % (c.get("id"), (c.get("text") or "").strip()))
    lines.append("")
    lines.append("上述为原文片段，具体适用须结合条款上下文判断；材料未覆盖的部分「%s」。" % REFUSE)
    return "\n".join(lines)


# ---------------- 生成后校验 ----------------
def _material_text(result):
    """所有可用于对账的文本：材料 + 问题（问题里的数字是提问方给的，不算编造）。"""
    parts = [result.get("question") or ""]
    for c in result.get("contexts") or []:
        parts.append(c.get("text") or "")
    for c in result.get("claims") or []:
        parts.append(c.get("claim_text") or "")
        for e in c.get("evidence") or []:
            parts.append(e.get("quote") or "")
        cond = c.get("conditions") or {}
        for v in cond.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend([str(x) for x in v])
        ts = c.get("temporal_scope") or {}
        for k in ("start", "end", "policy_as_of"):
            if ts.get(k):
                parts.append(str(ts[k]))
    return "\n".join(parts)


def _norm(s):
    return re.sub(r"\s+", "", s or "")


_BRACKET_REF_RE = re.compile(r"[\[［]([^\]］\n]{1,160})[\]］]")


def scan_refs(answer, ctx_ids):
    """抽出答案里的方括号引用并与材料 id 比对。

    **不能只认 `W-XX-NN`**：L2 原文层的 id 形如 `PV-NAT-2025-007-ATTACH#第22条`，
    模型如实引用它却会被前缀判断当成"零引用"（2026-11-14 全量首跑 W-A-22 就是这么被误判的）。
    判定改为「按内容比对」：带 `#`/`_` 或形如 `W-XX-NN` 的括号算引用，其余括号（如 `[1]`）忽略。
    """
    tokens = []
    for m in _BRACKET_REF_RE.finditer(answer or ""):
        t = _norm(m.group(1))
        if "#" in t or "_" in t or re.match(r"^W-[A-Z]+-\d+$", t):
            tokens.append(t)
    valid = [t for t in tokens if t in (ctx_ids or set())]
    bogus = [t for t in tokens if t not in (ctx_ids or set())]
    return tokens, valid, bogus


def verify_answer(answer, result):
    """生成后校验。返回 (ok, violations[(code, detail)])。"""
    violations = []
    material = _material_text(result)
    m_norm = _norm(material)
    a_norm = _norm(answer or "")
    ctx_ids = {c.get("id") for c in result.get("contexts") or []}

    # R1 引用：必须至少一条，且每一处引用都落在材料内（重复引用同一 id 是合法的）。
    # 例外：**拒答不需要引用**——「现有材料不足以确认」本身就是"不作答"，
    # 强制它引用反而会逼模型硬凑一条出处（2026-11-14 首跑实测：库外主题被 R1 误拦）。
    refusal = REFUSE in (answer or "")
    tokens, known, bogus = scan_refs(answer, ctx_ids)
    total_refs = len(tokens)
    if total_refs == 0 and not refusal:
        violations.append((R1_REF_OUT_OF_SCOPE, "答案没有引用任何材料 id（应至少引用 1 条）"))
    elif bogus:
        violations.append((R1_REF_OUT_OF_SCOPE,
                           "有 %d 处引用不在材料内：%s（合法 %s）"
                           % (len(bogus), "、".join(sorted(set(bogus))), known or "无")))

    # R2 数字/条号/日期：先把引用括号整段去掉（半角与全角都去），再逐个对账。
    # 不去干净的后果：模型引 `［PV-…#第22条］` 时，「第22条」会被当成"材料里没有的条号"。
    scan = _BRACKET_REF_RE.sub(" ", answer or "")
    for pat, label in ((_ART_RE, "条号"), (_DATE_RE, "日期"), (_NUM_RE, "数字")):
        for m in pat.finditer(scan):
            token = _norm(m.group(0))
            if token and token not in m_norm:
                violations.append((R2_NUMBER_UNSOURCED, "材料中找不到%s：%s" % (label, m.group(0))))

    # R3 时点：年份断言必须在材料里出现
    for m in _YEAR_RE.finditer(scan):
        if _norm(m.group(0)) not in m_norm:
            violations.append((R3_TIME_OVERWRITTEN, "答案出现材料外的年份：%s" % m.group(0)))

    # R4 unknown：结论层首位是 unknown，答案必须不给出确定性结论。
    # 两种合规写法都接受：①显式写「官方未明确」；②明确拒答「现有材料不足以确认」**且**引用了那条 unknown。
    # 2026-11-14 全量首跑 W-A-10 只用了第②种（写「无法确认是否仍有效」），被纯字符串检查误杀。
    if result.get("answer_kind") == wq.UNKNOWN_KIND:
        unknown_ids = {c.get("claim_id") for c in result.get("claims") or []
                       if c.get("answer_kind") == wq.UNKNOWN_KIND
                       or c.get("claim_status") == wq.UNKNOWN_KIND}
        said_unknown = any(p in (answer or "") for p in UNKNOWN_PHRASES)
        refused_and_cited = refusal and bool(unknown_ids & set(known))
        if not (said_unknown or refused_and_cited):
            violations.append((R4_UNKNOWN_UNMARKED,
                               "首位结论是「官方未明确」，答案既未说明、也未「拒答+引用该条」"))

    # R5 缺证拒答：没有任何材料时不得作答
    if not (result.get("contexts") or []):
        if REFUSE not in (answer or ""):
            violations.append((R5_NO_EVIDENCE_NO_REFUSAL, "无材料却未拒答"))

    # R6 条件：主张带非 any 的项目类型，答案至少提到其中一处（警告级）
    for c in result.get("claims") or []:
        pt = (c.get("conditions") or {}).get("project_type")
        vals = pt if isinstance(pt, list) else ([pt] if pt else [])
        vals = [v for v in vals if v and v != "any"]
        if vals and not any(v in (answer or "") for v in vals):
            violations.append((R6_CONDITION_LOST,
                               "%s 的类型条件未在答案出现（%s）" % (c.get("claim_id"), "、".join(vals))))

    hard = [v for v in violations if v[0] != R6_CONDITION_LOST]
    return (not hard), violations


# ---------------- 真实 provider（本轮未授权，默认拒绝） ----------------
def estimate_tokens(text):
    """粗估 token：中文按 1 字≈0.7 token、ASCII 按 4 字符≈1 token。只用于预审，不作计费依据。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    other = len(re.findall(r"[^\u4e00-\u9fff]", text or ""))
    return int(cjk * 0.7 + other / 4.0) + 1


def read_ledger_total():
    """累计已花（美元）。账本按行追加，一行一次调用。"""
    if not os.path.exists(LEDGER):
        return 0.0
    total = 0.0
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                total += float(json.loads(line).get("cost_usd") or 0)
            except (ValueError, TypeError):
                continue
    return total


def budget_check(cap, est):
    """预算闸门：已花 + 本次预估 ≤ 上限才放行。"""
    spent = read_ledger_total()
    return (spent + est <= cap), spent, est


def log_call(record):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def gen_model(result, approve, dry_run, cap=None):
    prompt = result.get("prompt") or ""
    inp = estimate_tokens(prompt)
    # 模型名按官方定价页在售名（2026-11-13 核实：只有 deepseek-flash / deepseek-v4-pro；
    # deepseek-chat 已不在价目表，旧名 deepseek-v4-flash 仍接受但按 Flash 计费）。
    model = os.environ.get("WIKI_MODEL", "deepseek-flash")
    cap = DEFAULT_MAX_COST_USD if cap is None else float(cap)
    est = inp * PRICE_IN_USD_PER_TOKEN + OUT_RESERVE_TOKENS * PRICE_OUT_USD_PER_TOKEN
    within, spent, _ = budget_check(cap, est)
    plan = {"provider": model, "requests": 1,
            "input_tokens_est": inp, "output_tokens_reserved": OUT_RESERVE_TOKENS,
            "thinking": "on（接口默认开启，思考 token 按输出计费）",
            "est_cost_usd": round(est, 6), "spent_usd": round(spent, 6),
            "cap_usd": cap, "node": "chat/completions"}
    if dry_run:
        return None, plan
    if not approve:
        return None, dict(plan, error="未授权：需 --i-approve-cost")
    if not within:
        return None, dict(plan, error="超预算：已花 %.4f + 本次约 %.4f > 上限 %.4f（未发请求）"
                                      % (spent, est, cap))
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return None, dict(plan, error="缺 DEEPSEEK_API_KEY")
    import urllib.request  # 延迟导入：未授权/超预算时不做任何网络准备
    # max_tokens 必须给 thinking 留足：2026-11-14 首跑实测，400 会被思考过程吃光，
    # 导致 content 为空或半截（该次 completion_tokens 正好 400）。
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": MAX_OUTPUT_TOKENS}).encode("utf-8")
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    finish = choice.get("finish_reason")
    usage = data.get("usage") or {}
    pin = int(usage.get("prompt_tokens") or inp)
    pout = int(usage.get("completion_tokens") or OUT_RESERVE_TOKENS)
    cost = pin * PRICE_IN_USD_PER_TOKEN + pout * PRICE_OUT_USD_PER_TOKEN
    log_call({"ts": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
              "model": model, "question": result.get("question"),
              "policy_as_of": result.get("route", {}).get("policy_as_of")
              or result.get("policy_as_of"), "layer": result["route"]["layer"],
              "prompt_tokens": pin, "completion_tokens": pout,
              "cost_usd": round(cost, 8), "cap_usd": cap, "finish_reason": finish})
    plan.update({"prompt_tokens": pin, "completion_tokens": pout,
                 "cost_usd": round(cost, 6), "spent_after_usd": round(spent + cost, 6),
                 "finish_reason": finish})
    # 截断/空答案必须显式失败，不能当成「模型答了个空的」混过去：钱已经花了，要记账也要记原因
    if finish == "length":
        return None, dict(plan, error="输出被 max_tokens 截断（finish_reason=length），本次无效")
    if not content.strip():
        return None, dict(plan, error="返回内容为空（思考占满输出或拒答被吞），本次无效")
    return content, plan


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wiki 生成层（带生成后校验）")
    ap.add_argument("--question", required=True)
    ap.add_argument("--policy-as-of", dest="policy_as_of", default=None)
    ap.add_argument("--event-as-of", dest="event_as_of", default=None)
    ap.add_argument("--layer", choices=["auto", "L1", "L2"], default="auto")
    ap.add_argument("--provider", choices=["stub", "model"], default="stub")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", help="只算请求与 token，不调用")
    ap.add_argument("--i-approve-cost", dest="approve", action="store_true",
                    help="确认为真实调用负责（未给则不发请求）")
    ap.add_argument("--max-cost-usd", dest="max_cost_usd", type=float, default=None,
                    help="累计费用上限（美元），默认 %.2f；已花+本次预估超限即拒绝" % DEFAULT_MAX_COST_USD)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        policy_as_of = wq.parse_date(args.policy_as_of, "--policy-as-of")
        event_as_of = wq.parse_date(args.event_as_of, "--event-as-of")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    forced = None if args.layer == "auto" else args.layer
    result = wq.build(args.question, policy_as_of, event_as_of, forced, 5, 6000)
    if result.get("exit") == 3:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    plan = None
    if args.provider == "model":
        answer, plan = gen_model(result, args.approve, args.dry_run, args.max_cost_usd)
        if answer is None:
            out = {"mode": "model", "plan": plan, "route": result["route"]}
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 4
    else:
        answer = gen_stub(result)

    ok, violations = verify_answer(answer, result)
    payload = {
        "question": args.question,
        "policy_as_of": policy_as_of,
        "route": result["route"],
        "answer_kind": result.get("answer_kind"),
        "provider": args.provider,
        "plan": plan,
        "answer": answer if ok else None,
        "rejected_answer": None if ok else answer,
        "verify": {"ok": ok,
                   "violations": [{"code": c, "detail": d} for c, d in violations],
                   "refs": scan_refs(answer, {x.get("id") for x in result.get("contexts") or []})[1],
                   "refs_total": len(scan_refs(answer, {x.get("id") for x in result.get("contexts") or []})[0])},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("问题：%s" % args.question)
        print("路由：%s —— %s" % (result["route"]["layer"], result["route"]["reason"]))
        print("生成：%s%s" % (args.provider, "" if plan is None else "（请求 %d 次）" % plan["requests"]))
        print("-" * 56)
        if ok:
            print(answer)
        else:
            print("【答案已退回】校验未通过，不返回答案：")
            for c, d in violations:
                print("  ✗ %s %s" % (c, d))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
