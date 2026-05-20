/**
 * 共享工具函数，所有页面共用。
 */

function escHtml(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

/**
 * 通用异步任务轮询器。
 * @param {string} taskId
 * @param {Function} onDone  - (taskData) => void
 * @param {Function} onError - (errorMessage: string) => void
 * @param {number}  [interval=5000] 轮询间隔 ms
 * @param {number}  [maxPolls=36]   最大轮询次数 (36×5s=3min)
 * @returns {{stop: Function}} 调用 .stop() 可取消轮询
 */
function pollTask(taskId, onDone, onError, interval, maxPolls) {
  interval = interval || 5000;
  maxPolls = maxPolls || 36;
  let count = 0;
  let timer = null;
  let stopped = false;

  function poll() {
    if (stopped) return;
    count++;
    fetch(`/api/tasks/${encodeURIComponent(taskId)}`)
      .then(r => r.json())
      .then(t => {
        if (stopped) return;
        if (!t.ok) {
          onError(t.error || "任务丢失");
          return;
        }
        if (t.status === "completed") {
          onDone(t);
          return;
        }
        if (t.status === "failed") {
          onError(t.error?.message || "生成失败");
          return;
        }
        if (count >= maxPolls) {
          onError("任务超时，请稍后重试");
          return;
        }
        timer = setTimeout(poll, interval);
      })
      .catch(() => {
        if (!stopped) timer = setTimeout(poll, interval);
      });
  }

  poll();

  return {
    stop() { stopped = true; clearTimeout(timer); },
  };
}

/**
 * 格式化日期 2026-05-15 → 5月15日
 */
function formatDate(s) {
  if (!s) return "";
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return s;
  return `${parseInt(m[2])}月${parseInt(m[3])}日`;
}
