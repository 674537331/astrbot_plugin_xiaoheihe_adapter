const bridge = window.AstrBotPluginPage;
const probeButton = document.getElementById("probe-topics");
const editButton = document.getElementById("edit-topic-selection");
const saveTopicButton = document.getElementById("save-topic-selection");
const cancelTopicButton = document.getElementById("cancel-topic-selection");
const saveFallbackButton = document.getElementById("save-fallback-sources");
const statusNode = document.getElementById("topic-probe-status");
const fallbackStatusNode = document.getElementById("fallback-source-status");
const listNode = document.getElementById("topic-probe-list");
const fallbackListNode = document.getElementById("fallback-source-list");
const editorNode = document.getElementById("topic-selection-editor");
const searchNode = document.getElementById("topic-search");
const selectionCountNode = document.getElementById("topic-selection-count");
const summaryNamesNode = document.getElementById("source-summary-names");
const summaryCountNode = document.getElementById("source-summary-count");
const sourceModeBadge = document.getElementById("source-mode-badge");
const profileSelect = document.getElementById("login-profile");
const sourcesTab = document.querySelector('.tabs button[data-tab="sources"]');
const MAX_SELECTED_TOPICS = 20;

const FALLBACK_SOURCES = [
  ["all", "全部"],
  ["pc_game", "PC 游戏"],
  ["mobile_game", "手机游戏"],
  ["console_game", "主机游戏"],
  ["community", "盒友杂谈"],
  ["daily", "盒友日常"],
  ["digital_tech", "数码科技"],
  ["anime", "动漫二次元"],
  ["film_tv", "影视娱乐"],
  ["esports", "电竞赛事"],
  ["guide", "游戏攻略"],
  ["deals", "优惠资讯"],
  ["indie_game", "独立游戏"],
];

let currentConfig = null;
let lastProbe = null;
let lastProbeProfile = "";
let draftTopicIds = new Set();

function selectedProfile() {
  return profileSelect?.value || "default";
}

function configuredTopicIds() {
  return (currentConfig?.proactive_feed?.topic_ids || []).map(String);
}

function configuredFallbackSources() {
  const values = currentConfig?.proactive_feed?.fallback_sources;
  return Array.isArray(values) && values.length ? values.map(String) : ["all"];
}

function setStatus(message, tone = "") {
  statusNode.textContent = message;
  statusNode.dataset.tone = tone;
}

function topicById() {
  return new Map((lastProbe?.topics || []).map((topic) => [String(topic.id), topic]));
}

function topicLabel(topic) {
  const group = String(topic?.group || "").trim();
  const name = String(topic?.name || topic?.id || "未知分区").trim();
  return group && group !== name ? `${name} · ${group}` : name;
}

function renderSummary() {
  const selected = configuredTopicIds();
  const lookup = topicById();
  const labels = selected.map((topicId) => {
    const topic = lookup.get(topicId);
    return topic ? String(topic.name || topicId) : `topic_id ${topicId}`;
  });
  summaryNamesNode.textContent = labels.length ? labels.join("、") : "尚未选择真实分区";
  summaryCountNode.textContent = `${selected.length} 个分区`;
  sourceModeBadge.textContent = selected.length ? "真实分区优先" : "推荐流分类";
  sourceModeBadge.dataset.mode = selected.length ? "topics" : "fallback";
  renderFallbackSources();
}

function renderProbeStatus() {
  if (!lastProbe) return;
  const verification = lastProbe.feed_verified
    ? `已验证分区 ${lastProbe.verified_topic_id} 的真实帖子流。`
    : `分区目录读取成功，但帖子流探测未通过：${lastProbe.feed_error || "未知错误"}`;
  setStatus(
    `读取到 ${lastProbe.topic_count || 0} 个真实分区。${verification}`,
    lastProbe.feed_verified ? "success" : "warning",
  );
}

