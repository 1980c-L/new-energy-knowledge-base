# -*- coding: utf-8 -*-
"""A 阶段第 5 步：三份来源的正文体检 —— 完整性、编号连续性、日期线索、解析遗漏。"""
import os, re, json, unicodedata
from bs4 import BeautifulSoup
import fitz

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
NORM = os.path.join(ROOT, "normalized")
os.makedirs(NORM, exist_ok=True)
report = {}


def load_html(name):
    html = open(os.path.join(RAW, name), "rb").read()
    for enc in ("utf-8", "gb18030"):
        try:
            txt = html.decode(enc)
            return txt, enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError("decode failed: " + name)


def html_main_text(txt):
    soup = BeautifulSoup(txt, "lxml")
    for bad in soup(["script", "style"]):
        bad.decompose()
    # 抓正文容器：优先 .TRS_Editor / #zoom / 常见正文 div
    node = (soup.select_one(".TRS_Editor") or soup.select_one("#zoom")
            or soup.select_one(".article") or soup.body)
    paras = [p.get_text(" ", strip=True) for p in node.find_all(["p", "h1", "h2", "h3"])]
    paras = [p for p in paras if p]
    return soup.title.get_text(strip=True) if soup.title else "", paras


print("=" * 70)
print("1) 管理办法正文（.doc → Word 转文本）")
print("=" * 70)
mp = os.path.join(NORM, "PV-NAT-2025-007__measure.txt")
mt = open(mp, encoding="utf-8").read()
bad_chars = [c for c in set(mt) if unicodedata.category(c) in ("Cc", "Cs", "Co") and c not in "\n\t"]
print("字符数:", len(mt), " 控制字符/替换符:", [hex(ord(c)) for c in bad_chars][:10])
print("含 U+FFFD:", "\ufffd" in mt)

lines = [x.strip() for x in mt.split("\n")]
arts = [x for x in lines if re.match(r"^第[一二三四五六七八九十百]+条", x)]
nums = []
cn = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
def cn2int(s):
    if s == "十": return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (cn.get(a, 1) if a else 1) * 10 + (cn.get(b, 0) if b else 0)
    return cn.get(s, 0)
for a in arts:
    m = re.match(r"^第([一二三四五六七八九十百]+)条", a)
    nums.append(cn2int(m.group(1)))
print(f"条数 {len(arts)}，编号范围 {min(nums)}–{max(nums)}，缺失: {sorted(set(range(1, max(nums)+1)) - set(nums))}")
print("重复编号:", sorted({n for n in nums if nums.count(n) > 1}))
print("页脚噪声行:", [x for x in lines if re.fullmatch(r"[-\s]*\d+[-\s]*", x)][:10])
for kw in ("2025年1月17日", "2025年1月23日", "2025年5月1日", "有效期五年"):
    print(f"  含「{kw}」: {kw in mt}")
report["measure"] = {
    "chars": len(mt), "articles": len(arts), "article_range": [min(nums), max(nums)],
    "missing_numbers": sorted(set(range(1, max(nums)+1)) - set(nums)),
    "has_replacement_char": "\ufffd" in mt,
}

print()
print("=" * 70)
print("2) 政策解读（HTML）")
print("=" * 70)
txt, enc = load_html("PV-EXPLAIN-20250123__explain.html")
title, paras = html_main_text(txt)
print("编码:", enc, " 标题:", title)
body = "\n".join(paras)
print("段落:", len(paras), " 字符:", len(body))
open(os.path.join(NORM, "PV-EXPLAIN-20250123__explain.txt"), "w", encoding="utf-8", newline="\n").write(body)
for p in paras[:5]:
    print("   |", p[:90])
report["explain"] = {"chars": len(body), "paras": len(paras), "title": title}

print()
print("=" * 70)
print("3) 咨询案例（HTML，eval_only）")
print("=" * 70)
txt, enc = load_html("PV-CASE-20250402__case.html")
title, paras = html_main_text(txt)
body = "\n".join(paras)
print("编码:", enc, " 标题:", title)
print("段落:", len(paras), " 字符:", len(body))
open(os.path.join(NORM, "PV-CASE-20250402__case.txt"), "w", encoding="utf-8", newline="\n").write(body)
for p in paras[:8]:
    print("   |", p[:100])
report["case"] = {"chars": len(body), "paras": len(paras), "title": title}

print()
print("=" * 70)
print("4) FAQ PDF（40 页）结构")
print("=" * 70)
doc = fitz.open(os.path.join(RAW, "PV-FAQ-20250411__faq2025.pdf"))
pages = []
for i, page in enumerate(doc):
    pages.append(page.get_text("text"))
doc.close()
full = "\n".join(pages)
qnums = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(\d{1,2})[.．、]", full)]
print("页数:", len(pages), " 字符:", len(full))
print("行首问答编号出现:", len(qnums), " 去重:", len(set(qnums)), " 范围:", min(qnums), "-", max(qnums))
print("缺失编号:", sorted(set(range(1, max(qnums) + 1)) - set(qnums)))
print("含 '答：' 次数:", full.count("答："))
# 印刷页码：页脚数字行
for i, t in enumerate(pages[:6]):
    tail = [x.strip() for x in t.strip().split("\n")[-3:] if x.strip()]
    print(f"  p{i+1} 尾部: {tail}")
open(os.path.join(NORM, "PV-FAQ-20250411__faq2025.rawtext.txt"), "w", encoding="utf-8", newline="\n").write(full)
report["faq"] = {
    "pages": len(pages), "chars": len(full),
    "question_tokens": len(qnums), "unique_questions": len(set(qnums)),
    "missing": sorted(set(range(1, max(qnums) + 1)) - set(qnums)),
    "answers_marker": full.count("答："),
}

json.dump(report, open(os.path.join(NORM, "_inspect-report.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n报告 -> normalized/_inspect-report.json")
