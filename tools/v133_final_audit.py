from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:100]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    if marker not in text:
        raise RuntimeError(f"{path}: append marker not found: {marker!r}")
    file_path.write_text(text.replace(marker, marker + addition, 1), encoding="utf-8")


# 1) Explicit original-post references must be unambiguous. Bare “原图” normally
# refers to the nearest/current image and must not resurrect a drifted original post.
replace_once(
    "xiaoheihe/context_relevance.py",
    '''    "楼主图",\n    "原图",\n    "上面的帖子",\n    "上面帖子",\n''',
    '''    "楼主图",\n    "首帖",\n    "本帖",\n    "该帖",\n    "这帖",\n    "那帖",\n    "这个帖子",\n    "这篇帖子",\n    "那个帖子",\n    "那篇帖子",\n    "帖子本身",\n    "上面的帖子",\n    "上面帖子",\n''',
)

# 2) If the fetched thread node exists but omits media fields, retain its trusted
# identity/text and fall back to the notification's comment_b media for the same
# direct target. This covers real “reply to somebody's image + @Bot” shapes.
old_reply_target = '''def _reply_target_image_context(\n    notification: Notification,\n    comments: list[dict[str, Any]],\n    target_id: str,\n) -> tuple[list[str], ImageAttribution | None]:\n    matched = next((item for item in comments if _comment_id(item) == target_id), None)\n    candidate = dict(matched) if isinstance(matched, dict) else {}\n    raw = notification.raw if isinstance(notification.raw, dict) else {}\n    comment_b = raw.get("comment_b", {})\n    if not candidate and isinstance(comment_b, dict):\n        candidate = dict(comment_b)\n    if candidate and not isinstance(candidate.get("user"), dict):\n        user_b = raw.get("user_b", {})\n        if isinstance(user_b, dict):\n            candidate["user"] = user_b\n    images = _image_values(candidate) if candidate else []\n    if not images:\n        return [], None\n    return images, _comment_image_attribution(\n        candidate,\n        source="direct_reply_target",\n        comment_id=target_id,\n    )\n'''
new_reply_target = '''def _reply_target_image_context(\n    notification: Notification,\n    comments: list[dict[str, Any]],\n    target_id: str,\n) -> tuple[list[str], ImageAttribution | None]:\n    matched = next((item for item in comments if _comment_id(item) == target_id), None)\n    candidate = dict(matched) if isinstance(matched, dict) else {}\n    raw = notification.raw if isinstance(notification.raw, dict) else {}\n    raw_comment_b = raw.get("comment_b", {})\n    comment_b = dict(raw_comment_b) if isinstance(raw_comment_b, dict) else {}\n\n    images = _image_values(candidate) if candidate else []\n    comment_b_id = _comment_id(comment_b) if comment_b else ""\n    comment_b_matches_target = bool(comment_b and (not comment_b_id or comment_b_id == target_id))\n    if not images and comment_b_matches_target:\n        # Some notification shapes retain the quoted comment media even when the\n        # separately fetched thread-tree node contains only text. Preserve the\n        # tree node as the identity authority and use comment_b only as a media\n        # fallback for the already-resolved direct target.\n        images = _image_values(comment_b)\n        if images and not candidate:\n            candidate = dict(comment_b)\n\n    if candidate and not isinstance(candidate.get("user"), dict):\n        fallback_user = comment_b.get("user", {}) if comment_b_matches_target else {}\n        user_b = raw.get("user_b", fallback_user)\n        if isinstance(user_b, dict):\n            candidate["user"] = user_b\n    if not images:\n        return [], None\n    return images, _comment_image_attribution(\n        candidate,\n        source="direct_reply_target",\n        comment_id=target_id,\n    )\n'''
replace_once("xiaoheihe/context_builder.py", old_reply_target, new_reply_target)

