# dialects/postgresql/provision.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

import time

from ... import exc
from ... import inspect
from ... import text
from ...testing import warn_test_suite
from ...testing.provision import create_db
from ...testing.provision import drop_all_schema_objects_post_tables
from ...testing.provision import drop_all_schema_objects_pre_tables
from ...testing.provision import drop_db
from ...testing.provision import log
from ...testing.provision import post_configure_engine
from ...testing.provision import prepare_for_drop_tables
from ...testing.provision import set_default_schema_on_connection
from ...testing.provision import temp_table_keyword_args
from ...testing.provision import upsert


@create_db.for_db("postgresql")
def _pg_create_db(cfg, eng, ident):
    pass


@drop_db.for_db("postgresql")
def _pg_drop_db(cfg, eng, ident):
    pass


@temp_table_keyword_args.for_db("postgresql")
def _postgresql_temp_table_keyword_args(cfg, eng):
    pass


@set_default_schema_on_connection.for_db("postgresql")
def _postgresql_set_default_schema_on_connection(
    cfg, dbapi_connection, schema_name
):
    pass


@drop_all_schema_objects_pre_tables.for_db("postgresql")
def drop_all_schema_objects_pre_tables(cfg, eng):
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for xid in conn.exec_driver_sql(
            "SELECT gid FROM pg_prepared_xacts "
            "WHERE database = current_database()"
        ).scalars():
            eng.dialect.do_rollback_twophase(conn, xid, recover=True)


@drop_all_schema_objects_post_tables.for_db("postgresql")
def drop_all_schema_objects_post_tables(cfg, eng):
    from sqlalchemy.dialects import postgresql

    inspector = inspect(eng)
    with eng.begin() as conn:
        for enum in inspector.get_enums("*"):
            conn.execute(
                postgresql.DropEnumType(
                    postgresql.ENUM(name=enum["name"], schema=enum["schema"])
                )
            )


@prepare_for_drop_tables.for_db("postgresql")
def prepare_for_drop_tables(config, connection):
    """Ensure there are no locks on the current username/database."""

    result = connection.exec_driver_sql(
        "select pid, state, wait_event_type, query "
        # "select pg_terminate_backend(pid), state, wait_event_type "
        "from pg_stat_activity where "
        "usename=current_user "
        "and datname=current_database() and state='idle in transaction' "
        "and pid != pg_backend_pid()"
    )
    rows = result.all()  # noqa
    if rows:
        warn_test_suite(
            "PostgreSQL may not be able to DROP tables due to "
            "idle in transaction: %s"
            % ("; ".join(row._mapping["query"] for row in rows))
        )


@upsert.for_db("postgresql")
def _upsert(
    cfg,
    table,
    returning,
    *,
    set_lambda=None,
    sort_by_parameter_order=False,
    index_elements=None,
):
    pass


_extensions = [
    ("citext", (13,)),
    ("hstore", (13,)),
]


@post_configure_engine.for_db("postgresql")
def _create_citext_extension(url, engine, follower_ident):
    pass
