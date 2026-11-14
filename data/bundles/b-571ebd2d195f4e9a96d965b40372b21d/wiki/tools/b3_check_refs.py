# -*- coding: utf-8 -*-
"""核对参照答案所依赖的原文（打印第 4/5/28/35/40/43 条与问答 13/14/71/73、解读第 4 段）。"""
import os, json, re

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
NORM = os.path.join(ROOT, "normalized")

S = [json.loads(x) for x in open(os.path.join(NORM, "PV-NAT-2025-007__measure.structure.jsonl"), encoding="utf-8")]
A = {x["article_no"]: x for x in S if x["unit"] == "article"}
Q = {x["q_no"]: x for x in [json.loads(l) for l in open(os.path.join(NORM, "PV-FAQ-20250411__faq2025.qa.jsonl"), encoding="utf-8")]}
E = [json.loads(l) for l in open(os.path.join(NORM, "PV-EXPLAIN-20250123__explain.paras.jsonl"), encoding="utf-8")]

for n in (4, 5, 28, 35, 40, 43):
    print(f"=== 第{n}条（第{A[n]['chapter_no']}章）===")
    print(re.sub(r"\s+", " ", A[n]["text"]))
    print()

for n in (13, 14, 71, 73):
    x = Q[n]
    print(f"=== 问答 第{n}问（印刷页 {x['print_pages']}）===")
    print("Q:", x["question"])
    print("A:", x["answer"])
    print()

print("=== 解读 第4段 ===")
print(E[3]["text"])