# 3) relation_to_post is a routing signal, not chat content. Do not prime the main
# model to announce “drifted/off-topic”; simply omit the low-value post payload.
replace_once(
    "xiaoheihe/context_compression.py",
    '''    relation = RELATION_LABELS[result.relation_to_post]\n    speaker_keys = {\n''',
    '''    speaker_keys = {\n''',
)
replace_once(
    "xiaoheihe/context_compression.py",
    '''        if preserve_original_post\n        else ["原帖背景: [当前楼层已明显偏离原帖，本轮省略原帖文字和原帖图片摘要]"]\n    )\n''',
    '''        if preserve_original_post\n        else []\n    )\n''',
)
replace_once(
    "xiaoheihe/context_compression.py",
    '''            f"压缩器派生的当前局部话题（仅供参考）: {result.local_topic}",\n            f"压缩器派生的楼层与原帖关系（仅供参考）: {relation}",\n            "当前消息直接回复对象（高相关性，保留原文）:",\n''',
    '''            f"压缩器派生的当前局部话题（仅供参考）: {result.local_topic}",\n            "当前消息直接回复对象（高相关性，保留原文）:",\n''',
)

# 4) Slow-provider optimization: decide whether semantic compression is needed
# from the same post window the deterministic fallback would actually expose,
# plus the wider bounded floor history. A huge post alone should not force an
# auxiliary LLM call. Once triggered, the full bounded compression source is
# still sent to preserve semantic quality.
replace_once(
    "main.py",
    '''        trigger_chars = int(context_settings["thread_reply_compression_trigger_chars"])\n        image_chars = int(context_settings["thread_reply_compressed_image_chars"])\n        image_requires_compression = len(source.post_image_caption) > image_chars\n        if source.compressible_chars <= trigger_chars and not image_requires_compression:\n            return None\n''',
    '''        trigger_chars = int(context_settings["thread_reply_compression_trigger_chars"])\n        image_chars = int(context_settings["thread_reply_compressed_image_chars"])\n        fallback_post_chars = max(0, int(context_settings.get("thread_reply_post_chars", 1600)))\n        trigger_input_chars = (\n            len(source.post_title)\n            + min(len(source.post_body), fallback_post_chars)\n            + len(source.recent_comments)\n        )\n        image_requires_compression = len(source.post_image_caption) > image_chars\n        if trigger_input_chars <= trigger_chars and not image_requires_compression:\n            return None\n''',
)
# Add trigger diagnostics to both attempt and success log records in this method.
main_path = Path("main.py")
main_text = main_path.read_text(encoding="utf-8")
needle = '"input_chars": source.compressible_chars,\n'
if main_text.count(needle) < 2:
    raise RuntimeError("main.py: expected at least two compression input_chars diagnostics")
main_text = main_text.replace(
    needle,
    '"input_chars": source.compressible_chars,\n                "trigger_input_chars": trigger_input_chars,\n',
    2,
)
main_path.write_text(main_text, encoding="utf-8")

# Regression coverage: ambiguity, internal routing text, notification-media
# fallback, and avoiding a slow auxiliary call for a long post + short floor.
replace_once(
    "tests/test_v133_relevance.py",
    '''    assert detect_explicit_original_post_reference("回到原帖那张图") is True\n    assert detect_explicit_original_post_reference("这图是真的假的") is False\n''',
    '''    assert detect_explicit_original_post_reference("回到原帖那张图") is True\n    assert detect_explicit_original_post_reference("这个帖子楼主说的") is True\n    assert detect_explicit_original_post_reference("求原图") is False\n    assert detect_explicit_original_post_reference("这图是真的假的") is False\n''',
)
replace_once(
    "tests/test_v133_relevance.py",
    '''    assert "楼层正在讨论图灵测试" in rendered\n    assert "具体怎么测试？" in rendered\n    assert "省略原帖文字和原帖图片摘要" in rendered\n''',
    '''    assert "楼层正在讨论图灵测试" in rendered\n    assert "具体怎么测试？" in rendered\n    assert "已明显偏离原帖" not in rendered\n    assert "歪楼" not in rendered\n    assert "省略原帖文字和原帖图片摘要" not in rendered\n''',
)

