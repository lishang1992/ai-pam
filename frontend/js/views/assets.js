import { api } from "../api.js";
import { badge, closeModal, h, modal, statusBadge, toast } from "../ui.js";

const EXECUTORS = [
  { value: "ssh", label: "ssh · 真实 Linux（SSH 协议）" },
  { value: "mock", label: "mock · 演示模拟主机" },
  { value: "local", label: "local · 本机执行（联调用）" },
];

export async function render(container, param, ctx) {
  const me = JSON.parse(localStorage.getItem("aib_user") || "{}");
  const isAdmin = me.role === "security_admin";

  const draw = async () => {
    const [assets, users] = await Promise.all([api.get("/api/assets"), api.get("/api/users")]);
    container.innerHTML = "";

    container.appendChild(h("div", { class: "card", style: "margin-bottom:14px" }, [
      h("div", { class: "row", style: "justify-content:space-between" }, [
        h("h3", { style: "margin:0", text: "资产管理（Linux / SSH）" }),
        isAdmin ? h("button", {
          class: "primary small", text: "+ 添加 Linux 资产",
          onclick: () => editAsset(null, users, () => draw()),
        }) : h("span", { class: "mute", text: "只有安全管理员可以新增 / 修改资产" }),
      ]),
      h("div", { class: "mute", style: "margin-top:8px", text:
        "纳管真实 Linux 主机只需三步：填写主机与 SSH 账号 → 测试连通性 → 授权给用户和 AI Agent。" +
        "口令写入本地凭据库（文件权限 600）或使用环境变量，数据库里不保存明文。" }),
    ]));

    const tableBox = h("div", { class: "card" });
    tableBox.appendChild(h("h3", { text: "资产清单（" + assets.length + "）" }));
    if (assets.length === 0) {
      tableBox.appendChild(h("div", { class: "empty", text: "还没有资产" }));
    } else {
      tableBox.appendChild(h("table", {}, [
        h("thead", {}, [h("tr", {}, ["名称", "环境", "执行器", "地址", "SSH 账号", "凭据", "状态",
          "关联 Agent", "授权用户", "操作", "连通性"].map((t) => h("th", { text: t })))]),
        h("tbody", {}, assets.map((asset) => row(asset, users, isAdmin, draw))),
      ]));
    }
    container.appendChild(tableBox);

    if (!isAdmin) {
      container.appendChild(h("div", { class: "card", style: "margin-top:14px" },
        [h("div", { class: "mute", text: "提示：想接入新的生产服务器，请让安全管理员在「资产管理」中添加，并在「Agent 管理」里把资产授权给对应 Agent。" })]));
    }
  };

  await draw();
  ctx.on("data:refresh", draw);
}

function row(asset, users, isAdmin, reload) {
  const testButton = h("button", { class: "ghost small", text: "测试连接" });
  const actionCell = h("td", {}, [
    testButton,
    isAdmin ? h("button", { class: "ghost small", style: "margin-left:6px", text: "编辑",
      onclick: () => editAsset(asset, users, reload) }) : null,
    isAdmin ? h("button", { class: "ghost small", style: "margin-left:6px", text: "删除",
      onclick: async () => {
        if (!confirm("确认删除资产 " + asset.name + " ？该操作会同时移除其授权关系。")) return;
        try {
          await api.del("/api/assets/" + asset.id);
          toast("资产已删除", "ok");
          reload();
        } catch (err) { toast(err.message, "error"); }
      } }) : null,
  ]);
  const testCell = h("td", { class: "mute", text: "-" });
  testButton.onclick = async (event) => {
    event.target.disabled = true;
    event.target.textContent = "连接中…";
    testCell.innerHTML = "";
    try {
      const result = await api.post("/api/assets/" + asset.id + "/test");
      testCell.appendChild(h("span", { class: result.ok ? "badge success" : "badge DENY",
        text: result.ok ? "连通" : "失败" }));
      testCell.appendChild(h("div", { class: "mute", style: "margin-top:4px",
        text: (result.message || "") + (result.latency_ms ? "（" + result.latency_ms + " ms）" : "") }));
      toast(result.ok ? asset.name + " 连接成功" : "连接失败：" + result.message, result.ok ? "ok" : "error");
    } catch (err) {
      testCell.appendChild(h("span", { class: "badge DENY", text: err.message }));
    } finally {
      event.target.disabled = false;
      event.target.textContent = "测试连接";
    }
  };

  return h("tr", {}, [
    h("td", {}, [
      h("div", { style: "font-weight:600", text: asset.name }),
      h("div", { class: "mute", text: asset.description || "" }),
    ]),
    h("td", {}, [badge(asset.env_tag === "production" ? "HIGH" : "neutral", asset.env_tag)]),
    h("td", { class: "mono", text: asset.executor }),
    h("td", { class: "mono", text: asset.executor === "mock" ? "（模拟主机）" : (asset.host + ":" + asset.port) }),
    h("td", { class: "mono", text: asset.ssh_user || "-" }),
    h("td", {}, [
      h("span", { class: "badge " + (asset.has_credential ? "success" : "neutral"),
        text: asset.has_credential ? "已配置" : "未配置" }),
      h("div", { class: "mute", text: asset.credential_hint || "" }),
    ]),
    h("td", {}, [statusBadge(asset.status)]),
    h("td", { text: String(asset.agent_count) }),
    h("td", { class: "mute", text: asset.authorized ? "含当前账号" : "未授权给您" }),
    actionCell,
    testCell,
  ]);
}

