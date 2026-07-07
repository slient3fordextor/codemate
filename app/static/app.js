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
    enhanceCommands(assistant.bubble);
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

function enhanceCommands(bubble) {
  const text = bubble.textContent || "";
  const commandBlocks = extractShellCodeBlocks(text);
  if (commandBlocks.length === 0) {
    return;
  }

  bubble.textContent = "";
  const intro = text.replace(/```(?:bash|sh|shell|zsh)?\n[\s\S]*?```/g, "").trim();
  if (intro) {
    const introEl = document.createElement("p");
    introEl.textContent = intro;
    bubble.append(introEl);
  }

  for (const command of commandBlocks) {
    bubble.append(createCommandCard(command));
  }
}

function extractShellCodeBlocks(text) {
  const blocks = [];
  const pattern = /```(bash|sh|shell|zsh)?\n([\s\S]*?)```/g;
  let match = pattern.exec(text);
  while (match) {
    const command = match[2].trim();
    if (command && looksLikeShellCommand(command)) {
      blocks.push(command);
    }
    match = pattern.exec(text);
  }
  return blocks;
}

function looksLikeShellCommand(command) {
  const firstLine = command.split("\n").find(Boolean) || "";
  return /^(python|pytest|uvicorn|pip|uv|npm|pnpm|yarn|git|make|curl|docker|docker compose|ruff|mypy|cd|ls|cat|export)\b/.test(
    firstLine.trim()
  );
}

function createCommandCard(command) {
  const card = document.createElement("details");
  card.className = "command-card";
  card.open = true;

  const summary = document.createElement("summary");
  summary.className = "command-summary";
  summary.textContent = "可执行命令";

  const pre = document.createElement("pre");
  pre.textContent = command;

  const actions = document.createElement("div");
  actions.className = "command-actions";

  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "复制";
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(command);
    copy.textContent = "已复制";
    window.setTimeout(() => {
      copy.textContent = "复制";
    }, 1400);
  });

  const explain = document.createElement("button");
  explain.type = "button";
  explain.textContent = "解释";
  explain.addEventListener("click", () => {
    input.value = `解释这个命令的作用、风险和预期输出：\n\n${command}`;
    input.focus();
  });

  actions.append(copy, explain);
  card.append(summary, pre, actions);
  return card;
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
