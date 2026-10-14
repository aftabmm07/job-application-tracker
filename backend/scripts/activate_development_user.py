"""Private operator tool for disposable development accounts; never a public API.

Do not execute without approval: this writes the selected temporary database.
No production activation or email verification delivery is implemented here.
"""
import argparse
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import get_auth_settings
from app.migration_safety import check_target, migration_url
from app.models import User
from app.session_service import SessionStorageError, commit_session_changes

ALLOWED_DEVELOPMENT_DATABASES = {
    "jobtracker_auth_fresh_9b2", "jobtracker_auth_legacy_9b2", "jobtracker_auth_ci",
}


def activate_user(db, user_id, settings, target_name, *, confirmed=False):
    if (settings.environment != "development" or target_name not in ALLOWED_DEVELOPMENT_DATABASES
            or not confirmed):
        raise RuntimeError("Activation requires explicit confirmation and an allowlisted development database")
    try:
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise RuntimeError("Development account not found")
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        user.is_active = True
        commit_session_changes(db)
    except SQLAlchemyError:
        db.rollback()
        raise SessionStorageError("Development activation unavailable") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--confirm-development-activation", action="store_true")
    args = parser.parse_args()
    settings = get_auth_settings()
    url = migration_url()
    # Guard BEFORE connecting, then verify actual database identity as well.
    if (settings.environment != "development" or url.database not in ALLOWED_DEVELOPMENT_DATABASES
            or not args.confirm_development_activation):
        parser.error("Only explicitly confirmed, allowlisted development activation is supported")
    print(f"Explicit development activation target: {url.database}")
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            check_target(db.connection(), url.database)
            activate_user(db, args.user_id, settings, url.database,
                          confirmed=args.confirm_development_activation)
        print("Development account activated. No email verification was performed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
