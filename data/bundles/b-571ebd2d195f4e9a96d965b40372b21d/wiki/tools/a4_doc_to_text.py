# -*- coding: utf-8 -*-
"""A 阶段第 4 步：用本机已装的 Word（Office16 COM）把旧版 .doc 转为可定位文本。

只读打开、不修改原始 .doc；输出 normalized/ 下的 UTF-8 文本与基本体检数据。
"""
import os, sys, datetime, json

ROOT = r"D:\codebuddy产出\项目\新能源知识Wiki"
RAW = os.path.join(ROOT, "raw")
NORM = os.path.join(ROOT, "normalized")
os.makedirs(NORM, exist_ok=True)

SRC = os.path.join(RAW, "PV-NAT-2025-007__measure.doc")
TMP = os.path.join(NORM, "_measure.word.txt")     # Word 导出的 UTF-16
OUT = os.path.join(NORM, "PV-NAT-2025-007__measure.txt")

import win32com.client as win32
from win32com.client import constants as C

word = None
doc = None
try:
    word = win32.gencache.EnsureDispatch("Word.Application")
except Exception:
    word = win32.Dispatch("Word.Application")

word.Visible = False
word.DisplayAlerts = 0
try:
    doc = word.Documents.Open(
        SRC,
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
        PasswordDocument="",
    )
    # wdFormatUnicodeText = 7
    doc.SaveAs2(TMP, FileFormat=7)
    pages = doc.ComputeStatistics(2)   # wdStatisticPages
    words = doc.ComputeStatistics(0)   # wdStatisticWords
    chars = doc.ComputeStatistics(3)   # wdStatisticCharacters
    print(f"[com] 打开成功  页数={pages} 字数={words} 字符数={chars}")
finally:
    try:
        if doc is not None:
            doc.Close(SaveChanges=0)
    except Exception as e:
        print("[com] close warn:", e)
    try:
        if word is not None:
            word.Quit(SaveChanges=0)
    except Exception as e:
        print("[com] quit warn:", e)

# 转 UTF-8 并体检
raw = open(TMP, "rb").read()
for enc in ("utf-16", "utf-8", "gb18030"):
    try:
        text = raw.decode(enc)
        print(f"[text] 解码={enc} 长度={len(text)}")
        break
    except UnicodeDecodeError:
        continue
text = text.replace("\r\n", "\n").replace("\r", "\n")

with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)

lines = [x.strip() for x in text.split("\n") if x.strip()]
print("[text] 非空行数:", len(lines))
print("[text] 首 12 行:")
for x in lines[:12]:
    print("   |", x[:100])
print("[text] 末 6 行:")
for x in lines[-6:]:
    print("   |", x[:100])

# 结构线索：章条
import re
chapters = [x for x in lines if re.match(r"^第[一二三四五六七八九十]+章", x)]
articles = re.findall(r"第[一二三四五六七八九十百]+条", text)
print(f"[struct] 章 {len(chapters)} 个: {chapters}")
print(f"[struct] '第X条' 出现 {len(articles)} 次，去重 {len(set(articles))} 个")

meta = {
    "source_id": "PV-NAT-2025-007-ATTACH",
    "text_path": os.path.join("normalized", os.path.basename(OUT)),
    "text_bytes": os.path.getsize(OUT),
    "chars": len(text),
    "nonempty_lines": len(lines),
    "chapters": chapters,
    "article_tokens": len(articles),
    "unique_article_tokens": len(set(articles)),
    "converted_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "tool": "Microsoft Word COM (SaveAs2 wdFormatUnicodeText)",
}
json.dump(meta, open(os.path.join(NORM, "_measure-meta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n" + json.dumps(meta, ensure_ascii=False, indent=1))
