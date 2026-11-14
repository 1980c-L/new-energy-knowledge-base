# -*- coding: utf-8 -*-
"""A 阶段第 1 步：抓取首批官方来源到 raw/，记录 sha256 与抓取时间，并勘察通知页附件。"""
import os, json, hashlib, datetime, sys
import requests
from bs4 import BeautifulSoup

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
os.makedirs(RAW, exist_ok=True)

SOURCES = [
    dict(source_id="PV-NAT-2025-007", role="build",
         url="https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/c.html",
         fname="PV-NAT-2025-007__notice.html", kind="html"),
    dict(source_id="PV-EXPLAIN-20250123", role="build",
         url="https://www.nea.gov.cn/20250123/d38e5436b4d04159863ddbc10a6ede10/c.html",
         fname="PV-EXPLAIN-20250123__explain.html", kind="html"),
    dict(source_id="PV-FAQ-20250411", role="build",
         url="https://www.nea.gov.cn/20250411/dca28f9186e848dda1d690d192fa50f8/20250411dca28f9186e848dda1d690d192fa50f8_113000de00999043e8a7fe4446dd6d97d8.pdf",
         fname="PV-FAQ-20250411__faq2025.pdf", kind="pdf"),
    dict(source_id="PV-CASE-20250402", role="eval_only",
         url="https://www.nea.gov.cn/20250402/1c56ebc576504b4e8acb2ae9f5a3ee9b/c.html",
         fname="PV-CASE-20250402__case.html", kind="html"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

out = []
for s in SOURCES:
    rec = {"source_id": s["source_id"], "role": s["role"], "canonical_url": s["url"]}
    try:
        r = requests.get(s["url"], headers=HEADERS, timeout=60, allow_redirects=True)
        rec["fetched_url"] = r.url
        rec["http_status"] = r.status_code
        rec["content_type"] = r.headers.get("Content-Type", "")
        rec["retrieved_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        body = r.content
        rec["bytes"] = len(body)
        rec["raw_sha256"] = hashlib.sha256(body).hexdigest()
        path = os.path.join(RAW, s["fname"])
        with open(path, "wb") as fh:
            fh.write(body)
        rec["raw_path"] = os.path.join("raw", s["fname"])
        ok = r.status_code == 200 and len(body) > 2000
        # 闸门：HTML 来源必须含主题词，防止把 404/跳转页当正文落盘
        if ok and s["kind"] == "html":
            probe = body.decode("utf-8", errors="ignore")
            if "分布式光伏" not in probe and "gb18030" not in rec.get("content_type", ""):
                try:
                    probe = body.decode("gb18030", errors="ignore")
                except Exception:
                    pass
            if "分布式光伏" not in probe:
                ok = False
                rec["gate_error"] = "页面不含主题词「分布式光伏」，疑似错误页或跳转页"
        rec["fetch_ok"] = ok
        print(f"[{s['source_id']}] {r.status_code} {len(body)} B  {s['fname']}"
              + ("" if ok else "   << 未通过内容闸门 >>"))
    except Exception as e:
        rec["fetch_ok"] = False
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"[{s['source_id']}] FAIL {rec['error']}")
    out.append(rec)

with open(os.path.join(RAW, "_fetch-ingest.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)

# 勘察通知页里的附件链接
p = os.path.join(RAW, "PV-NAT-2025-007__notice.html")
if os.path.exists(p):
    print("\n=== 通知页附件链接 ===")
    html = open(p, "rb").read()
    for enc in ("utf-8", "gb18030"):
        try:
            txt = html.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    soup = BeautifulSoup(txt, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    print("页面标题:", title)
    hits = 0
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        if any(h.endswith(ext) for ext in (".doc", ".docx", ".pdf", ".wps", ".xls", ".xlsx", ".zip")):
            print(f"  {a.get_text(strip=True)[:40]!r} -> {a['href']}")
            hits += 1
    print("附件数:", hits)
    # 正文首段抽样
    paras = [x.get_text(strip=True) for x in soup.find_all("p") if x.get_text(strip=True)]
    print("段落数:", len(paras))
    for t in paras[:6]:
        print("   |", t[:90])
