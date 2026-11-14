# -*- coding: utf-8 -*-
"""勘察通知页里 .doc 附件的原始 href、base 与可能的完整地址。"""
import os, re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
p = os.path.join(ROOT, "raw", "PV-NAT-2025-007__notice.html")
html = open(p, "rb").read()
for enc in ("utf-8", "gb18030"):
    try:
        txt = html.decode(enc)
        print("解码:", enc)
        break
    except UnicodeDecodeError:
        continue

soup = BeautifulSoup(txt, "lxml")
base = soup.find("base")
print("base 标签:", base.get("href") if base else None)
print("页面 URL 前缀参考: https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/c.html")

print("\n全部含 .doc 的原始片段:")
for m in re.finditer(r'''href\s*=\s*["']([^"']*\.doc[x]?[^"']*)["']''', txt, re.I):
    print("  href repr:", repr(m.group(1)))
    print("  urljoin :", urljoin("https://www.nea.gov.cn/20250123/112c5b199c5f45dd8e7ac93c9f5e4eaf/c.html", m.group(1)))

print("\n页面附近文本（找附件说明）:")
for kw in ("附件", "下载"):
    for m in re.finditer(kw, txt):
        s = max(0, m.start() - 120)
        seg = re.sub(r"<[^>]+>", "", txt[s:m.start() + 160])
        seg = re.sub(r"\s+", " ", seg).strip()
        print(f"  [{kw}] ...{seg}...")
        break
