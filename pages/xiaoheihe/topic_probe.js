const bridge = window.AstrBotPluginPage;
const probeButton = document.getElementById("probe-topics");
const saveButton = document.getElementById("save-topic-selection");
const statusNode = document.getElementById("topic-probe-status");
const listNode = document.getElementById("topic-probe-list");
const profileSelect = document.getElementById("login-profile");
const MAX_SELECTED_TOPICS = 20;

let lastProbe = null;

function selectedProfile() {
  return profileSelect?.value || "default";
}

function setStatus(message, tone = "") {
  statusNode.textContent = message;
  statusNode.dataset.tone = tone;
}

function checkedTopicIds() {
  return [...listNode.querySelectorAll('input[type="checkbox"][data-topic-id]:checked')]
    .map((input) => input.dataset.topicId)
    .filter(Boolean);
}

function updateSelectionState() {
  const selected = checkedTopicIds();
  saveButton.disabled = !lastProbe || selected.length > MAX_SELECTED_TOPICS;
  if (selected.length > MAX_SELECTED_TOPICS) {
    setStatus(`最多选择 ${MAX_SELECTED_TOPICS} 个真实分区；当前已选择 ${selected.length} 个。`, "error");
    return;
  }
  if (lastProbe) {
    const verification = lastProbe.feed_verified
      ? `已验证分区 ${lastProbe.verified_topic_id} 的真实帖子流。`
      : `分区目录读取成功，但帖子流探测未通过：${lastProbe.feed_error || "未知错误"}`;
    setStatus(
      `读取到 ${lastProbe.topic_count || 0} 个真实分区；当前选择 ${selected.length} 个。${verification}`,
      lastProbe.feed_verified ? "success" : "warning",
    );
  }
}

function topicLabel(topic) {
  const group = String(topic.group || "").trim();
  const name = String(topic.name || topic.id || "未知分区").trim();
  return group && group !== name ? `${name} · ${group}` : name;
}

function renderTopics(result) {
  lastProbe = result;
  listNode.replaceChildren();
  const configured = new Set((result.configured_topic_ids || []).map(String));
  const groups = new Map();
  (result.topics || []).forEach((topic) => {
    const group = String(topic.group || "其他分区").trim() || "其他分区";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(topic);
  });

  if (!result.topics?.length) {
    setStatus("真实分区目录为空。可能是登录失效或小黑盒接口结构发生变化。", "error");
    saveButton.disabled = true;
    return;
  }

  for (const [groupName, topics] of groups) {
    const section = document.createElement("details");
    section.className = "config-section";
    section.open = groups.size <= 5;
    const summary = document.createElement("summary");
    const heading = document.createElement("span");
    heading.textContent = groupName;
    const count = document.createElement("small");
    count.textContent = `${topics.length} 个`;
    summary.append(heading, count);
    section.append(summary);

    const grid = document.createElement("div");
    grid.className = "config-grid";
    topics.forEach((topic) => {
      const topicId = String(topic.id || "").trim();
      if (!topicId) return;
      const row = document.createElement("label");
      row.className = "config-toggle";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = topicLabel(topic);
      const hint = document.createElement("small");
      hint.className = "config-hint";
      hint.textContent = `topic_id: ${topicId}`;
      copy.append(title, hint);
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.topicId = topicId;
      checkbox.checked = configured.has(topicId);
      checkbox.addEventListener("change", updateSelectionState);
      row.append(copy, checkbox);
      grid.append(row);
    });
    section.append(grid);
    listNode.append(section);
  }

  updateSelectionState();
}

async function probeTopics() {
  probeButton.disabled = true;
  saveButton.disabled = true;
  setStatus("正在只读探测真实分区和帖子流……");
  try {
    const result = await bridge.apiGet("feed/topics/probe", {
      profile_id: selectedProfile(),
    });
    renderTopics(result);
  } catch (error) {
    lastProbe = null;
    listNode.replaceChildren();
    setStatus(`真实分区探测失败：${error.message}`, "error");
  } finally {
    probeButton.disabled = false;
  }
}

async function saveSelection() {
  const selected = checkedTopicIds();
  if (selected.length > MAX_SELECTED_TOPICS) {
    updateSelectionState();
    return;
  }
  saveButton.disabled = true;
  setStatus("正在保存真实分区选择……");
  try {
    const config = await bridge.apiGet("config");
    config.proactive_feed ||= {};
    config.proactive_feed.topic_ids = selected;
    await bridge.apiPost("config/save", config);
    setStatus(`已保存 ${selected.length} 个真实分区。页面将刷新以同步统一配置。`, "success");
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    setStatus(`保存真实分区失败：${error.message}`, "error");
    saveButton.disabled = false;
  }
}

probeButton?.addEventListener("click", probeTopics);
saveButton?.addEventListener("click", saveSelection);
profileSelect?.addEventListener("change", () => {
  lastProbe = null;
  listNode.replaceChildren();
  saveButton.disabled = true;
  setStatus("账号档案已切换，请重新探测真实分区。");
});
