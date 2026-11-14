# -*- coding: utf-8 -*-
"""t12_173_narrow_guard.py —— 173 号 F1a / F2 的窄回归护栏（可复跑，不调模型、不写数据）。

F1a：四题 ×（不给 event / 显式 event=2025-05-01）在 policy_as_of=2025-12-31 下都必须给出
     W-TIME-04 与 W-TIME-05；A3 对照（policy=2025-04-30）同样要给出；反向对照：
     policy=2024-06-01 不得命中 W-DEF-02（rule_validity 端点照常过滤）。
F2：两题命中 W-GRID-08 时，材料与 prompt 都必须带「安全运行所必需的必要技术要求除外」，
     且页面脚注、CH、PG 三处文本同样要有；「无正当理由拒绝」的绝对禁止不得因加例外而消失。

用法：python tools\\t12_173_narrow_guard.py [--json]
退出码：0 全过；1 有断言失败。
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_query as wq  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TIME_CASES = ["W-A-08", "W-A-09", "W-A-13", "W-A-14"]
EXC = "安全运行所必需的必要技术要求除外"
GRID08_QS = [
    "电网企业能否以技术要求更高为由拖延并网？",
    "电网企业拒绝并网又没有正当理由，违规吗？",
]


def load_jsonl(name):
    p = os.path.join(ROOT, name)
    return [json.loads(x) for x in io.open(p, encoding="utf-8") if x.strip()]


def ids(result):
    return [c["claim_id"] for c in result["claims"]]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cases = {c["case_id"]: c for c in load_jsonl("eval/cases.jsonl")}
    checks, fails = [], []

    def add(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            fails.append(name)

    # ---- F1a：四题 × 两种 event ----
    for cid in TIME_CASES:
        q = cases[cid]["question"]
        for ev in (None, "2025-05-01"):
            r = wq.build(q, "2025-12-31", ev, None, 5, 6000)
            got = ids(r)
            add("F1a %s event=%s 含 W-TIME-04/05" % (cid, ev or "无"),
                "W-TIME-04" in got and "W-TIME-05" in got,
                {"layer": r["route"]["layer"], "claims": got})

    # ---- F1a：A3 正常对照 ----
    q = cases["W-A-08"]["question"]
    r = wq.build(q, "2025-04-30", None, None, 5, 6000)
    add("F1a A3 policy=2025-04-30 仍含 W-TIME-04/05",
        "W-TIME-04" in ids(r) and "W-TIME-05" in ids(r),
        {"layer": r["route"]["layer"], "claims": ids(r)})

    # ---- F1a：反向对照，rule_validity 端点照常过滤 ----
    r = wq.build("分布式光伏怎么分类", "2024-06-01", None, None, 5, 6000)
    add("F1a 反向对照 policy=2024-06-01 不含 W-DEF-02",
        "W-DEF-02" not in ids(r),
        {"layer": r["route"]["layer"], "claims": ids(r)})

    # ---- F2：claim / prompt ----
    for q in GRID08_QS:
        r = wq.build(q, "2025-12-31", None, None, 5, 6000)
        got = ids(r)
        hit = "W-GRID-08" in got
        text_ok = (not hit) or any(c["claim_id"] == "W-GRID-08" and EXC in (c["claim_text"] or "")
                                   for c in r["claims"])
        add("F2 %s 命中 W-GRID-08 且带例外" % q, hit and text_ok,
            {"layer": r["route"]["layer"], "claims": got})
        add("F2 %s prompt 带例外" % q, EXC in r["prompt"], {"prompt_has_exc": EXC in r["prompt"]})

    # ---- F2：页面 / CH / PG ----
    page = io.open(os.path.join(ROOT, "wiki", "topics", "备案与时间衔接.md"), encoding="utf-8").read()
    add("F2 页面含例外", EXC in page.replace("**", ""), {"page": "wiki/topics/备案与时间衔接.md"})
    add("F2 页面保留无正当理由禁止", "不得无正当理由拒绝或拖延" in page, {})

    chunks = {c["chunk_id"]: c for c in load_jsonl("exports/wiki_chunks.jsonl")}
    add("F2 CH-W-GRID-08 含例外", EXC in chunks["CH-W-GRID-08"]["text"], {})
    add("F2 CH-W-GRID-08 exception 非空",
        bool((chunks["CH-W-GRID-08"].get("conditions") or {}).get("exception")), {})
    add("F2 PG-备案与时间衔接 含例外", EXC in chunks["PG-备案与时间衔接"]["text"], {})

    payload = {"total": len(checks), "passed": sum(1 for c in checks if c["pass"]),
               "all_pass": not fails, "fails": fails, "checks": checks}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            print("%s  %s" % ("PASS" if c["pass"] else "FAIL", c["check"]))
        print("%d/%d PASS；ALL_PASS=%s" % (payload["passed"], payload["total"], payload["all_pass"]))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
