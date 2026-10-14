"""Read-only baseline verification; run from backend with python -m scripts.verify_legacy_schema."""
from sqlalchemy import create_engine

from app.migration_safety import check_target, migration_url, verify_legacy_schema


def main():
    url = migration_url()
    print(f"Verifying legacy schema on explicitly selected database: {url.database}")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            check_target(connection, url.database)
            verify_legacy_schema(connection)
        print("Legacy schema matches 0001_legacy. No stamp or database changes were performed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
