# -*- coding: utf-8 -*-
"""t2_manifest.py —— 生成交付清单（字节数 + sha256），用于证明「本轮哪些文件变了、哪些没变」。

默认纳入：claims.jsonl、sources.jsonl、tools/wiki_tool.py、tools/t1_check_negative.py、
          wiki/ 下全部 .md、04-主张层字段契约.md、05-页面层规则.md
用法：
    python tools\\t2_manifest.py --out handoff\\C2\\manifest.json
    python tools\\t2_manifest.py --out handoff\\C2\\manifest.json --extra 当前状态.md
输出：写 JSON 到 --out，并在 stdout 打印同样内容。
仅用标准库。
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = [
    "claims.jsonl",
    "sources.jsonl",
    "tools/wiki_tool.py",
    "tools/t1_check_negative.py",
    "tools/t2_manifest.py",
    "04-主张层字段契约.md",
    "05-页面层规则.md",
]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(items):
    wiki = os.path.join(ROOT, "wiki")
    for base, _, files in os.walk(wiki):
        for fn in sorted(files):
            if fn.endswith(".md"):
                items.append(os.path.relpath(os.path.join(base, fn), ROOT).replace(os.sep, "/"))
    seen, out = set(), {}
    for rel in items:
        rel = rel.replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(path):
            out[rel] = {"missing": True}
            continue
        out[rel] = {"bytes": os.path.getsize(path), "sha256": sha256_of(path)}
    return dict(sorted(out.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成交付清单（bytes + sha256）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra", nargs="*", default=[])
    args = ap.parse_args(argv)

    items = list(DEFAULTS) + list(args.extra)
    manifest = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d"),
        "root": ROOT,
        "claims": sum(1 for line in open(os.path.join(ROOT, "claims.jsonl"), encoding="utf-8") if line.strip()),
        "items": collect(items),
    }
    out_path = os.path.join(ROOT, args.out.replace("/", os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    back = open(out_path, encoding="utf-8").read()
    print(json.dumps({"out": args.out, "files": len(manifest["items"]),
                      "readback_ok": back == text}, ensure_ascii=False, indent=2))
    return 0 if back == text else 1


if __name__ == "__main__":
    sys.exit(main())
