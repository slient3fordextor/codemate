import { runComputerTask } from "/static/computer.js";
import { createWorkspace } from "/static/workspace.js";
import { readSseStream } from "/static/stream.js";
import { createAgentWorkspace } from "/static/agent.js";

const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const newSessionButton = document.querySelector("#newSessionButton");
const statusEl = document.querySelector("#connectionStatus");
const currentFileInput = document.querySelector("#currentFile");
const selectedTextInput = document.querySelector("#selectedText");
const modelPanel = document.querySelector("#modelPanel");
const modelConfigForm = document.querySelector("#modelConfigForm");
const modelCloseButton = document.querySelector("#modelCloseButton");
const modelSearchInput = document.querySelector("#modelSearchInput");
const modelPresetList = document.querySelector("#modelPresetList");
const modelProviderInput = document.querySelector("#modelProvider");
const modelBaseUrlInput = document.querySelector("#modelBaseUrl");
const modelNameInput = document.querySelector("#modelName");
const modelApiKeyInput = document.querySelector("#modelApiKey");
const modelNoteInput = document.querySelector("#modelNote");
const modelTemperatureInput = document.querySelector("#modelTemperature");
const modelMaxTokensInput = document.querySelector("#modelMaxTokens");
const modelTimeoutInput = document.querySelector("#modelTimeout");
const modelRetriesInput = document.querySelector("#modelRetries");
const modelContextWindowInput = document.querySelector("#modelContextWindow");
const clearApiKeyInput = document.querySelector("#clearApiKey");
const modelKeyState = document.querySelector("#modelKeyState");
const composerModelSelect = document.querySelector("#composerModelSelect");
const messageCount = document.querySelector("#messageCount");
const quickPrompts = document.querySelectorAll("[data-prompt]");
const modelSettingsButton = document.querySelector("#modelSettingsButton");
const newSessionSidebarButton = document.querySelector("#newSessionSidebarButton");
const settingsNavItems = document.querySelectorAll("[data-settings-section]");
const settingsOverview = document.querySelector("#settingsOverview");
const settingsOptions = document.querySelector("#settingsOptions");
const settingsTitle = document.querySelector("#settingsTitle");
const settingsEyebrow = document.querySelector("#settingsEyebrow");
const settingsForm = document.querySelector("#modelConfigForm");
const settingsModelPresets = document.querySelector(".model-presets");
const draftStorageKey = "codemate.composerDraft";

let sessionId = localStorage.getItem("codemate.sessionId") || "";
let activeController = null;
let modelConfigLoaded = false;
let preparingSession = false;
const agentWorkspace = createAgentWorkspace({ isChatBusy: () => !!activeController || preparingSession });

const modelPresets = [
  {
    label: "Mock",
    provider: "mock",
    baseUrl: "",
    model: "mock-model",
    note: "本地占位响应",
  },
  {
    label: "DeepSeek Chat",
    provider: "deepseek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    note: "OpenAI 兼容",
  },
  {
    label: "Qwen Plus",
    provider: "qwen",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
    note: "DashScope",
  },
  {
    label: "Moonshot Kimi",
    provider: "moonshot",
    baseUrl: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k",
    note: "OpenAI 兼容",
  },
  {
    label: "Anthropic Claude",
    provider: "anthropic",
    baseUrl: "https://api.anthropic.com/v1",
    model: "claude-3-5-sonnet-latest",
    note: "Messages API",
  },
  {
    label: "Ollama Local",
    provider: "ollama",
    baseUrl: "http://127.0.0.1:11434/v1",
    model: "llama3.1",
    note: "本地模型",
  },
  {
    label: "OpenAI Compatible",
    provider: "openai_compatible",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4.1-mini",
    note: "自定义兼容端点",
  },
];

const providerMarks = {
  anthropic: "AN",
  baichuan: "BC",
  claude: "CL",
  deepseek: "DS",
  mock: "MK",
  moonshot: "MS",
  ollama: "OL",
  openai_compatible: "AI",
  qwen: "QW",
  zhipu: "ZP",
};

