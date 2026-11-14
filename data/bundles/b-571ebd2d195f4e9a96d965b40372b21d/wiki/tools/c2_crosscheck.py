# -*- coding: utf-8 -*-
"""交叉校验：
1) Word 转出的 43 条 vs 商务部转载全文（逐条首句比对）
2) PDF 目录条目 vs 正文切出的 73 问（数量与编号集合）
"""
import os, re, json
from bs4 import BeautifulSoup
import fitz

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
NORM = os.path.join(ROOT, "normalized")
RAW = os.path.join(ROOT, "raw")


def norm(s):
    s = s.replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    return s


# ---------------- 1) 与 mofcom 全文比对 ----------------
alt = open(os.path.join(RAW, "ALT-MOFCOM-PV-NAT-2025-007.html"), encoding="utf-8").read()
soup = BeautifulSoup(alt, "lxml")
for bad in soup(["script", "style"]):
    bad.decompose()
node = soup.select_one(".content") or soup.body
alt_text = norm(node.get_text("\n", strip=True))
print(f"商务部转载正文（规范化后）: {len(alt_text)} 字")

S = [json.loads(x) for x in open(os.path.join(NORM, "PV-NAT-2025-007__measure.structure.jsonl"), encoding="utf-8")]
arts = [x for x in S if x["unit"] == "article"]
CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
def cn2int(s):
    if s == "十": return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return CN.get(s, 0)

hit, miss = 0, []
for a in arts:
    body = norm(a["text"])
    probe = body[:40]
    if probe and probe in alt_text:
        hit += 1
    else:
        # 退化到前 20 字再试
        if body[:20] in alt_text:
            hit += 1
            print(f"  [部分] 第{a['article_no']}条 仅前 20 字命中")
        else:
            miss.append(a["article_no"])
print(f"逐条首句命中: {hit}/{len(arts)}  未命中: {miss}")

alt_arts = sorted({cn2int(m) for m in re.findall(r"第([一二三四五六七八九十百]+)条", alt_text)})
print(f"商务部版出现的条号: {len(alt_arts)} 个，范围 {min(alt_arts)}–{max(alt_arts)}")
print(f"  与 Word 版差异（仅商务部有 / 仅 Word 有）: "
      f"{sorted(set(alt_arts)-set(a['article_no'] for a in arts))} / "
      f"{sorted(set(a['article_no'] for a in arts)-set(alt_arts))}")

# 关键句逐个核对（防 Word 转换丢字）
KEY = [
    "分布式光伏发电分为自然人户用、非自然人户用、一般工商业和大型工商业四种类型",
    "总装机容量原则上不超过20兆瓦或者与公共电网连接点电压等级为110千伏（66千伏）、总装机容量原则上不超过50兆瓦",
    "大型工商业分布式光伏原则上选择全部自发自用模式；在电力现货市场连续运行地区，大型工商业分布式光伏可采用自发自用余电上网模式参与现货市场",
    "接入系统电压等级为35千伏及以下的，电网企业应当于10个工作日内出具答复意见",
    "仍按原有政策执行",
    "本办法自发布之日起施行，有效期五年",
]
print("\n关键句核对（Word 版 / 商务部版 / 原 .doc 均需一致）:")
for k in KEY:
    in_word = norm(k) in norm(open(os.path.join(NORM, "PV-NAT-2025-007__measure.clean.txt"), encoding="utf-8").read())
    in_alt = norm(k) in alt_text
    print(f"  [{'OK' if in_word and in_alt else 'DIFF'}] word={in_word} alt={in_alt}  {k[:38]}…")

# ---------------- 2) PDF 目录 vs 正文问数 ----------------
doc = fitz.open(os.path.join(RAW, "PV-FAQ-20250411__faq2025.pdf"))
pages = [p.get_text("text") for p in doc]
doc.close()
toc_text = "\n".join(pages[1:8])          # p2–p8 = 目录
toc_nums = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(\d{1,2})[.．]", toc_text)]
qa = [json.loads(l) for l in open(os.path.join(NORM, "PV-FAQ-20250411__faq2025.qa.jsonl"), encoding="utf-8")]
body_nums = [x["q_no"] for x in qa]
print(f"\n目录页提取编号: {len(toc_nums)} 个（去重 {len(set(toc_nums))}）")
print(f"正文切出问数: {len(body_nums)} 个")
print(f"  目录缺号 / 正文缺号: {sorted(set(range(1,74))-set(toc_nums))} / {sorted(set(range(1,74))-set(body_nums))}")
print(f"  目录有而正文无: {sorted(set(toc_nums)-set(body_nums))}")
print(f"  正文有而目录无: {sorted(set(body_nums)-set(toc_nums))}")

# 每题答案长度异常检查（可能是切分错误信号）
short = [(x["q_no"], len(x["answer"])) for x in qa if len(x["answer"]) < 20]
long_ = [(x["q_no"], len(x["answer"])) for x in qa if len(x["answer"]) > 600]
print(f"  答案过短(<20字): {short}")
print(f"  答案过长(>600字): {long_}")
print(f"  跨页问数: {sum(1 for x in qa if len(x['print_pages'])>1)}")