function updateDraftState() {
  selectionCountNode.textContent = `${draftTopicIds.size} / ${MAX_SELECTED_TOPICS}`;
  saveTopicButton.disabled = !lastProbe || draftTopicIds.size > MAX_SELECTED_TOPICS;
  if (draftTopicIds.size > MAX_SELECTED_TOPICS) {
    setStatus(`最多选择 ${MAX_SELECTED_TOPICS} 个真实分区；当前已选择 ${draftTopicIds.size} 个。`, "error");
    return;
  }
  renderProbeStatus();
}

function renderTopics() {
  listNode.replaceChildren();
  if (!lastProbe?.topics?.length) return;

  const query = String(searchNode.value || "").trim().toLowerCase();
  const groups = new Map();
  lastProbe.topics.forEach((topic) => {
    const topicId = String(topic.id || "").trim();
    if (!topicId) return;
    const group = String(topic.group || "其他分区").trim() || "其他分区";
    const haystack = `${topic.name || ""} ${group} ${topicId}`.toLowerCase();
    if (query && !haystack.includes(query)) return;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(topic);
  });

  if (!groups.size) {
    const empty = document.createElement("p");
    empty.className = "result";
    empty.textContent = "没有匹配的真实分区。";
    listNode.append(empty);
    updateDraftState();
    return;
  }

  for (const [groupName, topics] of groups) {
    const section = document.createElement("details");
    section.className = "config-section";
    section.open =
      Boolean(query) ||
      topics.some((topic) => draftTopicIds.has(String(topic.id))) ||
      groupName === "推荐";
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
      checkbox.checked = draftTopicIds.has(topicId);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) draftTopicIds.add(topicId);
        else draftTopicIds.delete(topicId);
        updateDraftState();
      });
      row.append(copy, checkbox);
      grid.append(row);
    });
    section.append(grid);
    listNode.append(section);
  }
  updateDraftState();
}

function renderFallbackSources() {
  fallbackListNode.replaceChildren();
  const selected = new Set(configuredFallbackSources());
  const realTopicsEnabled = configuredTopicIds().length > 0;

  FALLBACK_SOURCES.forEach(([value, label]) => {
    const row = document.createElement("label");
    row.className = "fallback-source-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.fallbackSource = value;
    checkbox.checked = selected.has(value);
    checkbox.disabled = realTopicsEnabled;
    checkbox.addEventListener("change", () => {
      if (value === "all" && checkbox.checked) {
        fallbackListNode
          .querySelectorAll('input[data-fallback-source]:not([data-fallback-source="all"])')
          .forEach((input) => {
            input.checked = false;
          });
      } else if (value !== "all" && checkbox.checked) {
        const all = fallbackListNode.querySelector('input[data-fallback-source="all"]');
        if (all) all.checked = false;
      }
      const checked = checkedFallbackSources();
      saveFallbackButton.disabled = realTopicsEnabled || checked.length === 0;
    });
    const text = document.createElement("span");
    text.textContent = label;
    row.append(checkbox, text);
    fallbackListNode.append(row);
  });

  saveFallbackButton.disabled = realTopicsEnabled;
  if (realTopicsEnabled) {
    fallbackStatusNode.textContent =
      "已启用真实分区浏览，此项当前不生效。若本轮全部真实分区请求失败，将自动使用这里已保存的分类回退。";
    fallbackStatusNode.dataset.tone = "warning";
  } else {
    fallbackStatusNode.textContent = `当前生效：${configuredFallbackSources()
      .map((value) => FALLBACK_SOURCES.find(([key]) => key === value)?.[1] || value)
      .join("、")}`;
    fallbackStatusNode.dataset.tone = "success";
  }
}

function checkedFallbackSources() {
  return [...fallbackListNode.querySelectorAll('input[data-fallback-source]:checked')]
    .map((input) => input.dataset.fallbackSource)
    .filter(Boolean);
}

async function loadSourceConfig() {
  currentConfig = await bridge.apiGet("config");
  renderSummary();
}

