# -*- coding: utf-8 -*-
"""再校验三件事：
1) 目录标注的印刷页码 vs 正文实际印刷页码
2) 两处超长答案（Q24 / Q67）的切分边界是否吞并了下一问
3) 每个答案里是否混入了下一个问号编号
"""
import os, re, json

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
NORM = os.path.join(ROOT, "normalized")
qa = [json.loads(l) for l in open(os.path.join(NORM, "PV-FAQ-20250411__faq2025.qa.jsonl"), encoding="utf-8")]
Q = {x["q_no"]: x for x in qa}

# 1) 目录页码：从 p2–p8 目录文本里取「N. xxx......页码」
import fitz
doc = fitz.open(os.path.join(ROOT, "raw", "PV-FAQ-20250411__faq2025.pdf"))
pages = [p.get_text("text") for p in doc]
doc.close()
toc = "\n".join(pages[1:8])
toc = toc.replace("\n", " ")
pairs = re.findall(r"(\d{1,2})[.．].*?\.{3,}\s*(\d{1,2})", toc)
toc_map = {}
for n, pg in pairs:
    toc_map.setdefault(int(n), int(pg))
print(f"目录解析出 {len(toc_map)} 条「问号 → 印刷页」映射")
mismatch = []
for n, pg in sorted(toc_map.items()):
    if n in Q:
        actual = Q[n]["print_pages"][0]
        if actual != pg:
            mismatch.append((n, pg, actual))
print(f"目录页码与实际不一致: {len(mismatch)} 条")
for m in mismatch[:10]:
    print(f"   问{m[0]}: 目录写 p{m[1]}，正文实际 p{m[2]}")

# 2) 超长答案边界
for n in (23, 24, 25, 66, 67, 68):
    x = Q[n]
    print(f"\n--- 问{n}（印刷页 {x['print_pages']}）---")
    print("Q:", x["question"][:100])
    print("A 尾 60 字:", x["answer"][-60:])

# 3) 答案里是否混入下一个问号
bad = []
for x in qa[:-1]:
    nxt = x["q_no"] + 1
    pat = rf"(?<!\d){nxt}\s*[.．]"
    if re.search(pat, x["answer"]):
        bad.append(x["q_no"])
print(f"\n答案中疑似混入下一问编号: {bad}")
print(f"总问数: {len(qa)}；跨页问: {sum(1 for x in qa if len(x['print_pages'])>1)}；"
      f"单页问: {sum(1 for x in qa if len(x['print_pages'])==1)}")
print(f"印刷页覆盖: {min(min(x['print_pages']) for x in qa)}–{max(max(x['print_pages']) for x in qa)}")
