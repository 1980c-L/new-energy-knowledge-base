/* ============================================================
   轻量 Markdown 渲染器 + 回答结构解析（无依赖）
   渲染只覆盖后端模板实际产出的语法。
   解析把回答模板拆成 状态 / 结论 / 证据 / 页脚，
   模板不匹配时返回 null，由调用方回退到纯 markdown。
   ============================================================ */

(function (global) {
  "use strict";

  // 后端契约：8 个状态 key，可改样式，不可删改名。
  const STATUS_BADGE = {
    materials_ready: "",
    supported: "",
    insufficient: "warn",
    out_of_scope: "warn",
    no_match: "warn",
    official_unclear: "warn",
    conflict: "danger",
    source_error: "danger",
  };

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function inline(text) {
    let out = escapeHtml(text);

    const codes = [];
    out = out.replace(/`([^`]+)`/g, function (_, code) {
      const token = "\u0000C" + codes.length + "\u0000";
      const key = code.trim();
      if (Object.prototype.hasOwnProperty.call(STATUS_BADGE, key)) {
        const cls = STATUS_BADGE[key] ? " " + STATUS_BADGE[key] : "";
        codes.push('<span class="badge' + cls + '">' + key + "</span>");
      } else {
        codes.push("<code>" + key + "</code>");
      }
      return token;
    });

    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, label, href) {
      const safe = /^https?:\/\//i.test(href) ? href : "#";
      return '<a href="' + safe + '" target="_blank" rel="noreferrer">' + label + "</a>";
    });

    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    out = out.replace(/\u0000C(\d+)\u0000/g, function (_, i) {
      return codes[Number(i)];
    });
    return out;
  }

  function render(src) {
    if (!src) return "";
    const lines = String(src).replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (!line.trim()) { i++; continue; }

      if (/^\s*-{3,}\s*$/.test(line)) { html.push("<hr>"); i++; continue; }

      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        html.push("<h" + h[1].length + ">" + inline(h[2]) + "</h" + h[1].length + ">");
        i++;
        continue;
      }

      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        html.push("<blockquote><p>" + inline(buf.join(" ")) + "</p></blockquote>");
        continue;
      }

      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
          i++;
        }
        html.push("<ol>" + items.map(function (t) { return "<li>" + inline(t) + "</li>"; }).join("") + "</ol>");
        continue;
      }

      if (/^\s*[-*]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
          i++;
        }
        html.push("<ul>" + items.map(function (t) { return "<li>" + inline(t) + "</li>"; }).join("") + "</ul>");
        continue;
      }

      const buf = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^\s*([-*]\s+|\d+\.\s+|>\s?|#{1,4}\s+|(-{3,}))/.test(lines[i])
      ) {
        buf.push(lines[i]);
        i++;
      }
      if (buf.length) html.push("<p>" + inline(buf.join(" ")) + "</p>");
      else i++;
    }

    return html.join("");
  }

  /* ---------------- 回答模板解析 ---------------- */

  function toneOf(code) {
    const v = STATUS_BADGE[code];
    return v === "danger" ? "danger" : v === "warn" ? "warn" : "ok";
  }

  function parseAnswer(src) {
    if (!src) return null;
    const text = String(src).replace(/\r\n/g, "\n");
    const lines = text.split("\n");

    let firstIdx = -1;
    for (let k = 0; k < lines.length; k++) {
      if (lines[k].trim()) { firstIdx = k; break; }
    }
    if (firstIdx < 0) return null;
    const consumed = new Set([firstIdx]);
    const notes = [];
    for (let k = firstIdx + 1; k < lines.length; k++) {
      const note = lines[k].trim().match(/^\*([^*].*?)\*$/);
      if (note && !/^以上为/.test(note[1])) {
        notes.push(note[1]);
        consumed.add(k);
      }
    }

    const head = lines[firstIdx].trim();
    const sm = head.match(/^(.*?)\s*·\s*`?([a-z_]+)`?\s*$/);
    if (!sm) return null;
    const code = sm[2];
    if (!Object.prototype.hasOwnProperty.call(STATUS_BADGE, code)) return null;

    const label = sm[1].replace(/\*\*/g, "").replace(/`/g, "").trim();
    if (!label) return null;

    let version = "";
    let hit = 0;
    let shown = 0;
    for (let k = 0; k < lines.length; k++) {
      if (/资料版本/.test(lines[k])) {
        consumed.add(k);
        const vm = lines[k].match(/资料版本\s*`?([A-Za-z0-9._-]+)`?/);
        if (vm) version = vm[1];
        const hm = lines[k].match(/命中\s*(\d+)\s*段/);
        if (hm) hit = Number(hm[1]);
        const shm = lines[k].match(/展示\s*(?:前\s*)?(\d+)\s*段/);
        if (shm) shown = Number(shm[1]);
        break;
      }
    }

    // 结论：最接近的一条
    let conclusion = "";
    const cIdx = lines.findIndex(function (l) { return /最接近的一条/.test(l); });
    if (cIdx >= 0) {
      consumed.add(cIdx);
      for (let k = cIdx + 1; k < lines.length; k++) {
        const l = lines[k].trim();
        if (!l) continue;
        if (/证据清单/.test(l)) break;
        if (/^-{3,}$/.test(l)) break;
        if (/^\*\*/.test(l)) continue;
        conclusion = l.replace(/^>\s?/, "").replace(/^\*\*|\*\*$/g, "");
        consumed.add(k);
        break;
      }
    }

    // 证据索引：[n] ID · 定位｜类型
    const typeMap = {};
    const refs = [];
    const eIdx = lines.findIndex(function (l) { return /证据清单/.test(l); });
    if (eIdx >= 0) {
      consumed.add(eIdx);
      for (let k = eIdx + 1; k < lines.length; k++) {
        const l = lines[k].trim();
        if (/^-{3,}$/.test(l) || /^\*\*\d+\./.test(l)) break;
        const m = l.match(/^-\s+\[(\d+)\]\s+(.+)$/);
        if (!m) continue;
        consumed.add(k);
        const parts = m[2].split("｜");
        const left = parts[0].split("·");
        const id = (left[0] || "").trim();
        const loc = (left[1] || "").trim();
        const type = (parts[1] || "").trim();
        const key = id + "|" + loc;
        if (type) typeMap[key] = type;
        refs.push({ n: Number(m[1]), id: id, loc: loc, type: type });
      }
    }

    // 证据明细：**N. 标题** / ID · 定位 / > 摘录
    const evidence = [];
    for (let k = 0; k < lines.length; k++) {
      const tm = lines[k].trim().match(/^\*\*(\d+)\.\s*(.+?)\*\*$/);
      if (!tm) continue;
      consumed.add(k);
      const item = { n: Number(tm[1]), title: tm[2].trim(), id: "", loc: "", type: "", excerpt: "" };
      let j = k + 1;
      for (; j < lines.length; j++) {
        const l = lines[j].trim();
        if (!l) continue;
        if (/^-{3,}$/.test(l)) break;
        if (/^>/.test(l)) break;
        const idm = l.match(/`([^`]+)`/);
        if (idm) item.id = idm[1].trim();
        const lm = l.match(/定位\s*([^·｜|]+)/);
        if (lm) item.loc = lm[1].trim();
        if (idm || lm) consumed.add(j);
        j++;
        break;
      }
      const qu = [];
      for (; j < lines.length; j++) {
        const l = lines[j].trim();
        if (!l) continue;
        if (!/^>/.test(l)) break;
        consumed.add(j);
        qu.push(l.replace(/^>\s?/, ""));
      }
      item.excerpt = qu.join(" ").trim();
      item.type = typeMap[item.id + "|" + item.loc] || "";
      if (item.title && (item.excerpt || item.id)) evidence.push(item);
      k = j;
    }

    let footer = "";
    for (let k = lines.length - 1; k >= 0; k--) {
      const l = lines[k].trim();
      if (!l) continue;
      const clean = l.replace(/^[*_]+|[*_]+$/g, "");
      if (/^以上为/.test(clean)) {
        footer = clean;
        consumed.add(k);
      }
      break;
    }

    // 未识别的实质正文必须完整回退，不能在卡片化过程中静默丢弃。
    for (let k = 0; k < lines.length; k++) {
      const line = lines[k].trim();
      if (line && !/^-{3,}$/.test(line) && !consumed.has(k)) return null;
    }

    return {
      label: label,
      code: code,
      tone: toneOf(code),
      version: version,
      hit: hit,
      shown: shown,
      conclusion: conclusion,
      evidence: evidence,
      footer: footer,
      refs: refs,
      notes: notes,
    };
  }

  global.MiniMarkdown = { render: render, parseAnswer: parseAnswer, STATUS_BADGE: STATUS_BADGE };
})(window);
