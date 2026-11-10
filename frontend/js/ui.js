export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  appendChildren(el, children);
  return el;
}

/** 递归展开子节点数组，自动跳过 null / false（条件渲染的常见写法） */
function appendChildren(el, children) {
  if (Array.isArray(children)) {
    children.forEach((child) => appendChildren(el, child));
    return;
  }
  if (children === null || children === undefined || children === false || children === true) return;
  if (typeof children === "string" || typeof children === "number") {
    el.appendChild(document.createTextNode(String(children)));
    return;
  }
  el.appendChild(children);
}

export function escapeHtml(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>");
}

/** 极简 Markdown：加粗、行内代码、列表、引用 —— 够用且无依赖 */
export function mdLite(text) {
  const lines = escapeHtml(text || "").split("\n");
  let html = "";
  let inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (/^\s*[-·]\s+/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + inline(line.replace(/^\s*[-·]\s+/, "")) + "</li>";
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    if (/^&gt;\s?/.test(line)) { html += "<blockquote>" + inline(line.replace(/^&gt;\s?/, "")) + "</blockquote>"; continue; }
    if (line === "") { html += "<br>"; continue; }
    html += inline(line) + "<br>";
  }
  if (inList) html += "</ul>";
  return html;
}

export function badge(value, extra = "") {
  const label = String(value || "-");
  return h("span", { class: "badge " + label + (extra ? " " + extra : ""), text: label });
}

export function riskBadge(level) {
  const map = { LOW: "低风险", MEDIUM: "中风险", HIGH: "高风险", CRITICAL: "严重" };
  return h("span", { class: "badge " + (level || "LOW"), text: (map[level] || level || "-") });
}

export function actionBadge(action) {
  const map = { ALLOW: "自动放行", APPROVAL: "需人工审批", DENY: "直接拒绝" };
  return h("span", { class: "badge " + action, text: map[action] || action });
}

export function statusBadge(status) {
  const map = {
    active: "进行中", waiting_approval: "等待审批", finished: "已结束", terminated: "已终止",
    success: "成功", failed: "失败", denied: "已拒绝", blocked: "已阻断", pending: "待处理",
    running: "执行中", approved: "已批准", rejected: "已驳回", expired: "已过期",
    enabled: "已启用", disabled: "已停用", online: "在线", offline: "离线",
  };
  return h("span", { class: "badge " + (status || "neutral"), text: map[status] || status || "-" });
}

export function fmtTime(iso) {
  if (!iso) return "-";
  const text = String(iso);
  const match = text.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : text.slice(11, 19) || text;
}

export function fmtDateTime(iso) {
  if (!iso) return "-";
  return String(iso).replace("T", " ").slice(0, 19);
}

export function toast(message, kind = "") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: "toast " + kind, text: message });
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, 3600);
  setTimeout(() => el.remove(), 4200);
}

export function modal(nodeOrHtml) {
  const wrap = document.getElementById("modal");
  const body = document.getElementById("modal-body");
  body.innerHTML = "";
  if (typeof nodeOrHtml === "string") body.innerHTML = nodeOrHtml;
  else body.appendChild(nodeOrHtml);
  wrap.classList.remove("hidden");
  wrap.onclick = (event) => { if (event.target === wrap) closeModal(); };
}

export function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

export function jsonPreview(value) {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

export function section(title, right = null) {
  return h("div", { class: "row", style: "justify-content:space-between;margin-bottom:12px" }, [
    h("h3", { text: title, style: "margin:0;font-size:14px" }),
    right || h("span"),
  ]);
}
