import { api } from "../api.js";
import { badge, fmtDateTime, h, jsonPreview, riskBadge, statusBadge, toast } from "../ui.js";

const LEVEL_COLORS = { LOW: "#33d69f", MEDIUM: "#ffb02e", HIGH: "#ff8a4c", CRITICAL: "#ff5c6c" };

const ROLE_TIPS = {
  operator: "你是运维人员：这里只显示你自己的会话与待审批。进入「AI 运维工作台」下达任务，高风险操作会转给安全管理员审批。",
  security_admin: "你是安全管理员：可以看到全部会话与待审批。下方「待我审批」可以直接放行或驳回 AI 提出的高风险操作。",
  auditor: "你是审计管理员：只读视角。可以检索全部会话、还原完整操作链路并导出审计报告，但不能下达 AI 任务。",
};

export async function render(container, param, ctx) {
  const me = JSON.parse(localStorage.getItem("aib_user") || "{}");

  const draw = async () => {
    const data = await api.get("/api/dashboard/summary");
    container.innerHTML = "";

    container.appendChild(h("div", { class: "card", style: "margin-bottom:16px" }, [
      h("div", { class: "row", style: "justify-content:space-between" }, [
        h("h3", { style: "margin:0", text: "当前身份：" + (data.role_name || me.role) }),
        h("span", { class: "pill", text: data.scope === "mine" ? "数据范围：仅我的会话" : "数据范围：全部会话" }),
      ]),
      h("div", { class: "mute", style: "margin-top:8px", text: ROLE_TIPS[data.role] || "" }),
    ]));

    container.appendChild(h("div", { class: "grid cols-6", style: "margin-bottom:16px" }, [
      stat(data.scope === "mine" ? "我的会话（今日）" : "今日 AI 会话", data.cards.sessions_today,
        "累计 " + data.cards.sessions_total + " 条"),
      stat("待人工审批", data.cards.pending_approvals, "高风险操作等待放行"),
      stat("高风险事件", data.cards.high_risk, "HIGH / CRITICAL"),
      stat("风险阻断", data.cards.blocked, "策略拒绝 + Prompt 阻断"),
      stat("已执行工具", data.cards.executed, "经过安全网关的真实执行"),
      stat("审批通过 / 驳回", data.cards.approved + " / " + data.cards.rejected, "人工审批结果"),
    ]));

    if (data.pending_approvals.length) {
      container.appendChild(h("div", { class: "card", style: "margin-bottom:16px" }, [
        h("h3", { text: "⏳ 待我审批（" + data.pending_approvals.length + "）" }),
        h("div", { class: "mute", style: "margin-bottom:10px",
          text: "这些是 AI 提出但被策略判定为「需要人工审批」的高风险操作，在审批之前不会下发到目标服务器。" }),
        h("div", {}, data.pending_approvals.map((item) => approvalCard(item, draw))),
      ]));
    }

    const levels = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
    const max = Math.max(1, ...levels.map((level) => data.risk_by_level[level] || 0));
    container.appendChild(h("div", { class: "grid cols-2", style: "margin-bottom:16px" }, [
      h("div", { class: "card" }, [
        h("h3", { text: "风险等级分布" }),
        h("div", { class: "bars" }, levels.map((level) => h("div", { class: "bar-row" }, [
          riskBadge(level),
          h("div", { class: "bar" }, [
            h("i", { style: "width:" + Math.round(((data.risk_by_level[level] || 0) / max) * 100) +
              "%;background:" + LEVEL_COLORS[level] }),
          ]),
          h("div", { class: "mute", text: String(data.risk_by_level[level] || 0) + " 条" }),
        ]))),
        h("div", { class: "mute", style: "margin-top:14px", text: "运行模式：" +
          (data.runtime.llm_enabled ? "外部大模型" : "本地规则 Planner（演示模式）") }),
      ]),
      h("div", { class: "card" }, [
        h("h3", { text: "最近风险事件" }),
        h("div", {}, data.recent_risks.length === 0
          ? [h("div", { class: "empty", text: "暂无风险事件" })]
          : data.recent_risks.map((risk) => h("div", { class: "row",
            style: "justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid rgba(38,48,77,.5)" }, [
            h("div", { class: "row", style: "gap:8px" }, [
              riskBadge(risk.level),
              h("span", { class: "mono", text: risk.session_code || "-" }),
            ]),
            h("div", { class: "dim", style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
              text: risk.detail }),
            h("div", { class: "mute", text: (risk.user_name || "") + " " + fmtDateTime(risk.created_at).slice(11, 19) }),
          ]))),
      ]),
    ]));

    container.appendChild(h("div", { class: "card" }, [
      h("h3", { text: data.scope === "mine" ? "我的最近会话" : "最近会话" }),
      h("table", {}, [
        h("thead", {}, [h("tr", {}, ["会话编号", "用户", "Agent", "目标资产", "环境", "风险", "状态", "时间"]
          .map((title) => h("th", { text: title })))]),
        h("tbody", {}, data.recent_sessions.map((session) => h("tr", {
          class: "clickable", onclick: () => { location.hash = "#/audit/" + session.id; },
        }, [
          h("td", { class: "mono", text: session.code }),
          h("td", { text: session.user_name }),
          h("td", { text: session.agent_name }),
          h("td", { class: "mono", text: session.asset_name }),
          h("td", {}, [badge(session.env_tag === "production" ? "HIGH" : "neutral", session.env_tag)]),
          h("td", {}, [riskBadge(session.risk_level)]),
          h("td", {}, [statusBadge(session.status)]),
          h("td", { class: "mute", text: fmtDateTime(session.created_at) }),
        ]))),
      ]),
    ]));
  };

  await draw();
  ctx.on("data:refresh", draw);
}