const workspaceState = createWorkspace({ appendMessage, setStatus,
  getSession: () => sessionId, isBusy: () => Boolean(activeController),
  setSession: (id) => { sessionId = id; localStorage.setItem("codemate.sessionId", id); },
});
let followOutput = true;
messages.addEventListener("scroll", () => {
  followOutput = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || activeController || preparingSession || agentWorkspace.isBusy()) {
    return;
  }
  if (!navigator.onLine) {
    setStatus("网络不可用", "error");
    return;
  }

  preparingSession = true;
  try { await workspaceState.ensure(); } catch (error) { setStatus(error.message, "error"); return; }
  finally { preparingSession = false; }
  appendMessage("user", message);
  input.value = "";
  try { sessionStorage.removeItem(draftStorageKey); } catch { /* Storage is optional. */ }
  updateComposerMetrics();
  if (document.querySelector("#computerMode")?.checked) {
    activeController = new AbortController();
    setBusy(true);
    const assistant = appendMessage("assistant", "正在连接桌面 Agent…");
    try {
      await runComputerTask(message, assistant.bubble, activeController.signal);
    } finally {
      activeController = null;
      setBusy(false);
    }
  } else {
    await sendMessage(message);
  }
});

sendButton.addEventListener("click", () => {
  if (activeController) activeController.abort();
});

newSessionButton.addEventListener("click", () => {
  if (activeController) return;
  workspaceState.newSession();
  input.focus();
});

newSessionSidebarButton?.addEventListener("click", () => {
  newSessionButton.click();
});

modelSettingsButton?.addEventListener("click", () => {
  openModelPanel();
  if (!modelConfigLoaded) {
    void loadModelConfig();
  }
});

modelCloseButton.addEventListener("click", () => {
  closeModelPanel();
});

modelPanel.addEventListener("close", () => {
  document.body.classList.remove("modal-open");
  void loadModelConfig();
});

modelPanel.addEventListener("click", (event) => {
  if (event.target === modelPanel) {
    closeModelPanel();
  }
});

modelConfigForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveModelConfig();
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    form.requestSubmit();
  }
});

function updateComposerMetrics() {
  if (messageCount) {
    messageCount.textContent = `${input.value.length.toLocaleString("zh-CN")} / 16,000`;
  }
  input.style.height = "auto";
  input.style.height = `${Math.min(Math.max(input.scrollHeight, 82), 220)}px`;
}

input.addEventListener("input", updateComposerMetrics);
try {
  const draft = sessionStorage.getItem(draftStorageKey);
  if (draft) input.value = draft;
} catch { /* Storage is optional. */ }
input.addEventListener("input", () => {
  try { sessionStorage.setItem(draftStorageKey, input.value); } catch { /* Storage is optional. */ }
});
updateComposerMetrics();

window.addEventListener("online", () => setStatus("已连接"));
window.addEventListener("offline", () => setStatus("网络不可用", "error"));
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    input.focus();
  }
  if (event.key === "Escape" && document.activeElement === input && !input.value) {
    input.blur();
  }
});

quickPrompts.forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt || "";
    updateComposerMetrics();
    input.focus();
  });
});

renderModelPresets();
renderComposerModels();
workspaceState.initialize();
void loadModelConfig();

setSettingsSection("overview");
settingsNavItems.forEach((item) => {
  item.addEventListener("click", () => {
    const section = item.dataset.settingsSection || "overview";
    if (section === "overview" && item.classList.contains("settings-back")) {
      closeModelPanel();
      return;
    }
    setSettingsSection(section);
  });
});


