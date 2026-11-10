import { api, download } from "./api.js";
import { actionBadge, badge, fmtDateTime, fmtTime, h, jsonPreview, riskBadge, statusBadge, toast } from "./ui.js";

const EVENT_ICON = {
  session_created: "🚪", prompt_received: "💬", prompt_blocked: "⛔", ai_analysis: "🧠",
  ai_tool_request: "📤", policy_decision: "📜", risk_assessment: "⚠️", approval_requested: "🙋",
  approval_decided: "✅", tool_executed: "⚙️", tool_failed: "❗", tool_denied: "🚫",
  session_finished: "🏁", session_terminated: "⛔", risk_event: "⚠️", asset_updated: "🔧",
  login_success: "🔑", login_failed: "❗", demo_reset: "↺",
};

export async function renderDetail(container, sessionId) {
  const bundle = await api.get("/api/audit/sessions/" + sessionId);
  const session = bundle.session;
  const events = bundle.events || [];
  const calls = bundle.tool_calls || [];
  const approvals = bundle.approvals || [];

  container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
    h("div", { class: "row", style: "justify-content:space-between;align-items:flex-start" }, [
      h("div", {}, [
        h("div", { class: "row", style: "gap:10px" }, [
          h("span", { class: "mono", style: "font-size:16px;font-weight:700", text: session.code }),
          riskBadge(session.risk_level), statusBadge(session.status),
          badge(session.env_tag === "production" ? "HIGH" : "neutral", session.env_tag),
        ]),
        h("div", { class: "dim", style: "margin-top:8px", text: session.title || "" }),
      ]),
      h("div", { class: "row" }, [
        h("button", { class: "ghost small", text: "导出 JSON", onclick: () => exportFile(sessionId, "json", session.code) }),
        h("button", { class: "ghost small", text: "导出 CSV", onclick: () => exportFile(sessionId, "csv", session.code) }),
      ]),
    ]),
    h("div", { class: "grid cols-4", style: "margin-top:14px" }, [
      info("发起人", (session.user_name || session.username || "-") + " (" + session.username + ")"),
      info("AI Agent", session.agent_name || "-"),
      info("目标资产", session.asset_name + " · " + session.env_tag),
      info("时间", fmtDateTime(session.created_at) + " → " + fmtDateTime(session.ended_at)),
    ]),
    session.summary ? h("div", { class: "mute", style: "margin-top:12px", text: "会话结论：" + session.summary }) : null,
  ]));

  container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
    h("h3", { text: "完整操作链路（用户 → Prompt → AI → Tool → 风险 → 审批 → 命令 → 服务器 → 结果）" }),
    h("div", { class: "timeline" }, events.map((event) => timelineItem(event))),
  ]));

  container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
    h("h3", { text: "工具调用明细（" + calls.length + " 次）" }),
    calls.length === 0 ? h("div", { class: "empty", text: "本会话没有工具调用记录" }) : h("table", {}, [
      h("thead", {}, [h("tr", {}, ["#", "工具", "参数", "风险", "策略判定", "状态", "耗时", "结果"].map((t) => h("th", { text: t })))]),
      h("tbody", {}, calls.map((call) => h("tr", {}, [
        h("td", { text: String(call.seq) }),
        h("td", { class: "mono", text: call.tool }),
        h("td", { class: "mono", text: jsonPreview(call.arguments).replace(/\s+/g, " ") }),
        h("td", {}, [riskBadge(call.risk_level)]),
        h("td", {}, [actionBadge(call.decision), h("div", { class: "mute", text: call.policy_name || "" })]),
        h("td", {}, [statusBadge(call.status)]),
        h("td", { class: "mute", text: (call.duration_ms || 0) + " ms" }),
        h("td", { class: "mono", style: "max-width:420px;white-space:normal", text: String(call.result || call.error || "").slice(0, 400) }),
      ]))),
    ]),
  ]));

  if (approvals.length) {
    container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
      h("h3", { text: "人工审批记录" }),
      h("table", {}, [
        h("thead", {}, [h("tr", {}, ["审批对象", "状态", "审批人", "意见", "申请时间", "决定时间"].map((t) => h("th", { text: t })))]),
        h("tbody", {}, approvals.map((a) => {
          const call = calls.find((c) => c.id === a.tool_call_id) || {};
          return h("tr", {}, [
            h("td", { class: "mono", text: (call.tool || "-") + " " + jsonPreview(call.arguments || {}).replace(/\s+/g, " ") }),
            h("td", {}, [statusBadge(a.status)]),
            h("td", { text: a.decided_by || "-" }),
            h("td", { class: "dim", text: a.comment || "-" }),
            h("td", { class: "mute", text: fmtDateTime(a.requested_at) }),
            h("td", { class: "mute", text: fmtDateTime(a.decided_at) }),
          ]);
        })),
      ]),
    ]));
  }

  if ((bundle.risk_events || []).length) {
    container.appendChild(h("div", { class: "card" }, [
      h("h3", { text: "风险事件" }),
      (bundle.risk_events || []).map((risk) => h("div", { class: "row", style: "gap:10px;padding:8px 0;border-bottom:1px solid rgba(38,48,77,.5)" }, [
        riskBadge(risk.level), badge("neutral", risk.category),
        h("div", { class: "dim", style: "flex:1", text: risk.detail }),
        h("div", { class: "mute", text: fmtDateTime(risk.created_at) }),
      ])),
    ]));
  }
}

