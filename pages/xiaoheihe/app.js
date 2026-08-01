const bridge = window.AstrBotPluginPage;
const state = {
  config: null,
  configSchema: null,
  status: null,
  loginTimer: null,
  loginRefreshTimer: null,
  sseId: null,
  reconnectTimer: null,
  logs: [],
  eventPage: 1,
  eventPages: 1,
  toastTimer: null,
  confirmResolve: null,
  confirmPreviousFocus: null,
  unloaded: false,
};

const $ = (id) => document.getElementById(id);
const text = (value) => value === null || value === undefined || value === "" ? "—" : String(value);
const eventStatusText = (value) => value === "dry_run" ? "模拟运行" : text(value);
const bytes = (value) => {
  const number = Number(value || 0);
  if (number < 1024) return `${number} B`;
  if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`;
  return `${(number / 1024 ** 2).toFixed(1)} MB`;
};

function toast(message, tone = "info") {
  const node = $("toast");
  if (state.toastTimer) clearTimeout(state.toastTimer);
  node.textContent = message;
  node.dataset.tone = tone;
  node.classList.add("show");
  state.toastTimer = setTimeout(() => {
    node.classList.remove("show");
    state.toastTimer = null;
  }, 3200);
}

function closeConfirmation(accepted) {
  const resolve = state.confirmResolve;
  state.confirmResolve = null;
  $("confirm-overlay").hidden = true;
  $("confirm-accept").classList.remove("danger");
  const previousFocus = state.confirmPreviousFocus;
  state.confirmPreviousFocus = null;
  previousFocus?.focus?.();
  resolve?.(accepted);
}

function confirmAction(message, { confirmText = "确认", danger = false } = {}) {
  if (state.confirmResolve) closeConfirmation(false);
  state.confirmPreviousFocus = document.activeElement;
  $("confirm-message").textContent = message;
  const accept = $("confirm-accept");
  accept.textContent = confirmText;
  accept.classList.toggle("danger", danger);
  $("confirm-overlay").hidden = false;
  return new Promise((resolve) => {
    state.confirmResolve = resolve;
    queueMicrotask(() => accept.focus());
  });
}

async function busy(button, work) {
  button.disabled = true;
  try { return await work(); }
  catch (error) { toast(error.message, "error"); throw error; }
  finally { button.disabled = false; }
}

function metric(label, value) {
  const card = document.createElement("article");
  card.className = "metric";
  const labelNode = document.createElement("div");
  labelNode.className = "label";
  labelNode.textContent = label;
  const valueNode = document.createElement("div");
  valueNode.className = "value";
  valueNode.textContent = text(value);
  card.append(labelNode, valueNode);
  return card;
}

function notificationMetric(profile, eventType) {
  const poll = profile.notification_polls?.[eventType];
  if (!poll) return "—";
  return `${Number(poll.raw_count || 0)} / ${Number(poll.accepted_count || 0)}`;
}

function table(headers, rows) {
  const tableNode = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((header) => {
    const cell = document.createElement("th");
    cell.textContent = header;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === row.length - 1) cell.className = "wrap";
      cell.textContent = text(value);
      tr.append(cell);
    });
    body.append(tr);
  });
  tableNode.append(head, body);
  return tableNode;
}

function selectedProfile() {
  return $("login-profile").value || "default";
}

async function loadConfig() {
  const [config, schema] = await Promise.all([
    bridge.apiGet("config"),
    bridge.apiGet("config/schema"),
  ]);
  state.config = config;
  state.configSchema = schema;
  renderConfigForm();
  populateProfileSelect();
}

function populateProfileSelect() {
  const select = $("login-profile");
  const previous = select.value;
  select.replaceChildren();
  (state.config.profiles || []).forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.profile_id;
    option.textContent = `${profile.display_name || profile.profile_id} · ${profile.profile_id}`;
    select.append(option);
  });
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
}

function schemaDefault(descriptor) {
  if (Object.hasOwn(descriptor, "default")) {
    return structuredClone(descriptor.default);
  }
  if (descriptor.type === "bool") return false;
  if (descriptor.type === "int" || descriptor.type === "float") return 0;
  if (descriptor.type === "list") return [];
  if (descriptor.type === "object") {
    return Object.fromEntries(
      Object.entries(descriptor.items || {}).map(([key, item]) => [key, schemaDefault(item)]),
    );
  }
  return "";
}

function fieldLabel(descriptor, key) {
  return descriptor.description || key;
}

function appendHint(host, descriptor) {
  if (!descriptor.hint) return;
  const hint = document.createElement("small");
  hint.className = "config-hint";
  hint.textContent = descriptor.hint;
  host.append(hint);
}

function createScalarField(key, descriptor, value, update) {
  if (descriptor.type === "bool") {
    const row = document.createElement("label");
    row.className = "config-toggle";
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = fieldLabel(descriptor, key);
    copy.append(title);
    appendHint(copy, descriptor);
    const control = document.createElement("input");
    control.type = "checkbox";
    control.checked = Boolean(value);
    control.addEventListener("change", () => update(control.checked));
    row.append(copy, control);
    return row;
  }

  const field = document.createElement("label");
  field.className = "config-field";
  const title = document.createElement("span");
  title.className = "config-label";
  title.textContent = fieldLabel(descriptor, key);
  field.append(title);

  let control;
  if (descriptor.type === "list") {
    control = document.createElement("textarea");
    control.className = "config-list";
    control.rows = 4;
    control.value = Array.isArray(value) ? value.map(String).join("\n") : "";
    control.placeholder = "每行一项";
    control.addEventListener("input", () => {
      update(control.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean));
    });
  } else if (descriptor.type === "string" && Array.isArray(descriptor.options)) {
    control = document.createElement("select");
    descriptor.options.forEach((optionValue) => {
      const option = document.createElement("option");
      const value = typeof optionValue === "object" ? optionValue.value : optionValue;
      option.value = String(value);
      option.textContent = String(
        (typeof optionValue === "object" && optionValue.label)
          || descriptor.option_labels?.[value]
          || value,
      );
      control.append(option);
    });
    control.value = String(value ?? descriptor.default ?? "");
    control.addEventListener("change", () => update(control.value));
  } else {
    control = document.createElement("input");
    const numeric = descriptor.type === "int" || descriptor.type === "float";
    control.type = numeric ? "number" : "text";
    control.value = value ?? descriptor.default ?? "";
    if (numeric) {
      const slider = descriptor.slider || {};
      if (slider.min !== undefined) control.min = String(slider.min);
      if (slider.max !== undefined) control.max = String(slider.max);
      control.step = String(slider.step ?? (descriptor.type === "int" ? 1 : 0.1));
      control.addEventListener("change", () => {
        const parsed = descriptor.type === "int"
          ? Number.parseInt(control.value, 10)
          : Number.parseFloat(control.value);
        if (Number.isFinite(parsed)) {
          update(parsed);
          control.setCustomValidity("");
        } else {
          control.setCustomValidity("请输入有效数字");
          control.reportValidity();
        }
      });
    } else {
      control.addEventListener("input", () => update(control.value));
    }
  }
  control.dataset.configKey = key;
  field.append(control);
  appendHint(field, descriptor);
  return field;
}

function profileTemplate(schema) {
  const entries = Object.entries(schema.templates || {});
  if (!entries.length) throw new Error("账号档案界面定义缺少模板");
  const [templateKey, template] = entries[0];
  const profile = { __template_key: templateKey };
  Object.entries(template.items || {}).forEach(([key, descriptor]) => {
    profile[key] = schemaDefault(descriptor);
  });
  return { template, profile };
}

function nextProfileId() {
  const used = new Set((state.config.profiles || []).map((item) => String(item.profile_id)));
  let number = 2;
  while (used.has(`profile_${number}`)) number += 1;
  return `profile_${number}`;
}

function renderProfiles(schema) {
  const wrapper = document.createElement("details");
  wrapper.className = "config-section";
  wrapper.open = true;
  const summary = document.createElement("summary");
  const heading = document.createElement("span");
  heading.textContent = schema.description || "账号档案";
  const count = document.createElement("small");
  count.textContent = `${(state.config.profiles || []).length} 个档案`;
  summary.append(heading, count);
  wrapper.append(summary);
  appendHint(wrapper, schema);

  const profilesHost = document.createElement("div");
  profilesHost.className = "profile-cards";
  const { template } = profileTemplate(schema);
  (state.config.profiles || []).forEach((profile, index) => {
    const card = document.createElement("article");
    card.className = "profile-card";
    const cardHeading = document.createElement("div");
    cardHeading.className = "profile-heading";
    const title = document.createElement("h3");
    title.textContent = profile.display_name || profile.profile_id || `账号 ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger secondary compact";
    remove.textContent = "删除档案";
    remove.disabled = state.config.profiles.length <= 1;
    remove.addEventListener("click", async () => {
      const name = profile.profile_id || index + 1;
      const confirmed = await confirmAction(
        `即将只删除账号档案 ${name}。如需同时清除登录凭证，请先取消并在扫码登录页执行安全退出。确认继续？`,
        { confirmText: "删除档案", danger: true },
      );
      if (!confirmed) return;
      state.config.profiles.splice(index, 1);
      renderConfigForm();
    });
    cardHeading.append(title, remove);
    const grid = document.createElement("div");
    grid.className = "config-grid";
    Object.entries(template.items || {}).forEach(([key, descriptor]) => {
      grid.append(createScalarField(key, descriptor, profile[key], (nextValue) => {
        profile[key] = nextValue;
      }));
    });
    card.append(cardHeading, grid);
    profilesHost.append(card);
  });
  const add = document.createElement("button");
  add.type = "button";
  add.className = "secondary add-profile";
  add.textContent = "添加账号档案";
  add.addEventListener("click", () => {
    const { profile } = profileTemplate(schema);
    profile.profile_id = nextProfileId();
    profile.display_name = `账号 ${state.config.profiles.length + 1}`;
    state.config.profiles.push(profile);
    renderConfigForm();
  });
  wrapper.append(profilesHost, add);
  return wrapper;
}