append_once(
    "tests/test_v133_relevance.py",
    '''    assert [item.owner_uid for item in context.image_attributions] == [\n        "current-user",\n        "target-user",\n        "root-user",\n        "author",\n    ]\n''',
    '''\n\ndef test_reply_target_image_falls_back_to_notification_media_when_tree_omits_it() -> None:\n    notification = Notification(\n        profile_id="default",\n        external_event_id="event-target-media",\n        external_comment_id="current",\n        notification_id="event-target-media",\n        event_type=NotificationType.REPLY,\n        sender_uid="current-user",\n        sender_nickname="当前用户",\n        post_id="post-1",\n        root_comment_id="root",\n        parent_comment_id="current",\n        content="这张图是真的假的？",\n        created_at=1_800_000_000,\n        raw={\n            "comment_b_id": "target",\n            "comment_b": {\n                "id": "target",\n                "images": ["https://img.example/target-from-notification.png"],\n            },\n            "user_b": {"uid": "target-user", "nickname": "被回复用户"},\n        },\n    )\n\n    class Client:\n        async def fetch_thread_context(\n            self, post_id: str, *, root_comment_id: str = "", post_context=None\n        ) -> ThreadContext:\n            return ThreadContext(\n                post_id=post_id,\n                title="原帖",\n                body="正文",\n                author_uid="author",\n                author_name="楼主",\n                comments=[\n                    {\n                        "id": "root",\n                        "user": {"uid": "root-user", "nickname": "根评论用户"},\n                        "content": "根评论",\n                    },\n                    {\n                        "id": "target",\n                        "user": {"uid": "target-user", "nickname": "被回复用户"},\n                        "content": "楼层树里有文字但漏了图片字段",\n                    },\n                ],\n                image_urls=["https://img.example/post.png"],\n            )\n\n    context = asyncio.run(\n        ContextBuilder(max_images=3, host_resolver=_public_resolver).build(notification, Client())\n    )\n    assert context.image_urls == [\n        "https://img.example/target-from-notification.png",\n        "https://img.example/post.png",\n    ]\n    assert context.image_sources == ["direct_reply_target", "original_post"]\n    assert context.image_attributions[0].owner_uid == "target-user"\n    assert context.image_attributions[0].owner_role == "direct_reply_target"\n''',
)

append_once(
    "tests/test_main_contract.py",
    '''    assert rejected.source == "event_image"\n    assert rejected.owner_role == "unknown"\n''',
    '''\n\nasync def test_v133_long_post_short_floor_does_not_force_context_llm(\n    isolated_smoke_import,\n) -> None:\n    root = Path.cwd()\n    spec = importlib.util.spec_from_file_location(\n        "xhh_plugin_smoke",\n        root / "main.py",\n        submodule_search_locations=[str(root)],\n    )\n    assert spec and spec.loader\n    module = importlib.util.module_from_spec(spec)\n    sys.modules[spec.name] = module\n    spec.loader.exec_module(module)\n\n    class Compressor:\n        def __init__(self) -> None:\n            self.provider_config = {"modalities": ["text"]}\n            self.calls = 0\n\n        async def text_chat(self, **kwargs):\n            self.calls += 1\n            raise AssertionError("long post alone must not trigger context LLM")\n\n    compressor = Compressor()\n\n    class Context:\n        def register_web_api(self, *args):\n            return None\n\n        def get_provider_by_id(self, provider_id):\n            return compressor\n\n        def get_using_provider(self, umo=None):\n            return compressor\n\n    class Event:\n        unified_msg_origin = "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1"\n        message_obj = type(\n            "Message",\n            (),\n            {"raw_message": {"route": {"profile_id": "default"}}},\n        )()\n        extras = {}\n\n        @classmethod\n        def get_extra(cls, key, default=""):\n            return cls.extras.get(key, default)\n\n        @classmethod\n        def set_extra(cls, key, value):\n            cls.extras[key] = value\n\n    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())\n    source = ThreadCompressionSource(\n        post_id="post-1",\n        post_author="楼主",\n        post_title="一个很长的帖子",\n        post_body="正文" * 4000,\n        recent_comments="只有一条很短的普通回复",\n        reply_target="直接回复对象",\n        current_sender="当前用户",\n        current_message="继续说",\n    )\n    result = await plugin._compress_thread_context(\n        Event(),\n        source,\n        provider_settings={\n            "context_provider_id": "compress-fixed",\n            "llm_provider_id": "compress-fixed",\n        },\n        context_settings={\n            "enable_thread_reply_compression": True,\n            "thread_reply_compression_trigger_chars": 2400,\n            "thread_reply_post_chars": 1600,\n            "thread_reply_compressed_image_chars": 800,\n            "thread_reply_compressed_post_chars": 700,\n            "thread_reply_compressed_comments_chars": 1400,\n            "thread_reply_compression_timeout_seconds": 30,\n        },\n        profile_id="default",\n    )\n    assert result is None\n    assert compressor.calls == 0\n    assert Event.extras.get(module.EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA) is None\n    await plugin.terminate()\n''',
)