function info(label, value) {
  return h("div", {}, [
    h("div", { class: "mute", text: label }),
    h("div", { style: "margin-top:4px;font-size:13.5px", text: value || "-" }),
  ]);
}

function timelineItem(event) {
  const payload = event.payload || {};
  const detail = describe(event.event_type, payload);
  const tone = ["tool_denied", "prompt_blocked", "tool_failed"].includes(event.event_type) ? "HIGH"
    : (event.event_type === "tool_executed" || event.event_type === "approval_decided" ? "good" : "");
  return h("div", { class: "tl-item" }, [
    h("div", { class: "tl-time", text: fmtTime(event.ts) }),
    h("div", { class: "tl-dot " + tone }, [h("span")]),
    h("div", { class: "tl-content" }, [
      h("div", { class: "tl-title", text: (EVENT_ICON[event.event_type] || "•") + " " + (event.label || event.event_type) }),
      detail ? h("div", { class: "tl-detail", text: detail }) : null,
      payload.command ? h("div", { class: "tl-detail mono", text: "$ " + payload.command }) : null,
      payload.stdout_excerpt ? h("details", {}, [
        h("summary", { class: "mute", text: "查看原始输出" }),
        h("pre", { class: "console", text: payload.stdout_excerpt }),
      ]) : null,
    ]),
  ]);
}

function describe(type, payload) {
  switch (type) {
    case "session_created":
      return "用户 " + (payload.user || "-") + " 使用 " + (payload.agent || "-") + " 访问 " + (payload.asset || "-");
    case "prompt_received":
      return "用户任务：" + (payload.prompt || "");
    case "prompt_blocked":
      return "风险等级 " + payload.level + "，命中：" + (payload.reasons || []).join("；");
    case "ai_tool_request":
      return "AI 请求调用 " + payload.tool + "(" + jsonPreview(payload.arguments || {}).replace(/\s+/g, " ") + ")，原因：" + (payload.reason || "");
    case "risk_assessment":
      return "风险等级 " + payload.level + "：" + (payload.reasons || []).join("；");
    case "policy_decision":
      return "策略「" + (payload.policy || "-") + "」判定为 " + payload.decision + "：" + (payload.reason || "");
    case "approval_requested":
      return "为高风险操作 " + payload.tool + "（" + payload.risk_level + "）发起人工审批";
    case "approval_decided":
      return "审批结果：" + payload.status + "，审批人：" + (payload.decided_by || "-") + "，意见：" + (payload.comment || "-");
    case "tool_executed":
      return "在 " + (payload.target || "-") + " 上执行完成（" + payload.status + "，" + (payload.duration_ms || 0) + " ms）：" + (payload.summary || "");
    case "tool_denied":
      return "被拦截：" + (payload.reason || "");
    case "tool_failed":
      return "执行失败：" + (payload.error || "");
    case "ai_analysis":
      return String(payload.content || "").slice(0, 300);
    case "risk_event":
      return payload.detail || "";
    case "session_finished":
    case "session_terminated":
      return "会话状态：" + (payload.status || type);
    default:
      return "";
  }
}

async function exportFile(sessionId, format, code) {
  try {
    await download("/api/audit/sessions/" + sessionId + "/export?format=" + format,
      "audit-" + code + "." + format);
    toast("审计报告已导出", "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

