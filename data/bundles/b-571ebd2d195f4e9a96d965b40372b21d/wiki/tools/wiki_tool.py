# -*- coding: utf-8 -*-
"""wiki_tool.py —— 主张层与 Wiki 的自检/检索/读回/导出入口。

子命令：
  check                       结构 + 引用定位检查，退出码 0 通过 / 1 结构错 / 2 引用定位缺失
  search --query Q [--mode wiki|raw] [--top-k 5]
  show --claim-id W-XXX-01
  export [--out exports/wiki_chunks.jsonl]

约定见 04-主张层字段契约.md。仅用标准库，项目根为脚本上级目录（可用 --root 覆盖）。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_retrieval as wr  # noqa: E402  检索/组装/引用公共层（移植自 RAG，见文件头）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PAGES = [
    "wiki/topics/项目分类.md",
    "wiki/topics/上网模式.md",
    "wiki/topics/备案与时间衔接.md",
    "wiki/topics/概念辨析.md",
    "wiki/topics/来源目录.md",
]
INDEX_PATH = "wiki/index.md"
PAGE_TYPES = {"entity", "concept", "comparison", "synthesis", "reference"}
PENDING_KW = "待确认"
CLAIM_ID_RE = re.compile(r"^W-(DEF|MODE|MX|REG|TIME|GRID|CON)-\d{2}$")
CLAIM_REF_RE = re.compile(r"W-[A-Z]{2,4}-\d{2}")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
STATUS = {"supported", "conflict", "unknown"}
KIND = {"extracted", "synthesis"}
INCL = {"start", "end", "both", "none"}
EFF = {"checked_effective", "checked_superseded", "not_checked"}
# 端点角色（`04` §七，2026-11-14 增补）：rule_validity=规则自身有效期；event_deadline=项目事件截止
ROLES = {"rule_validity", "event_deadline"}
VERDICT = {"may", "may_not", "conditional", "unknown"}
MODES = ["全额上网", "全部自发自用", "自发自用余电上网"]
TYPES = ["自然人户用", "非自然人户用", "一般工商业", "大型工商业"]
REQUIRED = ["claim_id", "page_path", "claim_text", "source_refs",
            "conditions", "temporal_scope", "claim_status", "knowledge_kind"]
REGIONS = {"national", "spot_market_continuous", "provincial_variable"}


def norm(s):
    """去所有空白，用于 quote 与原文比对。"""
    return re.sub(r"\s+", "", s or "")


class Project(object):
    def __init__(self, root):
        self.root = root
        self.sources = []
        self.claims = []
        self._unit_cache = {}

    # ---------- 载入 ----------
    def load_sources(self):
        path = os.path.join(self.root, "sources.jsonl")
        self.sources = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self.sources.append(json.loads(line))
        self.source_by_id = {s["source_id"]: s for s in self.sources}

    def load_claims(self):
        path = os.path.join(self.root, "claims.jsonl")
        self.claims = []
        self.claim_errors = []
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    self.claim_errors.append({"code": "S1", "line": lineno,
                                              "detail": "JSON 解析失败：%s" % exc})
                    continue
                if not isinstance(obj, dict):
                    self.claim_errors.append({"code": "S1", "line": lineno,
                                              "detail": "该行不是 JSON 对象（实际类型 %s）" % type(obj).__name__})
                    continue
                obj["_line"] = lineno
                self.claims.append(obj)
        self.claim_by_id = {c.get("claim_id"): c for c in self.claims}

    # ---------- 单元解析 ----------
    def units(self, source_id):
        """返回 {key: text}；key 为 '第N条' / '问N' / '第N段' / '通知段N' / '段N'。"""
        if source_id in self._unit_cache:
            return self._unit_cache[source_id]
        src = self.source_by_id.get(source_id)
        out = {}
        if src and src.get("text_path"):
            path = os.path.join(self.root, src["text_path"].replace("\\", os.sep))
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        if "article_no" in d:
                            out["第%d条" % d["article_no"]] = d.get("text", "")
                        elif "q_no" in d:
                            out["问%d" % d["q_no"]] = (d.get("question", "") + "\n" + (d.get("answer") or ""))
                        elif "para_no" in d:
                            if "notice" in os.path.basename(path):
                                out["通知段%d" % d["para_no"]] = d.get("text", "")
                            elif "explain" in os.path.basename(path):
                                out["第%d段" % d["para_no"]] = d.get("text", "")
                            else:
                                out["段%d" % d["para_no"]] = d.get("text", "")
        self._unit_cache[source_id] = out
        return out

    def structure_summary(self, source_id):
        """`全文结构` 虚拟单元：按 normalized 的 unit 字段统计章数与条数，并给出末章条号区间。"""
        src = self.source_by_id.get(source_id)
        if not src or not src.get("text_path"):
            return "来源无 text_path：%s" % source_id
        path = os.path.join(self.root, src["text_path"].replace("\\", os.sep))
        if not os.path.exists(path):
            return "text_path 不存在：%s" % src["text_path"]
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        chapters = [r for r in rows if r.get("unit") == "chapter"]
        arts = [r for r in rows if r.get("unit") == "article"]
        if not chapters:
            return "共 %d 条可定位条文" % len(arts)
        last = max(chapters, key=lambda r: r.get("chapter_no") or 0)
        na = [a.get("article_no") for a in arts if a.get("chapter_no") == last.get("chapter_no")]
        tail = "；第%s章「%s」为第 %s–%s 条" % (last.get("chapter_no"), last.get("chapter_title"),
                                              min(na), max(na)) if na else ""
        return "共 %d 章 %d 条%s" % (len(chapters), len(arts), tail)

    def resolve(self, source_id, locator):
        """返回 (ok, text_or_reason)。"""
        src = self.source_by_id.get(source_id)
        if not src:
            return False, "source_id 不在 sources.jsonl：%s" % source_id
        if not src.get("text_path"):
            return False, "来源无 text_path：%s" % source_id
        path = os.path.join(self.root, src["text_path"].replace("\\", os.sep))
        if not os.path.exists(path):
            return False, "text_path 不存在：%s" % src["text_path"]
        if locator == "全文结构":
            return True, self.structure_summary(source_id)
        units = self.units(source_id)
        if locator in units:
            return True, units[locator]
        return False, "locator 无法解析：%s → %s" % (source_id, locator)


# ---------------- 页面层工具（M3） ----------------
def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_front(text):
    """解析页面开头的 frontmatter（只支持 key: value 单行形式）。无 frontmatter 返回 None。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            return meta
        if ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def first_existing(cands):
    for c in cands:
        if os.path.exists(os.path.normpath(c)):
            return os.path.normpath(c)
    return None