# User-facing and maintainer docs must match the refined final semantics.
replace_once(
    "README.md",
    '''- 楼层上下文按局部相关性路由：普通短回复不新增额外 LLM 判断，正常讨论继续保留原帖；只有既有长楼层压缩明确判定 `drifted` 时才省略低相关原帖文字与原帖视觉，当前消息明确提到原帖、楼主或原帖图片时立即恢复原帖。\n- 图片来源扩展为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”，近距离回复链图片优先占用视觉槽位，并继续执行所有者身份绑定、来源校验和失败降级。\n- 长楼层压缩拥有独立超时预算，同一事件最多真正尝试一次压缩 Provider；失败后直接使用确定性上下文 fallback，不重复消耗慢 Provider 预算。无新增小黑盒 API、数据库迁移、配置项或运行依赖。\n''',
    '''- 楼层上下文按局部相关性路由：普通短回复不新增额外 LLM 判断；长帖的短楼层也不会仅因原帖很长触发辅助压缩。只有既有长楼层压缩明确判定 `drifted` 时才省略低相关原帖文字与视觉，明确引用原帖时立即恢复。\n- 图片来源扩展为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”；近距离图片优先占用视觉槽位，楼层树漏掉被回复评论图片时可使用同一通知里的直接回复对象媒体回退，并继续执行所有者/来源校验。\n- 长楼层压缩拥有独立超时预算，同一事件最多真正尝试一次压缩 Provider；相关性结果只用于内部路由，不作为回复正文提示。失败后直接使用确定性上下文 fallback，为慢 Provider 主回复和 fallback 保留预算。无新增小黑盒 API、数据库迁移、配置项或运行依赖。\n''',
)
replace_once(
    "CHANGELOG.md",
    '''- 新增楼层局部相关性路由。默认继续保留原帖背景；只有长楼层语义压缩明确判定 `drifted` 时才省略低相关原帖文字和原帖图片摘要，当前消息显式引用原帖、楼主、帖子内容或原帖图片时继续保留原帖。\n- 图片上下文按距离调整为当前评论、直接回复对象、楼层锚点、原帖的稳定优先级；补齐直接回复对象与楼层锚点图片所有者绑定和来源校验，近距离图片优先占用有界图片槽位。\n- 为长楼层压缩单独预留回复超时预算，并增加单事件一次性压缩闸门：前置视觉阶段已经尝试过压缩后，后续主 Agent 注入不再因失败重复请求慢 Provider；压缩失败继续回退已有确定性上下文。\n''',
    '''- 新增楼层局部相关性路由。默认继续保留原帖背景；只有长楼层语义压缩明确判定 `drifted` 时才省略低相关原帖文字/视觉，相关性结果只作为内部路由信号，不再向最终回答模型重复暴露“已偏离原帖”等提示。明确引用原帖时继续 fail-open 恢复背景，模糊的“原图”不再被误判为原帖引用。\n- 图片上下文按距离调整为当前评论、直接回复对象、楼层锚点、原帖的稳定优先级；补齐直接回复对象与楼层锚点图片所有者绑定和来源校验。楼层树存在回复对象但缺少媒体字段时，可回退同一通知 `comment_b` 的目标媒体，近距离图片优先占用有界图片槽位。\n- 为长楼层压缩单独预留回复超时预算，并增加单事件一次性压缩闸门；压缩触发按实际确定性原帖窗口 + 宽楼层窗口估算，避免“原帖很长但楼层很短”无意义调用慢 Provider。前置阶段尝试失败后，后续主 Agent 注入不再重复请求，继续回退确定性上下文。\n''',
)

