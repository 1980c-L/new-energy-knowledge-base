# -*- coding: utf-8 -*-
"""A 阶段收尾：sources.jsonl 自校验 —— 原始文件存在、sha256 与登记一致、normalized 可定位。

退出码：0 全通过；1 存在错误（直接影响后续定位）。
"""
import os, sys, json, hashlib

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
rows = [json.loads(l) for l in open(os.path.join(ROOT, "sources.jsonl"), encoding="utf-8") if l.strip()]

errs, warns, ok = [], [], 0

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

print(f"校验 {len(rows)} 条来源\n")
for r in rows:
    sid = r["source_id"]
    problems = []
    raw = os.path.join(ROOT, r["raw_path"] or "")
    if not r["raw_path"] or not os.path.exists(raw):
        problems.append("原始文件缺失")
    else:
        actual = sha256(raw)
        if actual != r["raw_sha256"]:
            problems.append(f"sha256 不符（登记 {r['raw_sha256'][:12]} / 实际 {actual[:12]}）")
        if os.path.getsize(raw) != r["raw_bytes"]:
            problems.append(f"字节数不符（登记 {r['raw_bytes']} / 实际 {os.path.getsize(raw)}）")
    tp = os.path.join(ROOT, r["text_path"] or "")
    n_lines = None
    if not r["text_path"] or not os.path.exists(tp):
        problems.append("normalized 文本缺失")
    else:
        with open(tp, encoding="utf-8") as fh:
            content = fh.read()
        if len(content.strip()) < 50:
            problems.append("normalized 文本过短")
        n_lines = content.count("\n") + (0 if content.endswith("\n") else 1)

    if problems:
        errs.append((sid, problems))
        print(f"[FAIL] {sid:<26} {'; '.join(problems)}")
    else:
        ok += 1
        print(f"[ OK ] {sid:<26} {r['raw_bytes']:>8} B  sha={r['raw_sha256'][:12]}  "
              f"text_lines={n_lines}  role={r['role']}")

# 角色约束：eval_only 不得进入 wiki 建设语料（此处只做登记层面的可见性检查）
build = [r["source_id"] for r in rows if r["role"] == "build"]
corr = [r["source_id"] for r in rows if r["role"] == "corroborate"]
evalonly = [r["source_id"] for r in rows if r["role"] == "eval_only"]
print(f"\nbuild 语料 {len(build)}: {build}")
print(f"corroborate {len(corr)}: {corr}（只做交叉校验，不作引用首选）")
print(f"eval_only {len(evalonly)}: {evalonly}（不得并入 B 阶段建设语料）")

# 题量与参照对齐
cases = [json.loads(l) for l in open(os.path.join(ROOT, "eval", "cases.jsonl"), encoding="utf-8") if l.strip()]
refs = [json.loads(l) for l in open(os.path.join(ROOT, "eval", "reference.jsonl"), encoding="utf-8") if l.strip()]
cid, rid = {c["case_id"] for c in cases}, {r["case_id"] for r in refs}
print(f"\n评估题 {len(cases)} 条，参照 {len(refs)} 条，对齐: {cid == rid}（差集 {cid ^ rid or '无'}）")
if cid != rid:
    errs.append(("eval", ["case_id 与 reference 不对齐"]))
for r in refs:
    if r["blind_status"] != "non_blind" or r["independent_review"] != "pending":
        warns.append((r["case_id"], "参照状态字段非预期"))

print(f"\n结果：通过 {ok}/{len(rows)}，错误 {len(errs)}，提醒 {len(warns)}")
if warns:
    for w in warns:
        print("  [warn]", w)
sys.exit(0 if not errs else 1)