function setSettingsSection(section) {
  if (section === "overview") void workspaceState.stats().catch((error) => setStatus(error.message, "error"));
  if (section === "memory") void workspaceState.memory().catch((error) => setStatus(error.message, "error"));
  const titles = {
    archive: ["archive", "归档会话"],
    appearance: ["appearance", "外观"],
    browser: ["browser", "浏览器控制"],
    commands: ["commands", "命令"],
    computer: ["computer", "电脑控制"],
    hooks: ["hooks", "钩子"],
    mcp: ["mcp", "MCP 服务器"],
    memory: ["memory", "记忆"],
    models: ["models", "模型设置"],
    profiles: ["profiles", "模型档案"],
    overview: ["overview", "使用统计"],
    plugins: ["plugins", "插件"],
    shortcuts: ["shortcuts", "键盘快捷键"],
    skills: ["skills", "技能"],
    subagents: ["subagents", "子智能体"],
  };
  const [eyebrow, title] = titles[section] || titles.overview;
  settingsEyebrow.textContent = eyebrow;
  settingsTitle.textContent = title;
  settingsOverview.hidden = section !== "overview";
  settingsOptions.hidden = section === "overview" || section === "models";
  settingsModelPresets.hidden = section !== "models";
  for (const option of settingsOptions.querySelectorAll("[data-settings-panel]")) {
    option.hidden = option.dataset.settingsPanel !== section;
  }
  for (const field of settingsForm.querySelectorAll(":scope > label, :scope > .model-actions")) {
    field.hidden = section !== "models";
  }
  settingsForm.classList.toggle("settings-model-active", section === "models");
  for (const item of document.querySelectorAll(".settings-nav-item")) {
    item.classList.toggle("active", item.dataset.settingsSection === section);
  }
}

modelSearchInput.addEventListener("input", () => {
  renderModelPresets(modelSearchInput.value);
});

composerModelSelect.addEventListener("change", () => {
  if (composerModelSelect.value === "__add__") {
    openModelPanel();
    if (!modelConfigLoaded) {
      void loadModelConfig();
    }
    return;
  }
  const preset = modelPresets.find((item) => modelKey(item) === composerModelSelect.value);
  if (preset) {
    applyModelPreset(preset);
    openModelPanel();
    setStatus("保存模型配置后生效", "busy");
  }
});

async function sendMessage(message) {
  activeController = new AbortController();
  setBusy(true);

  const assistant = appendMessage("assistant", "");
  const payload = {
    session_id: sessionId || null,
    message,
    current_file: currentFileInput?.value.trim() || null,
    selected_text: selectedTextInput?.value.trim() || null,
    turn_id: crypto.randomUUID(),
    context: workspaceState.context(),
    stream: true,
  };
  const temperature = parseOptionalNumber(modelTemperatureInput.value);
  const maxTokens = parseOptionalInteger(modelMaxTokensInput.value);
  if (modelNameInput.value.trim()) {
    payload.model = modelNameInput.value.trim();
  }
  if (temperature !== null) {
    payload.temperature = temperature;
  }
  if (maxTokens !== null) {
    payload.max_tokens = maxTokens;
  }

  try {
    let streamFailed = false;
    let completed = false;
    const response = await fetch("/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(payload),
      signal: activeController.signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`请求失败：HTTP ${response.status}`);
    }

    await readSseStream(response.body, (eventName, data) => {
      if (eventName === "completion.done") completed = true;
      streamFailed = handleStreamEvent(eventName, data, assistant) || streamFailed;
    });
    if (!completed && !streamFailed) throw new Error("连接中断，回答未完整保存");
    enhanceCodeBlocks(assistant.bubble);
    if (!streamFailed) {
      setStatus("就绪");
    }
  } catch (error) {
    const stopped = error?.name === "AbortError";
    const note = document.createElement("small"); note.className = "turn-state";
    note.textContent = stopped ? "已停止" : (error instanceof Error ? error.message : "请求失败");
    assistant.bubble.after(note);
    setStatus(stopped ? "已停止" : "出错", stopped ? "" : "error");
  } finally {
    activeController = null;
    setBusy(false);
    void workspaceState.refresh().catch((error) => setStatus(error.message, "error"));
  }
}

