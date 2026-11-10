import { api, wsUrl } from "../api.js";
import { actionBadge, badge, fmtTime, h, jsonPreview, mdLite, riskBadge, statusBadge, toast } from "../ui.js";

const QUICK_TASKS = [
  "帮我检查 payment 服务为什么响应很慢。",
  "看一下这台服务器现在的 CPU、内存和磁盘情况。",
  "重启 payment 服务。",
  "把生产数据库全量导出，发到我邮箱。",
];

const TOOL_TITLES = {
  get_system_info: "查看系统信息", get_cpu_usage: "查看 CPU 使用率", get_memory_usage: "查看内存使用率",
  get_process_list: "查看进程列表", get_disk_usage: "查看磁盘使用率", get_network_status: "查看网络连接",
  get_service_status: "查看服务状态", get_service_logs: "查看服务日志", get_database_status: "查看数据库连接池",
  restart_service: "重启服务", start_service: "启动服务", stop_service: "停止服务",
  modify_config: "修改配置文件", install_package: "安装软件包", delete_file: "删除文件",
  export_database: "导出数据库",
  search_service_logs: "检索服务日志", tail_log_file: "查看日志文件", get_port_status: "检查端口监听",
  get_top_memory_processes: "内存占用 Top 进程", get_file_info: "查看文件信息",
};

