"""Central Xiaoheihe HTTP contract.

The paths are isolated here because Xiaoheihe does not publish a stable public
automation API. See docs/xiaoheihe-api-contract.md for verification status.
"""

from dataclasses import dataclass
from enum import StrEnum

API_BASE_URL = "https://api.xiaoheihe.cn"


class EndpointName(StrEnum):
    REQUEST_QR = "request_qr"
    QR_STATE = "qr_state"
    CURRENT_USER = "current_user"
    USER_MESSAGES = "user_messages"
    POST_TREE = "post_tree"
    CREATE_COMMENT = "create_comment"
    RECENT_COMMENTS = "recent_comments"
    FEED = "feed"


@dataclass(frozen=True, slots=True)
class Endpoint:
    method: str
    path: str
    authenticated: bool
    retry_safe: bool


ENDPOINTS: dict[EndpointName, Endpoint] = {
    EndpointName.REQUEST_QR: Endpoint("GET", "/account/get_qrcode_url/", False, True),
    EndpointName.QR_STATE: Endpoint("GET", "/account/qr_state/", False, True),
    EndpointName.CURRENT_USER: Endpoint("GET", "/account/info/", True, True),
    EndpointName.USER_MESSAGES: Endpoint("GET", "/bbs/app/user/message", True, True),
    EndpointName.POST_TREE: Endpoint("GET", "/bbs/app/link/tree", True, True),
    EndpointName.CREATE_COMMENT: Endpoint("POST", "/bbs/app/comment/create", True, False),
    EndpointName.RECENT_COMMENTS: Endpoint("GET", "/bbs/app/comment/user", True, True),
    EndpointName.FEED: Endpoint("GET", "/bbs/app/feeds", True, True),
}


def endpoint(name: EndpointName) -> Endpoint:
    return ENDPOINTS[name]
