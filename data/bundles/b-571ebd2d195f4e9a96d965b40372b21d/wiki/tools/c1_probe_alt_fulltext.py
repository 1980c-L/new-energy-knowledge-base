# -*- coding: utf-8 -*-
"""探路：抓商务部政策库转载的《管理办法》全文，看能否作为 .doc 的第二来源交叉校验。"""
import os, re, hashlib, requests
from bs4 import BeautifulSoup

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
URL = "https://policy.mofcom.gov.cn/claw/clawContent.shtml?id=101798"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
           "Accept-Language": "zh-CN,zh;q=0.9"}

r = requests.get(URL, headers=HEADERS, timeout=60)
print("status:", r.status_code, "bytes:", len(r.content), "ct:", r.headers.get("Content-Type"))
print("sha256:", hashlib.sha256(r.content).hexdigest())

txt = None
for enc in ("utf-8", "gb18030"):
    try:
        txt = r.content.decode(enc)
        print("decode:", enc)
        break
    except UnicodeDecodeError:
        continue
if txt is None:
    print("解码失败")
    raise SystemExit(1)

open(os.path.join(RAW, "ALT-MOFCOM-PV-NAT-2025-007.html"), "w", encoding="utf-8", newline="\n").write(txt)

soup = BeautifulSoup(txt, "lxml")
for bad in soup(["script", "style"]):
    bad.decompose()
print("title:", soup.title.get_text(strip=True) if soup.title else None)

body_txt = soup.get_text("\n", strip=True)
print("整页字符:", len(body_txt))

# 关键特征串
for kw in ("分布式光伏发电开发建设管理办法", "国能发新能规", "第四十三条", "有效期五年",
           "2025年1月17日", "2025年1月23日", "第一条", "第四十条"):
    print(f"  含「{kw}」: {kw in body_txt}")

# 找正文容器
cands = []
for sel in ("#zoom", ".claw-content", ".content", ".TRS_Editor", "article", ".article", "div.main"):
    for n in soup.select(sel):
        t = n.get_text(" ", strip=True)
        if len(t) > 3000:
            cands.append((sel, len(t)))
print("候选正文容器:", cands[:6])