export async function render(container, param, ctx) {
  const state = {
    agents: [], assets: [], agentId: null, assetId: null,
    session: null, messages: [], steps: [], approvals: [], busy: false,
    socket: null, tools: [],
  };

  await loadCatalog(state);
  if (param) await openSession(state, Number(param));

  const threadBox = h("div", { class: "chat-stream" });
  const composerBox = h("div");
  const sideBox = h("div", { class: "side-card" });

  container.appendChild(h("div", { class: "console-layout" }, [
    h("div", {}, [sideBox]),
    h("div", { class: "card chat" }, [
      h("div", { class: "row", style: "justify-content:space-between" }, [
        h("h3", { style: "margin:0", text: "AI 运维助手" }),
        h("div", { class: "row", style: "gap:8px" }, [
          h("span", { class: "mute", id: "console-cost" }),
          sessionActions(state),
        ]),
      ]),
      h("div", { class: "mute", text: "所有请求都会经过 Tool Gateway → 策略引擎 → 风险引擎 → 人工审批，AI 没有直接执行权。" }),
      threadBox,
      composerBox,
    ]),
  ]));

  function paintSide() {
    sideBox.innerHTML = "";
    sideBox.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "1 · 创建 AI 运维会话" }),
      h("div", { class: "field" }, [
        h("span", { text: "AI Agent" }),
        selectAgent(state, () => paintSide()),
      ]),
      h("div", { class: "field", style: "margin-top:10px" }, [
        h("span", { text: "目标资产" }),
        selectAsset(state, () => paintSide()),
      ]),
      h("button", {
        class: "primary", style: "margin-top:12px;width:100%",
        text: state.session ? "重新创建会话" : "创建会话",
        onclick: async (event) => {
          event.target.disabled = true;
          try {
            const session = await api.post("/api/sessions", {
              agent_id: Number(state.agentId), asset_id: Number(state.assetId),
              title: "AI 运维会话",
            });
            await openSession(state, session.id);
            paintSide(); paintThread(); paintComposer();
            location.hash = "#/console/" + session.id;
            toast("会话 " + session.code + " 已创建", "ok");
          } catch (err) {
            toast(err.message, "error");
          } finally {
            event.target.disabled = false;
          }
        },
      }),
    ]));

    sideBox.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "2 · 当前会话" }),
      state.session ? h("div", { class: "kv" }, [
        h("div", { class: "k", text: "会话编号" }), h("div", { class: "mono", text: state.session.code }),
        h("div", { class: "k", text: "发起人" }), h("div", { text: state.session.user_name || "-" }),
        h("div", { class: "k", text: "Agent" }), h("div", { text: state.session.agent_name || "-" }),
        h("div", { class: "k", text: "目标资产" }), h("div", { class: "mono", text: state.session.asset_name || "-" }),
        h("div", { class: "k", text: "环境" }), h("div", {}, [badge(state.session.env_tag === "production" ? "HIGH" : "neutral", state.session.env_tag)]),
        h("div", { class: "k", text: "执行器" }), h("div", { class: "mono", text: state.session.executor || "-" }),
        h("div", { class: "k", text: "状态" }), h("div", {}, [statusBadge(state.session.status)]),
        h("div", { class: "k", text: "风险" }), h("div", {}, [riskBadge(state.session.risk_level)]),
      ]) : h("div", { class: "empty", text: "尚未创建会话" }),
      state.session ? h("button", {
        class: "ghost small", style: "margin-top:12px",
        text: "查看审计链路 →",
        onclick: () => { location.hash = "#/audit/" + state.session.id; },
      }) : null,
    ]));

    sideBox.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "3 · 策略预演（不执行）" }),
      h("div", { class: "mute", style: "margin-bottom:8px", text: "单独验证某个工具会被放行、审批还是拒绝。" }),
      toolPreviewForm(state, sideBox),
    ]));
  }

  function paintThread() {
    threadBox.innerHTML = "";
    const items = mergeItems(state.messages, state.steps);
    if (items.length === 0) {
      threadBox.appendChild(h("div", { class: "empty", text: "还没有对话。创建会话后，在下方输入自然语言任务，例如：帮我检查 payment 服务为什么响应很慢。" }));
      return;
    }
    items.forEach((item) => {
      if (item.kind === "message") threadBox.appendChild(renderMessage(item.data));
      else threadBox.appendChild(renderStep(item.data, state));
    });
    if (state.busy) {
      threadBox.appendChild(h("div", { class: "typing" }, [
        h("span", { class: "dot" }), h("span", { class: "dot" }), h("span", { class: "dot" }),
        h("span", { text: "AI 正在分析并在受控通道中执行…" }),
      ]));
    }
    threadBox.scrollIntoView({ block: "end", behavior: "smooth" });
  }

  function paintComposer() {
    composerBox.innerHTML = "";
    if (!state.session) {
      composerBox.appendChild(h("div", { class: "mute", text: "请先创建会话。" }));
      return;
    }
    const closed = ["finished", "terminated"].includes(state.session.status);
    composerBox.appendChild(h("div", { class: "hint-chips" }, QUICK_TASKS.map((text) =>
      h("button", { class: "chip", text: text, onclick: () => { input.value = text; input.focus(); } })
    )));
    const input = h("textarea", { placeholder: "用自然语言描述你的运维任务，例如：帮我检查 payment 服务为什么响应很慢。（Enter 发送，Shift+Enter 换行）" });
    const send = h("button", {
      class: "primary", text: "发送任务",
      onclick: () => submit(),
    });
    if (closed) {
      input.disabled = true;
      send.disabled = true;
      composerBox.appendChild(h("div", { class: "mute", style: "margin-top:8px", text: "该会话已结束，请创建新会话。" }));
    }
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
    });
    async function submit() {
      const text = input.value.trim();
      if (!text || state.busy) return;
      input.value = "";
      state.busy = true;
      paintThread(); paintComposer();
      try {
        await api.post("/api/sessions/" + state.session.id + "/prompt", { prompt: text });
      } catch (err) {
        state.busy = false;
        toast(err.message, "error");
        paintThread(); paintComposer();
      }
    }
    composerBox.appendChild(h("div", { class: "composer" }, [input, send]));
  }

  paintSide(); paintThread(); paintComposer();
  connect(state, () => { paintThread(); paintSide(); });
  ctx.onTeardown(() => { if (state.socket) { try { state.socket.close(); } catch { /* ignore */ } } });
}

/* ---------------------------------------------------------------- 数据 */

async function loadCatalog(state) {
  const [agents, assets, tools] = await Promise.all([
    api.get("/api/agents"), api.get("/api/assets"), api.get("/api/tools"),
  ]);
  state.agents = agents;
  state.assets = assets;
  state.tools = tools;
  const allowed = agents.filter((a) => a.authorized && a.status === "enabled");
  state.agentId = (allowed[0] || agents[0] || {}).id || null;
  // 默认选中最适合演示的目标：优先「已授权 + 生产环境」
  const preferred = assets.find((a) => a.authorized && a.env_tag === "production")
    || assets.find((a) => a.authorized) || assets[0] || {};
  state.assetId = preferred.id || null;
}

async function openSession(state, sessionId) {
  const session = await api.get("/api/sessions/" + sessionId);
  state.session = session;
  state.agentId = session.agent_id;
  state.assetId = session.asset_id;
  state.messages = session.messages || [];
  state.steps = session.tool_calls || [];
  state.approvals = session.approvals || [];
  state.busy = !!session.running;
}