function renderObjectGroup(groupKey, schema) {
  const wrapper = document.createElement("details");
  wrapper.className = "config-section";
  wrapper.open = true;
  const summary = document.createElement("summary");
  const heading = document.createElement("span");
  heading.textContent = schema.description || groupKey;
  const key = document.createElement("small");
  key.textContent = groupKey;
  summary.append(heading, key);
  const grid = document.createElement("div");
  grid.className = "config-grid";
  const group = state.config[groupKey] || (state.config[groupKey] = {});
  Object.entries(schema.items || {}).forEach(([fieldKey, descriptor]) => {
    grid.append(createScalarField(fieldKey, descriptor, group[fieldKey], (nextValue) => {
      group[fieldKey] = nextValue;
    }));
  });
  wrapper.append(summary, grid);
  return wrapper;
}

function renderConfigForm() {
  const host = $("config-form");
  host.replaceChildren();
  if (!state.config || !state.configSchema) return;
  Object.entries(state.configSchema).forEach(([groupKey, schema]) => {
    if (schema.type === "template_list" && groupKey === "profiles") {
      host.append(renderProfiles(schema));
    } else if (schema.type === "object") {
      host.append(renderObjectGroup(groupKey, schema));
    }
  });
}

async function loadStatus() {
  state.status = await bridge.apiGet("status");
  const profiles = state.status.profiles || [];
  const first = profiles[0] || {};
  const cards = $("status-cards");
  cards.replaceChildren(
    metric("插件版本", state.status.version),
    metric("登录状态", first.status || "idle"),
    metric("当前账号", first.nickname || first.profile_id),
    metric("UID", first.uid),
    metric("最后轮询", first.last_poll_at),
    metric("最近成功请求", first.last_success_request_at),
    metric("最近错误", first.last_error || first.last_client_error?.message),
    metric("@通知 原始/接收", notificationMetric(first, "mention")),
    metric("回复通知 原始/接收", notificationMetric(first, "reply")),
    metric("待处理队列", state.status.queue_length || 0),
    metric("今日回复", first.reply_count || 0),
    metric("今日主动浏览", first.proactive_count || 0),
    metric("模拟运行", first.dry_run ? "开启" : "关闭"),
    metric("数据库", bytes(state.status.database_size)),
    metric("日志", bytes(state.status.log_size)),
    metric("后台任务", (state.status.tasks || []).length),
    metric(
      "熔断状态",
      Number(first.circuit_open_until || 0) > Date.now() / 1000 ? "已打开" : "正常",
    ),
  );
  const adapters = state.status.adapters || [];
  const adapterHost = $("adapter-list");
  adapterHost.replaceChildren(table(
    ["实例 ID", "profile_id", "运行状态"],
    adapters.map((item) => [item.id, item.profile_id, item.running ? "运行中" : "停止"]),
  ));
  const alerts = $("alerts");
  alerts.replaceChildren();
  (state.status.alerts || []).forEach((item) => {
    const node = document.createElement("div");
    node.className = "alert";
    node.textContent = item.message;
    alerts.append(node);
  });
}

