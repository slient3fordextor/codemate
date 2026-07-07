const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const newSessionButton = document.querySelector("#newSessionButton");
const statusEl = document.querySelector("#connectionStatus");
const currentFileInput = document.querySelector("#currentFile");
const selectedTextInput = document.querySelector("#selectedText");

let sessionId = localStorage.getItem("codemate.sessionId") || "";
let activeController = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || activeController) {
    return;
  }

  appendMessage("user", message);
  input.value = "";
  await sendMessage(message);
});

newSessionButton.addEventListener("click", () => {
  sessionId = "";
  localStorage.removeItem("codemate.sessionId");
  messages.replaceChildren();
  appendMessage(
    "assistant",
    "已开始新会话。可以继续发送 CLI 任务、命令或终端报错。"
  );
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    form.requestSubmit();
  }
});

async function sendMessage(message) {
  activeController = new AbortController();
  setBusy(true);

  const assistant = appendMessage("assistant", "");
  const payload = {
    session_id: sessionId || null,
    message,
    current_file: currentFileInput.value.trim() || null,
    selected_text: selectedTextInput.value.trim() || null,
    stream: true,
  };

  try {
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

    await readSse(response.body, (eventName, data) => {
      handleStreamEvent(eventName, data, assistant);
    });
    enhanceCodeBlocks(assistant.bubble);
    setStatus("就绪");
  } catch (error) {
    assistant.bubble.textContent =
      error instanceof Error ? error.message : "请求失败，请稍后重试。";
    setStatus("出错", "error");
  } finally {
    activeController = null;
    setBusy(false);
  }
}

async function readSse(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      const parsed = parseSseEvent(rawEvent);
      if (parsed) {
        onEvent(parsed.event, parsed.data);
      }
    }
  }
}

function parseSseEvent(rawEvent) {
  const lines = rawEvent.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) {
    return null;
  }

  try {
    return {
      event: eventLine.slice("event:".length).trim(),
      data: JSON.parse(dataLine.slice("data:".length).trim()),
    };
  } catch {
    return null;
  }
}

function handleStreamEvent(eventName, data, assistant) {
  if (eventName === "message.start" && data.session_id) {
    sessionId = data.session_id;
    localStorage.setItem("codemate.sessionId", sessionId);
    return;
  }

  if (eventName === "message.delta" && data.content) {
    assistant.content += data.content;
    assistant.bubble.textContent = assistant.content;
    scrollToBottom();
    return;
  }

  if (eventName === "error") {
    assistant.bubble.textContent = data.message || "模型服务返回错误。";
    setStatus("出错", "error");
  }
}

function appendMessage(role, content) {
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
  sendButton.disabled = isBusy;
  newSessionButton.disabled = isBusy;
  if (isBusy) {
    setStatus("生成中", "busy");
  }
}

function setStatus(text, className = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${className}`.trim();
}

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}
