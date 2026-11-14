# -*- coding: utf-8 -*-
"""t6_semantic_guards.py —— 语义护栏（把已发生的语义错误固化成可复跑的断言）。

背景：`check` 只验证「结论挂了 claim、claim 能定位到原文」，对语义无感。
C1 的 Q2（5 月 1 日边界）与 C3 的 F01（日期方向反）都是在 `check` 全绿时错的。
本工具把这两轮复审指出的**具体失败模式**写成逐条断言，复发即 exit 1。

不是穷举语义测试，每类错误只有一条护栏；新增护栏前先问「这个错误是否已经真实发生过」。

两种用法：
    python tools\\t6_semantic_guards.py              # 跑护栏（期望 7/7 PASS）
    python tools\\t6_semantic_guards.py --self-test  # 逐条注入回退，验证护栏真的会拦（期望 7/7 拦住）

--self-test 的意义：护栏如果只会「恒绿」，等于没有。自检在临时副本上把每类错误改回去，
若对应护栏没有 FAIL，说明护栏写松了（例如关键词换了写法就漏），必须收紧。

退出码：0 全绿 / 自检全部拦住；1 有护栏触发或自检漏拦。
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wiki_tool as wt  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(HERE)
TIME_PAGE = "wiki/topics/备案与时间衔接.md"
DEF_PAGE = "wiki/topics/项目分类.md"
MODE_PAGE = "wiki/topics/上网模式.md"


def read(rel, root=ROOT):
    return wt.read_text(os.path.join(root, rel.replace("/", os.sep)))


def load_claims(root=ROOT):
    out = {}
    for line in open(os.path.join(root, "claims.jsonl"), encoding="utf-8"):
        line = line.strip()
        if line:
            c = json.loads(line)
            out[c["claim_id"]] = c
    return out


# ---------------- 护栏定义 ----------------
def g1_date_direction(claims, root=ROOT):
    """F01：日期示例方向必须正确（落款→新规、官方→原政策）。"""
    text = read(TIME_PAGE, root)
    bad = []
    for ln in text.splitlines():
        if "界后" in ln and "原政策" in ln:
            bad.append("「界后」却写成走原政策：" + ln.strip()[:80])
        if "界前" in ln and "新规" in ln:
            bad.append("「界前」却写成走新规：" + ln.strip()[:80])
    for need in ("落款 1 月 17 日当成发布日", "官方 1 月 23 日口径", "4 月 30 日"):
        if need not in text:
            bad.append("缺少方向算例要素：%s" % need)
    return (not bad), bad or ["方向与算例要素齐备"]


def g2_voltage_not_exclusive(claims, root=ROOT):
    """F02：不得把电压写成互斥分档（380 伏≠必然自然人户用）。"""
    text = read(DEF_PAGE, root)
    bad = []
    for pat, why in ((r"380\s*伏[^。；\n]{0,6}(指向|即|就是|对应)[^。；\n]{0,8}自然人户用",
                      "把 380 伏当作自然人户用的判定依据"),
                     (r"排除掉不可能的类型", "用电压排除法直接定类型")):
        for m in re.finditer(pat, text):
            bad.append("%s：%s" % (why, m.group(0)))
    return (not bad), bad or ["未出现电压互斥分档句式"]


def g3_def08_no_hard_cap(claims, root=ROOT):
    """F02：DEF-08 必须表达「原则上 / 现有材料不足」，不得用硬上限做排除。"""
    c = claims["W-DEF-08"]
    text = c["claim_text"] + json.dumps(c.get("conditions") or {}, ensure_ascii=False)
    bad = []
    for need in ("原则上", "现有材料不足以确认"):
        if need not in text:
            bad.append("缺要素：%s" % need)
    for forbid in ("硬上限", "必然不落入", "属真实空白"):
        if forbid in c["claim_text"]:
            bad.append("出现硬阈值式措辞：%s" % forbid)
    return (not bad), bad or ["措辞为「现有材料不足」，未用硬上限排除"]


def g4_redline_in_chunks(claims, root=ROOT):
    """F03：五个矩阵格的 conditions 必须随 chunk 带出「同一用地红线」。"""
    bad = []
    for cid in ("W-MX-02", "W-MX-05", "W-MX-08", "W-MX-11", "W-MX-12"):
        exc = (claims[cid].get("conditions") or {}).get("exception") or ""
        if "同一用地红线" not in exc:
            bad.append("%s 的 conditions.exception 缺红线条件" % cid)
    page = read(MODE_PAGE, root)
    if "共同前提" not in page:
        bad.append("上网模式页缺「共同前提」脚注")
    return (not bad), bad or ["五个矩阵格与页面脚注均含红线条件"]


def g5_no_provincial_overreach(claims, root=ROOT):
    """F03：不得把未证实的权限归给省级（例外/后果由省级细则决定）。"""
    bad = []
    for cid in ("W-MODE-04", "W-MX-09"):
        c = claims[cid]
        blob = c["claim_text"] + json.dumps(c.get("conditions") or {}, ensure_ascii=False)
        for m in re.finditer(r"省级细则(决定|规定|另定)", blob):
            bad.append("%s 出现无据归因：%s" % (cid, m.group(0)))
    return (not bad), bad or ["未见「省级细则决定/规定」类归因"]


def g6_reg09_scope(claims, root=ROOT):
    """F04：REG-09 必须是问59 题设范围，不得写成普遍规则。"""
    c = claims["W-REG-09"]
    text = c["claim_text"] + json.dumps(c.get("conditions") or {}, ensure_ascii=False)
    bad = []
    for need in ("35 千伏", "6 兆瓦", "14 兆瓦", "另行判断"):
        if need not in text:
            bad.append("缺题设要素：%s" % need)
    if "已建成的分布式项目在规模重大变更" in c["claim_text"]:
        bad.append("仍为普遍化表述（缺题设限定）")
    return (not bad), bad or ["已限定到问59 题设，其余情形不作断言"]


def g7_time_boundaries(claims, root=ROOT):
    """C1 Q2：两处时间边界的包含关系必须是官方结论（1-23 含当日、5-1 含当日且全容量）。"""
    bad = []
    for cid, end in (("W-TIME-02", "2025-01-23"), ("W-TIME-04", "2025-05-01"), ("W-TIME-05", "2025-05-01")):
        ts = claims[cid].get("temporal_scope") or {}
        if ts.get("end") != end or ts.get("boundary_inclusion") != "end":
            bad.append("%s 的 end/inclusion 为 %s/%s，应为 %s/end" % (
                cid, ts.get("end"), ts.get("boundary_inclusion"), end))
    if "全容量" not in claims["W-TIME-04"]["claim_text"]:
        bad.append("W-TIME-04 未写「全容量」前提")
    return (not bad), bad or ["1-23、5-1 两处边界与官方口径一致"]


GUARDS = [
    ("G1 日期示例方向（F01）", g1_date_direction),
    ("G2 电压不作互斥分档（F02）", g2_voltage_not_exclusive),
    ("G3 DEF-08 不用硬上限排除（F02）", g3_def08_no_hard_cap),
    ("G4 矩阵格带红线条件（F03）", g4_redline_in_chunks),
    ("G5 不越权归因省级（F03）", g5_no_provincial_overreach),
    ("G6 REG-09 限定题设（F04）", g6_reg09_scope),
    ("G7 时间边界含当日（C1 Q2）", g7_time_boundaries),
]


# ---------------- 自检：把错误改回去，确认护栏会拦 ----------------
def _mutate_page(root, rel, old, new):
    p = os.path.join(root, rel.replace("/", os.sep))
    text = open(p, encoding="utf-8").read()
    if old not in text:
        raise RuntimeError("自检注入失败：%s 中找不到 %r" % (rel, old[:30]))
    open(p, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))


def _mutate_claim(root, cid, fn):
    p = os.path.join(root, "claims.jsonl")
    rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
    for r in rows:
        if r["claim_id"] == cid:
            fn(r)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _break_g1(root):
    _mutate_page(root, TIME_PAGE, "→ **走新规**", "→ **走原政策**")


def _break_g2(root):
    p = os.path.join(root, DEF_PAGE.replace("/", os.sep))
    text = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8", newline="\n").write(text + "\n380 伏指向自然人户用。\n")


def _break_g3(root):
    _mutate_claim(root, "W-DEF-08", lambda r: r.__setitem__(
        "claim_text", "35 千伏 20 兆瓦以上属真实空白，超过上限必然不落入大型工商业，属硬上限排除。"))


def _break_g4(root):
    _mutate_claim(root, "W-MX-02", lambda r: r["conditions"].__setitem__("exception", None))


def _break_g5(root):
    _mutate_claim(root, "W-MODE-04", lambda r: r["conditions"].__setitem__(
        "exception", "「原则上」的例外条件由省级细则决定"))


def _break_g6(root):
    _mutate_claim(root, "W-REG-09", lambda r: r.__setitem__(
        "claim_text", "已建成的分布式项目在规模重大变更、按大型工商业管理后，先期建成的部分上网模式须同步调整为全部自发自用。"))


def _break_g7(root):
    _mutate_claim(root, "W-TIME-04", lambda r: r["temporal_scope"].__setitem__("boundary_inclusion", "none"))


BREAKERS = [_break_g1, _break_g2, _break_g3, _break_g4, _break_g5, _break_g6, _break_g7]


def self_test():
    ok_all = True
    for (name, guard), breaker in zip(GUARDS, BREAKERS):
        d = tempfile.mkdtemp(prefix="wt_guard_")
        try:
            for item in ("claims.jsonl", "sources.jsonl"):
                shutil.copy(os.path.join(ROOT, item), d)
            shutil.copytree(os.path.join(ROOT, "normalized"), os.path.join(d, "normalized"))
            shutil.copytree(os.path.join(ROOT, "wiki"), os.path.join(d, "wiki"))
            breaker(d)
            ok, detail = guard(load_claims(d), d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        caught = not ok
        ok_all = ok_all and caught
        print("%-28s %s  %s" % (name, "拦住" if caught else "漏拦", "；".join(detail)[:100]))
    print("SELF_TEST_PASS=%s" % ok_all)
    return 0 if ok_all else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="语义护栏 / 自检")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    claims = load_claims()
    fails = 0
    for name, fn in GUARDS:
        try:
            ok, detail = fn(claims)
        except Exception as exc:  # 护栏自身出错也要报，不能静默放行
            ok, detail = False, ["护栏执行异常：%r" % exc]
        print("%-28s %s  %s" % (name, "PASS" if ok else "FAIL", "；".join(detail)))
        fails += 0 if ok else 1
    print("GUARDS_PASS=%s（%d 条护栏，%d 条触发）" % (fails == 0, len(GUARDS), fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