async function loadLogin() {
  const result = await bridge.apiGet("auth/status", { profile_id: selectedProfile() });
  renderLogin(result);
}

function renderLogin(result) {
  const facts = $("login-facts");
  const values = {
    "状态": result.state,
    "昵称": result.nickname,
    "UID": result.uid,
    "登录时间": result.logged_in_at,
    "最近检查": result.last_login_check_at,
  };
  facts.replaceChildren();
  Object.entries(values).forEach(([key, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = text(value);
    facts.append(dt, dd);
  });
  if (result.qr_image) {
    $("qr-image").src = result.qr_image;
    $("qr-image").hidden = false;
    $("qr-placeholder").hidden = true;
  } else {
    $("qr-image").removeAttribute("src");
    $("qr-image").hidden = true;
    $("qr-placeholder").hidden = false;
  }
  clearInterval(state.loginTimer);
  if (result.expires_at) {
    state.loginTimer = setInterval(() => {
      const remaining = Math.max(0, Math.floor(result.expires_at - Date.now() / 1000));
      $("qr-countdown").textContent = remaining ? `二维码剩余 ${remaining} 秒` : "二维码已过期";
      if (!remaining) clearInterval(state.loginTimer);
    }, 500);
  } else {
    $("qr-countdown").textContent = "";
  }
  clearTimeout(state.loginRefreshTimer);
  const activeStates = new Set(["requesting_qr", "waiting_scan", "scanned_waiting_confirm"]);
  if (activeStates.has(result.state) && !state.unloaded) {
    state.loginRefreshTimer = setTimeout(async () => {
      try { await loadLogin(); }
      catch (error) { toast(`登录状态刷新失败：${error.message}`); }
    }, 3000);
  }
}

async function loadEvents() {
  const start = $("event-start").value;
  const end = $("event-end").value;
  const result = await bridge.apiGet("events", {
    status: $("event-status").value,
    uid: $("event-uid").value.trim(),
    post_id: $("event-post").value.trim(),
    keyword: $("event-keyword").value.trim(),
    start_time: start ? Date.parse(start) / 1000 : "",
    end_time: end ? Date.parse(end) / 1000 : "",
    page: state.eventPage,
    page_size: 50,
  });
  state.eventPages = Math.max(1, Math.ceil(result.total / result.page_size));
  $("event-page").textContent = `第 ${result.page} / ${state.eventPages} 页，共 ${result.total} 条`;
  $("event-prev").disabled = result.page <= 1;
  $("event-next").disabled = result.page >= state.eventPages;
  $("event-table").replaceChildren(table(
    ["时间", "类型", "状态", "UID", "帖子 / 楼层", "内容 / 结果"],
    result.items.map((item) => [
      new Date((item.event_time || item.discovered_at) * 1000).toLocaleString(),
      item.event_type,
      eventStatusText(item.status),
      item.sender_uid,
      `${item.post_id} / ${item.root_comment_id || "帖子"}`,
      `${item.content || ""}\n${item.reply_text ? `→ ${item.reply_text}` : ""}\n${item.error || ""}`.trim(),
    ]),
  ));
}

async function loadCandidates() {
  const [pending, unknown] = await Promise.all([
    bridge.apiGet("feed/candidates", { status: "pending", limit: 100 }),
    bridge.apiGet("feed/candidates", { status: "send_unknown", limit: 100 }),
  ]);
  const items = [...(unknown.items || []), ...(pending.items || [])];
  const host = $("candidate-list");
  host.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.textContent = "待审核候选为 0 条。";
    host.append(empty);
    return;
  }
  items.forEach((candidate) => {
    const card = document.createElement("article");
    card.className = "candidate";
    const title = document.createElement("h3");
    title.textContent = candidate.post_title || `帖子 ${candidate.post_id}`;
    const meta = document.createElement("p");
    const statusText = candidate.status === "send_unknown" ? "发送状态未知，请人工核对" : "待审核";
    meta.textContent = `作者 UID ${candidate.post_author_uid || "未知"} · ${statusText} · ${candidate.reason || "AI 候选"}`;
    const editor = document.createElement("textarea");
    editor.value = candidate.edited_text || candidate.generated_text;
    editor.readOnly = candidate.status === "send_unknown";
    const row = document.createElement("div");
    row.className = "button-row";
    if (candidate.status === "send_unknown") {
      const discard = document.createElement("button");
      discard.type = "button";
      discard.className = "danger secondary";
      discard.textContent = "人工丢弃";
      discard.addEventListener("click", () => busy(discard, async () => {
        const confirmed = await confirmAction(
          "请先在目标帖子核对评论。确认将这条发送状态未知候选标记为已丢弃？",
          { confirmText: "人工丢弃", danger: true },
        );
        if (!confirmed) return;
        await bridge.apiPost(`feed/candidates/${candidate.id}/reject`, {});
        toast("候选已人工丢弃", "success");
        await loadCandidates();
      }));
      row.append(discard);
      card.append(title, meta, editor, row);
      host.append(card);
      return;
    }
    const approve = document.createElement("button");
    approve.type = "button";
    approve.textContent = "批准";
    approve.addEventListener("click", () => busy(approve, async () => {
      const simulated = Boolean(state.config?.proactive_feed?.dry_run);
      const confirmed = await confirmAction(
        simulated
          ? "当前为主动浏览模拟运行。确认保存这条批准结果？"
          : "确认将当前编辑后的文本发送到目标帖子？",
        { confirmText: "批准" },
      );
      if (!confirmed) return;
      const result = await bridge.apiPost(
        `feed/candidates/${candidate.id}/approve`,
        { edited_text: editor.value },
      );
      toast(result.result === "dry_run" ? "模拟运行候选已批准" : "候选已发送", "success");
      await loadCandidates();
    }));
    const reject = document.createElement("button");
    reject.type = "button";
    reject.className = "danger secondary";
    reject.textContent = "拒绝";
    reject.addEventListener("click", () => busy(reject, async () => {
      await bridge.apiPost(`feed/candidates/${candidate.id}/reject`, {});
      toast("候选已拒绝");
      await loadCandidates();
    }));
    row.append(approve, reject);
    card.append(title, meta, editor, row);
    host.append(card);
  });
}

