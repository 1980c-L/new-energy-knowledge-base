# -*- coding: utf-8 -*-
"""A 阶段第 6 步：把已抓来源整理成可定位的 normalized 层，并生成 sources.jsonl。

- 管理办法：清洗页脚噪声，按「章 / 条」切分，输出 structure.jsonl
- FAQ PDF：按问答切分，记录 pdf_page 与印刷页码
- 解读 / 案例：正文纯文本 + 段落编号
- sources.jsonl：按方案 §2 的字段约定落盘
"""
import os, re, json, hashlib, datetime
import fitz
from bs4 import BeautifulSoup

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
NORM = os.path.join(ROOT, "normalized")
os.makedirs(NORM, exist_ok=True)

ingest = {}
for fn in ("_fetch-ingest.json", "_attach-ingest.json"):
    p = os.path.join(RAW, fn)
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, list):
            for x in d:
                ingest[x["source_id"]] = x
        else:
            ingest[d["source_id"]] = d

# ---------------------------------------------------------------- 管理办法
print("== 管理办法：清洗与切分 ==")
mp = os.path.join(NORM, "PV-NAT-2025-007__measure.txt")
text = open(mp, encoding="utf-8").read()
lines = text.split("\n")

FOOT = re.compile(r"^[-\s—–]*\d{1,3}[-\s—–]*$")
kept, dropped = [], []
for ln in lines:
    if FOOT.match(ln.strip()) and len(ln.strip()) <= 8:
        dropped.append(ln.strip())
        continue
    kept.append(ln)
clean = "\n".join(kept).strip() + "\n"
clean_path = os.path.join(NORM, "PV-NAT-2025-007__measure.clean.txt")
open(clean_path, "w", encoding="utf-8", newline="\n").write(clean)
print(f"  丢弃页脚行 {len(dropped)}: {dropped}")

CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
def cn2int(s):
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return CN.get(s, 0)

struct, cur_ch, cur_art = [], None, None
for ln in clean.split("\n"):
    s = ln.strip().strip("\u3000")
    if not s:
        continue
    mch = re.match(r"^第([一二三四五六七八九十]+)章\s*(.*)$", s)
    if mch:
        cur_ch = {"chapter_no": cn2int(mch.group(1)), "chapter_title": mch.group(2).replace(" ", "")}
        struct.append({"unit": "chapter", **cur_ch, "text": ""})
        cur_art = None
        continue
    mart = re.match(r"^第([一二三四五六七八九十百]+)条\s*(.*)$", s)
    if mart:
        cur_art = {
            "unit": "article",
            "article_no": cn2int(mart.group(1)),
            "chapter_no": cur_ch["chapter_no"] if cur_ch else None,
            "chapter_title": cur_ch["chapter_title"] if cur_ch else None,
            "text": mart.group(2).strip(),
            "locator": f"第{cn2int(mart.group(1))}条",
        }
        struct.append(cur_art)
        continue
    if cur_art is not None:
        cur_art["text"] += "\n" + s
    elif struct:
        struct[-1]["text"] += "\n" + s

arts = [x for x in struct if x["unit"] == "article"]
chs = [x for x in struct if x["unit"] == "chapter"]
sp = os.path.join(NORM, "PV-NAT-2025-007__measure.structure.jsonl")
with open(sp, "w", encoding="utf-8", newline="\n") as fh:
    for x in struct:
        fh.write(json.dumps(x, ensure_ascii=False) + "\n")
print(f"  章 {len(chs)} / 条 {len(arts)}；条号 {[a['article_no'] for a in arts][:5]}…{[a['article_no'] for a in arts][-3:]}")
assert [a["article_no"] for a in arts] == list(range(1, 44)), "条号不连续，需人工核查"
print("  条号 1–43 连续 ✓")

# ---------------------------------------------------------------- FAQ PDF
print("\n== FAQ PDF：按问答切分 ==")
doc = fitz.open(os.path.join(RAW, "PV-FAQ-20250411__faq2025.pdf"))
pages = [p.get_text("text") for p in doc]
doc.close()

qa = []
cur = None
expected = 1
print_page = None
for idx in range(0, len(pages)):
    lines = [x for x in pages[idx].split("\n")]
    # 页首印刷页码
    k = 0
    while k < len(lines) and not lines[k].strip():
        k += 1
    if k < len(lines):
        head = lines[k].strip()
        if re.fullmatch(r"\d{1,2}", head) and head.isdigit() and 1 <= int(head) <= 40:
            print_page = int(head)
            lines = lines[k + 1:]
    if print_page is None:
        continue   # 封面 / 目录页
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^(\d{1,2})[.．]\s*(.*)$", s)
        if m and int(m.group(1)) == expected:
            if cur:
                qa.append(cur)
            cur = {"q_no": expected, "question": m.group(2).strip(), "answer": "",
                   "pdf_pages": [idx + 1], "print_pages": [print_page], "state": "question"}
            expected += 1
            continue
        if cur is None:
            continue
        if idx + 1 not in cur["pdf_pages"]:
            cur["pdf_pages"].append(idx + 1)
        if print_page not in cur["print_pages"]:
            cur["print_pages"].append(print_page)
        if cur["state"] == "question":
            if s.startswith("答：") or s.startswith("答:"):
                cur["answer"] = s[2:].strip()
                cur["state"] = "answer"
            elif "答：" in s:
                qi = s.index("答：")
                cur["question"] += "\n" + s[:qi].strip()
                cur["answer"] = s[qi + 2:].strip()
                cur["state"] = "answer"
            else:
                cur["question"] += "\n" + s
        else:
            cur["answer"] += "\n" + s
