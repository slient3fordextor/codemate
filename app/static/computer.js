// The backend owns the model/tool loop. UI approvals are bound to one action ID.
export function runComputerTask(objective, bubble, signal) {
  return new Promise((resolve) => {
    const url = new URL('/api/v1/computer/run', window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url);
    let finished = false;
    let approval = null;
    const status = document.createElement('p');
    status.textContent = '正在连接桌面 Agent…';
    bubble.replaceChildren(status);
    const history = document.createElement('div');
    bubble.append(history);
    const stop = () => {
      status.textContent = '已停止电脑任务';
      finished = true;
      socket.close();
    };
    signal.addEventListener('abort', stop, { once: true });
    socket.addEventListener('open', () => {
      if (signal.aborted) { stop(); return; }
      status.textContent = 'Agent 正在分析任务…';
      socket.send(JSON.stringify({ objective }));
    });
    socket.addEventListener('message', (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.type === 'approval') {
        approval?.remove();
        approval = document.createElement('div');
        approval.className = 'computer-approval';
        const preview = document.createElement('p');
        preview.textContent = data.preview;
        approval.append(preview);
        for (const [label, approved] of [['确认执行', true], ['停止任务', false]]) {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = label;
          button.addEventListener('click', () => {
            if (!approved) { stop(); return; }
            if (socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ id: data.id, approved: true }));
            const record = document.createElement('p');
            record.textContent = `已确认：${data.preview}`;
            history.append(record);
            approval.remove();
            approval = null;
            status.textContent = 'Agent 正在执行并继续分析…';
          }, { once: true });
          approval.append(button);
        }
        status.textContent = '等待确认';
        bubble.append(approval);
      } else if (data.type === 'done' || data.type === 'error') {
        finished = true;
        status.textContent = [data.answer, data.error, data.message].filter(Boolean).join('\n') || '任务结束';
        approval?.remove();
      }
    });
    socket.addEventListener('error', () => {
      status.textContent = '桌面连接失败，请确认从本机 127.0.0.1 打开客户端';
      finished = true;
    });
    socket.addEventListener('close', () => {
      approval?.remove();
      if (!finished) status.textContent = '桌面连接已断开，任务已取消';
      signal.removeEventListener('abort', stop);
      resolve();
    });
  });
}