async function loadLogs() {
  const result = await bridge.apiGet("logs", {
    level: $("log-level").value,
    keyword: $("log-keyword").value.trim(),
    limit: 300,
  });
  state.logs = result.items.reverse();
  renderLogs();
}

function renderLogs() {
  $("log-output").textContent = state.logs.map((entry) =>
    `${entry.time} ${entry.level.padEnd(7)} [${entry.profile_id || "-"}] ${entry.message} ${JSON.stringify(entry.details || {})}`
  ).join("\n");
  $("log-output").scrollTop = $("log-output").scrollHeight;
}

async function connectSse() {
  if (state.unloaded || state.sseId) return;
  try {
    state.sseId = await bridge.subscribeSSE("logs/stream", {
      onOpen() { $("sse-state").textContent = "SSE 已连接"; },
      onMessage(event) {
        const payload = event.parsed;
        if (payload?.type === "log" && payload.entry) {
          state.logs.push(payload.entry);
          state.logs = state.logs.slice(-400);
          renderLogs();
        }
      },
      onError() {
        $("sse-state").textContent = "SSE 已断开，正在重连…";
        scheduleReconnect();
      },
    });
  } catch {
    $("sse-state").textContent = "SSE 连接失败，正在重连…";
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  clearTimeout(state.reconnectTimer);
  const current = state.sseId;
  state.sseId = null;
  if (current) bridge.unsubscribeSSE(current);
  state.reconnectTimer = setTimeout(connectSse, 2500);
}

async function loadStorage() {
  const result = await bridge.apiGet("storage");
  const cards = $("storage-cards");
  cards.replaceChildren(
    metric("数据库", bytes(result.database_size)),
    metric("日志总量", bytes(result.log_size)),
    metric("上次清理", result.last_cleanup_at),
    ...Object.entries(result.counts || {}).map(([key, value]) => metric(key, value)),
  );
}

async function initialLoad() {
  await bridge.ready();
  await Promise.all([loadConfig(), loadStatus(), loadLogs(), loadStorage()]);
  await Promise.all([loadLogin(), loadEvents(), loadCandidates()]);
  await connectSse();
}

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.tab));
  });
});

