# -*- coding: utf-8 -*-
"""t4_update_drill.py —— M7 更新演练：在临时副本上模拟「补主张」，量出受影响面。

做法：
  1. 把 claims.jsonl / sources.jsonl / normalized / wiki / exports 复制到临时目录；
  2. 按 drills/*.json 的 scenario 追加候选主张（不写项目数据）；
  3. 在副本上跑 wiki_tool.py check，收集结构错（S）与页面漏引（P4）——P4 就是「哪些页面必须同步改」；
  4. 在副本上跑 wiki_tool.py export，与项目当前 exports/ 比对，给出 chunk 级差异；
  5. 结论写 reviews/<drill_id>-差异清单.json，并打印对照表。

用法：
    python tools\\t4_update_drill.py [--spec drills\\2026-11-11-补主张影响面.json]
退出码：0 演练完成（无论场景是否符合 expect）；1 复制/执行失败。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "wiki_tool.py")
DEFAULT_SPEC = os.path.join(ROOT, "drills", "2026-11-11-补主张影响面.json")


def make_clone():
    d = tempfile.mkdtemp(prefix="wt_drill_")
    for name in ("claims.jsonl", "sources.jsonl"):
        shutil.copy(os.path.join(ROOT, name), d)
    for name in ("normalized", "wiki"):
        shutil.copytree(os.path.join(ROOT, name), os.path.join(d, name))
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    return d


def run(cmd, root):
    p = subprocess.run([sys.executable, TOOL, "--root", root, cmd], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"_raw": (p.stdout or "")[-400:], "_err": (p.stderr or "")[-400:]}


def load_chunks(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ch = json.loads(line)
                    out[ch["chunk_id"]] = ch
    return out


def run_scenario(spec):
    d = make_clone()
    try:
        with open(os.path.join(d, "claims.jsonl"), "a", encoding="utf-8") as fh:
            for c in spec["claims"]:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        rc_check, rep = run("check", d)
        rc_export, exp = run("export", d)
        clone_chunks = load_chunks(os.path.join(d, "exports", "wiki_chunks.jsonl"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    base_chunks = load_chunks(os.path.join(ROOT, "exports", "wiki_chunks.jsonl"))
    added = sorted(set(clone_chunks) - set(base_chunks))
    changed = sorted(cid for cid in set(clone_chunks) & set(base_chunks)
                     if clone_chunks[cid] != base_chunks[cid])
    removed = sorted(set(base_chunks) - set(clone_chunks))

    struct = rep.get("structure_errors") or []
    loc = rep.get("locator_errors") or []
    pages_need_edit = [e for e in struct if e.get("code") == "P4"]

    return {
        "label": spec["label"],
        "expect": spec.get("expect"),
        "check_exit": rc_check,
        "structure_error_codes": sorted({e.get("code") for e in struct}),
        "structure_errors": struct,
        "locator_error_codes": sorted({e.get("code") for e in loc}),
        "locator_errors": loc,
        "pages_need_edit": [{"page": e.get("page"), "claim_id": e.get("claim_id")} for e in pages_need_edit],
        "export_exit": rc_export,
        "export_readback_ok": exp.get("readback_ok"),
        "chunks_added": added,
        "chunks_changed": changed,
        "chunks_removed": removed,
        "counts": exp.get("claim_chunks"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="M7 更新演练（临时副本）")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    args = ap.parse_args(argv)

    with open(args.spec, encoding="utf-8") as fh:
        drill = json.load(fh)

    results = [run_scenario(s) for s in drill["scenarios"]]

    base_chunks = load_chunks(os.path.join(ROOT, "exports", "wiki_chunks.jsonl"))
    report = {
        "drill_id": drill["drill_id"],
        "title": drill["title"],
        "why": drill["why"],
        "note": drill["note"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": {"claims": sum(1 for line in open(os.path.join(ROOT, "claims.jsonl"), encoding="utf-8")
                               if line.strip()),
                 "chunks": len(base_chunks)},
        "scenarios": results,
        "verdict": "演练在临时副本执行，项目数据未改；结论见 reviews/ 下同名报告。",
    }
    os.makedirs(os.path.join(ROOT, "reviews"), exist_ok=True)
    out = os.path.join(ROOT, "reviews", "%s-差异清单.json" % drill["drill_id"])
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    for r in results:
        print("== %s" % r["label"])
        print("   check exit=%s 结构错=%s 定位错=%s" % (
            r["check_exit"], ",".join(r["structure_error_codes"]) or "无",
            ",".join(r["locator_error_codes"]) or "无"))
        print("   需同步改的页面：%s" % ([p["page"] for p in r["pages_need_edit"]] or "无"))
        print("   导出：新增 %s / 变化 %s / 移除 %s（claim chunk %s）" % (
            r["chunks_added"] or "无", r["chunks_changed"] or "无", r["chunks_removed"] or "无", r["counts"]))
    print("报告：reviews/%s-差异清单.json" % drill["drill_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
