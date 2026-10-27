"""No sockets, files, or database access: private preview memory lifecycle."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.auth_config import AuthSettings
from app.development_verification_preview import (
    DevelopmentPreviewStore, DevelopmentVerificationPreview, MAX_PREVIEWS,
)


def settings():
    return AuthSettings.from_environment({'DEV_EMAIL_VERIFICATION_PREVIEW': 'true',
                                          'VERIFICATION_FRONTEND_ORIGIN': 'http://localhost:5173'})


def preview(user_id=1, expired=False):
    return DevelopmentVerificationPreview(user_id,
        datetime.now(UTC) + timedelta(hours=-1 if expired else 1),
        'http://localhost:5173/verify-email#token=synthetic-private-token')


def test_store_refuses_disabled_and_directly_constructed_production_settings():
    enabled = settings()
    store = DevelopmentPreviewStore()
    for disabled in (replace(enabled, dev_email_verification_preview=False),
                     replace(enabled, environment='production')):
        store.publish(preview(), disabled)
        assert store.take(1, enabled) is None
    store.publish(preview(), enabled)
    assert store.take(1, replace(enabled, environment='production')) is None
    assert store.take(1, enabled) is not None


def test_expired_preview_is_not_published_or_retrieved():
    enabled = settings()
    store = DevelopmentPreviewStore()
    store.publish(preview(expired=True), enabled)
    assert store.take(1, enabled) is None
    store.publish(preview(), enabled)
    # Simulate time passing without a sleep or wall-clock dependency.
    store._entries[1] = preview(expired=True)
    assert store.take(1, enabled) is None


def test_store_is_bounded_does_not_overwrite_and_repr_hides_link():
    enabled = settings()
    store = DevelopmentPreviewStore()
    first = preview()
    store.publish(first, enabled)
    store.publish(preview(), enabled)
    assert store.take(1, enabled) is first
    assert 'synthetic-private-token' not in repr(first)
    for user_id in range(MAX_PREVIEWS + 1):
        store.publish(preview(user_id), enabled)
    assert store.take(0, enabled) is None
    assert len(store._entries) == MAX_PREVIEWS
    assert 'synthetic-private-token' not in repr(store)
