from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .api_client import SendUncertainError, XiaoheiheApiClient
from .models import Notification, NotificationType, RoutingTarget
from .repository import Repository
from .security import clean_untrusted_text, sanitize_reply_text

SyntheticDispatch = Callable[[Notification, dict[str, Any]], Awaitable[None]]
ReviewedDelivery = Callable[[RoutingTarget, str], Awaitable[dict[str, Any]]]

SKIP_PATTERN = re.compile(r"(??|??|??|??|??|??|??|??|??|??|??)", re.I)

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "all": (),
    "pc_game": ("pc??", "pc ??", "steam??", "steam", "????"),
    "mobile_game": ("????", "??"),
    "console_game": ("????", "switch", "xbox", "playstation", "ps5", "ps4", "???"),
    "community": ("????", "??"),
    "daily": ("????", "??", "??"),
    "digital_tech": ("????", "??", "??", "??", "??"),
    "anime": ("??", "???", "??", "??", "??"),
    "film_tv": ("??", "??", "???", "??"),
    "esports": ("??", "??"),
    "guide": ("??", "??", "??"),
    "deals": ("????", "??", "??", "??"),
    "indie_game": ("????", "indie"),
}


class FeedService:
    def __init__(
        self,
        profile_id: str,
        config: dict[str, Any],
        client: XiaoheiheApiClient,
        repository: Repository,
        synthetic_dispatch: SyntheticDispatch,
        reviewed_delivery: ReviewedDelivery,
        approval_lock: asyncio.Lock | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.config = config
        self.client = client
        self.repository = repository
        self.synthetic_dispatch = synthetic_dispatch
        self.reviewed_delivery = reviewed_delivery
        self._approval_lock = approval_lock or asyncio.Lock()

    async def run_once(self) -> int:
        feed_config = self.config["proactive_feed"]
        if not feed_config.get("enabled", False):
            return 0
        counters = await self.repository.today_counters(self.profile_id)
        remaining = max(0, int(feed_config["max_per_day"]) - counters["proactive_count"])
        if not remaining:
            return 0
        page = await self.client.fetch_feed(offset=0)
        browsed_posts = page.items[:remaining]
        if browsed_posts:
            await self.repository.increment_counter(self.profile_id, proactive=len(browsed_posts))
        generated = 0
        for post in browsed_posts:
            if generated >= int(feed_config["max_per_run"]):
                break
            post_id = str(post.get("link_id", post.get("post_id", post.get("id", ""))))
            author = post.get("author", post.get("user", {}))
            author = author if isinstance(author, dict) else {}
            author_uid = str(
                author.get(
                    "uid",
                    author.get("heybox_id", author.get("user_id", author.get("id", ""))),
                )
            )
            if not self._eligible(post, author_uid):
                continue
            if await self.repository.has_replied_to_post(self.profile_id, post_id):
                continue
            notification = Notification(
                profile_id=self.profile_id,
                external_event_id=f"feed:{post_id}",
                external_comment_id=f"feed:{post_id}",
                notification_id=f"feed:{post_id}",
                event_type=NotificationType.PROACTIVE_FEED,
                sender_uid=author_uid,
                sender_nickname=str(author.get("nickname", author.get("username", ""))),
                post_id=post_id,
                root_comment_id="",
                parent_comment_id="",
                content=clean_untrusted_text(
                    str(post.get("content", post.get("description", post.get("text", "")))),
                    max_chars=4000,
                ),
                created_at=time.time(),
                post_author_uid=author_uid,
                explicit_wake=True,
                image_urls=[
                    str(url) for url in post.get("image_urls", []) if isinstance(url, str) and url
                ],
                raw={"event_type": "proactive_feed", "post": post},
            )
            await self.synthetic_dispatch(
                notification,
                {
                    "candidate_reason": (
                        f"??? ? {feed_config.get('source', 'all')} ? ???????"
                    ),
                    "post_title": str(post.get("title", "")),
                    "post_author_uid": author_uid,
                },
            )
            generated += 1
        return generated

    def _eligible(self, post: dict[str, Any], author_uid: str) -> bool:
        title = str(post.get("title", ""))
        body = str(post.get("content", post.get("description", post.get("text", ""))))
        text = f"{title}\n{body}".strip()
        if not text or len(clean_untrusted_text(text)) < 4:
            return False
        if SKIP_PATTERN.search(text):
            return False
        post_type = str(post.get("content_type", post.get("type", ""))).lower()
        if post_type in {"ad", "advertisement", "lottery", "trade"}:
            return False
        credentials = getattr(self.client, "credentials", None)
        self_uid = str(getattr(credentials, "uid", "") or "")
        if self_uid and author_uid == self_uid:
            return False
        author_blacklist = {
            str(value) for value in self.config["permissions"].get("author_blacklist", [])
        }
        if author_uid and author_uid in author_blacklist:
            return False
        allowed_types = {
            str(value).casefold()
            for value in self.config["proactive_feed"].get("allowed_post_types", [])
            if str(value).strip()
        }
        if allowed_types and post_type not in allowed_types:
            return False
        source = str(self.config["proactive_feed"].get("source", "all"))
        aliases = SECTION_ALIASES.get(source, ())
        if aliases:
            section_names = {
                str(value).strip().casefold()
                for value in post.get("section_names", [])
                if str(value).strip()
            }
            if not any(alias.casefold() in name for alias in aliases for name in section_names):
                return False
        keywords = [
            str(item).casefold()
            for item in self.config["proactive_feed"].get("keywords", [])
            if str(item).strip()
        ]
        return not keywords or any(keyword in text.casefold() for keyword in keywords)

    async def approve(self, candidate_id: int, edited_text: str | None = None) -> str:
        async with self._approval_lock:
            candidate = await self.repository.feed_candidate(candidate_id)
            if not candidate:
                raise ValueError("?????")
            if candidate["status"] not in {"pending", "approved"}:
                raise ValueError("?????")
            text = sanitize_reply_text(
                edited_text or candidate.get("edited_text") or candidate["generated_text"],
                int(self.config["reply"]["max_reply_chars"]),
            )
            if self.config["proactive_feed"].get("dry_run", True):
                changed = await self.repository.review_feed_candidate(
                    candidate_id,
                    "approved",
                    text,
                )
                if not changed:
                    raise ValueError("?????")
                return "dry_run"
            claimed = await self.repository.claim_feed_candidate_for_send(
                candidate_id,
                text,
            )
            if claimed is None:
                raise ValueError("????????????")
            event_id = claimed.get("incoming_event_id")
            if event_id is None:
                event_id = await self.repository.proactive_event_id(
                    self.profile_id,
                    str(claimed["post_id"]),
                )
            route = RoutingTarget(
                profile_id=self.profile_id,
                post_id=str(claimed["post_id"]),
                incoming_event_id=int(event_id) if event_id is not None else None,
            )
            try:
                result = await self.reviewed_delivery(route, text)
            except asyncio.CancelledError:
                await self.repository.finish_feed_candidate_send(
                    candidate_id,
                    "send_unknown",
                    text,
                )
                raise
            except SendUncertainError:
                await self.repository.finish_feed_candidate_send(
                    candidate_id,
                    "send_unknown",
                    text,
                )
                raise
            except Exception:
                await self.repository.finish_feed_candidate_send(
                    candidate_id,
                    "failed",
                    text,
                )
                raise
            comment_id = str(result["external_comment_id"])
            await self.repository.finish_feed_candidate_send(
                candidate_id,
                "sent",
                text,
                sent_comment_id=comment_id,
            )
            return comment_id

    async def reject(self, candidate_id: int) -> bool:
        return await self.repository.reject_feed_candidate(candidate_id)

    async def reject_expired(self, cutoff_timestamp: float) -> int:
        candidates = await self.repository.list_feed_candidates(status="pending", limit=500)
        count = 0
        for candidate in candidates:
            if float(candidate["created_at"]) < cutoff_timestamp:
                if await self.repository.review_feed_candidate(int(candidate["id"]), "expired"):
                    count += 1
        return count
