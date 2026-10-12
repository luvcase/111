"use strict";
const $ = id => document.getElementById(id);
const names = {concise: "简明答案", explain: "详细解析", code: "编程题"};
const states = {queued: "等待处理", processing: "正在解答", done: "已完成", error: "处理失败"};
let token = sessionStorage.getItem("screenbridge-token") || "";
let socket = null, reconnectTimer = null, generation = 0;
const records = new Map();
const fragment = new URLSearchParams(location.hash.slice(1)).get("token");
if (fragment) { token = fragment; history.replaceState(null, "", location.pathname); }

function connection(text, online = false) {
  $("connection").textContent = text;
  $("connection").classList.toggle("online", online);
}
function textNode(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
function render() {
  $("jobs").replaceChildren();
  const list = [...records.values()].sort((a, b) => b.created_at - a.created_at).slice(0, 30);
  $("job-count").textContent = `${list.length} 条`;
  $("empty").hidden = list.length > 0;
  for (const job of list) {
    const card = document.createElement("article"); card.className = "card job";
    const head = document.createElement("div"); head.className = "job-header";
    const title = textNode("div", names[job.mode] || "解答", "job-title");
    title.append(textNode("span", new Date(job.created_at * 1000).toLocaleTimeString(), "job-time"));
    head.append(title, textNode("span", states[job.status], `badge ${job.status === "error" ? "error" : ""}`));
    card.append(head);
    if (job.status === "done") {
      const answer = document.createElement("div"); answer.className = "answer";
      const chunks = job.answer.split(/```/);
      chunks.forEach((chunk, index) => {
        if (index % 2) {
          const code = chunk.replace(/^\w*\n/, "");
          answer.append(textNode("pre", code));
        } else answer.append(document.createTextNode(chunk));
      });
      card.append(answer);
      const copy = textNode("button", "复制答案", "copy-button");
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(job.answer); copy.textContent = "已复制"; }
        catch { copy.textContent = "请长按答案选择复制"; }
      });
      card.append(copy);
    } else card.append(textNode("div", job.error || (job.status === "queued" ? "已收到截图，正在排队…" : "正在识别题目并生成解答…"), job.status === "error" ? "error" : "pending"));
    $("jobs").append(card);
  }
}
function connectSocket() {
  clearTimeout(reconnectTimer);
  if (!token) return;
  const current = generation;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({token}));
  ws.onmessage = event => {
    if (current !== generation) return;
    let data; try { data = JSON.parse(event.data); } catch { return; }
    connection("● 已连接", true);
    if (data.type === "snapshot") { records.clear(); data.jobs.forEach(job => records.set(job.id, job)); render(); }
    if (data.type === "job") { records.set(data.job.id, data.job); render(); }
  };
  ws.onclose = event => {
    if (current !== generation) return;
    if (event.code === 4401) { disconnect(); $("pair-error").textContent = "配对已失效，请重新扫描电脑二维码。"; return; }
    connection("正在重连…"); reconnectTimer = setTimeout(connectSocket, 2000);
  };
  ws.onerror = () => ws.close();
}
async function pair(value) {
  try {
    const response = await fetch("/api/status", {headers: {Authorization: `Bearer ${value}`}});
    if (!response.ok) throw new Error(response.status === 401 ? "配对令牌无效，请重新输入。" : "服务暂时不可用。");
    const status = await response.json();
    generation++; if (socket) socket.close();
    token = value; sessionStorage.setItem("screenbridge-token", token);
    $("pairing").hidden = true; $("workspace").hidden = false;
    $("demo-banner").hidden = !status.demo;
    $("provider-status").textContent = status.demo ? "设备已配对 · 演示传输模式" : `设备已配对 · ${status.model}`;
    $("pair-error").textContent = "";
    connectSocket();
  } catch (error) { $("pair-error").textContent = error.message; connection("未连接"); }
}
function disconnect() {
  generation++; token = ""; sessionStorage.removeItem("screenbridge-token");
  clearTimeout(reconnectTimer); if (socket) socket.close();
  records.clear(); render(); $("workspace").hidden = true; $("pairing").hidden = false;
  $("token").value = ""; connection("未连接");
}
$("pair-form").addEventListener("submit", event => { event.preventDefault(); pair($("token").value.trim()); });
$("disconnect").addEventListener("click", disconnect);
$("image-input").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 8 * 1024 * 1024) { $("upload-status").textContent = "图片不能超过 8 MB"; event.target.value = ""; return; }
  const body = new FormData(); body.append("image", file); body.append("mode", $("mode").value);
  $("upload-status").textContent = "正在发送…"; event.target.disabled = true;
  try {
    const response = await fetch("/api/screenshots", {method: "POST", headers: {Authorization: `Bearer ${token}`}, body});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "上传失败");
    $("upload-status").textContent = "已发送，解答会自动同步到此页面。";
  } catch (error) { $("upload-status").textContent = error.message; }
  finally { event.target.disabled = false; event.target.value = ""; }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && token && (!socket || socket.readyState > 1)) connectSocket(); });
if (token) pair(token);