if cur:
    qa.append(cur)

# 收拾：无答案的问标 unknown；剥掉答案尾部误并入的篇标题（如「第三篇备案管理」）
PART_TAIL = re.compile(r"\s*第[一二三四五六七]篇\s*[\u4e00-\u9fa5]{2,8}\s*$")
stripped_parts = []
for x in qa:
    x["question"] = x["question"].replace("\n", " ").strip()
    x["answer"] = x["answer"].replace("\n", " ").strip()
    new_ans = PART_TAIL.sub("", x["answer"]).strip()
    if new_ans != x["answer"]:
        stripped_parts.append(x["q_no"])
        x["answer"] = new_ans
    if not x["answer"]:
        x["state"] = "missing_answer"

qp = os.path.join(NORM, "PV-FAQ-20250411__faq2025.qa.jsonl")
with open(qp, "w", encoding="utf-8", newline="\n") as fh:
    for x in qa:
        fh.write(json.dumps(x, ensure_ascii=False) + "\n")
miss = [x["q_no"] for x in qa if x["state"] == "missing_answer"]
print(f"  切出 {len(qa)} 问，编号 {qa[0]['q_no']}–{qa[-1]['q_no']}，缺答案 {miss}")
print(f"  剥离尾部篇标题 {len(stripped_parts)} 处: {stripped_parts}")
print(f"  印刷页码范围 {min(min(x['print_pages']) for x in qa)}–{max(max(x['print_pages']) for x in qa)}")
print(f"  平均答案长度 {sum(len(x['answer']) for x in qa)/len(qa):.0f} 字")
for x in qa[:2] + qa[-1:]:
    print(f"   Q{x['q_no']} p{x['print_pages']}: {x['question'][:60]}")
    print(f"      A: {x['answer'][:80]}")

# ---------------------------------------------------------------- 解读 / 案例
def html_paras(name, sel=None):
    html = open(os.path.join(RAW, name), "rb").read()
    for enc in ("utf-8", "gb18030"):
        try:
            txt = html.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    soup = BeautifulSoup(txt, "lxml")
    for bad in soup(["script", "style"]):
        bad.decompose()
    if sel:
        node = soup.select_one(sel) or soup.body
    else:
        node = (soup.select_one(".TRS_Editor") or soup.select_one("#zoom")
                or soup.select_one(".article") or soup.body)
    return [p.get_text(" ", strip=True) for p in node.find_all(["p", "h1", "h2", "h3"])
            if p.get_text(strip=True)]

for name, out, sel in (
        ("PV-NAT-2025-007__notice.html", "PV-NAT-2025-007__notice.paras.jsonl", None),
        ("PV-EXPLAIN-20250123__explain.html", "PV-EXPLAIN-20250123__explain.paras.jsonl", None),
        ("PV-CASE-20250402__case.html", "PV-CASE-20250402__case.paras.jsonl", None),
        ("ALT-MOFCOM-PV-NAT-2025-007.html", "ALT-MOFCOM-PV-NAT-2025-007.paras.jsonl", ".content")):
    paras = html_paras(name, sel)
    with open(os.path.join(NORM, out), "w", encoding="utf-8", newline="\n") as fh:
        for i, p in enumerate(paras, 1):
            fh.write(json.dumps({"para_no": i, "text": p}, ensure_ascii=False) + "\n")
    print(f"\n== {name}: {len(paras)} 段 -> {out}")