replace_once(
    "docs/architecture.md",
    '''正常帖子和短楼层不增加一次额外 LLM 分类。默认按 `unclear` / fail-open 处理，继续保留原帖背景；达到既有长楼层压缩条件时，复用同一次上下文 Provider 输出的 `relation_to_post`：\n''',
    '''正常帖子和短楼层不增加一次额外 LLM 分类。压缩触发长度按**确定性 fallback 实际会暴露的原帖窗口 + 更宽但有界的楼层窗口**估算，因此原帖本身很长、楼层仍很短时不会仅因隐藏的完整原帖文本额外调用上下文 Provider；一旦确实达到压缩条件，仍使用完整的有界压缩源保证语义质量。默认按 `unclear` / fail-open 处理，继续保留原帖背景；达到既有长楼层压缩条件时，复用同一次上下文 Provider 输出的 `relation_to_post`：\n''',
)
replace_once(
    "docs/architecture.md",
    '''`drifted` 的含义是“当前局部对话已经能脱离原帖独立理解且最近楼层持续转向新的局部话题”，不是简单的关键词不相似。模型可以直接跟随歪楼后的自然对话，不应把内部路由状态主动说成“你们歪楼了”。压缩失败、超时、返回未知关系值或无法判断时都保留原帖，避免边界判断损害正常讨论。\n''',
    '''`drifted` 的含义是“当前局部对话已经能脱离原帖独立理解且最近楼层持续转向新的局部话题”，不是简单的关键词不相似。这个关系标签只保存在插件事件元数据/日志用于路由，**不会作为“已明显偏离原帖/歪楼”等自然语言提示再注入最终回答模型**；最终模型只看到筛选后的必要上下文。压缩失败、超时、返回未知关系值或无法判断时都保留原帖，避免边界判断损害正常讨论。\n''',
)
replace_once(
    "docs/architecture.md",
    '''图片槽位先为每个近距离来源保留代表图，再加入同来源的额外图片；原帖图永远位于本地回复链图片之后。因此“别人发图 → 用户回复该评论并 @Bot”时，被直接回复的图片不会被主贴图片挤掉。直接回复对象和楼层锚点图片各自保留 `comment:<id>` 身份键；来源、角色或身份键不一致时降级为未知来源，不把外部字段直接当成可信所有权。\n''',
    '''图片槽位先为每个近距离来源保留代表图，再加入同来源的额外图片；原帖图永远位于本地回复链图片之后。因此“别人发图 → 用户回复该评论并 @Bot”时，被直接回复的图片不会被主贴图片挤掉。若帖子/楼层树已经找到直接回复对象但该节点缺少媒体字段，插件会仅对**同一已解析目标**回退通知中的 `comment_b` 媒体；楼层树仍优先承担身份/文本归属。直接回复对象和楼层锚点图片各自保留 `comment:<id>` 身份键；来源、角色或身份键不一致时降级为未知来源，不把外部字段直接当成可信所有权。\n''',
)

