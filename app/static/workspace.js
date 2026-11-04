export async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options, headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || `HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

export function createWorkspace({ appendMessage, setSession, getSession, isBusy, setStatus }) {
  let conversations = [];
  let archived = [];
  let attachments = [];
  let conversationOffset = 0;
  let messageOffset = 0;
  let viewEpoch = 0;
  let refreshEpoch = 0;
  const search = document.querySelector("#sessionSearch");
  const history = document.querySelector("#taskHistoryList");
  const contextList = document.querySelector("#contextAttachments");
  const error = document.querySelector("#workspaceError");
  const report = (reason) => { error.textContent = reason.message || String(reason); setStatus("出错", "error"); };
  const action = (work) => Promise.resolve().then(work).catch(report);
  const button = (text, title, callback) => {
    const node = document.createElement("button");
    node.type = "button"; node.textContent = text; node.title = title;
    node.setAttribute("aria-label", title); node.addEventListener("click", () => action(callback));
    return node;
  };

  async function refresh(reset = true) {
    const epoch = ++refreshEpoch;
    if (reset) conversationOffset = 0;
    const result = await api(`/sessions?q=${encodeURIComponent(search.value)}&offset=${conversationOffset}`);
    if (epoch !== refreshEpoch) return;
    conversations = reset ? result.items : [...conversations, ...result.items];
    conversationOffset = conversations.length;
    history.replaceChildren(...conversations.map((session) => {
      const row = document.createElement("div"); row.className = "session-row";
      const open = button(session.title, session.title, () => load(session.id));
      open.className = `task-item ${session.id === getSession() ? "active" : ""}`;
      const tools = document.createElement("div"); tools.className = "session-tools";
      tools.append(button("✎", "重命名", () => rename(session.id)),
                   button("↧", "归档", () => archive(session.id)),
                   button("×", "删除历史", () => remove(session.id)));
      row.append(open, tools); return row;
    }));
    document.querySelector("#taskCount").textContent = String(result.total);
    document.querySelector("#moreSessions").hidden = conversations.length >= result.total;
    await refreshArchived(true, epoch);
  }

  async function refreshArchived(reset = true, epoch = refreshEpoch) {
    const resultArchived = await api(`/sessions?archived=true&limit=50&offset=${reset ? 0 : archived.length}`);
    if (epoch !== refreshEpoch) return;
    archived = reset ? resultArchived.items : [...archived, ...resultArchived.items];
    const list = document.querySelector("#archiveList");
    list.replaceChildren(...archived.map((session) => {
      const row = document.createElement("div"); row.className = "archive-item";
      row.append(button(session.title, session.title, () => load(session.id)),
                 button("↥", "恢复", () => update(session.id, { archived: false })),
                 button("×", "删除历史", () => remove(session.id)));
      return row;
    }));
    document.querySelector("#archivedCount").textContent = String(resultArchived.total);
    document.querySelector("#archiveEmpty").hidden = resultArchived.total > 0;
    document.querySelector("#moreArchived").hidden = archived.length >= resultArchived.total;
  }

  async function ensure() {
    if (getSession()) return getSession();
    const session = await api("/sessions", { method: "POST", body: JSON.stringify({ title: "新对话" }) });
    setSession(session.id);
    await refresh();
    return session.id;
  }

  async function load(id, more = false) {
    if (isBusy()) throw new Error("请先停止当前生成");
    const epoch = ++viewEpoch;
    if (!more) messageOffset = 0;
    const result = await api(`/sessions/${encodeURIComponent(id)}/messages?offset=${messageOffset}&limit=50`);
    if (epoch !== viewEpoch) return;
    if (!more) {
      setSession(id); document.querySelector("#messages").replaceChildren();
      document.querySelector(".workspace").classList.toggle("is-empty", result.total === 0);
      attachments = []; renderContext();
    }
    for (const turn of result.items) {
      appendMessage("user", turn.user);
      const answer = appendMessage("assistant", turn.assistant || "（暂无回答）");
      const note = document.createElement("small"); note.className = "turn-state";
      const labels = { completed: "已保存", running: "运行中", cancelled: "已停止", interrupted: "已中断", failed: "失败", memory_warning: "记忆未保存" };
      note.textContent = `${labels[turn.status] || turn.status} · ${turn.request_id || ""}`;
      answer.bubble.after(note);
    }
    messageOffset += result.items.length;
    document.querySelector("#moreMessages").hidden = messageOffset >= result.total;
    await refresh();
  }

  async function update(id, changes) {
    await api(`/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(changes) });
    await refresh();
  }
  async function rename(id) {
    const session = [...conversations, ...archived].find((item) => item.id === id);
    const title = window.prompt("会话名称", session?.title)?.trim();
    if (title) await update(id, { title });
  }
  const archive = (id) => update(id, { archived: true });
  async function remove(id) {
    if (!window.confirm("删除完整聊天历史？模型记忆不会随之删除。")) return;
    await api(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (id === getSession()) newSession();
    await refresh();
  }
  function newSession() {
    viewEpoch++; setSession(""); attachments = []; renderContext();
    document.querySelector("#messages").replaceChildren();
    document.querySelector(".workspace").classList.add("is-empty");
    document.querySelector("#moreMessages").hidden = true;
  }
  async function download(format) {
    if (!getSession()) return;
    const response = await fetch(`/api/v1/sessions/${encodeURIComponent(getSession())}/export?format=${format}`);
    if (!response.ok) throw new Error(`导出失败：${response.status}`);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a"); anchor.href = url;
    anchor.download = `${getSession()}.${format === "json" ? "json" : "md"}`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function renderContext() {
    contextList.replaceChildren(...attachments.map((item, index) => {
      const row = document.createElement("div"); row.className = "context-item";
      const title = document.createElement("span"); title.textContent = `${item.path || item.kind} · ${new TextEncoder().encode(item.content).length} B`;
      const preview = document.createElement("pre"); preview.textContent = item.content;
      row.append(title, button("×", "移除上下文", () => { attachments.splice(index, 1); renderContext(); }), preview);
      return row;
    }));
  }
  function attach(item) {
    const next = [...attachments, item];
    if (next.length > 8 || next.reduce((size, part) => size + new TextEncoder().encode(part.content).length, 0) > 120000) throw new Error("上下文超过 8 项或 120 KB");
    attachments = next; renderContext();
  }
  async function fileList() {
    const result = await api("/project/files");
    const list = document.querySelector("#projectFiles");
    list.replaceChildren(...result.files.map((path) => button(path, path, async () => {
      const file = await api(`/project/file?path=${encodeURIComponent(path)}`);
      document.querySelector("#projectPreview").value = file.content;
      document.querySelector("#projectPath").value = file.path;
    })));
    document.querySelector("#projectSummary").textContent = `${result.entries} 个文件${result.truncated ? " · 已截断" : ""}`;
  }

  async function memory() {
    const capabilities = await api("/memory/capabilities");
    document.querySelector("#memoryCapabilities").textContent = `${capabilities.backend} · ${capabilities.layers.join(" / ")} · ${capabilities.persistent ? "持久化" : "仅内存"}`;
    for (const control of document.querySelectorAll("[data-memory-managed]")) control.disabled = !capabilities.preference_management;
    const preferences = await api("/memory/preferences");
    const list = document.querySelector("#memoryPreferences");
    list.replaceChildren(...preferences.map((preference) => {
      const row = document.createElement("div"); row.className = "memory-row";
      const text = document.createElement("span"); text.textContent = `${preference.scope} · ${preference.content} · ${preference.active ? "生效" : "已停用"}`;
      row.append(text, button("×", "删除偏好", async () => {
        if (!window.confirm("删除这条偏好？")) return;
        await api(`/memory/preferences/${preference.id}`, { method: "DELETE" }); await memory();
      })); return row;
    }));
    const conflicts = await api("/memory/conflicts");
    document.querySelector("#memoryConflicts").textContent = conflicts.length ? conflicts.map((conflict) => `${conflict.relation} · ${conflict.resolution} · ${conflict.created_at}`).join("\n") : "暂无冲突";
  }

  search.addEventListener("input", () => action(() => refresh()));
  document.querySelector("#showMetrics").addEventListener("change", (event) => {
    document.querySelector("#requestMetrics").hidden = !event.target.checked;
  });
  document.querySelector("#moreSessions").addEventListener("click", () => action(() => refresh(false)));
  document.querySelector("#moreArchived").addEventListener("click", () => action(() => refreshArchived(false)));
  document.querySelector("#moreMessages").addEventListener("click", () => action(() => load(getSession(), true)));
  document.querySelector("#exportSession").addEventListener("click", () => action(() => download(document.querySelector("#exportFormat").value)));
  document.querySelector("#contextToggle").addEventListener("click", () => {
    const panel = document.querySelector("#projectPanel"); panel.hidden = !panel.hidden;
    if (!panel.hidden) action(fileList);
  });
  document.querySelector("#attachFile").addEventListener("click", () => action(() => attach({ kind: "file", path: document.querySelector("#projectPath").value, content: document.querySelector("#projectPreview").value })));
  document.querySelector("#attachSelection").addEventListener("click", () => action(() => {
    const preview = document.querySelector("#projectPreview");
    const selected = preview.value.slice(preview.selectionStart, preview.selectionEnd);
    if (!selected) throw new Error("未选择文本");
    attach({ kind: "selection", path: document.querySelector("#projectPath").value, content: selected });
  }));
  document.querySelector("#attachDiff").addEventListener("click", () => action(async () => {
    const diff = await api("/project/diff"); attach({ kind: "diff", content: diff.content });
  }));
  document.querySelector("#refreshMemory").addEventListener("click", () => action(memory));
  for (const control of document.querySelectorAll("[data-memory-clear]")) control.addEventListener("click", () => action(async () => {
    if (isBusy()) throw new Error("请先停止当前生成");
    if (!window.confirm("清除模型记忆？聊天历史仍保留。")) return;
    const scope = control.dataset.memoryClear;
    if (scope === "session" && !getSession()) throw new Error("没有当前会话");
    await api(`/memory/${scope === "session" ? `sessions/${encodeURIComponent(getSession())}` : scope}`, { method: "DELETE" });
    await memory();
  }));
  let legacy = [];
  try {
    for (const key of ["codemate.tasks", "codemate.archivedTasks"]) {
      const entries = JSON.parse(localStorage.getItem(key) || "[]");
      if (Array.isArray(entries)) legacy.push(...entries);
    }
  } catch { /* Old storage cannot block loading server-backed conversations. */ }
  if (legacy.length) {
    const target = document.querySelector("#legacyPrompts"); target.hidden = false;
    target.replaceChildren(...legacy.filter((item) => typeof item === "string").map((title) => button(title, "旧快捷输入", () => { document.querySelector("#messageInput").value = title; })));
  }
  async function stats() {
    const values = await api("/sessions/stats");
    document.querySelector("#realUsage").textContent = `已保存轮次 ${values.turns} · 已完成 ${values.completed} · 累计 Token ${values.tokens} · 单轮峰值 ${values.peak_tokens} · 最长 ${Math.round(values.longest_ms / 1000)} 秒`;
  }
  return { ensure, refresh, load, newSession, rename, archive, remove, memory, stats,
    context: () => attachments.map((item) => ({ ...item })),
    initialize: () => action(async () => { await refresh(); if (getSession()) await load(getSession()); }),
  };
}
