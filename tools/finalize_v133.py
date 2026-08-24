from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match for {old[:80]!r}, got {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_once(path: str, marker: str, insertion: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(marker)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one insertion marker, got {count}")
    file_path.write_text(text.replace(marker, insertion + marker, 1), encoding="utf-8")


replace_once(
    "main.py",
    'EARLY_THREAD_RELATION_EXTRA = "xiaoheihe_early_thread_relation"\n',
    'EARLY_THREAD_RELATION_EXTRA = "xiaoheihe_early_thread_relation"\n'
    'EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA = "xiaoheihe_early_thread_compression_attempted"\n',
)

replace_once(
    "main.py",
    '''        if source.compressible_chars <= trigger_chars and not image_requires_compression:\n            return None\n\n        post_chars = int(context_settings["thread_reply_compressed_post_chars"])\n''',
    '''        if source.compressible_chars <= trigger_chars and not image_requires_compression:\n            return None\n        if bool(event.get_extra(EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, False)):\n            return None\n        self._set_event_extra(event, EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, True)\n\n        post_chars = int(context_settings["thread_reply_compressed_post_chars"])\n''',
)

insert_once(
    "tests/test_main_contract.py",
    "\n\ndef test_v133_direct_reply_image_attribution_survives_validation(\n",
    '''\n\nasync def test_v133_thread_compression_failure_is_attempted_only_once_per_event(\n    isolated_smoke_import,\n) -> None:\n    root = Path.cwd()\n    spec = importlib.util.spec_from_file_location(\n        "xhh_plugin_smoke",\n        root / "main.py",\n        submodule_search_locations=[str(root)],\n    )\n    assert spec and spec.loader\n    module = importlib.util.module_from_spec(spec)\n    sys.modules[spec.name] = module\n    spec.loader.exec_module(module)\n\n    class BrokenCompressor:\n        def __init__(self) -> None:\n            self.provider_config = {"modalities": ["text"]}\n            self.calls = 0\n\n        async def text_chat(self, **kwargs):\n            self.calls += 1\n            raise RuntimeError("compressor unavailable")\n\n    compressor = BrokenCompressor()\n\n    class Context:\n        def register_web_api(self, *args):\n            return None\n\n        def get_provider_by_id(self, provider_id):\n            return compressor if provider_id == "compress-fixed" else None\n\n        def get_using_provider(self, umo=None):\n            return compressor\n\n    plugin = module.XiaoheiheAdapterPlugin(Context(), AstrBotConfig())\n    source = ThreadCompressionSource(\n        "post-1",\n        "楼主",\n        "标题",\n        "正文" * 300,\n        "最近评论",\n        "直接回复",\n        "用户",\n        "当前消息",\n    )\n    extras = {}\n\n    class Event:\n        unified_msg_origin = "xiaoheihe:GroupMessage:xhh_thread_post-1_root-1"\n        message_obj = type(\n            "Message",\n            (),\n            {"raw_message": {"route": {"profile_id": "default"}}},\n        )()\n\n        @staticmethod\n        def get_extra(key, default=""):\n            return extras.get(key, default)\n\n        @staticmethod\n        def set_extra(key, value):\n            extras[key] = value\n\n    provider_settings = {\n        "context_provider_id": "compress-fixed",\n        "llm_provider_id": "compress-fixed",\n    }\n    context_settings = {\n        "enable_thread_reply_compression": True,\n        "thread_reply_compression_trigger_chars": 500,\n        "thread_reply_compressed_image_chars": 800,\n        "thread_reply_compressed_post_chars": 1200,\n        "thread_reply_compressed_comments_chars": 1200,\n        "thread_reply_compression_timeout_seconds": 1,\n    }\n\n    first = await plugin._compress_thread_context(\n        Event(),\n        source,\n        provider_settings=provider_settings,\n        context_settings=context_settings,\n        profile_id="default",\n    )\n    second = await plugin._compress_thread_context(\n        Event(),\n        source,\n        provider_settings=provider_settings,\n        context_settings=context_settings,\n        profile_id="default",\n    )\n\n    assert first is None\n    assert second is None\n    assert compressor.calls == 1\n    assert extras[module.EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA] is True\n    await plugin.terminate()\n''',
)

replace_once("metadata.yaml", "version: v1.3.2\n", "version: v1.3.3\n")
replace_once("pyproject.toml", 'version = "1.3.2"\n', 'version = "1.3.3"\n')
replace_once("xiaoheihe/__init__.py", '__version__ = "1.3.2"\n', '__version__ = "1.3.3"\n')
replace_once(
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    "      value: v1.3.2\n",
    "      value: v1.3.3\n",
)
replace_once("README.md", "当前版本：**v1.3.2**\n", "当前版本：**v1.3.3**\n")
insert_once(
    "README.md",
    "## v1.3.2 重点\n",
    '''## v1.3.3 重点\n\n- 楼层上下文按局部相关性路由：正常讨论继续保留原帖；只有长楼层压缩明确判定已经 `drifted` 时才省略低相关原帖文字与原帖视觉，当前消息明确提到原帖、楼主或原帖图片时保持 fail-open。\n- 图片来源扩展为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”，近距离回复链图片优先占用视觉槽位，并继续执行所有者身份绑定、来源校验和失败降级。\n- 长楼层压缩拥有独立超时预算，同一事件最多真正尝试一次压缩 Provider；失败后直接使用确定性上下文 fallback，不重复消耗慢公益站预算。无新增小黑盒 API、数据库迁移或运行依赖。\n\n''',
)
insert_once(
    "CHANGELOG.md",
    "## v1.3.2 - 2026-08-24\n",
    '''## v1.3.3 - 2026-08-24\n\n- 新增楼层局部相关性路由。默认继续保留原帖背景；只有长楼层语义压缩明确判定 `drifted` 时才省略低相关原帖文字和原帖图片摘要，当前消息显式引用原帖、楼主、帖子内容或原帖图片时继续保留原帖。\n- 图片上下文按距离调整为当前评论、直接回复对象、楼层锚点、原帖的稳定优先级；补齐直接回复对象与楼层锚点图片所有者绑定和来源校验，近距离图片优先占用有界图片槽位。\n- 为长楼层压缩单独预留回复超时预算，并增加单事件一次性压缩闸门：前置视觉阶段已经尝试过压缩后，后续主 Agent 注入不再因失败重复请求慢 Provider；压缩失败继续回退已有确定性上下文。\n- 保持正常短回复、主动浏览、权限、AI 额度、审核、幂等发送与数据库行为不变；无新增小黑盒 API、数据库迁移或运行依赖。\n\n''',
)