function handleStreamEvent(eventName, data, assistant) {
  if (eventName === "message.start" && data.session_id) {
    sessionId = data.session_id;
    localStorage.setItem("codemate.sessionId", sessionId);
    return false;
  }

  if (eventName === "message.delta" && data.content) {
    assistant.content += data.content;
    assistant.bubble.textContent = assistant.content;
    if (followOutput) scrollToBottom();
    return false;
  }

  if (eventName === "error") {
    const note = document.createElement("small"); note.className = "turn-state";
    note.textContent = data.message || "模型服务返回错误。";
    assistant.bubble.after(note);
    setStatus("出错", "error");
    return true;
  }
  if (eventName === "memory.warning") {
    appendMessage("assistant", `记忆未保存：${data.message || "存储不可用"}`);
    setStatus("记忆警告", "error");
    return true;
  }
  if (eventName === "context.usage") {
    document.querySelector("#requestMetrics").textContent = `上下文 ${data.input_tokens} / ${data.input_budget} Token${data.estimated ? "（估算）" : ""} · 记忆 ${data.memory_tokens} Token`;
  }
  if (eventName === "usage.update") {
    document.querySelector("#requestMetrics").textContent += ` · 输入 ${data.input_tokens ?? "—"} / 输出 ${data.output_tokens ?? "—"} Token`;
  }
  if (eventName === "completion.done") {
    document.querySelector("#requestMetrics").textContent += ` · 首字 ${data.first_token_ms ?? "—"} ms · 总计 ${data.duration_ms} ms · ${data.request_id || ""}`;
  }
  return false;
}

function appendMessage(role, content) {
  document.querySelector(".workspace").classList.remove("is-empty");
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "你" : "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;

  article.append(avatar, bubble);
  messages.append(article);
  scrollToBottom();

  return { article, bubble, content };
}

async function loadModelConfig() {
  setStatus("读取模型", "busy");
  try {
    const response = await fetch("/api/v1/model-config");
    if (!response.ok) {
      throw new Error(`读取模型配置失败：HTTP ${response.status}`);
    }
    const config = await response.json();
    const provider = config.provider || "mock";
    modelProviderInput.value = provider;
    modelBaseUrlInput.value = config.base_url || "";
    modelNameInput.value = config.name || "mock-model";
    modelApiKeyInput.value = "";
    modelNoteInput.value = config.note || "";
    modelTimeoutInput.value = String(config.timeout_seconds || 60);
    modelRetriesInput.value = String(config.max_retries ?? 2);
    modelContextWindowInput.value = String(config.context_window || 32768);
    clearApiKeyInput.checked = false;
    modelKeyState.textContent =
      provider === "mock"
        ? "当前使用 mock 默认配置"
        : config.api_key_set
          ? "后端已保存 API Key"
          : "未保存 API Key";
    modelConfigLoaded = true;
    highlightActivePreset();
    syncComposerModel(config);
    setStatus("就绪");
  } catch (error) {
    setStatus("出错", "error");
    modelKeyState.textContent = error instanceof Error ? error.message : "读取失败";
  }
}

function openModelPanel() {
  if (!modelPanel.open) {
    modelPanel.showModal();
  }
  document.body.classList.add("modal-open");
}

function closeModelPanel() {
  if (modelPanel.open) {
    modelPanel.close();
  }
}

function renderModelPresets(query = "") {
  modelPresetList.replaceChildren();
  const normalizedQuery = query.trim().toLowerCase();
  const groups = groupModelPresets(
    modelPresets.filter((preset) => {
      const text = `${preset.label} ${preset.provider} ${preset.model} ${preset.note}`.toLowerCase();
      return !normalizedQuery || text.includes(normalizedQuery);
    })
  );

  if (groups.length === 0) {
    const empty = document.createElement("p");
    empty.className = "model-empty";
    empty.textContent = "没有匹配的模型";
    modelPresetList.append(empty);
    return;
  }

  for (const group of groups) {
    const heading = document.createElement("div");
    heading.className = "model-group";
    heading.textContent = group.label;
    modelPresetList.append(heading);

    for (const preset of group.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "model-preset";
      button.dataset.provider = preset.provider;
      button.dataset.model = preset.model;
      button.innerHTML = `
        <b class="model-mark">${providerMark(preset.provider)}</b>
        <span class="model-main">
          <strong>${preset.label}</strong>
          <small>${preset.model}</small>
        </span>
        <span class="model-meta">${preset.note}</span>
      `;
      button.addEventListener("click", () => {
        applyModelPreset(preset);
      });
      modelPresetList.append(button);
    }
  }
  highlightActivePreset();
}

