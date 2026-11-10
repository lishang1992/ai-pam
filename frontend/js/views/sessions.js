import { api } from "../api.js";
import { fmtDateTime, h, riskBadge, statusBadge } from "../ui.js";
import { renderDetail } from "../session_detail.js";

export async function render(container, param, ctx) {
  const me = JSON.parse(localStorage.getItem("aib_user") || "{}");
  const canTerminate = me.role === "security_admin";
  const draw = async () => {
    const sessions = await api.get("/api/sessions?limit=60");
    container.innerHTML = "";
    const list = h("div", { class: "list" }, sessions.length === 0
      ? [h("div", { class: "empty", text: "暂无会话" })]
      : sessions.map((session) => h("div", {
        class: "list-item" + (String(session.id) === String(param) ? " active" : ""),
        onclick: () => { location.hash = "#/sessions/" + session.id; },
      }, [
        h("div", { class: "row", style: "justify-content:space-between" }, [
          h("span", { class: "code mono", text: session.code }),
          statusBadge(session.status),
        ]),
        h("div", { class: "dim", style: "margin-top:6px;font-size:12.5px", text: session.title }),
        h("div", { class: "row", style: "margin-top:8px;gap:6px" }, [
          riskBadge(session.risk_level),
          h("span", { class: "mute", text: session.user_name + " · " + session.agent_name }),
        ]),
        h("div", { class: "mute", style: "margin-top:6px", text: session.asset_name + " · " + fmtDateTime(session.created_at) }),
      ])));

    const detail = h("div", {});
    container.appendChild(h("div", { class: "split" }, [
      h("div", { class: "card" }, [h("h3", { text: "会话列表" }), list]),
      detail,
    ]));

    if (param) {
      detail.appendChild(h("div", { class: "empty", text: "加载会话详情…" }));
      detail.innerHTML = "";
      await renderDetail(detail, Number(param));
      if (canTerminate) {
        const session = sessions.find((item) => String(item.id) === String(param));
        if (session && !["finished", "terminated"].includes(session.status)) {
          detail.insertBefore(h("div", { class: "card", style: "margin-bottom:12px" }, [
            h("div", { class: "row", style: "justify-content:space-between" }, [
              h("div", {}, [
                h("div", { style: "font-weight:600", text: "会话管控（安全管理员）" }),
                h("div", { class: "mute", text: "强制终止会立即失效该会话的临时权限，并让所有待审批请求作废。" }),
              ]),
              h("button", {
                class: "danger", text: "强制终止会话",
                onclick: async () => {
                  if (!confirm("确认强制终止会话 " + session.code + " ？")) return;
                  try {
                    await api.del("/api/sessions/" + session.id);
                    toast("会话已终止，临时权限已失效", "warn");
                    draw();
                  } catch (err) { toast(err.message, "error"); }
                },
              }),
            ]),
          ]), detail.firstChild);
        }
      }
    } else {
      detail.appendChild(h("div", { class: "card empty", text: "← 选择左侧任意一条会话，查看它的完整操作链路" }));
    }
  };

  await draw();
  ctx.on("data:refresh", draw);
}