def resolve_links(text, page_rel, root):
    """解析页内链接，返回 [(原始写法, 绝对路径或 None)]。"""
    page_dir = os.path.dirname(os.path.join(root, page_rel.replace("/", os.sep)))
    wiki_root = os.path.join(root, "wiki")
    out = []
    for m in MD_LINK_RE.finditer(text):
        raw = m.group(1).strip()
        if raw.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", raw):
            continue
        tgt = raw.split("#")[0]
        if not tgt:
            continue
        out.append((raw, first_existing([os.path.join(page_dir, tgt),
                                         os.path.join(wiki_root, tgt),
                                         os.path.join(root, tgt)])))
    for m in WIKI_LINK_RE.finditer(text):
        raw = m.group(1).strip()
        name = raw if raw.endswith(".md") else raw + ".md"
        out.append(("[[%s]]" % raw, first_existing([os.path.join(wiki_root, "topics", name),
                                                    os.path.join(wiki_root, name),
                                                    os.path.join(root, name)])))
    return out


def pending_section(text):
    """取「待确认」区块正文；无该标题返回 None。区块止于同级或更高级标题。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m and PENDING_KW in m.group(2):
            level = len(m.group(1))
            body = []
            for ln2 in lines[i + 1:]:
                m2 = re.match(r"^(#{1,6})\s+", ln2)
                if m2 and len(m2.group(1)) <= level:
                    break
                body.append(ln2)
            return "\n".join(body)
    return None


def check_pages(proj, errors, warnings):
    """页面级检查：P1 缺失 / P2 frontmatter / P3 主张断链 / P4 漏引 / P5 孤立页 / P6 链接断链 / P7 unknown 未入待确认区。"""
    root = proj.root
    wiki_root = os.path.join(root, "wiki")
    index_abs = os.path.normpath(os.path.join(root, INDEX_PATH.replace("/", os.sep)))
    report = []

    index_text = ""
    if not os.path.exists(index_abs):
        errors.append({"code": "P1", "page": INDEX_PATH, "detail": "缺少阅读目录页：%s" % INDEX_PATH})
    else:
        index_text = read_text(index_abs)

    md_files = []
    if os.path.isdir(wiki_root):
        for base, _, files in os.walk(wiki_root):
            for fn in files:
                if fn.endswith(".md"):
                    md_files.append(os.path.normpath(os.path.join(base, fn)))

    for abs_p in md_files:
        rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
        for raw, resolved in resolve_links(read_text(abs_p), rel, root):
            if resolved is None:
                errors.append({"code": "P6", "page": rel, "detail": "断链：%s 指向的文件不存在" % raw})

    linked_from_index = {r for _, r in resolve_links(index_text, INDEX_PATH, root) if r}
    for abs_p in md_files:
        rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
        if os.path.normpath(abs_p) == index_abs:
            continue
        if os.path.normpath(abs_p) not in linked_from_index:
            errors.append({"code": "P5", "page": rel, "detail": "孤立页：%s 未被 %s 链接" % (rel, INDEX_PATH)})
        if rel not in PAGES:
            warnings.append({"code": "W-EXTRA", "detail": "wiki/ 下存在不在页面枚举内的文件：%s" % rel})

    for p in PAGES:
        abs_p = os.path.normpath(os.path.join(root, p.replace("/", os.sep)))
        if not os.path.exists(abs_p):
            errors.append({"code": "P1", "page": p, "detail": "页面缺失：%s" % p})
            report.append({"page": p, "exists": False})
            continue
        text = read_text(abs_p)
        stem = os.path.splitext(os.path.basename(p))[0]
        meta = parse_front(text)
        if meta is None:
            errors.append({"code": "P2", "page": p, "detail": "缺 frontmatter（需含 page / type / updated）"})
        else:
            if meta.get("page") != stem:
                errors.append({"code": "P2", "page": p, "detail": "frontmatter.page=%r 与文件名 %r 不一致" % (meta.get("page"), stem)})
            if meta.get("type") not in PAGE_TYPES:
                errors.append({"code": "P2", "page": p, "detail": "frontmatter.type 非法：%r" % meta.get("type")})
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", meta.get("updated") or ""):
                errors.append({"code": "P2", "page": p, "detail": "frontmatter.updated 不是 ISO 日期：%r" % meta.get("updated")})

        ref_ids = set(CLAIM_REF_RE.findall(text))
        own = [c.get("claim_id") for c in proj.claims if c.get("page_path") == p]
        for cid in sorted(ref_ids):
            c = proj.claim_by_id.get(cid)
            if not c:
                errors.append({"code": "P3", "page": p, "claim_id": cid,
                               "detail": "页面引用了 claims.jsonl 中不存在的 claim_id"})
            elif c.get("page_path") != p:
                warnings.append({"code": "W-CROSS", "page": p, "claim_id": cid,
                                 "detail": "跨页引用（该主张归属 %s）" % c.get("page_path")})
        missing = [cid for cid in own if cid not in ref_ids]
        if missing:
            errors.append({"code": "P4", "page": p, "claim_id": ",".join(missing),
                           "detail": "本页主张未在页面出现（结论未挂 claim_id）：%s" % ",".join(missing)})

        sec = pending_section(text)
        unk = [c.get("claim_id") for c in proj.claims
               if c.get("page_path") == p and c.get("claim_status") == "unknown"]
        for cid in unk:
            if sec is None:
                errors.append({"code": "P7", "page": p, "claim_id": cid,
                               "detail": "unknown 主张所在页缺「%s」区块" % PENDING_KW})
            elif cid not in sec:
                errors.append({"code": "P7", "page": p, "claim_id": cid,
                               "detail": "unknown 主张未放进「%s」区，构成无条件结论" % PENDING_KW})

        report.append({"page": p, "exists": True, "type": (meta or {}).get("type"),
                       "claims_expected": len(own),
                       "claims_cited": len([x for x in own if x in ref_ids]),
                       "claim_refs_total": len(ref_ids),
                       "unknown_claims": len(unk),
                       "links": len(resolve_links(text, p, root)),
                       "pending_section": sec is not None})
    return report


# ---------------- check ----------------
# ---------------- 时序一致性（T1，2026-11-12 新增） ----------------
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def ts_within(ts, day):
    """temporal_scope 在某日是否有效；start/end 为 null 表示该侧开放。"""
    start, end = ts.get("start"), ts.get("end")
    incl = ts.get("boundary_inclusion") or "none"
    if start is None and end is None:
        return True
    if start is not None:
        if day < iso_date(start):
            return False
        if day == iso_date(start) and incl not in ("start", "both"):
            return False
    if end is not None:
        if day > iso_date(end):
            return False
        if day == iso_date(end) and incl not in ("end", "both"):
            return False
    return True


def ts_rule_within(ts, day):
    """只按「规则自身有效期」判断该日是否适用，**忽略** `event_deadline` 端点。

    背景（`04` §七 端点角色 / 173 号复审 F1a）：`W-TIME-04/05` 的 `end=2025-05-01` 是
    项目并网截止日，不是过渡条款的失效日。拿 `policy_as_of` 去比会把过渡条款滤掉，
    导致 2025-12-31 提问「5 月 1 日前并网的存量项目」时查不到该条。
    事件截止只作为结论里的条件，随 `claim_text` / `conditions` 传给生成层。
    """
    rule = dict(ts)
    if (ts.get("start_role") or "rule_validity") == "event_deadline":
        rule["start"] = None
    if (ts.get("end_role") or "rule_validity") == "event_deadline":
        rule["end"] = None
    return ts_within(rule, day)


def ranges_overlap(ta, tb):
    """两个 temporal_scope 是否在某个日期同时有效；返回 (是否, 首个重合日或说明)。"""
    if ((not ta.get("start") and not ta.get("end"))
            or (not tb.get("start") and not tb.get("end"))):
        return True, "无端点（覆盖任意日期）"
    probes = set()
    for ts in (ta, tb):
        for k in ("start", "end"):
            v = ts.get(k)
            if v and ISO_DATE_RE.match(str(v)):
                probes.add(iso_date(v))
    hit = sorted(d for d in probes if ts_within(ta, d) and ts_within(tb, d))
    return (True, hit[0].isoformat()) if hit else (False, None)


def check_supersession(claims, err):
    """T1：`superseded_by` 声明的一致性（2026-11-12 新增）。

    为什么不用「区间重叠」去猜谁取代谁：实测过，现有 60 条里绝大多数主张的
    区间是「不按时间分段」（start/end 都 null），任何一条标 checked_superseded
    都会与它们全部重叠——给 W-TIME-03 标作废即报出 50 条噪音。区间字段承载不了
    「谁取代谁」，所以只校验**显式声明**的配对：

      1) `superseded_by` 必须指向存在的主张，且不能指向自身；
      2) 被取代者与取代者不得在同一日期同时有效（演替必须真的分段）。
    """
    by_id = {c.get("claim_id"): c for c in claims}
    for c in claims:
        target = c.get("superseded_by")
        if not target:
            continue
        cid = c.get("claim_id")
        if target == cid:
            err("T1", cid, "superseded_by 指向自身")
            continue
        b = by_id.get(target)
        if not b:
            err("T1", cid, "superseded_by 指向不存在的主张：%s" % target)
            continue
        ta, tb = c.get("temporal_scope") or {}, b.get("temporal_scope") or {}
        overlap, where = ranges_overlap(ta, tb)
        if overlap:
            err("T1", cid, "%s 声明由 %s 取代，但两条在 %s 同时有效（演替未分段）"
                % (cid, target, where))


def cmd_check(proj, args):
    proj.load_sources()
    proj.load_claims()
    errors = list(proj.claim_errors)

    def err(code, claim_id, detail):
        errors.append({"code": code, "claim_id": claim_id, "detail": detail})

    seen = {}
    matrix_cells = {}
    eval_only_used = []

    for c in proj.claims:
        cid = c.get("claim_id")
        # S2 必填
        missing = [k for k in REQUIRED if c.get(k) in (None, "", [], {})]
        if missing:
            err("S2", cid, "缺必填字段：%s" % ",".join(missing))
        # S3 ID
        if not cid or not CLAIM_ID_RE.match(str(cid)):
            err("S3", cid, "claim_id 不符合 W-<DOMAIN>-<NN>")
        elif cid in seen:
            err("S3", cid, "claim_id 重复（行 %s 与 %s）" % (seen[cid], c.get("_line")))
        else:
            seen[cid] = c.get("_line")
        # S4 枚举
        if c.get("claim_status") not in STATUS:
            err("S4", cid, "claim_status 非法：%r" % c.get("claim_status"))
        if c.get("knowledge_kind") not in KIND:
            err("S4", cid, "knowledge_kind 非法：%r" % c.get("knowledge_kind"))
        ts = c.get("temporal_scope") or {}
        for k in ("policy_as_of", "retrieved_at", "boundary_inclusion", "current_effectiveness"):
            if k not in ts:
                err("S4", cid, "temporal_scope 缺字段：%s" % k)
        if ts.get("boundary_inclusion") not in INCL:
            err("S4", cid, "boundary_inclusion 非法：%r" % ts.get("boundary_inclusion"))
        if ts.get("current_effectiveness") not in EFF:
            err("S4", cid, "current_effectiveness 非法：%r" % ts.get("current_effectiveness"))
        if ts.get("end") and ts.get("boundary_inclusion") == "none" and not ts.get("note"):
            err("S4", cid, "end 非空但 inclusion=none 且未在 note 说明")
        # 端点角色（04 §七，2026-11-14）：枚举合法，且 event_deadline 一侧必须有日期
        for side in ("start", "end"):
            role = ts.get("%s_role" % side)
            if role is None:
                continue
            if role not in ROLES:
                err("S4", cid, "%s_role 非法：%r" % (side, role))
            elif role == "event_deadline" and not ts.get(side):
                err("S4", cid, "%s_role=event_deadline 但 %s 为空" % (side, side))
        # S5 页面
        if c.get("page_path") not in PAGES:
            err("S5", cid, "page_path 不在冻结枚举：%r" % c.get("page_path"))
        # S6 conditions
        cond = c.get("conditions") or {}
        if not any(cond.get(k) for k in ("project_type", "region", "subject", "exception")):
            err("S6", cid, "conditions 全为空，构成无条件结论")
        pt = cond.get("project_type")
        if isinstance(pt, list):
            bad = [x for x in pt if x not in TYPES]
            if bad:
                err("S6", cid, "conditions.project_type 含未知类型：%s" % bad)
        elif pt not in (None, "any") and pt not in TYPES:
            err("S6", cid, "conditions.project_type 非法：%r" % pt)
        if cond.get("region") and cond["region"] not in REGIONS:
            err("S6", cid, "conditions.region 非法：%r" % cond["region"])
        # S7 + 推导依据定位
        der = c.get("derivation")
        if c.get("knowledge_kind") == "synthesis":
            if not isinstance(der, dict):
                err("S7", cid, "synthesis 必须有 derivation")
            else:
                total = len(der.get("basis_claim_ids") or []) + len(der.get("basis_source_refs") or [])
                if total < 2:
                    err("S7", cid, "derivation 依据合计 < 2（当前 %d）" % total)
                for bid in der.get("basis_claim_ids") or []:
                    if bid not in proj.claim_by_id:
                        err("S8", cid, "derivation 引用了不存在的主张：%s" % bid)
        # S8 矩阵
        mx = c.get("matrix")
        if mx:
            if mx.get("verdict") not in VERDICT:
                err("S8", cid, "matrix.verdict 非法：%r" % mx.get("verdict"))
            if mx.get("mode") not in MODES:
                err("S8", cid, "matrix.mode 非法：%r" % mx.get("mode"))
            if mx.get("project_type") not in TYPES:
                err("S8", cid, "matrix.project_type 非法：%r" % mx.get("project_type"))
            for bid in mx.get("basis_claim_ids") or []:
                if bid not in proj.claim_by_id:
                    err("S8", cid, "matrix 引用了不存在的主张：%s" % bid)
            cell = (mx.get("project_type"), mx.get("mode"))
            if cell in matrix_cells:
                err("S8", cid, "矩阵格重复：%s × %s（另见 %s）" % (cell[0], cell[1], matrix_cells[cell]))
            else:
                matrix_cells[cell] = cid
        # S9 source_refs
        refs = c.get("source_refs") or []
        if not refs:
            err("S9", cid, "source_refs 为空")
        for r in refs:
            if not isinstance(r, dict) or not all(k in r for k in ("source_id", "locator", "quote")):
                err("S9", cid, "source_ref 缺 source_id/locator/quote")
                continue
            src = proj.source_by_id.get(r["source_id"])
            if not src:
                err("S9", cid, "source_id 不存在：%s" % r["source_id"])
            elif src.get("role") == "eval_only":
                eval_only_used.append(cid)
                err("S9", cid, "引用了 eval_only 来源（评测与建设同源）：%s" % r["source_id"])

    # 矩阵 12 格齐全
    for t in TYPES:
        for m in MODES:
            if (t, m) not in matrix_cells:
                err("S8", None, "矩阵缺格：%s × %s" % (t, m))

    # L1/L2/L3
    for c in proj.claims:
        cid = c.get("claim_id")
        refs = list(c.get("source_refs") or [])
        der = c.get("derivation") or {}
        refs += list(der.get("basis_source_refs") or [])
        for r in refs:
            if not isinstance(r, dict) or "source_id" not in r:
                continue
            ok, payload = proj.resolve(r.get("source_id"), r.get("locator"))
            if not ok:
                err("L1", cid, payload)
                continue
            if "quote" in r and norm(r["quote"]) not in norm(payload):
                err("L2", cid, "quote 不是该单元原文子串：%s %s ← %s"
                    % (r.get("source_id"), r.get("locator"), r.get("quote")[:40]))

    # T1 演替一致性（2026-11-12）：superseded_by 指向有效、且新旧真的分段
    check_supersession(proj.claims, err)

    # 页面层（M3）：P1 缺失 / P2 frontmatter / P3 主张断链 / P4 漏引 / P5 孤立页 / P6 链接断链 / P7 unknown 未入待确认区
    warnings = []
    pages_report = check_pages(proj, errors, warnings)
    pages_missing = [r["page"] for r in pages_report if not r.get("exists")]

    struct_err = [e for e in errors if e["code"][:1] in ("S", "P", "T")]
    loc_err = [e for e in errors if e["code"].startswith("L")]
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": proj.root,
        "counts": {
            "claims": len(proj.claims),
            "sources": len(proj.sources),
            "pages": len(PAGES),
            "pages_built": len(PAGES) - len(pages_missing),
            "matrix_cells": len(matrix_cells),
        },
        "structure_errors": struct_err,
        "locator_errors": loc_err,
        "warnings": warnings,
        "pages": pages_report,
        "matrix": [{"project_type": t, "mode": m, "claim_id": matrix_cells.get((t, m)),
                    "verdict": ((proj.claim_by_id.get(matrix_cells.get((t, m))) or {}).get("matrix") or {}).get("verdict")}
                   for t in TYPES for m in MODES],
        "exit_code": 1 if struct_err else (2 if loc_err else 0),
    }
    if eval_only_used:
        report["notes"] = ["eval_only 来源被引用，评测独立性受影响：%s" % sorted(set(eval_only_used))]
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_path = os.path.join(proj.root, args.out.replace("/", os.sep))
        os.makedirs(os.path.dirname(out_path) or proj.root, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)
    return report["exit_code"]


# ---------------- search（BM25，2026-11-12 换引擎） ----------------
def _raw_unit_sections(proj, src):
    """归一化单元 → section。办法正文按章，其余整份来源为一节。"""
    out = {}
    tp = src.get("text_path")
    if not tp:
        return out
    path = os.path.join(proj.root, tp.replace("\\", os.sep))
    if not os.path.exists(path):
        return out
    base = os.path.basename(path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if "article_no" in d:
                out["第%d条" % d["article_no"]] = "第%s章" % (d.get("chapter_no") or "")
            elif "q_no" in d:
                out["问%d" % d["q_no"]] = "问答"
            elif "para_no" in d:
                if "notice" in base:
                    out["通知段%d" % d["para_no"]] = "通知"
                elif "explain" in base:
                    out["第%d段" % d["para_no"]] = "解读"
                else:
                    out["段%d" % d["para_no"]] = ""
    return out


_CN_DIGITS = "零一二三四五六七八九"


def num_to_cn(n):
    """条号转中文（1..99）：12 → 十二，20 → 二十，43 → 四十三。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def build_wiki_docs(proj, mode="wiki"):
    """构造检索语料：[{id, kind, text, section, source_id/page_path, claim_ids}]。"""
    docs = []
    if mode == "wiki":
        wiki_dir = os.path.join(proj.root, "wiki")
        if os.path.isdir(wiki_dir):
            for base, _, files in os.walk(wiki_dir):
                for fn in sorted(files):
                    if not fn.endswith(".md"):
                        continue
                    p = os.path.join(base, fn)
                    rel = os.path.relpath(p, proj.root).replace(os.sep, "/")
                    docs.append({"id": rel, "kind": "page", "page_path": rel, "section": rel,
                                 "text": read_text(p), "claim_ids": CLAIM_REF_RE.findall(read_text(p))})
        for c in proj.claims:
            docs.append({"id": c.get("claim_id"), "kind": "claim",
                         "page_path": c.get("page_path"), "section": c.get("page_path"),
                         "text": c.get("claim_text") or "", "claim_ids": [c.get("claim_id")]})
    else:
        for src in proj.sources:
            secs = _raw_unit_sections(proj, src)
            for key, text in proj.units(src["source_id"]).items():
                # 条号补中文写法：「第12条」与用户常问的「第十二条」在分词后是不同 token
                alias = ""
                m = re.match(r"^第(\d+)条$", key)
                if m:
                    alias = " 第%s条" % num_to_cn(m.group(1))
                elif key.startswith("问") and key[1:].isdigit():
                    alias = " 问题%s" % num_to_cn(key[1:])
                docs.append({"id": "%s#%s" % (src["source_id"], key), "kind": "raw",
                             "source_id": src["source_id"], "section": secs.get(key, ""),
                             "text": text, "index_text": "%s%s %s" % (key, alias, text),
                             "claim_ids": []})
    return docs


