import { api } from "/static/workspace.js";
import { readSseStream } from "/static/stream.js";

export function taskControls(task, busy) {
  const terminal = ["delivered", "discarded"].includes(task?.state);
  const writable = task?.mode !== "readonly";
  return {
    run: !!task && !busy && !terminal,
    validate: !!task && !busy && !terminal && writable,
    deliver: !!task && !busy && writable && task.validation_current &&
      ["completed", "retained", "rolled_back"].includes(task.state),
    rollback: !!task && !busy && task.state === "delivered" && task.validation_current,
  };
}

export function createAgentWorkspace({ isChatBusy }) {
  const $ = (id) => document.getElementById(id);
  const panel = $("agentWorkspace");
  const chatChildren = [...document.querySelector(".workspace").children].filter((node) => node !== panel && node.tagName !== "DIALOG");
  let visible = false;
  let current = null;
  let controller = null;
  let pendingApproval = null;
  let profiles = [];
  let tasks = [];
  let report = null;
  let epoch = 0;
  let streamEpoch = 0;
  let profileBusy = false;
  const status = (text) => { $("agentStatus").textContent = text; };
  const error = (reason) => { $("agentError").textContent = reason.message || String(reason); status("出错"); };
  const action = (callback) => () => Promise.resolve().then(callback).catch(error);
  const labels = { created: "已创建", preparing: "准备中", active: "运行中", waiting_approval: "等待审批", completed: "已完成", failed: "失败", cancelled: "已停止", delivered: "已交付", rolled_back: "已回退", retained: "已保留", discarded: "已丢弃" };
  const button = (text, callback) => {
    const node = document.createElement("button"); node.type = "button"; node.textContent = text;
    node.addEventListener("click", action(callback)); return node;
  };
  function controls() {
    const gates = taskControls(current, !!controller);
    $("agentRun").disabled = !gates.run; $("agentStop").disabled = !controller;
    $("agentValidate").disabled = !gates.validate; $("agentDeliver").disabled = !gates.deliver;
    $("agentRollback").disabled = !gates.rollback;
    $("agentCreate").disabled = !!controller;
    $("agentNew").disabled = !!controller;
    $("agentReview").disabled = !current || !!controller;
    $("agentExportReview").disabled = !report;
  }
  function render(task) {
    current = task; report = null;
    $("agentCreateForm").hidden = true; $("agentDetail").hidden = false;
    $("agentTitle").textContent = task.objective;
    $("agentMeta").textContent = `${labels[task.state] || task.state} · ${task.mode} · ${task.task_id}`;
    const final = task.nodes.findLast((item) => item.output?.decision?.type === "final");
    $("agentAnswer").textContent = task.error || final?.output.decision.answer || "";
    const budget = task.budget;
    $("agentBudget").textContent = budget ? `轮次 ${budget.rounds}/${budget.max_rounds} · Token ${budget.tokens}/${budget.max_tokens} · 恢复 ${budget.replans}/${budget.max_replans}` : "旧任务无累计预算账本";
    $("agentDiff").textContent = task.diff || "暂无代码差异";
    $("agentDiffState").textContent = task.validation_current ? "· 当前补丁已验证" : "· 未验证 / 验证已过期";
    $("agentReviewReport").hidden = true;
    $("agentNodes").replaceChildren(...task.nodes.map((item) => {
      const detail = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = `${item.name} · ${item.state}`;
      const output = document.createElement("pre"); output.textContent = JSON.stringify({ input: item.input, output: item.output, error: item.error }, null, 2);
      detail.append(summary, output); return detail;
    }));
    $("agentRuns").replaceChildren(...task.runs.map((run) => {
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${run.status} · ${run.rounds} 轮 · ${run.tokens} Token · ${new Date(run.started_at).toLocaleString()}`;
      const output = document.createElement("pre"); output.textContent = `${run.id}\n${run.request_id || ""}`;
      detail.append(summary, output);
      detail.addEventListener("toggle", () => {
        if (detail.open && !detail.dataset.loaded) {
          detail.dataset.loaded = "true";
          api(`/agent/tasks/${task.task_id}/runs/${run.id}`).then((evidence) => { output.textContent = JSON.stringify(evidence, null, 2); }).catch((reason) => { detail.dataset.loaded = ""; output.textContent = reason.message; });
        }
      });
      return detail;
    }));
    pendingApproval = task.approvals.find((item) => item.status === "pending" && new Date(item.expires_at) > new Date()) || null;
    renderApproval(); controls();
  }
  function renderApproval() {
    $("agentApproval").hidden = !pendingApproval;
    $("agentApprove").disabled = !pendingApproval;
    $("agentDeny").disabled = !pendingApproval;
    if (!pendingApproval) return;
    $("agentApprovalMeta").textContent = `${pendingApproval.tool} · ${pendingApproval.run_id} · 到期 ${new Date(pendingApproval.expires_at).toLocaleTimeString()}`;
    $("agentApprovalPreview").textContent = pendingApproval.preview;
    status("等待审批");
  }
  async function refresh(reset = true) {
    const result = await api(`/agent/tasks?offset=${reset ? 0 : tasks.length}`);
    tasks = reset ? result.items : [...tasks, ...result.items];
    $("agentTasks").replaceChildren(...tasks.map((task) => {
      const row = button("", () => load(task.task_id)); row.className = "agent-task-row";
      const title = document.createElement("strong"); title.textContent = task.objective;
      const state = document.createElement("small"); state.textContent = labels[task.state] || task.state;
      row.append(title, state); row.title = task.objective; return row;
    }));
    if (!tasks.length) $("agentTasks").textContent = "暂无编码任务";
    $("agentMore").hidden = tasks.length >= result.total;
  }
  async function load(identity, afterStream = false) {
    if (controller && !afterStream) throw new Error("请先停止当前任务");
    const next = ++epoch;
    const task = await api(`/agent/tasks/${encodeURIComponent(identity)}`);
    if (next === epoch) { render(task); localStorage.setItem("codemate.agentTask", identity); }
  }
  function newTask() {
    if (controller) throw new Error("请先停止当前任务");
    epoch++; current = null; pendingApproval = null; report = null;
    $("agentDetail").hidden = true; $("agentCreateForm").hidden = false;
    $("agentError").textContent = ""; controls(); $("agentObjective").focus();
  }
  async function show(agent) {
    if (visible === agent) { if (agent) await refresh(); return; }
    if (agent && isChatBusy()) throw new Error("请先停止当前对话生成");
    visible = agent; panel.hidden = !agent;
    document.querySelector(".workspace").classList.toggle("agent-mode", agent);
    chatChildren.forEach((child) => {
      if (agent) child.dataset.previousHidden = String(child.hidden);
      child.hidden = agent || child.dataset.previousHidden === "true";
    });
    document.querySelector(".task-history").hidden = agent;
    $("chatViewButton").classList.toggle("active", !agent);
    $("agentViewButton").classList.toggle("active", agent);
    $("chatViewButton").removeAttribute("aria-current"); $("agentViewButton").removeAttribute("aria-current");
    (agent ? $("agentViewButton") : $("chatViewButton")).setAttribute("aria-current", "page");
    if (agent) {
      const caps = await api("/agent/capabilities");
      $("agentCreate").disabled = !caps.git_ready || !!controller;
      $("agentRounds").max = caps.max_rounds; $("agentTokens").max = caps.max_tokens;
      $("agentRounds").value = Math.min(Number($("agentRounds").value), caps.max_rounds);
      $("agentTokens").value = Math.min(Number($("agentTokens").value), caps.max_tokens);
      $("agentTemplate").replaceChildren(...caps.templates.map((item) => new Option(item.label, item.id)));
      if (!caps.git_ready) { status("项目不是 Git 仓库"); return; }
      await Promise.all([refresh(), refreshProfiles()]);
      const saved = localStorage.getItem("codemate.agentTask");
      if (!current && saved) await load(saved).catch(() => newTask());
    }
  }
  async function stream(path, body) {
    if (controller || !current) return;
    const identity = current.task_id;
    const own = new AbortController(); controller = own;
    const next = ++streamEpoch; let finished = false;
    $("agentError").textContent = ""; $("agentAnswer").textContent = "";
    pendingApproval = null; renderApproval(); controls(); status("运行中");
    try {
      const response = await fetch(`/api/v1/agent/tasks/${identity}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: own.signal });
      if (!response.ok) { const reason = await response.json(); throw new Error(reason.error?.message || `HTTP ${response.status}`); }
      await readSseStream(response.body, (event, data) => {
        if (next !== streamEpoch) return;
        if (event === "approval.required") { pendingApproval = data; renderApproval(); }
        else if (event === "approval.resolved") { pendingApproval = null; renderApproval(); status("运行中"); }
        else if (event === "task.done") {
          finished = true; $("agentAnswer").textContent = data.answer || data.error || (data.passed === false ? "验证失败" : "验证已完成");
          status(data.passed === false || ["failed", "needs_evidence"].includes(data.status) ? "失败" : "已完成");
        } else if (event === "error") { finished = true; error(new Error(`${data.code} · ${data.message}`)); }
        else if (event === "model.started") status(`决策第 ${data.round} 轮`);
        else if (event === "tool.completed") status(`已执行 ${data.tool}`);
      });
      if (!finished) throw new Error("连接中断，任务状态已保留，请刷新后恢复");
    } catch (reason) {
      if (reason.name === "AbortError") status("已停止"); else error(reason);
    } finally {
      controller = null; pendingApproval = null; renderApproval(); controls();
      await load(identity, true).catch(error); await refresh().catch(error);
    }
  }
  async function decide(approved) {
    if (!pendingApproval || !current) return;
    const decision = pendingApproval;
    $("agentApprove").disabled = true; $("agentDeny").disabled = true;
    try {
      await api(`/agent/tasks/${current.task_id}/approvals/${encodeURIComponent(decision.id)}`, { method: "POST", body: JSON.stringify({ run_id: decision.run_id, patch_digest: decision.patch_digest, approved }) });
      pendingApproval = null; renderApproval(); status(approved ? "已批准" : "已拒绝");
    } catch (reason) { renderApproval(); throw reason; }
  }
  async function delivery(rollback) {
    if (!current || controller) return;
    if (!window.confirm(rollback ? "回退本次交付到项目的修改？项目中重叠的后续修改会阻止回退。" : "将当前已验证的补丁交付到项目？不会自动提交 Git。")) return;
    await api(`/agent/tasks/${current.task_id}/${rollback ? "rollback" : "deliver"}`, { method: "POST", body: JSON.stringify({ approved: true, patch_digest: current.patch_digest }) });
    await load(current.task_id); await refresh(); status(rollback ? "已回退" : "已交付");
  }
  async function review() {
    if (!current) return;
    report = await api(`/agent/tasks/${current.task_id}/review`);
    $("agentReviewReport").hidden = false;
    $("agentReviewReport").textContent = `规则评审 · 仍需人工检查\n${JSON.stringify(report, null, 2)}`;
    controls();
  }
  async function refreshProfiles() {
    profiles = await api("/model-profiles");
    for (const [id, first] of [["profileSelect", "新建档案"], ["agentProfile", "当前默认模型"]]) {
      const selected = $(id).value;
      $(id).replaceChildren(new Option(first, ""), ...profiles.map((item) => new Option(item.label, item.id)));
      if (profiles.some((item) => item.id === selected)) $(id).value = selected;
    }
  }
  function editProfile() {
    const selected = profiles.find((item) => item.id === $("profileSelect").value);
    const model = selected?.model || { provider: "mock", name: "mock-model", timeout_seconds: 60, context_window: 32768 };
    $("profileLabel").value = selected?.label || "";
    for (const [id, key] of [["profileProvider", "provider"], ["profileUrl", "base_url"], ["profileModel", "name"], ["profileTimeout", "timeout_seconds"], ["profileContext", "context_window"]]) $(id).value = model[key] ?? "";
    $("profileKey").value = ""; $("profileClearKey").checked = false;
    $("profileStatus").textContent = selected ? `版本 ${selected.version} · ${selected.api_key_set ? "已保存密钥" : "无密钥"}` : "新建档案";
    $("profileDelete").disabled = !selected; $("profileCheck").disabled = !selected;
  }
  const profileAction = (work) => async () => {
    if (profileBusy) return;
    profileBusy = true;
    const fields = [...document.querySelectorAll(".profile-editor input, .profile-editor select, .profile-editor button")];
    fields.forEach((field) => { field.disabled = true; });
    try { await work(); } catch (reason) { $("profileStatus").textContent = reason.message; }
    finally {
      profileBusy = false; fields.forEach((field) => { field.disabled = false; });
      $("profileDelete").disabled = !$("profileSelect").value; $("profileCheck").disabled = !$("profileSelect").value;
    }
  };
  $("profileSave").addEventListener("click", profileAction(async () => {
    const identity = $("profileSelect").value;
    const selected = profiles.find((item) => item.id === identity);
    const key = $("profileKey").value.trim();
    const saved = await api(`/model-profiles${identity ? `/${identity}` : ""}`, { method: identity ? "PUT" : "POST", body: JSON.stringify({ label: $("profileLabel").value.trim(), model: { ...selected?.model, provider: $("profileProvider").value, base_url: $("profileUrl").value.trim() || null, name: $("profileModel").value.trim(), api_key: key || null, api_key_mode: $("profileClearKey").checked ? "clear" : key ? "replace" : "preserve", timeout_seconds: Number($("profileTimeout").value), context_window: Number($("profileContext").value) } }) });
    await refreshProfiles(); $("profileSelect").value = saved.id; editProfile();
    $("profileStatus").textContent += " · 已保存";
  }));
  $("profileCheck").addEventListener("click", profileAction(async () => {
    if (!window.confirm("连接检查会发送一次短请求，可能产生模型用量。继续？")) return;
    $("profileCheck").disabled = true; $("profileStatus").textContent = "检查中";
    try {
      const result = await api(`/model-profiles/${$("profileSelect").value}/check`, { method: "POST", body: JSON.stringify({ acknowledged_cost: true }) });
      $("profileStatus").textContent = `连接正常 · 工具协议 ${result.supports_tools ? "支持" : "JSON 决策"} · ${JSON.stringify(result.usage)}`;
    } finally { $("profileCheck").disabled = false; }
  }));
  $("profileDelete").addEventListener("click", profileAction(async () => {
    if (!window.confirm("删除此模型档案？仍有关联任务的档案会阻止删除。")) return;
    await api(`/model-profiles/${$("profileSelect").value}`, { method: "DELETE" });
    await refreshProfiles(); editProfile();
  }));
  $("profileSelect").addEventListener("change", editProfile);
  document.querySelector('[data-settings-section="profiles"]').addEventListener("click", profileAction(async () => { await refreshProfiles(); editProfile(); }));
  $("agentViewButton").addEventListener("click", action(() => show(true)));
  $("chatViewButton").addEventListener("click", (event) => { event.preventDefault(); action(() => show(false))(); });
  $("newSessionSidebarButton").addEventListener("click", (event) => { if (visible) { event.stopImmediatePropagation(); action(newTask)(); } }, true);
  $("agentNew").addEventListener("click", action(newTask));
  $("agentRefresh").addEventListener("click", action(async () => { await refresh(); if (current) await load(current.task_id, true); }));
  $("agentMore").addEventListener("click", action(() => refresh(false)));
  $("agentCreateForm").addEventListener("submit", (event) => {
    event.preventDefault(); action(async () => {
      $("agentCreate").disabled = true;
      try {
        const task = await api("/agent/tasks", { method: "POST", body: JSON.stringify({ objective: $("agentObjective").value, mode: $("agentMode").value, profile_id: $("agentProfile").value || null, max_rounds: Number($("agentRounds").value), max_tokens: Number($("agentTokens").value) }) });
        render(task); localStorage.setItem("codemate.agentTask", task.task_id); await refresh(); status("已创建");
      } finally { controls(); }
    })();
  });
  $("agentRun").addEventListener("click", action(() => stream("run", {})));
  $("agentStop").addEventListener("click", () => controller?.abort());
  $("agentApprove").addEventListener("click", action(() => decide(true)));
  $("agentDeny").addEventListener("click", action(() => decide(false)));
  $("agentValidate").addEventListener("click", action(async () => {
    if (window.confirm(`在无网络沙箱执行 ${$("agentTemplate").selectedOptions[0].text}？`)) await stream("validate", { approved: true, patch_digest: current.patch_digest, template_id: $("agentTemplate").value });
  }));
  $("agentDeliver").addEventListener("click", action(() => delivery(false)));
  $("agentRollback").addEventListener("click", action(() => delivery(true)));
  $("agentReview").addEventListener("click", action(review));
  $("agentExportReview").addEventListener("click", () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `${current.task_id}-review.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  controls(); editProfile();
  return { isBusy: () => !!controller };
}