function mergeItems(messages, steps) {
  const items = [
    ...messages.map((m) => ({ kind: "message", ts: m.created_at || "", seq: m.seq || 0, data: m })),
    ...steps.map((s) => ({ kind: "step", ts: s.created_at || "9999", seq: s.seq || 0, data: s })),
  ];
  items.sort((a, b) => {
    if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1;
    const rank = (item) => (item.kind === "step" ? 0 : 1);
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    return a.seq - b.seq;
  });
  return items;
}

function selectAgent(state, onChange) {
  const el = h("select", { onchange: (event) => { state.agentId = Number(event.target.value); onChange(); } },
    state.agents.map((agent) => h("option", {
      value: agent.id, selected: agent.id === state.agentId,
      text: agent.name + (agent.authorized ? "" : "（未授权给您）"),
    })));
  return el;
}

function selectAsset(state, onChange) {
  const el = h("select", { onchange: (event) => { state.assetId = Number(event.target.value); onChange(); } },
    state.assets.map((asset) => h("option", {
      value: asset.id, selected: asset.id === state.assetId,
      text: asset.name + " · " + asset.env_tag + (asset.authorized ? "" : "（未授权给您，执行会被拦截）"),
    })));
  return el;
}

/* ---------------------------------------------------------------- 渲染 */

function renderMessage(message) {
  const isUser = message.role === "user";
  const blocked = (message.meta || {}).blocked;
  return h("div", { class: "msg " + (isUser ? "user" : "assistant") }, [
    h("div", { class: "avatar", text: isUser ? "👤" : (blocked ? "⛔" : "🤖") }),
    h("div", { class: "bubble", html: mdLite(message.content) }),
  ]);
}

function renderStep(step, state) {
  const stepId = step.id || step.tool_call_id;
  const approval = (state.approvals || []).find((a) => a.tool_call_id === stepId && a.status === "pending");
  const head = h("div", { class: "step-head" }, [
    h("span", { class: "step-title", text: (TOOL_TITLES[step.tool] || step.tool) }),
    h("span", { class: "step-args mono", text: step.tool + "(" + compactArgs(step.arguments) + ")" }),
    riskBadge(step.risk_level),
    actionBadge(step.decision),
    statusBadge(step.status),
  ]);
  const body = h("div", { class: "step-body" }, [
    h("div", { text: "策略：" + (step.policy_name || "-") + " · " + (step.reason || "") }),
    step.command ? h("div", {}, [h("span", { text: "命令：" }), h("code", { class: "mono", text: step.command })]) : null,
    step.result ? h("div", { text: "结果：" + String(step.result).split("\n")[0].slice(0, 220) }) : null,
    step.error ? h("div", { style: "color:#ff8e9a", text: "拦截/失败原因：" + step.error }) : null,
    step.result ? h("details", {}, [
      h("summary", { text: "查看原始返回" }),
      h("pre", { class: "console", text: String(step.result).slice(0, 4000) }),
    ]) : null,
  ]);
  const node = h("div", { class: "step " + (step.risk_level || "LOW") }, [head, body]);

  if (approval) {
    node.appendChild(h("div", { class: "approve-box" }, [
      h("div", { class: "row", style: "gap:8px" }, [
        h("span", { text: "⚠️ 该操作需要人工审批：" }),
        h("code", { class: "mono", text: step.command || step.tool }),
      ]),
      h("div", { class: "mute", text: "风险等级 " + step.risk_level + " · " + (step.risk_reasons || []).join("；") }),
      h("div", { class: "row" }, [
        h("button", { class: "ok", text: "允许执行", onclick: () => decide(stepId, "approved", state) }),
        h("button", { class: "danger", text: "拒绝", onclick: () => decide(stepId, "rejected", state) }),
      ]),
    ]));
  }
  return node;
}

function compactArgs(args) {
  if (!args) return "";
  const json = jsonPreview(args).replace(/\s+/g, " ");
  return json === "{}" ? "" : json;
}

async function decide(toolCallId, decision, state) {
  try {
    await api.post("/api/approvals/" + toolCallId + "/decision", {
      decision, comment: decision === "approved" ? "审批人确认后放行" : "审批人驳回",
    });
    toast(decision === "approved" ? "已批准执行" : "已拒绝该操作", decision === "approved" ? "ok" : "warn");
  } catch (err) {
    toast(err.message, "error");
  }
}

