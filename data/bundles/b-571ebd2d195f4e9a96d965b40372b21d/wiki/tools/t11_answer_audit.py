# -*- coding: utf-8 -*-
"""t11_answer_audit.py —— 模型答案的「超引文」初筛（M14）。

用途：`verify_answer` 只查形式（引用在不在材料里、数字有没有出处），查不出
「引用了合法 id，但把适用范围放大」「材料没说的却说了」这类问题。语义对错无法自动判，
但**能把人要看的东西从 113 句压到几句**。

四条信号（按可靠性从高到低）：

  S2 量词越界（高）  句中有「所有/一律/任何/全部/均/无论」，材料里没有同样的量词 → 疑似放大
  S3 条件丢失（高）  句子引了带类型条件的主张，却没出现该类型词 → 疑似扩大适用
  S4 数据无据（高）  句中的数字/条号/日期在材料里找不到（按句定位，比 R2 的整篇对账细）
  S1 低支撑（低）    整句与材料任何一段的字符二元组重合度都低，且**不是拒答/边界说明**
                     ——只用来分流失效，不作判据

**刻意不做的**：不把「现有材料不足以确认」「不能类推」这类拒答/边界句当问题。
第一版就因为没排除它们，把 41.6% 的句子标成可疑，等于没筛。

用法：
    python tools\\t11_answer_audit.py                          # 审计 logs/model-run-2026-11-14.json
    python tools\\t11_answer_audit.py --run logs/x.json --top 8 --json-out logs/a.json
    python tools\\t11_answer_audit.py --selftest               # 两个方向都验：该标 / 不该标

退出码：0 正常；1 自测未通过。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_answer as wa  # noqa: E402
import wiki_query as wq   # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUANTIFIERS = ("所有", "一切", "任何", "一律", "全部", "均须", "都必须", "无论", "凡是")
# 拒答/边界句：这类句子天生不会在材料里出现，标它等于制造噪音
META_MARKERS = ("不足以确认", "未明确", "未规定", "不能类推", "不能从", "不能仅凭",
                "未说明", "未提及", "未使用", "资料未", "所给资料", "现有材料", "因此无法确认")
JUDGE_WORDS = ("应当", "须", "不得", "可以", "属于", "视为", "有效期", "按原政策", "执行")
MIN_LEN = 16
LOW_SUPPORT = 0.30


def bigrams(s):
    s = re.sub(r"[\s，。；：、（）()「」“”\"'《》\-—*%]+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def dice(a, b):
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def ngram_coverage(sent_key, material, n=6):
    """句子有多少比例的 n-元片段能在材料里找到（0–1）。"""
    if not sent_key or len(sent_key) < n:
        return 0.0
    grams = [sent_key[i:i + n] for i in range(len(sent_key) - n + 1)]
    hit = sum(1 for g in grams if g in material)
    return hit / float(len(grams))


def key_of(s):
    """比对用的"素"文本：去标点、markdown、序号、引号，只留汉字字母数字。"""
    s = re.sub(r"^\s*\d+[.、)）]\s*", "", s or "")
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)


def split_sentences(text):
    text = wa._BRACKET_REF_RE.sub("", text or "")
    parts = re.split(r"[。；\n]+|(?:^|\s)[-*]\s", text)
    return [p.strip(" 　·*-") for p in parts if p and len(p.strip()) >= 4]


def build_material(result):
    segs = []
    for c in result.get("contexts") or []:
        if (c.get("text") or "").strip():
            segs.append(c["text"].strip())
    for c in result.get("claims") or []:
        if (c.get("claim_text") or "").strip():
            segs.append(c["claim_text"].strip())
    return segs


def audit_answer(answer, result):
    segs = build_material(result)
    # 问题里出现的数字是提问方给的（如"6 月才并网"），不算材料外断言——与 verify 的口径一致
    material_norm = key_of(" ".join(segs) + " " + (result.get("question") or ""))
    seg_keys = [key_of(s) for s in segs]
    claim_types = {}
    for c in result.get("claims") or []:
        pt = (c.get("conditions") or {}).get("project_type")
        vals = pt if isinstance(pt, list) else ([pt] if pt else [])
        claim_types[c.get("claim_id")] = [v for v in vals if v and v != "any"]

    findings = []
    for sent in split_sentences(answer):
        if any(m in sent for m in META_MARKERS):
            continue          # 拒答/边界说明：材料里本来就不会有原话
        codes = []
        score, nearest = 0.0, ""
        sent_key = key_of(sent)
        for s in segs:
            d = dice(sent, s)
            if d > score:
                score, nearest = d, s
        # Dice 对长度差极敏感：整句是材料原话的片段时也会算低分。
        # 所以先做「双向包含」判断（去掉标点/markdown/序号后再比）：
        #   句子 ⊂ 材料（引述原句）或 材料片段 ⊂ 句子（句子在引用材料）→ 都算满支撑。
        if sent_key and sent_key in material_norm:
            score, nearest = 1.0, (nearest or "（与材料原文一致）")
        else:
            # 6-gram 覆盖率：句子里的片段有多少能在材料中按序找到。
            # 引述、拼接、加粗改写都能被这条兜住；真正编出来的句子覆盖率会很低。
            cov = ngram_coverage(sent_key, material_norm)
            if cov > score:
                score = cov
        # S2 量词
        for q in QUANTIFIERS:
            if q in sent and q not in material_norm:
                codes.append("S2:%s" % q)
                break
        # S3 条件
        for cid in wa.scan_refs(sent, {c.get("claim_id") for c in result.get("claims") or []})[1]:
            types = claim_types.get(cid) or []
            if types and not any(t in sent for t in types):
                codes.append("S3:%s" % cid)
        # S4 数据无据（逐句）
        for m in wa._NUM_RE.finditer(sent):
            tok = re.sub(r"\s+", "", m.group(0))
            if tok and tok not in material_norm:
                codes.append("S4:%s" % m.group(0))
                break
        # S1 低支撑：仅当这句在讲判断、且整句都对不上材料
        if (len(sent) >= MIN_LEN and score < LOW_SUPPORT
                and any(w in sent for w in JUDGE_WORDS)):
            codes.append("S1")
        if codes:
            findings.append({"sentence": sent, "codes": codes,
                             "support": round(score, 3), "nearest": nearest[:140]})
    hard = [f for f in findings if any(c[0] != "S1" for c in f["codes"])]
    soft = [f for f in findings if f not in hard]
    return hard + soft


def resolve_questions(rows):
    """给每条答案补上原问题：优先 cases.jsonl，其次 t9 的对抗用例（A1–A4）。"""
    qmap = {}
    cp = os.path.join(ROOT, "eval", "cases.jsonl")
    if os.path.exists(cp):
        for line in open(cp, encoding="utf-8"):
            if line.strip():
                c = json.loads(line)
                qmap[c["case_id"]] = (c["question"], c["policy_as_of"])
    try:
        import t9_answer_guard as t9
        for i, a in enumerate(t9.ADVERSARIAL, 1):
            qmap["A%d" % i] = (a["question"], a["policy"])
    except Exception:  # noqa: BLE001
        pass
    return qmap


def run_audit(path, top):
    rows = json.load(open(path, encoding="utf-8"))
    qmap = resolve_questions(rows)
    total_sent, hard_n, soft_n, skipped = 0, 0, 0, 0
    out = []
    for r in rows:
        ans = r.get("answer")
        if not ans or r.get("error"):
            continue
        qa = qmap.get(r.get("case_id"))
        if not qa:
            skipped += 1
            continue
        result = wq.build(qa[0], qa[1], None, None, 5, 6000)
        f = audit_answer(ans, result)
        nsent = len(split_sentences(ans))
        total_sent += nsent
        hard = [x for x in f if any(c[0] != "S1" for c in x["codes"])]
        hard_n += len(hard)
        soft_n += len(f) - len(hard)
        out.append({"case_id": r.get("case_id"), "layer": r.get("layer"),
                    "sentences": nsent, "hard": len(hard), "soft": len(f) - len(hard),
                    "findings": f})

    print("审计 %d 条答案、%d 句；**硬信号 %d 句**、低支撑提示 %d 句%s"
          % (len(out), total_sent, hard_n, soft_n,
             "；%d 条因日志缺问题跳过" % skipped if skipped else ""))

    picked = sorted([x for x in out if x["hard"]], key=lambda x: -x["hard"])[:top]
    if not picked:
        print("\n没有硬信号。可以只抽查 soft 里的几条，或直接转外部复核。")
    for x in picked:
        print("\n== %s（%s，硬信号 %d / 共 %d 句）"
              % (x["case_id"], x["layer"], x["hard"], x["sentences"]))
        for f in [y for y in x["findings"] if any(c[0] != "S1" for c in y["codes"])][:4]:
            print("   [%s] 句：%s" % (",".join(f["codes"]), f["sentence"][:90]))
            print("        材料最接近：%s" % (f["nearest"][:90] or "（无）"))
    return out


def selftest():
    q = "电网企业在接入服务上要公开哪些信息、提供哪些便利？"
    result = wq.build(q, "2025-12-31", None, None, 5, 6000)
    cid = next(c["id"] for c in result["contexts"] if "第22条" in (c["id"] or ""))
    good = "电网企业应当公布并及时更新接入系统典型设计方案［%s］" % cid
    bad_q = "所有电网企业一律必须无条件公开全部内部数据，任何情况下不得拒绝"
    bad_n = "电网企业应当于 99 个工作日内答复"
    refuse = "现有材料不足以确认该办理时限，办法未规定地市级时限，不能类推。"
    f_good = audit_answer(good, result)
    f_badq = [f for f in audit_answer(bad_q, result) if any(c.startswith("S2") for c in f["codes"])]
    f_badn = [f for f in audit_answer(bad_n, result) if any(c.startswith("S4") for c in f["codes"])]
    f_ref = audit_answer(refuse, result)
    checks = [
        ("C1 有原文支撑的句子不标", not f_good, len(f_good)),
        ("C2 全称量词越界要标 S2", bool(f_badq), [c for f in f_badq for c in f["codes"]]),
        ("C3 材料外数字要标 S4", bool(f_badn), [c for f in f_badn for c in f["codes"]]),
        ("C4 拒答/边界句不标", not f_ref, len(f_ref)),
    ]
    fails = 0
    for name, ok, detail in checks:
        fails += 0 if ok else 1
        print("%-6s %s   %s" % ("PASS" if ok else "FAIL", name, detail))
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="模型答案超引文初筛")
    ap.add_argument("--run", default=os.path.join(ROOT, "logs", "model-run-2026-11-14.json"))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    out = run_audit(args.run, args.top)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n明细已写 %s" % args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