function approvalCard(item, reload) {
  return h("div", { class: "step " + (item.risk_level || "HIGH"), style: "margin-bottom:10px" }, [
    h("div", { class: "step-head" }, [
      h("span", { class: "step-title", text: "会话 " + item.session_code }),
      h("span", { class: "mono", text: item.tool + " " + jsonPreview(item.arguments).replace(/\s+/g, " ") }),
      riskBadge(item.risk_level),
      h("span", { class: "badge APPROVAL", text: "待审批" }),
    ]),
    h("div", { class: "step-body" }, [
      h("div", { text: "发起人：" + item.user_name + " · 目标资产：" + item.asset_name }),
      h("div", {}, [h("span", { text: "命令：" }), h("code", { class: "mono", text: item.command || "-" })]),
      h("div", { text: "风险原因：" + (item.risk_reasons || []).join("；") }),
    ]),
    h("div", { class: "approve-box" }, [
      h("div", { class: "row" }, [
        h("button", { class: "ok", text: "允许执行", onclick: () => decide(item.tool_call_id, "approved", reload) }),
        h("button", { class: "danger", text: "拒绝", onclick: () => decide(item.tool_call_id, "rejected", reload) }),
        h("button", { class: "ghost small", text: "查看会话", onclick: () => { location.hash = "#/sessions/" + item.session_id; } }),
      ]),
    ]),
  ]);
}

async function decide(toolCallId, decision, reload) {
  try {
    await api.post("/api/approvals/" + toolCallId + "/decision", {
      decision, comment: decision === "approved" ? "安全管理员/发起人审批放行" : "审批驳回",
    });
    toast(decision === "approved" ? "已批准，AI 将继续执行" : "已拒绝该操作", decision === "approved" ? "ok" : "warn");
    await reload();
  } catch (err) {
    toast(err.message, "error");
  }
}

function stat(label, value, foot) {
  return h("div", { class: "stat" }, [
    h("div", { class: "label", text: label }),
    h("div", { class: "value", text: String(value) }),
    h("div", { class: "foot", text: foot }),
  ]);
}