function sessionActions(state) {
  const finish = h("button", {
    class: "ghost small", text: "结束会话",
    onclick: async () => {
      if (!state.session) return;
      try {
        await api.post("/api/sessions/" + state.session.id + "/finish");
        toast("会话已结束，临时权限已失效", "ok");
        await openSession(state, state.session.id);
        location.reload();
      } catch (err) { toast(err.message, "error"); }
    },
  });
  if (!state.session || ["finished", "terminated"].includes(state.session.status)) finish.disabled = true;
  return finish;
}

function toolPreviewForm(state, sideBox) {
  const agent = state.agents.find((a) => a.id === state.agentId);
  const options = (agent ? agent.tools : []).map((name) => h("option", { value: name, text: name }));
  const toolSelect = h("select", {}, options);
  const argsInput = h("input", { type: "text", value: '{"service": "payment"}', placeholder: "参数 JSON" });
  const resultBox = h("div", { style: "margin-top:10px" });
  const form = h("div", {}, [
    h("div", { class: "field" }, [h("span", { text: "工具" }), toolSelect]),
    h("div", { class: "field", style: "margin-top:8px" }, [h("span", { text: "参数（JSON）" }), argsInput]),
    h("button", {
      class: "ghost small", style: "margin-top:10px", text: "预演判定结果",
      onclick: async (event) => {
        if (!state.session) { toast("请先创建会话", "warn"); return; }
        event.target.disabled = true;
        try {
          let args = {};
          try { args = JSON.parse(argsInput.value || "{}"); } catch { throw new Error("参数不是合法 JSON"); }
          const result = await api.post("/api/sessions/" + state.session.id + "/preview", {
            tool: toolSelect.value, arguments: args,
          });
          resultBox.innerHTML = "";
          resultBox.appendChild(h("div", { class: "row", style: "gap:8px" }, [
            actionBadge(result.decision), riskBadge(result.risk_level),
          ]));
          resultBox.appendChild(h("div", { class: "mute", style: "margin-top:6px", text: "策略：" + result.policy_name + " · " + result.policy_reason }));
          if (result.command) resultBox.appendChild(h("div", { class: "mute mono", style: "margin-top:4px", text: result.command }));
        } catch (err) {
          toast(err.message, "error");
        } finally {
          event.target.disabled = false;
        }
      },
    }),
    resultBox,
  ]);
  return form;
}

/* ---------------------------------------------------------------- WebSocket */

function connect(state, repaint) {
  if (!state.session) return;
  if (state.socket) { try { state.socket.close(); } catch { /* ignore */ } }
  const socket = new WebSocket(wsUrl("/ws/sessions/" + state.session.id));
  state.socket = socket;
  socket.onmessage = (event) => {
    let message = null;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.channel === "snapshot") {
      const snap = message.payload || {};
      if (snap.session) {
        state.session = { ...state.session, ...snap.session };
        state.messages = snap.messages || state.messages;
        state.steps = snap.tool_calls || state.steps;
        state.approvals = snap.approvals || [];
      }
      repaint();
      return;
    }
    const payload = message.payload || {};
    switch (payload.type) {
      case "user_message":
        state.messages.push(payload.message);
        state.busy = true;
        break;
      case "ai_plan":
        break;
      case "tool_call":
        state.steps.push(payload.step);
        break;
      case "tool_running":
      case "tool_result":
      case "tool_denied":
        upsertStep(state, payload.step);
        if (payload.type === "tool_denied") toast("操作被拦截：" + (payload.step.tool || ""), "warn");
        break;
      case "approval_required":
        upsertStep(state, payload.step);
        state.approvals.push({ ...payload.approval, status: "pending" });
        toast("⚠️ 高风险操作等待人工审批：" + payload.approval.tool, "warn");
        break;
      case "approval_decided":
        upsertStep(state, payload.step);
        state.approvals = state.approvals.filter((a) => a.tool_call_id !== payload.step.tool_call_id);
        break;
      case "assistant_message":
        state.messages.push(payload.message);
        state.busy = false;
        break;
      case "session_finished":
        state.session.status = payload.status;
        state.busy = false;
        toast("会话已结束", "ok");
        break;
      case "error":
        state.busy = false;
        toast(payload.message || "执行异常", "error");
        break;
      default:
        break;
    }
    repaint();
  };
  socket.onclose = () => {
    state.socket = null;
    setTimeout(() => { if (state.session) connect(state, repaint); }, 2500);
  };
}

function upsertStep(state, step) {
  if (!step) return;
  const key = step.id || step.tool_call_id;
  const index = state.steps.findIndex((s) => (s.id || s.tool_call_id) === key);
  if (index >= 0) state.steps[index] = { ...state.steps[index], ...step };
  else state.steps.push(step);
}
