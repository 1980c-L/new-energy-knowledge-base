# -*- coding: utf-8 -*-
"""A 阶段第 2 步：抓通知页附件（旧版 .doc），判定真实格式并勘察内部流；同时勘察 PDF 结构。"""
import os, hashlib, datetime, json
import requests
from bs4 import BeautifulSoup

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

DOC_URL = ("https://www.nea.gov.cn/20250123/"
           "20250123112c5b199c5f45dd8e7ac93c9f5e4eaf_010446cd75b7d649969599bba99df7d0dd.doc")
DOC_NAME = "PV-NAT-2025-007__measure.doc"

r = requests.get(DOC_URL, headers=HEADERS, timeout=90)
body = r.content
p = os.path.join(RAW, DOC_NAME)
open(p, "wb").write(body)
print(f"[doc] {r.status_code} {len(body)} B  ct={r.headers.get('Content-Type')}")
print(f"[doc] sha256={hashlib.sha256(body).hexdigest()}")
print(f"[doc] magic={body[:8].hex()}")

# 真实格式判定
if body[:4] == b"\xd0\xcf\x11\xe0":
    print("[doc] 格式 = OLE2 (Word 97-2003 真身)")
    import olefile
    ole = olefile.OleFileIO(p)
    print("[doc] 流列表:")
    for s in ole.listdir():
        try:
            size = ole.get_size("/".join(s))
        except Exception:
            size = -1
        print(f"      {'/'.join(s):<40} {size} B")
    ole.close()
elif body[:2] == b"PK":
    print("[doc] 格式 = ZIP (实为 docx 或 OpenXML 伪装)")
elif body[:5] == b"{\\rtf":
    print("[doc] 格式 = RTF")
else:
    print("[doc] 未知格式，前 64 字节:", body[:64])

# 若附件名与页面给的相对路径不一致，登记
print("\n=== 记录附件 ===")
rec = {
    "source_id": "PV-NAT-2025-007-ATTACH",
    "parent_source_id": "PV-NAT-2025-007",
    "canonical_url": DOC_URL,
    "fetched_url": r.url,
    "http_status": r.status_code,
    "content_type": r.headers.get("Content-Type", ""),
    "retrieved_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "bytes": len(body),
    "raw_sha256": hashlib.sha256(body).hexdigest(),
    "raw_path": os.path.join("raw", DOC_NAME),
}
print(json.dumps(rec, ensure_ascii=False, indent=1))
json.dump(rec, open(os.path.join(RAW, "_attach-ingest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ---- PDF 勘察 ----
print("\n=== FAQ PDF 结构勘察 ===")
import fitz
pdf_path = os.path.join(RAW, "PV-FAQ-20250411__faq2025.pdf")
doc = fitz.open(pdf_path)
print("页数:", doc.page_count, " 元数据:", {k: v for k, v in doc.metadata.items() if v})
tot = 0
for i, page in enumerate(doc):
    t = page.get_text("text").strip()
    tot += len(t)
    if i < 3 or i == doc.page_count - 1:
        print(f"-- p{i+1} 字符 {len(t)} --")
        print("   " + t[:220].replace("\n", " / "))
print("全文提取字符数:", tot)
doc.close()
