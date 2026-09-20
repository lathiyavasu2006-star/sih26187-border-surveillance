import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.database import Base  # noqa: E402
import backend.models  # noqa: E402,F401  (registers all 10 tables on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# `alembic -x dburl=postgresql+psycopg2://... upgrade head` targets another database (used by tests).
_x_args = context.get_x_argument(as_dictionary=True)
if _x_args.get("dburl"):
    config.set_main_option("sqlalchemy.url", _x_args["dburl"])
elif not config.get_main_option("sqlalchemy.url", None):
    # alembic.ini ships without credentials; the real URL lives in .env.
    from backend.core.config import settings  # noqa: E402

    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)


def include_object(obj, name, type_, reflected, compare_to):
    # alembic_version is managed by Alembic itself.
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def _configure_kwargs() -> dict:
    return dict(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_configure_kwargs(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_configure_kwargs())
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
