import { api } from "../api.js";
import { actionBadge, h, modal, closeModal, toast } from "../ui.js";

export async function render(container) {
  const [policies, tools] = await Promise.all([api.get("/api/policies"), api.get("/api/tools")]);
  const me = JSON.parse(localStorage.getItem("aib_user") || "{}");
  const isAdmin = me.role === "security_admin";

  container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
    h("div", { class: "row", style: "justify-content:space-between" }, [
      h("h3", { style: "margin:0", text: "策略管理" }),
      isAdmin ? h("button", { class: "primary small", text: "+ 新建策略", onclick: () => newPolicy(tools, () => location.reload()) }) : h("span"),
    ]),
    h("div", { class: "mute", style: "margin-top:8px", text: "规则优先级：资产 > Agent > 环境 > 全局；同一层级 DENY 永远胜出。未命中任何规则时，只读工具默认放行，变更类工具默认需要人工审批。" }),
  ]));

  container.appendChild(h("div", { class: "grid" }, policies.map((policy) => h("div", { class: "card" }, [
    h("div", { class: "row", style: "justify-content:space-between" }, [
      h("div", { class: "row", style: "gap:8px" }, [
        h("span", { style: "font-weight:700", text: policy.name }),
        h("span", { class: "chip", text: policy.scope_type + " = " + policy.scope_value }),
        h("span", { class: "chip", text: "优先级 " + policy.priority }),
      ]),
      h("span", { class: "mute", text: policy.rules.length + " 条规则" }),
    ]),
    h("div", { class: "dim", style: "margin:8px 0 12px", text: policy.description }),
    h("table", {}, [
      h("thead", {}, [h("tr", {}, ["工具", "判定", "说明", isAdmin ? "操作" : ""].map((t) => h("th", { text: t })))]),
      h("tbody", {}, policy.rules.map((rule) => h("tr", {}, [
        h("td", { class: "mono", text: rule.tool }),
        h("td", {}, [actionBadge(rule.action)]),
        h("td", { class: "dim", text: rule.reason }),
        isAdmin ? h("td", {}, [h("button", {
          class: "ghost small", text: "删除",
          onclick: async () => {
            try {
              await api.del("/api/policies/rules/" + rule.id);
              toast("规则已删除", "ok");
              location.reload();
            } catch (err) { toast(err.message, "error"); }
          },
        })]) : h("td"),
      ]))),
    ]),
    isAdmin ? addRuleForm(policy, tools) : null,
  ]))));
}

function addRuleForm(policy, tools) {
  const toolSelect = h("select", {}, tools.map((tool) => h("option", { value: tool.name, text: tool.name })));
  const actionSelect = h("select", {}, ["ALLOW", "APPROVAL", "DENY"].map((action) => h("option", { value: action, text: action })));
  const reason = h("input", { type: "text", placeholder: "规则说明（会进入审计记录）" });
  const button = h("button", {
    class: "ghost small", text: "+ 添加规则",
    onclick: async (event) => {
      event.target.disabled = true;
      try {
        await api.post("/api/policies/" + policy.id + "/rules", {
          tool: toolSelect.value, action: actionSelect.value, reason: reason.value,
        });
        toast("规则已添加，立即生效", "ok");
        location.reload();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        event.target.disabled = false;
      }
    },
  });
  return h("div", { class: "row", style: "margin-top:12px" }, [toolSelect, actionSelect, reason, button]);
}

function newPolicy(tools, done) {
  const name = h("input", { type: "text", placeholder: "策略名称，例如：Production Strict" });
  const scopeValue = h("input", { type: "text", placeholder: "作用域取值，例如：production / Linux-Prod-01 / Agent 名称" });
  const scopeType = h("select", {}, ["env", "asset", "agent", "global"].map((s) => h("option", { value: s, text: s })));
  const priority = h("input", { type: "text", value: "150" });
  const description = h("input", { type: "text", placeholder: "策略说明" });
  const node = h("div", {}, [
    h("h3", { text: "新建策略" }),
    h("div", { class: "grid cols-2" }, [
      h("div", { class: "field" }, [h("span", { text: "策略名称" }), name]),
      h("div", { class: "field" }, [h("span", { text: "作用域类型" }), scopeType]),
      h("div", { class: "field" }, [h("span", { text: "作用域取值" }), scopeValue]),
      h("div", { class: "field" }, [h("span", { text: "优先级（越大越优先）" }), priority]),
      h("div", { class: "field", style: "grid-column:span 2" }, [h("span", { text: "说明" }), description]),
    ]),
    h("div", { class: "row", style: "margin-top:16px" }, [
      h("button", {
        class: "primary", text: "创建",
        onclick: async (event) => {
          event.target.disabled = true;
          try {
            await api.post("/api/policies", {
              name: name.value, description: description.value, scope_type: scopeType.value,
              scope_value: scopeValue.value, priority: Number(priority.value || 100), rules: [],
            });
            toast("策略已创建，可继续为其添加规则", "ok");
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

