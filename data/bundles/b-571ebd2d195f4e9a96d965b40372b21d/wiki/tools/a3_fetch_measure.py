# -*- coding: utf-8 -*-
"""A 阶段第 3 步：按页面真实相对路径抓《管理办法》正文附件，判定格式并列出 OLE 流。"""
import os, hashlib, datetime, json
import requests

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
           "Referer": "https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/c.html"}

URL = ("https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/"
       "20250123112c5b199c5f45dd8e7ac93c9f5e4eaf_010446cd75b7d649969599bba99df7d0dd.doc")
NAME = "PV-NAT-2025-007__measure.doc"

r = requests.get(URL, headers=HEADERS, timeout=120)
body = r.content

# 闸门 1：HTTP 状态必须 200
if r.status_code != 200:
    raise SystemExit(f"[doc] HTTP {r.status_code}，未落盘。响应前 200 字节: {body[:200]!r}")

# 闸门 2：必须是 Word 97-2003 (OLE2) 或 docx (ZIP)，否则视为错误页，不落盘
magic = body[:8].hex()
if body[:4] == b"\xd0\xcf\x11\xe0":
    fmt = "ole2"
elif body[:2] == b"PK":
    fmt = "zip"
elif body[:5] == b"{\\rtf":
    fmt = "rtf"
else:
    raise SystemExit(
        f"[doc] 内容不是 Word 文件（magic={magic}, ct={r.headers.get('Content-Type')}），"
        f"疑似错误页或跳转页，未落盘。前 200 字节: {body[:200]!r}"
    )

open(os.path.join(RAW, NAME), "wb").write(body)
print(f"[doc] {r.status_code} {len(body)} B ct={r.headers.get('Content-Type')}")
print(f"[doc] sha256={hashlib.sha256(body).hexdigest()}")
print(f"[doc] magic={magic}  fmt={fmt}")

if fmt == "ole2":
    import olefile
    ole = olefile.OleFileIO(os.path.join(RAW, NAME))
    print("[doc] OLE2 流:")
    for s in ole.listdir():
        try:
            sz = ole.get_size("/".join(s))
        except Exception:
            sz = -1
        print(f"      {'/'.join(s):<45} {sz} B")
    ole.close()

rec = {
    "source_id": "PV-NAT-2025-007-ATTACH",
    "parent_source_id": "PV-NAT-2025-007",
    "canonical_url": URL,
    "http_status": r.status_code,
    "content_type": r.headers.get("Content-Type", ""),
    "retrieved_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "bytes": len(body),
    "raw_sha256": hashlib.sha256(body).hexdigest(),
    "raw_path": os.path.join("raw", NAME),
    "detected_format": fmt,
}
json.dump(rec, open(os.path.join(RAW, "_attach-ingest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(json.dumps(rec, ensure_ascii=False, indent=1))
