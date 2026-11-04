export function parseEvent(raw) {
  const lines = raw.split("\n");
  const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
  if (!event || !data) return null;
  return { event, data: JSON.parse(data) };
}

export async function readSseStream(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const parsed = parseEvent(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (parsed) onEvent(parsed.event, parsed.data);
      }
      if (done) {
        if (buffer.trim()) throw new Error("不完整的流事件");
        return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