def cmd_search(proj, args):
    proj.load_sources()
    proj.load_claims()
    docs = build_wiki_docs(proj, args.mode)
    index = wr.build_index(docs)
    hits = index.scores(args.query, top_k=args.top_k)
    matched = len(index.scores(args.query))
    payload = {
        "query": args.query,
        "mode": args.mode,
        "engine": "BM25(k1=1.5,b=0.75)；分词=%s" % ("jieba" if wr._HAS_JIEBA else "字符二元组"),
        "total_matched": matched,
        "results": [{"score": round(s, 4), "kind": d["kind"], "pointer": d["id"],
                     "claim_ids": d.get("claim_ids"), "snippet": (d.get("text") or "")[:160]}
                    for s, d in hits],
    }
    if args.mode == "wiki" and not os.path.isdir(os.path.join(proj.root, "wiki")):
        payload["note"] = "wiki/ 尚未建立，本次检索范围为主张层 claims"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ---------------- show ----------------
def cmd_show(proj, args):
    proj.load_sources()
    proj.load_claims()
    c = proj.claim_by_id.get(args.claim_id)
    if not c:
        print(json.dumps({"error": "未找到主张：%s" % args.claim_id}, ensure_ascii=False, indent=2))
        return 2
    refs = []
    for r in c.get("source_refs") or []:
        ok, payload = proj.resolve(r.get("source_id"), r.get("locator"))
        refs.append({
            "source_id": r.get("source_id"),
            "locator": r.get("locator"),
            "quote": r.get("quote"),
            "resolved": ok,
            "source_text": (payload if ok else None),
            "quote_matched": bool(ok and norm(r.get("quote")) in norm(payload)),
        })
    payload = {
        "claim_id": c.get("claim_id"),
        "page_path": c.get("page_path"),
        "claim_text": c.get("claim_text"),
        "claim_status": c.get("claim_status"),
        "knowledge_kind": c.get("knowledge_kind"),
        "conditions": c.get("conditions"),
        "temporal_scope": c.get("temporal_scope"),
        "matrix": c.get("matrix"),
        "derivation": c.get("derivation"),
        "source_refs": refs,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(r["resolved"] and r["quote_matched"] for r in refs) else 2


# ---------------- gen-pages（来源目录页） ----------------
ROLE_NOTE = {
    "build": "可作为主张依据",
    "corroborate": "仅交叉校验，不作引用首选",
    "eval_only": "评估专用，禁止作为主张依据（check 强制）",
}
LOCATOR_BY_KIND = {
    "measure_fulltext": ("`第N条` / `全文结构`", "`第4条`、`全文结构`"),
    "official_faq": ("`问N`", "`问71`"),
    "official_explain": ("`第N段`", "`第5段`"),
    "notice": ("`通知段N`", "`通知段6`"),
    "reprint_fulltext": ("`段N`", "`段12`"),
    "consult_case": ("不可引用", "—（该角色禁止作为主张依据）"),
}


def build_source_page(proj, today):
    L = []
    L.append("---")
    L.append("type: reference")
    L.append("page: 来源目录")
    L.append("updated: %s" % today)
    L.append("generated_by: tools/wiki_tool.py gen-pages")
    L.append("sources: %d" % len(proj.sources))
    L.append("---")
    L.append("")
    L.append("# 来源目录")
    L.append("")
    L.append("> 本页由 `python tools\\wiki_tool.py gen-pages` 从 `sources.jsonl` 生成，**不要手改**：")
    L.append("> 要改来源清单请改 `sources.jsonl` 后重新生成，否则 `check` 的 P2/P4 会不一致。")
    L.append("> 本页只描述「有哪些来源、能不能引用、怎么定位」，不含政策结论。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、角色约定")
    L.append("")
    L.append("| 角色 | 条数 | 能否作为主张依据 |")
    L.append("|---|---|---|")
    for role in ("build", "corroborate", "eval_only"):
        n = len([s for s in proj.sources if s.get("role") == role])
        L.append("| `%s` | %d | %s |" % (role, n, ROLE_NOTE[role]))
    L.append("")
    L.append("`eval_only` 的来源（公开咨询答复）**只用于评测**，不得作为任何主张的依据——")
    L.append("评测材料若同时充当建设依据，题目与答案同源，评测失效（`check` 的 S9 强制）。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 二、来源清单")
    L.append("")
    L.append("| source_id | 标题 | 角色 | 类型 | 文号 | 发布 | 取数 | 字节 | sha256（前 16） |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for s in proj.sources:
        L.append("| `%s` | %s | `%s` | `%s` | %s | %s | %s | %s | `%s` |" % (
            s.get("source_id"), s.get("title"), s.get("role"), s.get("kind"),
            s.get("document_number") or "—", (s.get("published_at") or "")[:10],
            (s.get("retrieved_at") or "")[:10], s.get("raw_bytes"),
            (s.get("raw_sha256") or "")[:16]))
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 三、引用定位方式（locator 语法）")
    L.append("")
    L.append("| 来源类型 kind | locator 语法 | 例 |")
    L.append("|---|---|---|")
    for kind, (grammar, ex) in LOCATOR_BY_KIND.items():
        L.append("| `%s` | %s | %s |" % (kind, grammar, ex))
    L.append("")
    L.append("`locator` 是相对于某个 `source_id` 的单元键，不允许自由文本；`quote` 需为对应单元原文的子串")
    L.append("（去空白后比对）。详见 `04-主张层字段契约.md` §五。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 四、逐条详情")
    L.append("")
    for s in proj.sources:
        L.append("### %s" % s.get("source_id"))
        L.append("")
        L.append("- 标题：%s" % s.get("title"))
        L.append("- 发布机构：%s" % (s.get("issuer") or "—"))
        L.append("- 文号：%s" % (s.get("document_number") or "—"))
        L.append("- 发布 / 取数：%s / %s" % ((s.get("published_at") or "")[:10],
                                            (s.get("retrieved_at") or "")[:10]))
        L.append("- 角色：「%s」%s" % (s.get("role"), ROLE_NOTE.get(s.get("role"), "")))
        L.append("- 原文地址：%s" % s.get("canonical_url"))
        L.append("- 本地原件：`%s`（%s 字节，sha256 `%s`）" % (
            (s.get("raw_path") or "").replace("\\", "/"), s.get("raw_bytes"), s.get("raw_sha256")))
        L.append("- 可定位文本：`%s`（解析状态 `%s`）" % (
            (s.get("text_path") or "").replace("\\", "/"), s.get("parse_status")))
        L.append("- 说明：%s" % (s.get("note") or "—"))
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 五、复跑与校验")
    L.append("")
    L.append("```powershell")
    L.append("$env:PYTHONIOENCODING='utf-8'")
    L.append("python tools\\a6_verify_sources.py      # 校验 raw 存在 + sha256 一致 + 题参照对齐")
    L.append("python tools\\wiki_tool.py gen-pages     # 重新生成本页并回读校验")
    L.append("python tools\\wiki_tool.py check         # 全量结构与引用定位检查")
    L.append("```")
    L.append("")
    L.append("## 六、相关页")
    L.append("")
    L.append("- [项目分类](项目分类.md)")
    L.append("- [上网模式](上网模式.md)")
    L.append("- [备案与时间衔接](备案与时间衔接.md)")
    L.append("- [概念辨析](概念辨析.md)")
    L.append("")
    return "\n".join(L)


def cmd_gen_pages(proj, args):
    proj.load_sources()
    today = args.date or datetime.now().astimezone().strftime("%Y-%m-%d")
    path = os.path.join(proj.root, "wiki", "topics", "来源目录.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = build_source_page(proj, today)
    # newline="\n"：避免 Windows 下把 \n 翻成 \r\n，保证报告里的 bytes/sha256 与磁盘字节一致
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    back = read_text(path)
    ok = back == text and all(s.get("source_id") in back for s in proj.sources)
    payload = {
        "out": os.path.relpath(path, proj.root).replace(os.sep, "/"),
        "sources": len(proj.sources),
        "bytes": len(back.encode("utf-8")),
        "sha256": hashlib.sha256(back.encode("utf-8")).hexdigest(),
        "readback_ok": ok,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


# ---------------- export ----------------
def _cond_text(cond):
    parts = []
    pt = cond.get("project_type")
    if pt:
        parts.append("项目类型=%s" % ("、".join(pt) if isinstance(pt, list) else pt))
    if cond.get("region"):
        parts.append("地区口径=%s" % cond["region"])
    sub = cond.get("subject")
    if sub:
        parts.append("主体=%s" % ("、".join(sub) if isinstance(sub, list) else sub))
    if cond.get("exception"):
        parts.append("例外=%s" % cond["exception"])
    return "；".join(parts)


def _page_body(text):
    """去掉 frontmatter，返回正文。"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).strip("\n")
    return text.strip("\n")


def _page_ts(own):
    """页面级 temporal_scope：取本页主张的时点极值，端点交由各 claim chunk 承载。"""
    ts_list = [c.get("temporal_scope") or {} for c in own]
    asof = max([t.get("policy_as_of") for t in ts_list if t.get("policy_as_of")] or [None])
    retrieved = max([t.get("retrieved_at") for t in ts_list if t.get("retrieved_at")] or [None])
    effs = sorted({t.get("current_effectiveness") for t in ts_list if t.get("current_effectiveness")})
    eff = effs[0] if len(effs) == 1 else "not_checked"
    note = "页面级汇总：policy_as_of 取本页主张的最大值；端点与边界包含关系见各 claim chunk（页面不承载单一边界）"
    if len(effs) > 1:
        note += "；本页含多种效力状态（%s），统一按 not_checked 保守给出" % "/".join(effs)
    return {"policy_as_of": asof, "retrieved_at": retrieved, "start": None, "end": None,
            "boundary_inclusion": "none", "current_effectiveness": eff, "note": note}


def _page_chunk(proj, page_rel):
    """页面级 chunk；页面不存在或 0 条主张时返回 (None, 原因)。"""
    abs_p = os.path.join(proj.root, page_rel.replace("/", os.sep))
    if not os.path.exists(abs_p):
        return None, "页面不存在"
    own = sorted([c for c in proj.claims if c.get("page_path") == page_rel],
                 key=lambda c: str(c.get("claim_id")))
    if not own:
        return None, "本页 0 条主张，不导出页面 chunk"
    text = read_text(abs_p)
    meta = parse_front(text) or {}
    refs, seen = [], set()
    for c in own:
        for r in c.get("source_refs") or []:
            if not isinstance(r, dict) or "locator" not in r:
                continue
            key = (r.get("source_id"), r.get("locator"))
            if key in seen:
                continue
            seen.add(key)
            refs.append({"source_id": r["source_id"], "locator": r["locator"]})
    counts = {}
    for c in own:
        s = c.get("claim_status")
        counts[s] = counts.get(s, 0) + 1
    status = "unknown" if counts.get("unknown") else ("conflict" if counts.get("conflict") else "supported")
    slug = os.path.splitext(os.path.basename(page_rel))[0]
    head = "【页面：%s】类型 %s，共 %d 条主张（本页为二次整理，非官方原文；unknown 部分见页面「待确认」区）" % (
        slug, meta.get("type"), len(own))
    return {
        "chunk_id": "PG-%s" % slug,
        "chunk_kind": "page",
        "page_path": page_rel,
        "page_type": meta.get("type"),
        "text": head + "\n\n" + _page_body(text),
        "claim_ids": [c.get("claim_id") for c in own],
        "source_refs": refs,
        "temporal_scope": _page_ts(own),
        "claim_status": status,
        "claim_status_counts": counts,
        "content_kind": "derived_wiki",
    }, None


def cmd_export(proj, args):
    proj.load_sources()
    proj.load_claims()
    out_path = os.path.join(proj.root, args.out.replace("/", os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    chunks = []
    for c in sorted(proj.claims, key=lambda x: str(x.get("claim_id"))):
        ts = c.get("temporal_scope") or {}
        refs, seen_ref = [], set()
        for r in c.get("source_refs") or []:
            if not isinstance(r, dict) or "locator" not in r:
                continue
            key = (r["source_id"], r["locator"])
            if key in seen_ref:
                continue
            seen_ref.add(key)
            refs.append({"source_id": r["source_id"], "locator": r["locator"]})
        # 端点角色（04 §七）：事件截止日不是规则失效日，导出文本要写明，免得下游再拿它过滤版本
        ev_sides = [s for s in ("start", "end")
                    if (ts.get("%s_role" % s) or "rule_validity") == "event_deadline"]
        if ts.get("start") or ts.get("end"):
            window = "%s~%s（含端点：%s）" % (ts.get("start") or "开放", ts.get("end") or "开放",
                                         ts.get("boundary_inclusion"))
            if ev_sides:
                window += "；%s 是项目事件截止日，不是规则失效日" % "、".join(
                    str(ts.get(s)) for s in ev_sides)
        else:
            window = "未设端点，按 policy_as_of 时点判断"
        text = "【%s】%s｜适用：%s｜依据：%s｜时效：policy_as_of=%s；%s；效力=%s" % (
            c.get("claim_id"), c.get("claim_text"), _cond_text(c.get("conditions") or {}),
            "；".join("%s %s" % (r["source_id"], r["locator"]) for r in refs),
            ts.get("policy_as_of"), window, ts.get("current_effectiveness"))
        chunks.append({
            "chunk_id": "CH-%s" % c.get("claim_id"),
            "chunk_kind": "claim",
            "page_path": c.get("page_path"),
            "text": text,
            "claim_ids": [c.get("claim_id")],
            "source_refs": refs,
            "conditions": c.get("conditions"),
            "temporal_scope": ts,
            "claim_status": c.get("claim_status"),
            "knowledge_kind": c.get("knowledge_kind"),
            "content_kind": "derived_wiki",
        })
    claim_chunk_count = len(chunks)
    # 页面级 chunk（M5）：一条知识页一个 chunk，来源清单页（0 主张）不导出
    page_skipped = []
    for p in PAGES:
        ch, why = _page_chunk(proj, p)
        if ch:
            chunks.append(ch)
        else:
            page_skipped.append({"page": p, "reason": why})

    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for ch in chunks:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    # 回读校验：可解析 + 条数分组正确 + 必填字段齐 + page chunk 的 claim_ids 可反查
    with open(out_path, encoding="utf-8") as fh:
        back = [json.loads(x) for x in fh if x.strip()]
    claim_chunks = [c for c in back if c.get("chunk_kind") == "claim"]
    page_chunks = [c for c in back if c.get("chunk_kind") == "page"]
    required = ("chunk_id", "text", "claim_ids", "source_refs", "temporal_scope",
                "claim_status", "content_kind")
    ok = (
        len(claim_chunks) == claim_chunk_count == len(proj.claims)
        and all(ch.get("content_kind") == "derived_wiki" for ch in back)
        and all(c.get("chunk_id") for c in back)
        and all(all(k in c for k in required) for c in back)
        and all(c.get("claim_ids") and c.get("source_refs") for c in page_chunks)
        and all(all(cid in proj.claim_by_id for cid in c.get("claim_ids") or []) for c in page_chunks)
    )
    payload = {
        "out": os.path.relpath(out_path, proj.root).replace(os.sep, "/"),
        "chunks": len(back),
        "claim_chunks": len(claim_chunks),
        "page_chunks": len(page_chunks),
        "page_chunk_ids": [c.get("chunk_id") for c in page_chunks],
        "page_skipped": page_skipped,
        "readback_ok": ok,
        "fields": sorted(back[0].keys()) if back else [],
        "page_fields": sorted(page_chunks[0].keys()) if page_chunks else [],
        "bytes": os.path.getsize(out_path),
        "sha256": hashlib.sha256(open(out_path, "rb").read()).hexdigest(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="新能源知识 Wiki 自检与检索工具")
    ap.add_argument("--root", default=default_root, help="项目根目录（默认脚本上级）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("check", help="结构与引用定位检查")
    p.add_argument("--out", default=None, help="报告输出路径")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("search", help="词法检索")
    p.add_argument("--query", required=True)
    p.add_argument("--mode", default="wiki", choices=["wiki", "raw"])
    p.add_argument("--top-k", dest="top_k", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="按 claim_id 读回主张与原文")
    p.add_argument("--claim-id", dest="claim_id", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", help="导出 wiki_chunks.jsonl")
    p.add_argument("--out", default="exports/wiki_chunks.jsonl")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("gen-pages", help="重新生成来源目录页（wiki/topics/来源目录.md）")
    p.add_argument("--date", default=None, help="覆盖 frontmatter 的 updated 日期（默认今天）")
    p.set_defaults(func=cmd_gen_pages)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    proj = Project(args.root)
    return args.func(proj, args)


if __name__ == "__main__":
    sys.exit(main())
