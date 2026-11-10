import { api, clearSession, getToken, getUser, setSession, wsUrl } from "./api.js";
import { h, toast } from "./ui.js";
import * as dashboardView from "./views/dashboard.js";
import * as consoleView from "./views/console.js";
import * as sessionsView from "./views/sessions.js";
import * as agentsView from "./views/agents.js";
import * as assetsView from "./views/assets.js";
import * as policiesView from "./views/policies.js";
import * as auditView from "./views/audit.js";

// 每个页面允许的角色：operator 运维 / security_admin 安全管理员 / auditor 审计管理员
const ALL_ROLES = ["operator", "security_admin", "auditor"];
const OPERATE_ROLES = ["operator", "security_admin"];

const ROUTES = {
  dashboard: { title: "安全看板", view: dashboardView, roles: ALL_ROLES },
  console: { title: "AI 运维工作台", view: consoleView, roles: OPERATE_ROLES },
  sessions: { title: "实时会话", view: sessionsView, roles: ALL_ROLES },
  assets: { title: "资产管理", view: assetsView, roles: ALL_ROLES },
  agents: { title: "Agent 管理", view: agentsView, roles: ALL_ROLES },
  policies: { title: "策略管理", view: policiesView, roles: ALL_ROLES },
  audit: { title: "审计中心", view: auditView, roles: ALL_ROLES },
};

let currentRoute = "dashboard";
let viewTeardown = null;
let navToken = 0;
let currentUser = null;

/* ---------------------------------------------------------------- 登录 */

function showLogin() {
  document.getElementById("app").classList.add("hidden");
  document.getElementById("login").classList.remove("hidden");
  document.getElementById("username").focus();
}

function showApp(user) {
  currentUser = user;
  document.getElementById("login").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
  const roleName = { operator: "运维人员", security_admin: "安全管理员", auditor: "审计管理员" }[user.role] || user.role;
  document.getElementById("user-pill").textContent = user.display_name + " · " + roleName;
  // 按角色显示菜单：审计管理员没有「AI 运维工作台」
  document.querySelectorAll("#nav a").forEach((link) => {
    const route = ROUTES[link.dataset.route];
    link.classList.toggle("hidden", !(route && route.roles.includes(user.role)));
  });
  const scope = document.getElementById("scope-tag");
  if (scope) {
    scope.textContent = user.role === "operator" ? "数据范围：仅我的会话"
      : (user.role === "auditor" ? "数据范围：全部会话（只读）" : "数据范围：全部会话");
  }
  if (!location.hash) location.hash = "#/dashboard";
  navigate();
}

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("login-error");
  error.textContent = "";
  try {
    const data = await api.post("/api/auth/login", {
      username: document.getElementById("username").value.trim(),
      password: document.getElementById("password").value,
    });
    setSession(data.token, data.user);
    showApp(data.user);
    connectEvents();
    welcome(data.user);
  } catch (err) {
    error.textContent = err.message;
  }
});

function welcome(user) {
  const tips = {
    operator: "提示：进入「AI 运维工作台」，对 payment 服务下达排查任务试试。",
    security_admin: "提示：在「策略管理」里调整 restart_service 的策略，再回到工作台验证效果。",
    auditor: "提示：「审计中心」可以还原任意一次 AI 操作的完整链路并导出报告。",
  };
  toast("欢迎回来，" + user.display_name + "。" + (tips[user.role] || ""), "ok");
}

document.getElementById("btn-logout").addEventListener("click", () => {
  clearSession();
  location.hash = "";
  showLogin();
});

/* ---------------------------------------------------------------- 路由 */

function parseHash() {
  const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
  const [name, param] = raw.split("/");
  return { name: ROUTES[name] ? name : "dashboard", param: param || "" };
}