# ---------------------------------------------------------------- sources.jsonl
now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
SRC = [
    dict(source_id="PV-NAT-2025-007", role="build", kind="notice",
         title="国家能源局关于印发《分布式光伏发电开发建设管理办法》的通知",
         issuer="国家能源局", document_number="国能发新能规〔2025〕7号",
         published_at="2025-01-17",
         note="通知页本身只有文头与落款，办法全文在附件 .doc 中，不能把通知页当正文",
         text_path=os.path.join("normalized", "PV-NAT-2025-007__notice.paras.jsonl")),
    dict(source_id="PV-NAT-2025-007-ATTACH", role="build", kind="measure_fulltext",
         title="分布式光伏发电开发建设管理办法（正文，7 章 43 条）",
         issuer="国家能源局", document_number="国能发新能规〔2025〕7号",
         published_at="2025-01-17",
         note="附件实为 Word 97-2003 (.doc)；正文本身不含公布日期，日期须跨来源引用",
         text_path=os.path.join("normalized", "PV-NAT-2025-007__measure.structure.jsonl")),
    dict(source_id="PV-EXPLAIN-20250123", role="build", kind="official_explain",
         title="《分布式光伏发电开发建设管理办法》政策解读",
         issuer="国家能源局", document_number=None, published_at="2025-01-23",
         note="官方解读，不是规范正文；可用于背景与修订意图，不可当条款引用",
         text_path=os.path.join("normalized", "PV-EXPLAIN-20250123__explain.paras.jsonl")),
    dict(source_id="PV-FAQ-20250411", role="build", kind="official_faq",
         title="《分布式光伏发电开发建设管理办法》问答（2025 年版）",
         issuer="国家能源局", document_number=None, published_at="2025-04-11",
         note="40 页 PDF，73 问；正文从 PDF 第 9 页起，印刷页码 1–32",
         text_path=os.path.join("normalized", "PV-FAQ-20250411__faq2025.qa.jsonl")),
    dict(source_id="PV-CASE-20250402", role="eval_only", kind="consult_case",
         title="关于取得备案证及接入方案的项目，但因建设周期原因未能于5月1日前并网投产后续如何处置的咨询",
         issuer="国家能源局 新能源司", document_number=None, published_at="2025-04-02",
         note="公开咨询答复（咨询 2025-02-18 / 答复 2025-03-18），仅作评测，不并入建设语料",
         text_path=os.path.join("normalized", "PV-CASE-20250402__case.paras.jsonl")),
    dict(source_id="ALT-MOFCOM-PV-NAT-2025-007", role="corroborate", kind="reprint_fulltext",
         title="《分布式光伏发电开发建设管理办法》商务部「全球法规网」转载全文",
         issuer="商务部全球法规网（转载国家能源局文件）", document_number="国能发新能规〔2025〕7号",
         published_at="2025-01-17",
         note="非首选入口，仅用于交叉校验 .doc 转换完整性（43/43 条首句一致）；引用仍以能源局原文为准",
         text_path=os.path.join("normalized", "ALT-MOFCOM-PV-NAT-2025-007.paras.jsonl")),
]
URLS = {
    "PV-NAT-2025-007": "https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/c.html",
    "PV-NAT-2025-007-ATTACH": ("https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/"
                               "20250123112c5b199c5f45dd8e7ac93c9f5e4eaf_010446cd75b7d649969599bba99df7d0dd.doc"),
    "PV-EXPLAIN-20250123": "https://www.nea.gov.cn/20250123/d38e5436b4d04159863ddbc10a6ede10/c.html",
    "PV-FAQ-20250411": ("https://www.nea.gov.cn/20250411/dca28f9186e848dda1d690d192fa50f8/"
                        "20250411dca28f9186e848dda1d690d192fa50f8_113000de00999043e8a7fe4446dd6d97d8.pdf"),
    "PV-CASE-20250402": "https://www.nea.gov.cn/20250402/1c56ebc576504b4e8acb2ae9f5a3ee9b/c.html",
    "ALT-MOFCOM-PV-NAT-2025-007": "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=101798",
}
RAW_NAME_FALLBACK = {"ALT-MOFCOM-PV-NAT-2025-007": "ALT-MOFCOM-PV-NAT-2025-007.html"}
rows = []
for s in SRC:
    ig = ingest.get(s["source_id"], {})
    # 未走 a1 抓取流程的来源（如第二校验源），从 raw 目录兜底计算哈希
    raw_path = ig.get("raw_path")
    raw_sha = ig.get("raw_sha256")
    raw_bytes = ig.get("bytes")
    if not raw_path and s["source_id"] in RAW_NAME_FALLBACK:
        raw_path = os.path.join("raw", RAW_NAME_FALLBACK[s["source_id"]])
        fp = os.path.join(ROOT, raw_path)
        if os.path.exists(fp):
            with open(fp, "rb") as fh:
                data = fh.read()
            raw_sha = hashlib.sha256(data).hexdigest()
            raw_bytes = len(data)
            ig = dict(ig, retrieved_at=ig.get("retrieved_at") or now)
    row = {
        "source_id": s["source_id"],
        "title": s["title"],
        "canonical_url": URLS[s["source_id"]],
        "fetched_url": ig.get("fetched_url", URLS[s["source_id"]]),
        "issuer": s["issuer"],
        "document_number": s["document_number"],
        "published_at": s["published_at"],
        "retrieved_at": ig.get("retrieved_at"),
        "role": s["role"],
        "kind": s["kind"],
        "raw_path": raw_path,
        "raw_sha256": raw_sha,
        "raw_bytes": raw_bytes,
        "text_path": s["text_path"],
        "parse_status": "ok",
        "note": s["note"],
        "batch": "pv-national-2025-v1",
    }
    rows.append(row)
with open(os.path.join(ROOT, "sources.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"\n== sources.jsonl: {len(rows)} 条")
for r in rows:
    print(f"   {r['source_id']:<24} {r['role']:<9} {r['raw_sha256'][:12] if r['raw_sha256'] else 'NO-HASH'}  {r['text_path']}")
