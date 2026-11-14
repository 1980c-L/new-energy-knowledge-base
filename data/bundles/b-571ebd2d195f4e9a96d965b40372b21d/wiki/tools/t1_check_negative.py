# -*- coding: utf-8 -*-
"""t1_check_negative.py —— 对 wiki_tool.py check 的负向测试（闸门有效性证据）。

做法：把 sources.jsonl / claims.jsonl / normalized / wiki 复制到临时目录，逐项注入一种故障，
再调用 wiki_tool.py --root <临时目录> check，比对退出码（以及可选的规则代号）是否符合预期。

覆盖两组：
  S/L 组：主张层与引用定位（quote 错字、假 locator、缺字段、重复 ID、缺矩阵格、引 eval_only…）
  P 组：页面层（claim 断链、漏引、页面缺失、孤立页、链接断链、unknown 移出待确认区、frontmatter 不合规）

用法：python tools\\t1_check_negative.py
退出码：0 全部符合预期；1 有不符合项（说明闸门漏了）。
输出：可直接重定向存证，例 `python tools\\t1_check_negative.py > handoff\\C1\\negative-test.log`。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "wiki_tool.py")


def make_clone():
    d = tempfile.mkdtemp(prefix="wt_neg_")
    shutil.copy(os.path.join(ROOT, "sources.jsonl"), d)
    shutil.copy(os.path.join(ROOT, "claims.jsonl"), d)
    shutil.copytree(os.path.join(ROOT, "normalized"), os.path.join(d, "normalized"))
    shutil.copytree(os.path.join(ROOT, "wiki"), os.path.join(d, "wiki"))
    return d


def load(d):
    with open(os.path.join(d, "claims.jsonl"), encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def save(d, rows):
    with open(os.path.join(d, "claims.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(d):
    p = subprocess.run([sys.executable, TOOL, "--root", d, "check"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        rep = json.loads(p.stdout)
    except Exception:
        rep = {"structure_errors": [], "locator_errors": [], "raw": p.stdout[-300:]}
    codes = sorted({e.get("code") for e in rep.get("structure_errors", [])} |
                   {e.get("code") for e in rep.get("locator_errors", [])})
    return p.returncode, codes


def mutate(name, fn, expect, code=None):
    """改 claims.jsonl 的一条故障（fn 返回 list 时整表替换）。"""
    d = make_clone()
    try:
        rows = load(d)
        res = fn(rows)
        if isinstance(res, list):
            rows = res
        save(d, rows)
        rc, codes = run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return name, rc, codes, expect, code


def mutate_page(name, relpath, fn, expect, code=None):
    """改/删 wiki 下某个页面文件的一条故障（fn 返回 None 表示删除该文件）。"""
    d = make_clone()
    try:
        p = os.path.join(d, relpath.replace("/", os.sep))
        text = open(p, encoding="utf-8").read()
        new = fn(text)
        if new is None:
            os.remove(p)
        else:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(new)
        rc, codes = run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return name, rc, codes, expect, code


def main():
    cases = [mutate("baseline（未改动）", lambda r: r, 0)]

    def drop_quote_char(rows):
        rows[0]["source_refs"][0]["quote"] = rows[0]["source_refs"][0]["quote"].replace("用户侧", "用电侧")

    cases.append(mutate("quote 错字", drop_quote_char, 2))
    cases.append(mutate("locator 不存在",
                        lambda r: r[0]["source_refs"][0].__setitem__("locator", "第99条"), 2))
    cases.append(mutate("缺 claim_status", lambda r: r[1].pop("claim_status"), 1))
    cases.append(mutate("claim_id 重复",
                        lambda r: r[2].__setitem__("claim_id", r[3]["claim_id"]), 1))
    cases.append(mutate("矩阵缺格", lambda r: [x for x in r if x.get("claim_id") != "W-MX-05"], 1))
    cases.append(mutate("引用 eval_only 来源",
                        lambda r: r[0]["source_refs"][0].__setitem__("source_id", "PV-CASE-20250402"), 1))

    def drop_derivation(rows):
        for r in rows:
            if r.get("claim_id") == "W-CON-04":
                r.pop("derivation", None)

    cases.append(mutate("synthesis 缺 derivation", drop_derivation, 1))
    cases.append(mutate("conditions 全空",
                        lambda r: r[0].__setitem__("conditions", {"project_type": None, "region": None,
                                                                  "subject": None, "exception": None,
                                                                  "note": ""}), 1))
    cases.append(mutate("缺 boundary_inclusion",
                        lambda r: r[0]["temporal_scope"].pop("boundary_inclusion"), 1))
    cases.append(mutate("page_path 越界",
                        lambda r: r[0].__setitem__("page_path", "wiki/topics/别的页.md"), 1))

    # ---------- T 组：演替一致性（2026-11-12 新增） ----------
    def supersede_overlap(rows):
        for r in rows:
            if r.get("claim_id") == "W-TIME-03":
                r["superseded_by"] = "W-TIME-02"
                r["temporal_scope"]["current_effectiveness"] = "checked_superseded"

    cases.append(mutate("superseded_by 区间重叠（未分段）", supersede_overlap, 1, "T1"))

    def supersede_ok(rows):
        for r in rows:
            if r.get("claim_id") == "W-TIME-03":
                r["superseded_by"] = "W-TIME-01"
                r["temporal_scope"]["current_effectiveness"] = "checked_superseded"
                r["temporal_scope"]["end"] = "2025-01-22"

    cases.append(mutate("superseded_by 正确分段（不重叠）", supersede_ok, 0))

    def supersede_dangling(rows):
        for r in rows:
            if r.get("claim_id") == "W-TIME-03":
                r["superseded_by"] = "W-TIME-99"

    cases.append(mutate("superseded_by 指向不存在", supersede_dangling, 1, "T1"))

    # 追加一行「合法 JSON 但不是对象」，应报 S1 而不是让工具崩掉
    d = make_clone()
    try:
        with open(os.path.join(d, "claims.jsonl"), "a", encoding="utf-8") as fh:
            fh.write('"not-an-object"\n')
        rc, codes = run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    cases.append(("claims 行不是对象", rc, codes, 1, "S1"))

    # ---------- P 组：页面层 ----------
    DEF_PAGE = "wiki/topics/项目分类.md"
    cases.append(mutate_page(
        "页面引用不存在的 claim",
        DEF_PAGE,
        lambda t: t.replace("〔W-DEF-09〕", "〔W-DEF-99〕"),
        1, "P3"))
    cases.append(mutate_page(
        "页面漏引本页主张",
        DEF_PAGE,
        lambda t: t.replace("〔W-DEF-07〕", ""),
        1, "P4"))
    cases.append(mutate_page(
        "页面缺失",
        DEF_PAGE,
        lambda t: None,
        1, "P1"))
    cases.append(mutate_page(
        "链接断链（索引指向改名页）",
        "wiki/index.md",
        lambda t: t.replace("topics/概念辨析.md", "topics/概念辨析-改名.md"),
        1, "P6"))

    # unknown 主张移出「待确认」区（保留在正文，故不触发 P4，只触发 P7）
    def move_unknown_out(text):
        lines = text.splitlines()
        # 只搬「待确认」标题之后、且真正承载 W-DEF-08 的那一行；
        # 正文其它小节也可能引用 W-DEF-08，不能按全文件首个匹配去搬（否则搬错行、P7 不触发）
        head = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("#") and "待确认" in ln)
        idx = next(i for i in range(head + 1, len(lines)) if "W-DEF-08" in lines[i])
        bullet = lines.pop(idx)
        lines.insert(head, bullet)       # 插到「待确认」标题之前，仍在同一页
        return "\n".join(lines)

    cases.append(mutate_page("unknown 移出待确认区", DEF_PAGE, move_unknown_out, 1, "P7"))
    cases.append(mutate_page(
        "frontmatter 缺 updated",
        DEF_PAGE,
        lambda t: t.replace("updated: 2026-11-11", "updated: 2026/11/11"),
        1, "P2"))

    # 孤立页：新增一页不被 index 链接
    d = make_clone()
    try:
        with open(os.path.join(d, "wiki", "topics", "草稿页.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntype: concept\npage: 草稿页\nupdated: 2026-11-11\n---\n\n# 草稿\n")
        rc, codes = run(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    cases.append(("孤立页（未被索引链接）", rc, codes, 1, "P5"))

    ok = True
    for name, rc, codes, want, code in cases:
        hit = code is None or code in codes
        flag = "PASS" if (rc == want and hit) else "FAIL"
        ok = ok and rc == want and hit
        print("%-26s 期望 %d 实得 %d  %s  触发=%-16s 期望规则=%s" % (
            name, want, rc, flag, ",".join(codes) or "-", code or "-"))
    print("ALL_PASS=%s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