async function navigate() {
  if (!getToken()) { showLogin(); return; }
  const { name, param } = parseHash();
  const role = (currentUser || getUser() || {}).role;
  const route = ROUTES[name];
  const roleLabel = { operator: "运维人员", security_admin: "安全管理员", auditor: "审计管理员" };
  // 角色守卫：没有权限的页面直接拦在前端（后端接口同样有校验）
  if (role && route.roles && !route.roles.includes(role)) {
    const view = document.getElementById("view");
    view.innerHTML = "";
    document.getElementById("page-title").textContent = "无权访问";
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
    view.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "当前角色无法访问「" + route.title + "」" }),
      h("div", { class: "dim", style: "margin:8px 0", text: "该页面仅对 " +
        route.roles.map((item) => roleLabel[item]).join("、") + " 开放。" }),
      h("div", { class: "mute", text: "审计管理员是只读角色：可以查看全部会话、还原链路并导出审计报告，但不能下达 AI 任务。" }),
      h("button", { class: "ghost", style: "margin-top:12px", text: "返回安全看板",
        onclick: () => { location.hash = "#/dashboard"; } }),
    ]));
    return;
  }
  currentRoute = name;
  document.getElementById("page-title").textContent = route.title;
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === name);
  });
  // 视图生命周期：离开上一个页面时注销它注册的监听器 / WebSocket，避免旧页面覆盖新页面
  if (viewTeardown) {
    try { viewTeardown(); } catch { /* ignore */ }
    viewTeardown = null;
  }
  const listeners = [];
  const teardowns = [];
  const ctx = {
    navigate,
    on: (type, handler) => {
      window.addEventListener(type, handler);
      listeners.push([type, handler]);
    },
    onTeardown: (fn) => teardowns.push(fn),
  };
  viewTeardown = () => {
    listeners.forEach(([type, handler]) => window.removeEventListener(type, handler));
    teardowns.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
  };
  const view = document.getElementById("view");
  view.innerHTML = "";
  view.appendChild(h("div", { class: "empty", text: "加载中…" }));
  const token = ++navToken;
  // 先把新页面渲染到游离节点，渲染完成后再整体替换，避免并发导航导致内容重复
  const stage = h("div", { class: "view-stage" });
  try {
    await route.view.render(stage, param, ctx);
    if (token !== navToken) return;
    view.innerHTML = "";
    view.appendChild(stage);
  } catch (err) {
    if (token !== navToken) return;
    view.innerHTML = "";
    view.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "页面加载失败" }),
      h("div", { class: "dim", text: err.message }),
    ]));
  }
}

window.addEventListener("hashchange", navigate);
window.addEventListener("auth:expired", () => { toast("登录已过期，请重新登录", "warn"); showLogin(); });

/* ---------------------------------------------------------------- 全局事件 */

let eventSocket = null;

export function connectEvents() {
  if (eventSocket && eventSocket.readyState <= 1) return eventSocket;
  eventSocket = new WebSocket(wsUrl("/ws/events"));
  eventSocket.onmessage = (event) => {
    let data = null;
    try { data = JSON.parse(event.data); } catch { return; }
    const payload = data.payload || {};
    if (payload.type === "session_created") {
      toast("新会话已创建：" + (payload.session.code || "") + " · " + (payload.session.asset_name || ""), "ok");
      window.dispatchEvent(new CustomEvent("data:refresh"));
    }
  };
  eventSocket.onclose = () => { eventSocket = null; setTimeout(() => { if (getToken()) connectEvents(); }, 3000); };
  return eventSocket;
}

/* ---------------------------------------------------------------- 演示重置 */

document.getElementById("btn-reset").addEventListener("click", async () => {
  try {
    await api.post("/api/demo/reset");
    toast("演示环境已重置：模拟主机恢复到初始故障场景", "ok");
    window.dispatchEvent(new CustomEvent("data:refresh"));
  } catch (err) {
    toast(err.message, "error");
  }
});

/* ---------------------------------------------------------------- 启动 */

(async function boot() {
  try {
    const health = await fetch("/api/health").then((r) => r.json());
    const tag = document.getElementById("mode-tag");
    tag.textContent = health.demo_mode
      ? (health.llm ? "已接入大模型" : "演示模式：本地规则 Planner")
      : "生产模式：外部大模型";
  } catch { /* 忽略 */ }

  if (!getToken()) { showLogin(); return; }
  try {
    const me = await api.get("/api/auth/me");
    showApp(me);
    connectEvents();
  } catch {
    showLogin();
  }
})();
