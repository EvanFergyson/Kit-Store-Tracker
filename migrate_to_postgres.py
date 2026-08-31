"""One-time migration: copy every row from the local SQLite database
(instance/kitstore.db) into a fresh Postgres database (e.g. a new Neon
project), preserving primary keys so foreign keys stay intact.

Usage:
    python migrate_to_postgres.py "postgresql://user:password@host/dbname"

Deliberately takes the target as a command-line argument rather than reading
DATABASE_URL from .env, so this never accidentally runs against whatever
database happens to be configured there - you always pass the target
explicitly, once, on the command line.

Safe to re-run: refuses to touch a target database that already has any rows
in it, rather than risk creating duplicates.
"""
import os
import sys

if len(sys.argv) != 2:
    print(f"Usage: python {sys.argv[0]} <postgres-database-url>")
    raise SystemExit(1)

# Must happen before `import app` - config.py reads DATABASE_URL at import
# time, and create_app() sets up the target's schema (tables + the
# case-insensitive unique indexes) the exact same way a real deploy would.
os.environ["DATABASE_URL"] = sys.argv[1]

import app as app_module  # noqa: E402 (must follow the DATABASE_URL override above)
from models import (  # noqa: E402
    Category,
    ItemGroup,
    ItemRequest,
    KitBundle,
    KitBundleLine,
    KitItem,
    SignOut,
    Subcategory,
    db,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

SOURCE_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "instance", "kitstore.db")

# Every model here is migrated before anything with a foreign key pointing at
# it - Subcategory needs Category to already exist, KitItem needs both
# Subcategory and ItemGroup, and so on. Get this order wrong and Postgres
# (unlike SQLite, which mostly doesn't enforce foreign keys by default)
# rejects the insert.
MODELS_IN_FK_ORDER = [Category, Subcategory, ItemGroup, KitItem, KitBundle, KitBundleLine, SignOut, ItemRequest]


def copy_row(source_obj, model):
    """A plain {column_name: value} dict of a source row's actual Python
    values (real datetime/bool/date objects, not raw SQLite text) - reading
    through the ORM rather than a raw SELECT means SQLAlchemy's own type
    system already did the SQLite -> Python conversion, so this dict is safe
    to hand straight to a new instance bound to the Postgres session."""
    return {column.name: getattr(source_obj, column.name) for column in model.__table__.columns}


def main():
    if not os.path.exists(SOURCE_SQLITE_PATH):
        print(f"No local database found at {SOURCE_SQLITE_PATH} - nothing to migrate.")
        raise SystemExit(1)

    flask_app = app_module.app  # already created against the target URL above
    source_engine = create_engine(f"sqlite:///{SOURCE_SQLITE_PATH}")
    source_session = sessionmaker(bind=source_engine)()

    with flask_app.app_context():
        if Category.query.first() is not None:
            print("Target database already has data - refusing to migrate (would create duplicates).")
            raise SystemExit(1)

        for model in MODELS_IN_FK_ORDER:
            source_rows = source_session.query(model).all()
            for row in source_rows:
                db.session.add(model(**copy_row(row, model)))
            db.session.commit()
            print(f"{model.__tablename__}: migrated {len(source_rows)} row(s)")

        # Postgres tracks each table's next auto-increment id via a sequence,
        # which has no idea about the explicit ids just inserted above -
        # without this, the next real INSERT through the running app would
        # collide with an id that's already in use.
        with db.engine.begin() as pg_conn:
            for model in MODELS_IN_FK_ORDER:
                pg_conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{model.__tablename__}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {model.__tablename__}), 1), "
                        f"(SELECT MAX(id) FROM {model.__tablename__}) IS NOT NULL)"
                    )
                )
        print("Done - Postgres id sequences synced to match the migrated data.")

    source_session.close()


if __name__ == "__main__":
    main()