function editAsset(asset, users, done) {
  const isEdit = !!asset;
  const name = h("input", { type: "text", value: asset ? asset.name : "", placeholder: "例如 Linux-Prod-04" });
  const description = h("input", { type: "text", value: asset ? asset.description : "", placeholder: "用途说明，例如 生产订单服务" });
  const envTag = h("select", {}, ["production", "staging", "test"].map((value) =>
    h("option", { value, text: value, selected: asset && asset.env_tag === value })));
  const executor = h("select", {}, EXECUTORS.map((item) =>
    h("option", { value: item.value, text: item.label, selected: asset && asset.executor === item.value })));
  const host = h("input", { type: "text", value: asset ? asset.host : "", placeholder: "10.20.31.21" });
  const port = h("input", { type: "text", value: asset ? String(asset.port) : "22" });
  const sshUser = h("input", { type: "text", value: asset ? asset.ssh_user : "root", placeholder: "root / opsadm" });
  const authType = h("select", {}, [
    h("option", { value: "password", text: "口令认证" }),
    h("option", { value: "key", text: "私钥免密登录" }),
  ]);
  const secret = h("input", { type: "password", placeholder: isEdit ? "留空表示不修改口令" : "SSH 登录口令" });
  const keyPath = h("input", { type: "text", value: asset && asset.auth_type === "key" ? asset.credential_hint || "" : "",
    placeholder: "~/.ssh/id_rsa" });
  const userSet = new Set(asset ? (asset.user_ids || []) : []);
  const userBox = h("div", { class: "row", style: "gap:8px" }, users.map((user) => h("label", { class: "chip" }, [
    h("input", { type: "checkbox", checked: userSet.has(user.id),
      onchange: (event) => { event.target.checked ? userSet.add(user.id) : userSet.delete(user.id); } }),
    " " + user.display_name,
  ])));

  const resultBox = h("div", { class: "mute", style: "margin-top:10px" });
  const credentialRow = h("div", { class: "grid cols-2" }, [
    h("div", { class: "field" }, [h("span", { text: "SSH 口令" }), secret]),
    h("div", { class: "field" }, [h("span", { text: "私钥路径（私钥认证时填写）" }), keyPath]),
  ]);
  const syncAuthVisibility = () => { credentialRow.style.display = "grid"; };
  authType.addEventListener("change", syncAuthVisibility);

  const save = h("button", {
    class: "primary", text: isEdit ? "保存修改" : "保存资产",
    onclick: async (event) => {
      event.target.disabled = true;
      resultBox.textContent = "保存中…";
      const payload = {
        name: name.value.trim(),
        description: description.value.trim(),
        env_tag: envTag.value,
        executor: executor.value,
        host: host.value.trim(),
        port: Number(port.value || 22),
        ssh_user: sshUser.value.trim() || "root",
        auth_type: authType.value,
        user_ids: [...userSet],
      };
      if (secret.value) payload.secret = secret.value;
      if (keyPath.value.trim()) payload.key_path = keyPath.value.trim();
      try {
        const saved = isEdit
          ? await api.put("/api/assets/" + asset.id, payload)
          : await api.post("/api/assets", payload);
        resultBox.textContent = "已保存，正在测试连通性…";
        try {
          const test = await api.post("/api/assets/" + saved.id + "/test");
          resultBox.textContent = (test.ok ? "✔ 连接成功：" : "✘ 连接失败：") + (test.message || "");
        } catch (err) {
          resultBox.textContent = "已保存，但连通性测试未通过：" + err.message;
        }
        toast(isEdit ? "资产已更新" : "资产已创建", "ok");
        closeModal();
        done();
      } catch (err) {
        resultBox.textContent = "保存失败：" + err.message;
        toast(err.message, "error");
      } finally {
        event.target.disabled = false;
      }
    },
  });

  modal(h("div", {}, [
    h("h3", { text: isEdit ? "编辑资产：" + asset.name : "添加 Linux 资产（SSH）" }),
    h("div", { class: "mute", style: "margin-bottom:12px",
      text: "执行器选 ssh 表示真实 Linux 主机；保存后系统会自动尝试 SSH 登录并执行 uptime 验证连通性。" }),
    h("div", { class: "grid cols-2" }, [
      h("div", { class: "field" }, [h("span", { text: "资产名称" }), name]),
      h("div", { class: "field" }, [h("span", { text: "环境" }), envTag]),
      h("div", { class: "field" }, [h("span", { text: "执行器 / 协议" }), executor]),
      h("div", { class: "field" }, [h("span", { text: "用途说明" }), description]),
      h("div", { class: "field" }, [h("span", { text: "主机地址 / IP" }), host]),
      h("div", { class: "field" }, [h("span", { text: "SSH 端口" }), port]),
      h("div", { class: "field" }, [h("span", { text: "SSH 登录用户" }), sshUser]),
      h("div", { class: "field" }, [h("span", { text: "认证方式" }), authType]),
    ]),
    h("div", { style: "margin-top:12px" }, credentialRow),
    h("div", { style: "margin-top:14px" }, [
      h("div", { style: "font-weight:600;margin-bottom:6px", text: "授权给哪些用户（Agent 权限不会超过用户权限）" }),
      userBox,
    ]),
    resultBox,
    h("div", { class: "row", style: "margin-top:16px" }, [
      save,
      h("button", { class: "ghost", text: "取消", onclick: closeModal }),
    ]),
  ]));
}
