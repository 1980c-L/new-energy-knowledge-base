# -*- coding: utf-8 -*-
"""t10_export_verify.py —— 只读导出核验（M13）。

**只读**：不写任何文件、不重写 `exports/`，只读出来逐项核对并打印指纹，用于「交付前能不能信这份导出」。
要重新生成请用 `python tools\\wiki_tool.py export`（那条会写文件并回读）。

核对项：
  E1 每行是合法 JSON 且有必填字段
  E2 `chunk_id` 唯一、`chunk_kind ∈ {claim, page}`
  E3 覆盖完整：每条 claim 都有 `CH-` chunk；有主张的页都有 `PG-` chunk
  E4 `claim_ids` 全部能在 `claims.jsonl` 找到；`content_kind = derived_wiki`
  E5 `source_refs` 的 (source_id, locator) 都能在 normalized 层解析
  E6 `temporal_scope` 存在且 `policy_as_of` 非空
  E7 文本非空；打印文件字节数与 sha256

用法：python tools\\t10_export_verify.py
退出码：0 全过；1 有错。
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_tool as wt  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(ROOT, "exports", "wiki_chunks.jsonl")


def main():
    proj = wt.Project(ROOT)
    proj.load_sources()
    proj.load_claims()

    with open(EXPORT, "rb") as f:
        raw = f.read()
    sha = hashlib.sha256(raw).hexdigest()
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip()]

    errors = []
    seen = set()
    claims_covered = set()
    pages_covered = set()
    kinds = {}
    for i, line in enumerate(lines, 1):
        try:
            c = json.loads(line)
        except Exception as e:  # noqa: BLE001
            errors.append(("E1", "第 %d 行不是合法 JSON：%s" % (i, e)))
            continue
        cid = c.get("chunk_id") or ""
        if not cid:
            errors.append(("E1", "第 %d 行缺 chunk_id" % i))
            continue
        if cid in seen:
            errors.append(("E2", "chunk_id 重复：%s" % cid))
        seen.add(cid)
        kind = c.get("chunk_kind")
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind not in ("claim", "page"):
            errors.append(("E2", "%s 的 chunk_kind 非法：%r" % (cid, kind)))
        if c.get("content_kind") != "derived_wiki":
            errors.append(("E4", "%s 的 content_kind 不是 derived_wiki：%r"
                           % (cid, c.get("content_kind"))))
        ids = c.get("claim_ids") or []
        for x in ids:
            if x not in proj.claim_by_id:
                errors.append(("E4", "%s 引用了不存在的主张 %s" % (cid, x)))
        if kind == "claim":
            claims_covered.update(ids)
        else:
            pages_covered.add(c.get("page_path"))
        for r in c.get("source_refs") or []:
            ok, why = proj.resolve(r.get("source_id"), r.get("locator"))
            if not ok:
                errors.append(("E5", "%s 的引用无法定位：%s %s（%s）"
                               % (cid, r.get("source_id"), r.get("locator"), why)))
        ts = c.get("temporal_scope") or {}
        if not ts.get("policy_as_of"):
            errors.append(("E6", "%s 缺 temporal_scope.policy_as_of" % cid))
        if not (c.get("text") or "").strip():
            errors.append(("E7", "%s 的 text 为空" % cid))

    all_claims = set(proj.claim_by_id)
    miss = sorted(all_claims - claims_covered)
    if miss:
        errors.append(("E3", "有 %d 条主张没有导出 chunk：%s" % (len(miss), miss[:5])))
    page_need = {c.get("page_path") for c in proj.claims}
    miss_p = sorted(p for p in page_need if p not in pages_covered)
    if miss_p:
        errors.append(("E3", "有 %d 个页没有页面 chunk：%s" % (len(miss_p), miss_p)))

    print("文件：exports/wiki_chunks.jsonl")
    print("行数：%d（claim %d / page %d）" % (len(lines), kinds.get("claim", 0), kinds.get("page", 0)))
    print("字节：%d" % len(raw))
    print("sha256：%s" % sha)
    print("主张覆盖：%d/%d；页面覆盖：%d/%d" % (
        len(claims_covered & all_claims), len(all_claims), len(pages_covered), len(page_need)))
    if errors:
        print("\n发现问题 %d 项：" % len(errors))
        for code, d in errors[:20]:
            print("  ✗ %s %s" % (code, d))
        return 1
    print("\nE1–E7 全部通过（只读，未改动任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