async function probeTopics({ openEditor = false } = {}) {
  probeButton.disabled = true;
  editButton.disabled = true;
  setStatus("正在只读探测真实分区和帖子流……");
  try {
    const result = await bridge.apiGet("feed/topics/probe", {
      profile_id: selectedProfile(),
    });
    lastProbe = result;
    lastProbeProfile = selectedProfile();
    renderProbeStatus();
    renderSummary();
    if (openEditor) {
      draftTopicIds = new Set(configuredTopicIds());
      editorNode.hidden = false;
      renderTopics();
      searchNode.focus();
    }
  } catch (error) {
    lastProbe = null;
    lastProbeProfile = "";
    listNode.replaceChildren();
    setStatus(`真实分区探测失败：${error.message}`, "error");
  } finally {
    probeButton.disabled = false;
    editButton.disabled = false;
  }
}

async function editTopics() {
  if (!lastProbe || lastProbeProfile !== selectedProfile()) {
    await probeTopics({ openEditor: true });
    return;
  }
  draftTopicIds = new Set(configuredTopicIds());
  editorNode.hidden = false;
  renderTopics();
  searchNode.focus();
}

async function saveTopicSelection() {
  if (draftTopicIds.size > MAX_SELECTED_TOPICS) {
    updateDraftState();
    return;
  }
  saveTopicButton.disabled = true;
  setStatus("正在保存真实分区选择……");
  try {
    const config = await bridge.apiGet("config");
    config.proactive_feed ||= {};
    config.proactive_feed.topic_ids = [...draftTopicIds];
    await bridge.apiPost("config/save", config);
    setStatus(`已保存 ${draftTopicIds.size} 个真实分区。页面将刷新以同步全部设置状态。`, "success");
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    setStatus(`保存真实分区失败：${error.message}`, "error");
    saveTopicButton.disabled = false;
  }
}

async function saveFallbackSources() {
  const selected = checkedFallbackSources();
  if (!selected.length || configuredTopicIds().length) return;
  saveFallbackButton.disabled = true;
  fallbackStatusNode.textContent = "正在保存回退推荐流分类……";
  try {
    const config = await bridge.apiGet("config");
    config.proactive_feed ||= {};
    config.proactive_feed.fallback_sources = selected;
    await bridge.apiPost("config/save", config);
    fallbackStatusNode.textContent = "回退推荐流分类已保存。页面将刷新以同步全部设置状态。";
    fallbackStatusNode.dataset.tone = "success";
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    fallbackStatusNode.textContent = `保存回退分类失败：${error.message}`;
    fallbackStatusNode.dataset.tone = "error";
    saveFallbackButton.disabled = false;
  }
}

async function loadSources({ probe = true } = {}) {
  await bridge.ready();
  await loadSourceConfig();
  if (probe && (!lastProbe || lastProbeProfile !== selectedProfile())) {
    await probeTopics();
  }
}

probeButton?.addEventListener("click", () => probeTopics());
editButton?.addEventListener("click", editTopics);
saveTopicButton?.addEventListener("click", saveTopicSelection);
cancelTopicButton?.addEventListener("click", () => {
  editorNode.hidden = true;
  draftTopicIds = new Set(configuredTopicIds());
});
saveFallbackButton?.addEventListener("click", saveFallbackSources);
searchNode?.addEventListener("input", renderTopics);
sourcesTab?.addEventListener("click", () => {
  loadSources({ probe: true }).catch((error) =>
    setStatus(`浏览来源加载失败：${error.message}`, "error"),
  );
});
profileSelect?.addEventListener("change", () => {
  lastProbe = null;
  lastProbeProfile = "";
  editorNode.hidden = true;
  if (document.getElementById("sources")?.classList.contains("active")) {
    loadSources({ probe: true }).catch((error) =>
      setStatus(`浏览来源加载失败：${error.message}`, "error"),
    );
  }
});
