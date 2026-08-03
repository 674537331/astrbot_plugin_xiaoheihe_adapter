"""Independent Python implementation of the Xiaoheihe AstrBot adapter."""

from .models import (
    Credentials,
    EventState,
    LoginState,
    Notification,
    NotificationType,
    RoutingTarget,
)

__all__ = [
    "Credentials",
    "EventState",
    "LoginState",
    "Notification",
    "NotificationType",
    "RoutingTarget",
]

__version__ = "1.2.8"