replace_once(
    "docs/compatibility.md",
    '''同一事件的长楼层压缩最多真正请求一次上下文 Provider。前置阶段失败或超时后，后续 Agent 注入直接使用确定性 fallback，不会再次等待同一个慢 Provider。短楼层仍不新增独立相关性 LLM 调用，因此普通帖子/回复的平均模型调用数保持原行为。\n''',
    '''同一事件的长楼层压缩最多真正请求一次上下文 Provider。前置阶段失败或超时后，后续 Agent 注入直接使用确定性 fallback，不会再次等待同一个慢 Provider。压缩触发按实际 fallback 原帖窗口和宽楼层窗口估算，长原帖本身不会让短楼层无意义触发辅助模型；短楼层仍不新增独立相关性 LLM 调用，因此普通帖子/回复的平均模型调用数保持原行为。\n''',
)
replace_once(
    "docs/compatibility.md",
    '''近距离来源优先占用有界图片槽位，因此被回复评论自己的图片不会被主贴图片挤掉。当前评论和直接回复对象属于最高两级视觉来源，预处理全部失败时仍允许 AstrBot 原生视觉作为最后兜底；楼层锚点、原帖和未知来源继续按低优先级 fail-closed。来源、角色和 `comment:<id>` 归属键必须一致，否则降级为未知来源。\n''',
    '''近距离来源优先占用有界图片槽位，因此被回复评论自己的图片不会被主贴图片挤掉。楼层树命中直接回复对象但缺少媒体字段时，可使用同一通知 `comment_b` 的媒体作为该已解析目标的回退，不改变身份归属来源。当前评论和直接回复对象属于最高两级视觉来源，预处理全部失败时仍允许 AstrBot 原生视觉作为最后兜底；楼层锚点、原帖和未知来源继续按低优先级 fail-closed。来源、角色和 `comment:<id>` 归属键必须一致，否则降级为未知来源。\n''',
)

replace_once(
    "docs/xiaoheihe-api-contract.md",
    '''通知游标按 `profile_id + notification_type` 持久化最新 `message_id`。首次默认只建立历史基线；超过单轮页数的旧区间持久化为 backfill，实时 cursor 可以继续推进。\n''',
    '''通知游标按 `profile_id + notification_type` 持久化最新 `message_id`。首次默认只建立历史基线；超过单轮页数的旧区间持久化为 backfill，实时 cursor 可以继续推进。\n\nv1.3.3 的图片上下文没有新增请求：当帖子/楼层树已解析出直接回复对象、但该树节点恰好缺少媒体字段时，插件可使用**同一条通知已有的 `comment_b` 媒体字段**作为该目标的视觉回退；目标 ID、身份和文本仍以已解析回复链为准，这属于本地上下文归并，不改变 HTTP 端点或参数。\n''',
)

replace_once(
    "docs/testing.md",
    '''- 当前消息显式提到原帖、主帖、楼主、帖子内容或原帖图片时强制恢复原帖；\n- `drifted` 渲染仍完整保留当前消息、直接回复对象、局部楼层主题和参与者归属，不把内部“歪楼”判断当成 Bot 对用户的说教文本；\n''',
    '''- 当前消息显式提到原帖、主帖、楼主、帖子内容或原帖图片时强制恢复原帖；模糊的“求原图”仍按最近图片语境处理，不错误恢复主帖；\n- `drifted` 渲染仍完整保留当前消息、直接回复对象、局部楼层主题和参与者归属，内部关系标签不再以“已偏离原帖/歪楼”等自然语言注入最终模型；\n''',
)
replace_once(
    "docs/testing.md",
    '''- 直接回复对象与楼层锚点图片能够从楼层树 / 通知回退字段提取；\n''',
    '''- 直接回复对象与楼层锚点图片能够从楼层树提取；楼层树命中目标但媒体字段缺失时，直接回复对象图片可安全回退同一通知 `comment_b` 的媒体；\n''',
)
replace_once(
    "docs/testing.md",
    '''- 同一事件的长楼层压缩最多真正请求一次上下文 Provider；\n- 前置压缩失败后，后续 `on_llm_request` 不会再次请求同一个慢 Provider；\n''',
    '''- 同一事件的长楼层压缩最多真正请求一次上下文 Provider；\n- 长原帖 + 短楼层不会仅因完整原帖文本超过阈值而额外调用上下文 Provider；\n- 前置压缩失败后，后续 `on_llm_request` 不会再次请求同一个慢 Provider；\n''',
)

print("v1.3.3 final audit patch applied")
