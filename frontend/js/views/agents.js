import { api } from "../api.js";
import { badge, h, modal, closeModal, statusBadge, toast } from "../ui.js";

export async function render(container, param, ctx) {
  const [agents, assets, tools, users] = await Promise.all([
    api.get("/api/agents"), api.get("/api/assets"), api.get("/api/tools"), api.get("/api/users"),
  ]);
  const me = JSON.parse(localStorage.getItem("aib_user") || "{}");
  const isAdmin = me.role === "security_admin";

  container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
    h("h3", { text: "AI Agent 管理" }),
    h("div", { class: "mute", text: "Agent 本身也是一个需要被管理的身份：它拥有独立的模型、工具白名单、可访问资产与授权用户。" +
      "Agent 的权限永远无法超过使用它的用户权限。" }),
  ]));

  container.appendChild(h("div", { class: "grid cols-2" }, agents.map((agent) => h("div", { class: "card" }, [
    h("div", { class: "row", style: "justify-content:space-between" }, [
      h("div", { class: "row", style: "gap:8px" }, [
        h("span", { style: "font-weight:700", text: agent.name }),
        statusBadge(agent.status),
        agent.authorized ? badge("ALLOW", "已授权给您") : badge("DENY", "未授权给您"),
      ]),
      isAdmin ? h("button", {
        class: "ghost small", text: "编辑权限",
        onclick: () => editAgent(agent, assets, tools, users, () => { location.reload(); }),
      }) : h("span"),
    ]),
    h("div", { class: "dim", style: "margin-top:6px", text: agent.description }),
    h("div", { class: "kv", style: "margin-top:12px" }, [
      h("div", { class: "k", text: "模型" }), h("div", { class: "mono", text: agent.model }),
      h("div", { class: "k", text: "历史会话" }), h("div", { text: String(agent.session_count) + " 次" }),
    ]),
    h("div", { style: "margin-top:12px" }, [
      h("div", { class: "mute", text: "可访问资产" }),
      h("div", { class: "row", style: "margin-top:6px" }, agent.assets.length
        ? agent.assets.map((asset) => badge(asset.env_tag === "production" ? "HIGH" : "neutral", asset.name + " · " + asset.executor))
        : [h("span", { class: "mute", text: "无" })]),
    ]),
    h("div", { style: "margin-top:12px" }, [
      h("div", { class: "mute", text: "工具白名单（" + agent.tools.length + "）" }),
      h("div", { class: "row", style: "margin-top:6px" }, agent.tools.map((name) =>
        h("span", { class: "chip mono", text: name }))),
    ]),
    h("div", { style: "margin-top:12px" }, [
      h("div", { class: "mute", text: "授权用户" }),
      h("div", { class: "row", style: "margin-top:6px" }, agent.users.map((user) =>
        h("span", { class: "chip", text: user.display_name + "（" + user.role + "）" }))),
    ]),
  ]))));
}

function editAgent(agent, assets, tools, users, done) {
  const toolSet = new Set(agent.tools);
  const assetSet = new Set(agent.assets.map((a) => a.id));
  const userSet = new Set(agent.users.map((u) => u.id));

  const toolBox = h("div", { class: "row", style: "gap:8px" }, tools.map((tool) => h("label", { class: "chip" }, [
    h("input", {
      type: "checkbox", checked: toolSet.has(tool.name),
      onchange: (event) => { event.target.checked ? toolSet.add(tool.name) : toolSet.delete(tool.name); },
    }),
    " " + tool.name,
  ])));
  const assetBox = h("div", { class: "row", style: "gap:8px" }, assets.map((asset) => h("label", { class: "chip" }, [
    h("input", {
      type: "checkbox", checked: assetSet.has(asset.id),
      onchange: (event) => { event.target.checked ? assetSet.add(asset.id) : assetSet.delete(asset.id); },
    }),
    " " + asset.name,
  ])));
  const userBox = h("div", { class: "row", style: "gap:8px" }, users.map((user) => h("label", { class: "chip" }, [
    h("input", {
      type: "checkbox", checked: userSet.has(user.id),
      onchange: (event) => { event.target.checked ? userSet.add(user.id) : userSet.delete(user.id); },
    }),
    " " + user.display_name,
  ])));

  const node = h("div", {}, [
    h("h3", { text: "编辑 " + agent.name + " 的权限" }),
    h("div", { class: "mute", style: "margin-bottom:12px", text: "工具白名单、可访问资产与授权用户。修改会立即生效，后续所有 AI 调用都按新权限进行判定。" }),
    h("div", { style: "margin-bottom:14px" }, [h("div", { style: "font-weight:600;margin-bottom:8px", text: "工具白名单" }), toolBox]),
    h("div", { style: "margin-bottom:14px" }, [h("div", { style: "font-weight:600;margin-bottom:8px", text: "可访问资产" }), assetBox]),
    h("div", { style: "margin-bottom:16px" }, [h("div", { style: "font-weight:600;margin-bottom:8px", text: "授权用户" }), userBox]),
    h("div", { class: "row" }, [
      h("button", {
        class: "primary", text: "保存",
        onclick: async (event) => {
          event.target.disabled = true;
          try {
            await api.put("/api/agents/" + agent.id, {
              tools: [...toolSet], asset_ids: [...assetSet], user_ids: [...userSet],
            });
            toast("权限已更新", "ok");
            closeModal();
            done();
          } catch (err) {
            toast(err.message, "error");
          } finally {
            event.target.disabled = false;
          }
        },
      }),
      h("button", { class: "ghost", text: "取消", onclick: closeModal }),
    ]),
  ]);
  modal(node);
}