function groupModelPresets(presets) {
  const order = ["mock", "deepseek", "qwen", "moonshot", "anthropic", "ollama", "openai_compatible"];
  const labels = {
    anthropic: "Anthropic",
    deepseek: "DeepSeek",
    mock: "Local",
    moonshot: "Moonshot",
    ollama: "Local Runtime",
    openai_compatible: "Compatible",
    qwen: "Alibaba Qwen",
  };
  return order
    .map((provider) => ({
      label: labels[provider] || provider,
      items: presets.filter((preset) => preset.provider === provider),
    }))
    .filter((group) => group.items.length > 0);
}

function renderComposerModels() {
  composerModelSelect.replaceChildren();
  const custom = document.createElement("option");
  custom.value = "";
  custom.textContent = "Custom";
  composerModelSelect.append(custom);
  for (const preset of modelPresets) {
    const option = document.createElement("option");
    option.value = modelKey(preset);
    option.textContent = `${preset.label} ${providerMark(preset.provider)}`;
    composerModelSelect.append(option);
  }
  const add = document.createElement("option");
  add.value = "__add__";
  add.textContent = "添加模型...";
  composerModelSelect.append(add);
  syncComposerModel({
    provider: modelProviderInput.value,
    name: modelNameInput.value,
  });
}

function applyModelPreset(preset) {
  modelProviderInput.value = preset.provider;
  modelBaseUrlInput.value = preset.baseUrl;
  modelNameInput.value = preset.model;
  modelNoteInput.value = preset.note;
  syncComposerModel({ provider: preset.provider, name: preset.model });
  highlightActivePreset();
}

function highlightActivePreset() {
  for (const button of modelPresetList.querySelectorAll(".model-preset")) {
    const isActive =
      button.dataset.provider === modelProviderInput.value &&
      button.dataset.model === modelNameInput.value;
    button.classList.toggle("active", isActive);
  }
}

function syncComposerModel(config) {
  const matched = modelPresets.find(
    (preset) => preset.provider === config.provider && preset.model === config.name
  );
  composerModelSelect.value = matched ? modelKey(matched) : "";
}

function modelKey(preset) {
  return `${preset.provider}:${preset.model}`;
}

function providerMark(provider) {
  return providerMarks[provider] || "•";
}

async function saveModelConfig() {
  const apiKey = modelApiKeyInput.value.trim();
  const payload = {
    provider: modelProviderInput.value,
    base_url: modelBaseUrlInput.value.trim() || null,
    name: modelNameInput.value.trim(),
    api_key: apiKey || null,
    note: modelNoteInput.value.trim() || null,
    api_key_mode: clearApiKeyInput.checked ? "clear" : apiKey ? "replace" : "preserve",
    timeout_seconds: parseOptionalNumber(modelTimeoutInput.value) || 60,
    max_retries: parseOptionalInteger(modelRetriesInput.value) ?? 2,
    context_window: parseOptionalInteger(modelContextWindowInput.value) || 32768,
  };

  setStatus("保存中", "busy");
  try {
    const response = await fetch("/api/v1/model-config", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`保存失败：HTTP ${response.status} ${text}`);
    }
    const config = await response.json();
    modelApiKeyInput.value = "";
    clearApiKeyInput.checked = false;
    modelKeyState.textContent = config.api_key_set ? "后端已保存 API Key" : "未保存 API Key";
    modelConfigLoaded = true;
    highlightActivePreset();
    syncComposerModel(config);
    setStatus("已保存");
    window.setTimeout(() => setStatus("就绪"), 1400);
  } catch (error) {
    setStatus("出错", "error");
    modelKeyState.textContent = error instanceof Error ? error.message : "保存失败";
  }
}

