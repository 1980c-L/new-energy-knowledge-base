/* ============================================================
   新能源知识库 · 对话前端 — 核心逻辑
   接口：Dify WebApp API（同源 /api，仅带 X-App-Code，无密钥）
   ============================================================ */

(function () {
  "use strict";

  const CONFIG = {
    apiBase: "",
    appCode: "YOUR_DIFY_APP_CODE",
  };

  const el = {
    app: document.getElementById("app"),
    sidebar: document.getElementById("sidebar"),
    convList: document.getElementById("convList"),
    thread: document.getElementById("thread"),
    welcome: document.getElementById("welcome"),
    welcomeTitle: document.getElementById("welcomeTitle"),
    welcomeSub: document.getElementById("welcomeSub"),
    suggestions: document.getElementById("suggestions"),
    input: document.getElementById("input"),
    composer: document.getElementById("composer"),
    sendBtn: document.getElementById("sendBtn"),
    stopBtn: document.getElementById("stopBtn"),
    newChatBtn: document.getElementById("newChatBtn"),
    collapseBtn: document.getElementById("collapseBtn"),
    openSidebarBtn: document.getElementById("openSidebarBtn"),
    topbarTitle: document.getElementById("topbarTitle"),
    statusPill: document.getElementById("statusPill"),
    versionHint: document.getElementById("versionHint"),
    srStatus: document.getElementById("srStatus"),
  };

  const state = {
    conversationId: "",
    conversations: [],
    sending: false,
    lastMessageId: "",
    passport: "",
    userId: "",
    abort: null,
    lastQuestion: "",
    viewId: 0,
    historyLoading: false,
  };

  /* ---------------- 访客身份 ---------------- */

  const LS_USER = "energy176.user_id";

  function ensureUserId() {
    let id = localStorage.getItem(LS_USER);
    if (!id) {
      id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
      localStorage.setItem(LS_USER, id);
    }
    state.userId = id;
  }

  async function ensurePassport(force) {
    if (state.passport && !force) return state.passport;
    const res = await fetch(
      CONFIG.apiBase + "/api/passport?user_id=" + encodeURIComponent(state.userId),
      { headers: { "Content-Type": "application/json", "X-App-Code": CONFIG.appCode } }
    );
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    state.passport = data.access_token || "";
    return state.passport;
  }

  /* ---------------- HTTP ---------------- */

  function headers(extra) {
    const h = { "Content-Type": "application/json", "X-App-Code": CONFIG.appCode };
    if (state.passport) h["X-App-Passport"] = state.passport;
    return Object.assign(h, extra || {});
  }

  async function api(path, options) {
    let res = await fetch(CONFIG.apiBase + path, Object.assign({ headers: headers() }, options || {}));
    if (res.status === 401) {
      await ensurePassport(true);
      res = await fetch(CONFIG.apiBase + path, Object.assign({ headers: headers() }, options || {}));
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const type = res.headers.get("content-type") || "";
    return type.indexOf("json") >= 0 ? res.json() : {};
  }

  /* ---------------- 侧栏会话 ---------------- */

  function dayKey(createdAt) {
    const t = createdAt ? new Date(createdAt * 1000) : null;
    if (!t || isNaN(t.getTime())) return "更早";
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    const diff = Math.round((startToday - day) / 86400000);
    if (diff <= 0) return "今天";
    if (diff === 1) return "昨天";
    if (diff < 7) return "本周";
    return "更早";
  }

  async function loadConversations() {
    try {
      const pinned = await api("/api/conversations?limit=100&pinned=true");
      const normal = await api("/api/conversations?limit=100&pinned=false");
      state.conversations = [].concat(pinned.data || [], normal.data || []);
      renderConversations();
    } catch (e) {
      /* 未登录或站点关闭时静默降级 */
    }
  }

  function closeMenus() {
    document.querySelectorAll(".conv-menu").forEach(function (m) { m.remove(); });
    document.querySelectorAll(".conv-item-wrap.menu-open").forEach(function (w) { w.classList.remove("menu-open"); });
  }

  function openMenu(wrap, conv) {
    closeMenus();
    wrap.classList.add("menu-open");
    const menu = document.createElement("div");
    menu.className = "conv-menu";

    const rename = document.createElement("button");
    rename.type = "button";
    rename.textContent = "重命名";
    rename.addEventListener("click", function (e) {
      e.stopPropagation();
      closeMenus();
      renameConversation(conv);
    });

    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "删除";
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      closeMenus();
      deleteConversation(conv);
    });

    menu.appendChild(rename);
    menu.appendChild(del);
    wrap.appendChild(menu);

    setTimeout(function () {
      document.addEventListener("click", closeMenus, { once: true });
    }, 0);
  }

  async function renameConversation(conv) {
    const next = window.prompt("重命名对话", conv.name || "");
    if (next === null || !next.trim() || next.trim() === conv.name) return;
    try {
      await api("/api/conversations/" + encodeURIComponent(conv.id) + "/name", {
        method: "POST",
        body: JSON.stringify({ name: next.trim(), auto_generate: false }),
      });
      await loadConversations();
    } catch (e) {
      toast("重命名失败：" + e.message);
    }
  }

  async function deleteConversation(conv) {
    if (!window.confirm("删除对话「" + (conv.name || "未命名对话") + "」？此操作不可撤销。")) return;
    try {
      await api("/api/conversations/" + encodeURIComponent(conv.id), { method: "DELETE" });
      if (state.conversationId === conv.id) newChat();
      await loadConversations();
    } catch (e) {
      toast("删除失败：" + e.message);
    }
  }

  function renderConversations() {
    el.convList.innerHTML = "";
    if (!state.conversations.length) {
      const empty = document.createElement("p");
      empty.className = "conv-empty";
      empty.textContent = "还没有历史对话。";
      el.convList.appendChild(empty);
      return;
    }

    const order = ["今天", "昨天", "本周", "更早"];
    const groups = {};
    for (const conv of state.conversations) {
      const key = dayKey(conv.created_at);
      (groups[key] = groups[key] || []).push(conv);
    }

    for (const key of order) {
      const list = groups[key];
      if (!list || !list.length) continue;

      const title = document.createElement("div");
      title.className = "conv-group-title";
      title.textContent = key;
      el.convList.appendChild(title);

      for (const conv of list) {
        const wrap = document.createElement("div");
        wrap.className = "conv-item-wrap";

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");
        btn.title = conv.name || "";
        const label = document.createElement("span");
        label.textContent = conv.name || "未命名对话";
        btn.appendChild(label);
        btn.addEventListener("click", function () { openConversation(conv.id); });

        const more = document.createElement("button");
        more.type = "button";
        more.className = "conv-more";
        more.title = "更多";
        more.setAttribute("aria-label", "更多操作");
        more.textContent = "⋯";
        more.addEventListener("click", function (e) {
          e.stopPropagation();
          openMenu(wrap, conv);
        });

        wrap.appendChild(btn);
        wrap.appendChild(more);
        el.convList.appendChild(wrap);
      }
    }
  }

  async function openConversation(id) {
    detachView();
    const viewId = state.viewId;
    state.historyLoading = true;
    el.sendBtn.disabled = true;
    state.conversationId = id;
    state.lastMessageId = "";
    renderConversations();
    clearThread();
    try {
      const data = await api("/api/messages?conversation_id=" + encodeURIComponent(id) + "&limit=30&last_id=");
      if (viewId !== state.viewId) return;
      const list = (data.data || []).slice().reverse();
      for (const m of list) {
        if (m.query) appendTurn(m.answer || "", m.query);
      }
      if (list.length) state.lastMessageId = list[list.length - 1].id || "";
      if (!list.length) showWelcome();
    } catch (e) {
      if (viewId !== state.viewId) return;
      toast("读取历史失败：" + e.message);
    } finally {
      if (viewId === state.viewId) {
        state.historyLoading = false;
        el.sendBtn.disabled = false;
      }
    }
  }

  function detachView() {
    state.viewId += 1;
    if (state.abort) state.abort.abort();
    state.abort = null;
    state.sending = false;
    state.historyLoading = false;
    el.stopBtn.hidden = true;
    el.sendBtn.disabled = false;
  }

  function newChat() {
    detachView();
    state.conversationId = "";
    state.lastMessageId = "";
    clearThread();
    showWelcome();
    renderConversations();
    el.input.focus();
  }

  /* ---------------- 线程渲染 ---------------- */

  function clearThread() {
    el.thread.querySelectorAll(".msg").forEach(function (n) { n.remove(); });
    el.welcome.hidden = true;
  }

  function showWelcome() {
    el.welcome.hidden = false;
    el.thread.scrollTop = 0;
  }

  function scrollToBottom() {
    el.thread.scrollTop = el.thread.scrollHeight;
  }

  function makeTurn(question) {
    const article = document.createElement("article");
    article.className = "msg msg-assistant";

    const title = document.createElement("h2");
    title.className = "q-title";
    title.textContent = question;

    const row = document.createElement("div");
    row.className = "status-row";
    const dot = document.createElement("span");
    dot.className = "status-dot pending";
    const statusText = document.createElement("span");
    statusText.className = "status-text";
    statusText.textContent = "检索中";
    const actions = document.createElement("div");
    actions.className = "row-actions";
    row.appendChild(dot);
    row.appendChild(statusText);
    row.appendChild(actions);

    const body = document.createElement("div");
    body.className = "answer-body";

    article.appendChild(title);
    article.appendChild(row);
    article.appendChild(body);
    el.thread.appendChild(article);
    scrollToBottom();

    return { article: article, dot: dot, statusText: statusText, actions: actions, body: body };
  }

  function setStatus(turn, label, tone) {
    turn.statusText.textContent = label;
    turn.dot.className = "status-dot" + (tone && tone !== "ok" ? " " + tone : "");
  }

  function addActions(turn, question, raw) {
    turn.actions.innerHTML = "";

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "mini-btn";
    copy.textContent = "复制";
    copy.addEventListener("click", function () {
      navigator.clipboard.writeText(raw).then(function () {
        copy.textContent = "已复制";
        setTimeout(function () { copy.textContent = "复制"; }, 1500);
      });
    });

    const regen = document.createElement("button");
    regen.type = "button";
    regen.className = "mini-btn";
    regen.textContent = "重新查询";
    regen.addEventListener("click", function () {
      if (state.sending) return;
      send(question);
    });

    turn.actions.appendChild(copy);
    turn.actions.appendChild(regen);
  }

  function answerFoot(parsed) {
    const parts = [];
    if (parsed.footer) parts.push(parsed.footer);
    if (parsed.hit && parsed.shown && parsed.hit > parsed.shown) {
      parts.push("命中 " + parsed.hit + " 段，展示 " + parsed.shown + " 段");
    }
    return parts.join(" · ");
  }

  function structuredBody(parsed, raw) {
    const frag = document.createDocumentFragment();

    for (const note of parsed.notes || []) {
      const p = document.createElement("p");
      p.className = "answer-note";
      p.textContent = note;
      frag.appendChild(p);
    }

    if (parsed.conclusion) {
      const sec = document.createElement("section");
      sec.className = "conclusion";
      const label = document.createElement("div");
      label.className = "sec-label";
      label.textContent = parsed.code === "materials_ready" ? "最接近的材料" : "结论";
      const quote = document.createElement("blockquote");
      quote.className = "conclusion-quote";
      quote.textContent = parsed.conclusion;
      sec.appendChild(label);
      sec.appendChild(quote);
      frag.appendChild(sec);
    }

    if (parsed.refs.length) {
      const list = document.createElement("ul");
      list.className = "evidence-index";
      list.setAttribute("aria-label", "证据索引");
      for (const ref of parsed.refs) {
        const li = document.createElement("li");
        li.textContent = "[" + ref.n + "] " + ref.id + " · " + ref.loc + (ref.type ? "｜" + ref.type : "");
        list.appendChild(li);
      }
      frag.appendChild(list);
    }

    if (parsed.evidence.length) {
      const sec = document.createElement("section");
      sec.className = "evidence";
      const label = document.createElement("div");
      label.className = "sec-label";
      label.textContent = "证据";
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = parsed.evidence.length + " 条";
      label.appendChild(count);
      const excerptNote = document.createElement("p");
      excerptNote.className = "answer-note";
      excerptNote.textContent = "以下为原文节选；省略号表示未展示全文，命中材料不等于已得出完整结论。";

      const list = document.createElement("ol");
      list.className = "ev-list";
      for (const item of parsed.evidence) {
        const card = document.createElement("li");
        card.className = "ev-card";

        const head = document.createElement("div");
        head.className = "ev-head";
        const idx = document.createElement("span");
        idx.className = "ev-idx";
        idx.textContent = String(item.n).padStart(2, "0");
        const t = document.createElement("span");
        t.className = "ev-title";
        t.textContent = item.title;
        head.appendChild(idx);
        head.appendChild(t);

        if (item.type) {
          const type = document.createElement("span");
          type.className = "ev-type";
          type.textContent = item.type;
          head.appendChild(type);
        }

        const meta = document.createElement("div");
        meta.className = "ev-meta";
        if (item.id) {
          const s = document.createElement("span");
          const i = document.createElement("i");
          i.textContent = "文档编号";
          const c = document.createElement("code");
          c.textContent = item.id;
          s.appendChild(i);
          s.appendChild(c);
          meta.appendChild(s);
        }
        if (item.loc) {
          const s = document.createElement("span");
          const i = document.createElement("i");
          i.textContent = "条款定位";
          const b = document.createElement("b");
          b.textContent = item.loc;
          s.appendChild(i);
          s.appendChild(b);
          meta.appendChild(s);
        }

        card.appendChild(head);
        if (meta.childNodes.length) card.appendChild(meta);

        if (item.excerpt) {
          const q = document.createElement("blockquote");
          q.className = "ev-quote";
          q.textContent = item.excerpt;
          card.appendChild(q);
        }

        list.appendChild(card);
      }

      sec.appendChild(label);
      sec.appendChild(excerptNote);
      sec.appendChild(list);
      frag.appendChild(sec);
    }

    if (!frag.childNodes.length) return null;

    const foot = answerFoot(parsed);
    if (foot) {
      const p = document.createElement("p");
      p.className = "answer-foot";
      p.textContent = foot;
      frag.appendChild(p);
    }
    return frag;
  }

  function appendTurn(raw, question) {
    const turn = makeTurn(question);
    const parsed = window.MiniMarkdown.parseAnswer(raw);
    let frag = null;
    if (parsed) frag = structuredBody(parsed, raw);

    if (frag) {
      turn.body.appendChild(frag);
      setStatus(turn, parsed.label, parsed.tone);
      if (parsed.version) el.versionHint.textContent = "资料版本 " + parsed.version;
    } else {
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.innerHTML = window.MiniMarkdown.render(raw || "");
      turn.body.appendChild(bubble);
      setStatus(turn, "已生成", "ok");
    }

    addActions(turn, question, raw);
    return turn;
  }

  function toast(text) {
    el.statusPill.hidden = false;
    el.statusPill.className = "pill danger";
    el.statusPill.textContent = text;
    setTimeout(function () { el.statusPill.hidden = true; }, 4000);
  }

  /* ---------------- 发送与流式 ---------------- */

  async function send(question) {
    if (state.sending) return;
    if (state.historyLoading) {
      toast("正在读取这段对话，请稍候再发送。");
      return;
    }
    const q = (question || el.input.value || "").trim();
    if (!q) return;

    state.sending = true;
    state.lastQuestion = q;
    el.sendBtn.disabled = true;
    el.stopBtn.hidden = false;
    el.stopBtn.textContent = "停止生成";
    el.welcome.hidden = true;
    el.input.value = "";
    autoGrow();

    const turn = makeTurn(q);
    turn.body.innerHTML = '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>';

    let answer = "";
    let stopped = false;
    let completed = false;
    let failed = false;
    const viewId = state.viewId;
    let raf = 0;
    const controller = new AbortController();
    state.abort = controller;

    function paint() {
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = 0;
        if (viewId !== state.viewId) return;
        turn.body.innerHTML = '<div class="bubble">' + window.MiniMarkdown.render(answer) + "</div>";
        scrollToBottom();
      });
    }

    try {
      const payload = JSON.stringify({
        response_mode: "streaming",
        conversation_id: state.conversationId,
        files: [],
        query: q,
        inputs: {},
        parent_message_id: state.lastMessageId || undefined,
      });
      const doFetch = function () {
        return fetch(CONFIG.apiBase + "/api/chat-messages", {
          method: "POST",
          headers: headers(),
          body: payload,
          signal: controller.signal,
        });
      };

      if (!state.passport) await ensurePassport();
      if (viewId !== state.viewId) return;
      let res = await doFetch();
      if (res.status === 401) {
        await ensurePassport(true);
        res = await doFetch();
      }
      if (!res.ok || !res.body) throw new Error("HTTP " + res.status);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const read = await reader.read();
        if (read.done) break;
        buf += decoder.decode(read.value, { stream: true });
        buf = buf.replace(/\r\n/g, "\n");

        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          handleChunk(buf.slice(0, cut));
          buf = buf.slice(cut + 2);
        }
      }

      if (buf.trim()) handleChunk(buf);
      if (!completed) throw new Error("连接在回答完成前中断，请重新查询。");

      function handleChunk(chunk) {
        if (viewId !== state.viewId) return;
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let ev;
          try {
            ev = JSON.parse(raw);
          } catch (err) {
            continue;
          }
          if (ev.conversation_id) state.conversationId = ev.conversation_id;
          if (ev.message_id) state.lastMessageId = ev.message_id;

          if (ev.event === "message" || ev.event === "agent_message") {
            const piece = ev.answer || "";
            if (piece) {
              if (answer && piece.startsWith(answer)) answer = piece;
              else answer += piece;
              paint();
            }
          } else if (ev.event === "message_replace") {
            answer = ev.answer || answer;
            paint();
          } else if (ev.event === "error") {
            throw new Error(ev.message || "知识服务执行失败");
          } else if (ev.event === "message_end") {
            completed = true;
          }
        }
      }
    } catch (e) {
      if (e && e.name === "AbortError") {
        stopped = true;
        if (answer) answer += "\n\n_（已停止生成）_";
        else answer = "**已停止生成。**";
      } else {
        failed = true;
        answer += "\n\n**回答中断**：" + (e.message || "知识服务暂不可用，请重试。") + "\n\n本次结果不完整，请勿作为完整回答使用。";
      }
      paint();
    } finally {
      if (raf) cancelAnimationFrame(raf);
      if (viewId !== state.viewId) return;
      if (!answer) answer = "**没有返回内容。**";
      state.abort = null;
      finalizeTurn(turn, q, answer, stopped, failed);
      state.sending = false;
      el.stopBtn.hidden = true;
      el.sendBtn.disabled = false;
      scrollToBottom();
      el.input.focus();
      loadConversations();
    }
  }

  function finalizeTurn(turn, question, raw, stopped, failed) {
    const parsed = window.MiniMarkdown.parseAnswer(raw);
    const frag = parsed ? structuredBody(parsed, raw) : null;

    if (frag) {
      turn.body.innerHTML = "";
      turn.body.appendChild(frag);
      if (parsed.version) el.versionHint.textContent = "资料版本 " + parsed.version;
    } else {
      turn.body.innerHTML = '<div class="bubble">' + window.MiniMarkdown.render(raw) + "</div>";
    }

    if (failed) {
      setStatus(turn, "回答中断", "danger");
      el.srStatus.textContent = "回答中断，本次结果不完整";
    } else if (stopped) {
      setStatus(turn, "已停止生成", "pending");
      el.srStatus.textContent = "已停止生成";
    } else if (frag) {
      setStatus(turn, parsed.label, parsed.tone);
      el.srStatus.textContent = "回答已完成";
    } else if (/本地知识服务暂不可用|source_error/.test(raw)) {
      setStatus(turn, "服务暂不可用", "danger");
      el.srStatus.textContent = "服务暂不可用，本次未取得可靠结果";
    } else {
      setStatus(turn, "已生成", "ok");
      el.srStatus.textContent = "回答已完成";
    }

    addActions(turn, question, raw);
  }

  /* ---------------- 输入框与交互 ---------------- */

  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 168) + "px";
  }

  el.input.addEventListener("input", autoGrow);
  el.input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });

  el.composer.addEventListener("submit", function (e) {
    e.preventDefault();
    send();
  });

  el.stopBtn.addEventListener("click", function () {
    if (state.abort) {
      el.stopBtn.textContent = "正在停止…";
      state.abort.abort();
    }
  });

  el.newChatBtn.addEventListener("click", newChat);

  function toggleSidebar(force) {
    const collapsed = typeof force === "boolean" ? force : !el.app.classList.contains("collapsed");
    el.app.classList.toggle("collapsed", collapsed);
  }
  el.collapseBtn.addEventListener("click", function () { toggleSidebar(true); });
  el.openSidebarBtn.addEventListener("click", function () { toggleSidebar(false); });

  /* ---------------- 初始化 ---------------- */

  async function boot() {
    if (window.innerWidth <= 860) toggleSidebar(true);

    const wf = document.querySelector(".topbar-actions .ghost-link");
    if (wf && !/^(127\.0\.0\.1|localhost)$/.test(location.hostname)) wf.hidden = true;

    ensureUserId();
    try {
      await ensurePassport();
    } catch (e) {
      toast("访客令牌获取失败，请刷新重试");
    }

    try {
      const site = await api("/api/site");
      const s = site.site || {};
      if (s.title) document.title = s.title + " · 分布式光伏政策问答";
      if (s.input_placeholder) el.input.placeholder = s.input_placeholder;
    } catch (e) {
      /* 站点设置缺失不影响主流程 */
    }

    try {
      const p = await api("/api/parameters");
      if (p.suggested_questions && p.suggested_questions.length) {
        el.suggestions.innerHTML = "";
        for (const q of p.suggested_questions) {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "suggestion";
          b.textContent = q;
          b.addEventListener("click", function () {
            if (window.innerWidth <= 860) toggleSidebar(true);
            send(q);
          });
          el.suggestions.appendChild(b);
        }
      }
    } catch (e) {
      /* 建议问题缺失不影响主流程 */
    }

    loadConversations();
  }

  boot();
})();
