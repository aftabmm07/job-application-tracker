"""Private process memory only: no HTTP handlers, files, or logging."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

from app.auth_config import AuthSettings

MAX_PREVIEWS = 100


@dataclass(frozen=True)
class DevelopmentVerificationPreview:
    user_id: int
    expires_at: datetime
    link: str = field(repr=False)


def preview_enabled(settings: AuthSettings) -> bool:
    # Repeat the environment gate even if settings were constructed directly.
    return settings.environment == "development" and settings.dev_email_verification_preview


class DevelopmentPreviewStore:
    """Bounded, expiring, single-retrieval previews for a trusted local debugger.

    Expiration is lazy: expired references are dropped on the next operation.
    This is not secure memory erasure or an access boundary against process users.
    """

    def __init__(self):
        self._entries = OrderedDict()
        self._lock = Lock()

    def _expire(self, now):
        for user_id in list(self._entries):
            if self._entries[user_id].expires_at <= now:
                del self._entries[user_id]

    def publish(self, preview, settings):
        if not preview_enabled(settings):
            return
        with self._lock:
            now = datetime.now(UTC)
            self._expire(now)
            if preview.expires_at <= now or preview.user_id in self._entries:
                return
            self._entries[preview.user_id] = preview
            while len(self._entries) > MAX_PREVIEWS:
                self._entries.popitem(last=False)

    def take(self, user_id: int, settings: AuthSettings):
        """One-shot local Python access. Never expose this through a route."""
        if not preview_enabled(settings):
            return None
        with self._lock:
            self._expire(datetime.now(UTC))
            return self._entries.pop(user_id, None)


development_previews = DevelopmentPreviewStore()
