# -*- coding: utf-8 -*-
"""t5_c3_package.py —— 生成 C3 全面复审用的核查表（手写部分只补结论，不抄数字）。

生成到 handoff/C3/：
  checklist-pages.md       逐页逐节的「主张 → 原文位置」核查表（人工打勾）
  checklist-synthesis.md   7 条合成主张的推导链核查表
  checklist-verbatim.md    43 条原文完整性 + quote 命中统计 + 抽样建议
  checklist-eval.md        14 题参照答案与证据组核查表

用法：python tools\\t5_c3_package.py
退出码：0 生成成功；1 写盘/回读失败。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wiki_tool as wt  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "handoff", "C3")
ARTICLE_RE = re.compile(r"^第(\d+)条$")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def short(sid):
    """来源简称（只影响可读性，不影响定位）。"""
    s = sid or "?"
    for pre, name in (("PV-NAT", "办法正文"), ("PV-FAQ", "问答"), ("PV-EXP", "解读"),
                      ("PV-NOTICE", "通知"), ("PV-CASE", "咨询答复")):
        if s.startswith(pre):
            return name
    return s


def page_headings(page_rel, text):
    """claim_id → 所在小节标题（首次出现）；跳过 frontmatter，避免 claims: 行把全部 ID 归到页首。"""
    cur = "页首（正文开头）"
    mapping = {}
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    for ln in lines[start:]:
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            cur = m.group(2).strip()
        for cid in wt.CLAIM_REF_RE.findall(ln):
            mapping.setdefault(cid, cur)
    return mapping


def locator_str(claim):
    out = []
    for r in claim.get("source_refs") or []:
        out.append("%s / %s" % (short(r.get("source_id")), r.get("locator")))
    for r in (claim.get("derivation") or {}).get("basis_source_refs") or []:
        if any(r.get("locator") == x.get("locator") and r.get("source_id") == x.get("source_id")
               for x in claim.get("source_refs") or []):
            continue
        out.append("%s / %s（推导依据）" % (short(r.get("source_id")), r.get("locator")))
    return "；".join(out)


def build_pages(proj):
    L = ["# C3 核查表 ①：页面语义（逐节打勾）", "",
         "用法：每行是「页面 → 小节 → 主张」，去 `wiki/topics/<页面>` 找到该小节的结论句，",
         "按右侧「原文位置」用 `python tools\\wiki_tool.py show --claim-id <ID>` 读回原文单元，核对三件事：", "",
         "1. **支持**：结论句没有超出该单元的字面意思（不是「看起来合理」）；",
         "2. **条件**：`conditions` 里的限定（项目类型 / 电压 / 容量 / 地区 / 例外）在句子里没有丢；",
         "3. **边界**：时间类结论的端点与包含关系与 `temporal_scope` 一致（尤其 1-23、5-1 两处）。", "",
         "打勾写在「结论」列：`OK` / `改：<要改什么>` / `疑：<不确定在哪>`。", ""]
    for p in wt.PAGES:
        path = os.path.join(ROOT, p.replace("/", os.sep))
        if not os.path.exists(path):
            continue
        text = wt.read_text(path)
        heads = page_headings(p, text)
        own = [c for c in proj.claims if c.get("page_path") == p]
        if not own:
            L += ["## %s（0 条主张，无需逐条核）" % os.path.basename(p).replace(".md", ""), ""]
            continue
        L.append("## %s（%d 条）" % (os.path.basename(p).replace(".md", ""), len(own)))
        L.append("")
        L.append("| 小节 | 主张 | 类型 | 状态 | 结论摘要 | 原文位置 | 结论 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in sorted(own, key=lambda x: str(x.get("claim_id"))):
            cid = c.get("claim_id")
            L.append("| %s | `%s` | %s | %s | %s | %s | ☐ |" % (
                heads.get(cid, "—"), cid, c.get("knowledge_kind"), c.get("claim_status"),
                (c.get("claim_text") or "")[:70].replace("|", "／"), locator_str(c)))
        L.append("")
    return "\n".join(L)


def build_synthesis(proj):
    rows = [c for c in proj.claims if c.get("knowledge_kind") == "synthesis"]
    L = ["# C3 核查表 ②：合成主张的推导链", "",
         "这 7 条不是原文直述，是由两处以上材料推出来的。**只有这 7 条需要判断「推得对不对」**，其余 53 条只需核对字面。",
         "每条要看三点：方法是否成立、依据是否够、有没有更强的反例。", "",
         "| 主张 | 页面 | 推导方法 | 依据主张 | 依据原文 | 结论 |", "|---|---|---|---|---|---|"]
    for c in sorted(rows, key=lambda x: str(x.get("claim_id"))):
        d = c.get("derivation") or {}
        L.append("| `%s` | %s | %s | %s | %s | ☐ |" % (
            c.get("claim_id"), os.path.basename(c.get("page_path", "")).replace(".md", ""),
            d.get("method"), "、".join("`%s`" % x for x in d.get("basis_claim_ids") or []) or "—",
            "；".join("%s / %s" % (short(r.get("source_id")), r.get("locator"))
                     for r in d.get("basis_source_refs") or []) or "—"))
    L.append("")
    L.append("## 逐条说明")
    L.append("")
    for c in sorted(rows, key=lambda x: str(x.get("claim_id"))):
        d = c.get("derivation") or {}
        L += ["### `%s`" % c.get("claim_id"), "",
              "- 结论：%s" % c.get("claim_text"),
              "- 方法：`%s`" % d.get("method"),
              "- 依据主张：%s" % ("、".join("`%s`" % x for x in d.get("basis_claim_ids") or []) or "—"),
              "- 依据原文：%s" % ("；".join("%s / %s" % (r.get("source_id"), r.get("locator"))
                                          for r in d.get("basis_source_refs") or []) or "—"),
              "- 推导说明：%s" % (d.get("note") or "（无）"),
              "- 若你认为推导不成立，写在这里：", ""]
    return "\n".join(L)


def build_verbatim(proj):
    L = ["# C3 核查表 ③：原文完整性与引用命中", "",
         "本表是**机器结果**，你只需抽查；机器已做的部分不要重复劳动。", ""]

    src = proj.source_by_id.get("PV-NAT-2025-007-ATTACH") or {}
    units = proj.units("PV-NAT-2025-007-ATTACH") if src else {}
    arts = sorted(int(m.group(1)) for k in units for m in [ARTICLE_RE.match(k)] if m)
    expect = list(range(1, 44))
    L += ["## 1. 43 条完整性（机器）", "",
          "- 可定位条文数：**%d**" % len(arts),
          "- 条号范围：%s – %s" % (min(arts) if arts else "-", max(arts) if arts else "-"),
          "- 是否连续无缺号：**%s**" % ("是" if arts == expect else "否，缺 %s" % sorted(set(expect) - set(arts))),
          "- 是否重号：**%s**" % ("否（key 唯一）" if len(arts) == len(set(arts)) else "是"),
          "- 与商务部转载版对照：A 阶段已做 43/43 条首句一致（`tools/a6_verify_sources.py` 与 `02` §二）", ""]

    total = okc = 0
    bad = []
    for c in proj.claims:
        for r in c.get("source_refs") or []:
            total += 1
            okk, payload = proj.resolve(r.get("source_id"), r.get("locator"))
            hit = okk and norm(r.get("quote")) and norm(r.get("quote")) in norm(payload)
            if hit:
                okc += 1
            else:
                bad.append((c.get("claim_id"), r.get("locator")))
    L += ["## 2. quote 命中（机器）", "",
          "- 引用总数：**%d**，`quote` 去空白后能在该单元中找到：**%d**" % (total, okc),
          "- 未命中：%s" % ("无" if not bad else bad), "",
          "说明：这条只证明「引文确实存在于该单元」，**不证明「这句话支持那个结论」**——后者是表 ① 和表 ② 的事。", ""]

    sample = ["W-DEF-06", "W-DEF-08", "W-DEF-10", "W-MODE-04", "W-MX-12",
              "W-REG-08", "W-TIME-02", "W-TIME-03", "W-TIME-04", "W-GRID-05", "W-CON-04", "W-REG-10"]
    L += ["## 3. 抽查清单（12 条，覆盖 7 个域 + 2 条 unknown + 2 条 synthesis）", "",
          "| 主张 | 状态/类型 | 结论 | 引用单元与引文 |", "|---|---|---|---|"]
    for cid in sample:
        c = proj.claim_by_id.get(cid)
        if not c:
            continue
        cells = []
        for r in c.get("source_refs") or []:
            cells.append("%s / %s：%s" % (short(r.get("source_id")), r.get("locator"),
                                          norm(r.get("quote"))[:120]))
        L.append("| `%s` | %s / %s | %s | %s |" % (
            cid, c.get("claim_status"), c.get("knowledge_kind"),
            (c.get("claim_text") or "")[:60].replace("|", "／"),
            "<br>".join(cells).replace("|", "／")))
    L.append("")
    L.append("核对方式：`python tools\\wiki_tool.py show --claim-id <ID>`，看 `quote` 是否就是原文那句话、有没有被截断改义。")
    return "\n".join(L)


def build_eval(proj):
    cases = [json.loads(x) for x in open(os.path.join(ROOT, "eval", "cases.jsonl"), encoding="utf-8") if x.strip()]
    refs = {r["case_id"]: r for r in (json.loads(x) for x in open(os.path.join(ROOT, "eval", "reference.jsonl"),
                                                                  encoding="utf-8") if x.strip())}
    ans = {a["case_id"]: a for a in (json.loads(x) for x in open(os.path.join(ROOT, "eval", "results", "answers.jsonl"),
                                                                 encoding="utf-8") if x.strip())}
    L = ["# C3 核查表 ④：14 题参照答案", "",
         "重点看**时间类与条件类**：Q2 那次错误就出在「边界含不含当日」上，机器查不出来。", "",
         "| 题 | 类型 | 参考答案要点 | 参照证据 | 作答引用 | 结论 |", "|---|---|---|---|---|---|"]
    for c in cases:
        cid = c["case_id"]
        r = refs.get(cid) or {}
        a = ans.get(cid) or {}
        ev = "；".join("%s / %s" % (short(e.get("source_id")), e.get("locator"))
                       for e in r.get("evidence") or [])
        L.append("| %s | %s | %s | %s | %s | ☐ |" % (
            cid, c.get("type"), (r.get("reference_answer") or "")[:80].replace("|", "／"),
            ev.replace("|", "／"), "、".join("`%s`" % x for x in a.get("cited_claim_ids") or [])))
    L.append("")
    L.append("题目原文见 `eval/cases.jsonl`；逐题实跑结果见 `eval/results/summary.json`。")
    return "\n".join(L)


def main():
    proj = wt.Project(ROOT)
    proj.load_sources()
    proj.load_claims()
    os.makedirs(OUT, exist_ok=True)
    files = {
        "checklist-pages.md": build_pages(proj),
        "checklist-synthesis.md": build_synthesis(proj),
        "checklist-verbatim.md": build_verbatim(proj),
        "checklist-eval.md": build_eval(proj),
    }
    ok = True
    for name, text in files.items():
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        back = open(path, encoding="utf-8").read()
        good = back == text + "\n"
        ok = ok and good
        print("%-24s %6d B  回读=%s" % (name, len(back.encode("utf-8")), "一致" if good else "不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