function parseOptionalNumber(value) {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseOptionalInteger(value) {
  const parsed = parseOptionalNumber(value);
  return parsed === null ? null : Math.trunc(parsed);
}

function enhanceCodeBlocks(bubble) {
  const text = bubble.textContent || "";
  const codeBlocks = extractCodeBlocks(text);
  if (codeBlocks.length === 0) {
    return;
  }

  bubble.textContent = "";
  const intro = text.replace(/```[\w+-]*\n[\s\S]*?```/g, "").trim();
  if (intro) {
    const introEl = document.createElement("p");
    introEl.textContent = intro;
    bubble.append(introEl);
  }

  for (const block of codeBlocks) {
    bubble.append(createCodeCard(block));
  }
}

function extractCodeBlocks(text) {
  const blocks = [];
  const pattern = /```([\w+-]*)\n([\s\S]*?)```/g;
  let match = pattern.exec(text);
  while (match) {
    const language = normalizeLanguage(match[1]);
    const code = match[2].trim();
    if (code) {
      blocks.push({ code, language });
    }
    match = pattern.exec(text);
  }
  return blocks;
}

function normalizeLanguage(language) {
  const value = language.trim().toLowerCase();
  return value || "text";
}

function isShellCode(language, code) {
  return ["bash", "sh", "shell", "zsh"].includes(language) || looksLikeShellCommand(code);
}

function looksLikeShellCommand(command) {
  const firstLine = command.split("\n").find(Boolean) || "";
  return /^(python|pytest|uvicorn|pip|uv|npm|pnpm|yarn|git|make|curl|docker|docker compose|ruff|mypy|cd|ls|cat|export)\b/.test(
    firstLine.trim()
  );
}

function createCodeCard(block) {
  const card = document.createElement("details");
  card.className = "command-card";
  card.open = true;

  const summary = document.createElement("summary");
  summary.className = "command-summary";
  summary.textContent = getCodeTitle(block);

  const pre = document.createElement("pre");
  pre.dataset.language = block.language;
  pre.textContent = block.code;

  const actions = document.createElement("div");
  actions.className = "command-actions";

  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "复制";
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(block.code);
    copy.textContent = "已复制";
    window.setTimeout(() => {
      copy.textContent = "复制";
    }, 1400);
  });

  actions.append(copy);

  if (isShellCode(block.language, block.code)) {
    const explain = document.createElement("button");
    explain.type = "button";
    explain.textContent = "解释";
    explain.addEventListener("click", () => {
      input.value = `解释这个命令的作用、风险和预期输出：\n\n${block.code}`;
      input.focus();
    });
    actions.append(explain);
  }

  card.append(summary, pre, actions);
  return card;
}

function getCodeTitle(block) {
  if (isShellCode(block.language, block.code)) {
    return "可执行命令";
  }

  const labels = {
    css: "CSS 代码",
    html: "HTML 代码",
    javascript: "JavaScript 代码",
    js: "JavaScript 代码",
    json: "JSON 数据",
    jsx: "JSX 代码",
    markdown: "Markdown 文档",
    md: "Markdown 文档",
    python: "Python 代码",
    py: "Python 代码",
    text: "代码片段",
    ts: "TypeScript 代码",
    tsx: "TSX 代码",
    typescript: "TypeScript 代码",
    yaml: "YAML 配置",
    yml: "YAML 配置",
  };
  return labels[block.language] || `${block.language.toUpperCase()} 代码`;
}

function setBusy(isBusy) {
  sendButton.disabled = false;
  messages.setAttribute("aria-busy", String(isBusy));
  sendButton.textContent = isBusy ? "■" : "↑";
  sendButton.setAttribute("aria-label", isBusy ? "停止生成" : "发送");
  newSessionButton.disabled = isBusy;
  newSessionSidebarButton.disabled = isBusy;
  if (isBusy) {
    setStatus("生成中", "busy");
  }
}


function setStatus(text, className = "") {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = text;
  statusEl.className = `status ${className}`.trim();
}

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}
