import { api } from "../api.js";
import { badge, fmtDateTime, h, riskBadge, statusBadge } from "../ui.js";
import { renderDetail } from "../session_detail.js";

export async function render(container, param) {
  if (param) {
    const back = h("button", { class: "ghost small", style: "margin-bottom:12px", text: "← 返回审计列表", onclick: () => { location.hash = "#/audit"; } });
    container.appendChild(back);
    await renderDetail(container, Number(param));
    return;
  }

  const state = { keyword: "", risk_level: "", status: "", agent: "" };
  const filters = h("div", { class: "filters" });
  const tableBox = h("div", { class: "card" });

  const keyword = h("input", { type: "search", placeholder: "搜索会话编号 / 用户 / 任务", value: state.keyword });
  const risk = h("select", {}, [h("option", { value: "", text: "全部风险等级" }),
    ...["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((l) => h("option", { value: l, text: l }))]);
  const status = h("select", {}, [h("option", { value: "", text: "全部状态" }),
    ...["active", "waiting_approval", "finished", "terminated"].map((s) => h("option", { value: s, text: s }))]);
  const refresh = h("button", { class: "primary small", text: "查询", onclick: () => load() });

  filters.appendChild(h("div", { class: "field" }, [h("span", { text: "关键字" }), keyword]));
  filters.appendChild(h("div", { class: "field" }, [h("span", { text: "风险等级" }), risk]));
  filters.appendChild(h("div", { class: "field" }, [h("span", { text: "会话状态" }), status]));
  filters.appendChild(refresh);

  container.appendChild(h("div", {}, [
    h("div", { class: "card", style: "margin-bottom:14px" }, [
      h("h3", { text: "审计检索" }),
      h("div", { class: "mute", style: "margin-bottom:10px", text: "支持按用户、Agent、风险等级、状态检索；点击任意会话可还原完整链路并导出报告。" }),
      filters,
    ]),
    tableBox,
  ]));

  keyword.addEventListener("keydown", (event) => { if (event.key === "Enter") load(); });

  async function load() {
    const query = new URLSearchParams();
    if (keyword.value) query.set("keyword", keyword.value);
    if (risk.value) query.set("risk_level", risk.value);
    if (status.value) query.set("status", status.value);
    const sessions = await api.get("/api/audit/sessions?" + query.toString());
    tableBox.innerHTML = "";
    tableBox.appendChild(h("h3", { text: "会话审计（" + sessions.length + " 条）" }));
    if (sessions.length === 0) {
      tableBox.appendChild(h("div", { class: "empty", text: "没有符合条件的会话" }));
      return;
    }
    tableBox.appendChild(h("table", {}, [
      h("thead", {}, [h("tr", {}, ["会话编号", "时间", "用户", "Agent", "目标资产", "任务", "风险", "工具", "审批(通过/驳回)", "状态"].map((t) => h("th", { text: t })))]),
      h("tbody", {}, sessions.map((s) => h("tr", {
        class: "clickable", onclick: () => { location.hash = "#/audit/" + s.id; },
      }, [
        h("td", { class: "mono", text: s.code }),
        h("td", { class: "mute", text: fmtDateTime(s.created_at) }),
        h("td", { text: s.user_name }),
        h("td", { text: s.agent_name }),
        h("td", { class: "mono", text: s.asset_name }),
        h("td", { style: "max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", text: s.title }),
        h("td", {}, [riskBadge(s.risk_level)]),
        h("td", { text: String(s.tool_count) }),
        h("td", { text: String(s.approved_count) + " / " + String(s.rejected_count) }),
        h("td", {}, [statusBadge(s.status)]),
      ]))),
    ]));
  }

  await load();
}