$("refresh-all").addEventListener("click", () => busy($("refresh-all"), async () => {
  await Promise.all([loadStatus(), loadStorage(), loadLogin()]);
  toast("状态已刷新");
}));
$("login-profile").addEventListener("change", loadLogin);
$("request-qr").addEventListener("click", () => busy($("request-qr"), async () => {
  renderLogin(await bridge.apiPost("auth/qr", { profile_id: selectedProfile() }));
}));
$("check-login").addEventListener("click", () => busy($("check-login"), async () => {
  const result = await bridge.apiPost("auth/check", { profile_id: selectedProfile() });
  renderLogin(result);
  await loadStatus();
}));
$("logout").addEventListener("click", () => busy($("logout"), async () => {
  const confirmed = await confirmAction(
    "确认删除该 profile 的本地凭证并退出？",
    { confirmText: "安全退出", danger: true },
  );
  if (!confirmed) return;
  renderLogin(await bridge.apiPost("auth/logout", { profile_id: selectedProfile() }));
  $("qr-image").hidden = true;
  $("qr-placeholder").hidden = false;
  await loadStatus();
}));
$("save-config").addEventListener("click", () => busy($("save-config"), async () => {
  const result = await bridge.apiPost("config/save", state.config);
  const changed = (result.changed || []).join("、") || "无变化";
  $("config-result").textContent = `保存成功；已重载：${changed}`;
  toast(changed === "无变化" ? "设置已保存，配置内容无变化" : "设置已保存并生效", "success");
  await loadConfig();
  await loadStatus();
}));
$("restore-defaults").addEventListener("click", () => busy($("restore-defaults"), async () => {
  const confirmed = await confirmAction(
    "确认恢复插件默认配置？登录凭证将完整保留。",
    { confirmText: "载入默认值" },
  );
  if (!confirmed) return;
  const defaults = await bridge.apiGet("config/defaults");
  state.config = defaults;
  renderConfigForm();
  $("config-result").textContent = "默认值已载入表单；点击“校验并保存”后生效。";
}));
$("search-events").addEventListener("click", () => busy($("search-events"), async () => {
  state.eventPage = 1;
  await loadEvents();
}));
$("event-prev").addEventListener("click", () => busy($("event-prev"), async () => {
  state.eventPage = Math.max(1, state.eventPage - 1);
  await loadEvents();
}));
$("event-next").addEventListener("click", () => busy($("event-next"), async () => {
  state.eventPage = Math.min(state.eventPages, state.eventPage + 1);
  await loadEvents();
}));
$("search-logs").addEventListener("click", () => busy($("search-logs"), loadLogs));
$("reject-expired").addEventListener("click", () => busy($("reject-expired"), async () => {
  const confirmed = await confirmAction(
    "确认拒绝 72 小时前全部待审核候选？",
    { confirmText: "批量拒绝", danger: true },
  );
  if (!confirmed) return;
  const result = await bridge.apiPost("feed/candidates/reject-expired", { older_than_hours: 72 });
  toast(`已拒绝 ${result.rejected} 条`);
  await loadCandidates();
}));
$("copy-diagnostics").addEventListener("click", () => busy($("copy-diagnostics"), async () => {
  const result = await bridge.apiGet("diagnostics");
  const diagnosticText = JSON.stringify(result, null, 2);
  const fallback = $("diagnostics-fallback");
  const output = $("diagnostics-output");
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(diagnosticText);
    fallback.hidden = true;
    toast("脱敏诊断已复制");
  } catch {
    output.value = diagnosticText;
    fallback.hidden = false;
    output.focus();
    output.select();
    let copied = false;
    try { copied = document.execCommand("copy"); }
    catch { copied = false; }
    toast(copied ? "脱敏诊断已复制" : "剪贴板权限受限，请在下方文本框中手动复制");
  }
}));
$("preview-cleanup").addEventListener("click", () => busy($("preview-cleanup"), async () => {
  $("cleanup-output").textContent = JSON.stringify(await bridge.apiGet("storage/cleanup-preview"), null, 2);
}));
$("run-cleanup").addEventListener("click", () => busy($("run-cleanup"), async () => {
  const confirmed = await confirmAction(
    "仅清理插件自身的到期数据。确认执行？",
    { confirmText: "执行清理", danger: true },
  );
  if (!confirmed) return;
  $("cleanup-output").textContent = JSON.stringify(await bridge.apiPost("storage/cleanup", { confirm: true }), null, 2);
  await loadStorage();
}));

$("confirm-cancel").addEventListener("click", () => closeConfirmation(false));
$("confirm-accept").addEventListener("click", () => closeConfirmation(true));
$("confirm-overlay").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeConfirmation(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.confirmResolve) closeConfirmation(false);
});

window.addEventListener("beforeunload", () => {
  state.unloaded = true;
  if (state.confirmResolve) closeConfirmation(false);
  clearInterval(state.loginTimer);
  clearTimeout(state.loginRefreshTimer);
  clearTimeout(state.reconnectTimer);
  if (state.sseId) bridge.unsubscribeSSE(state.sseId);
});

initialLoad().catch((error) => {
  $("hero-summary").textContent = `管理页加载失败：${error.message}`;
  toast(error.message);
});
