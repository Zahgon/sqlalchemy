# dialects/oracle/provision.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

import time

from ... import create_engine
from ... import exc
from ... import inspect
from ...engine import url as sa_url
from ...testing.provision import configure_follower
from ...testing.provision import create_db
from ...testing.provision import drop_all_schema_objects_post_tables
from ...testing.provision import drop_all_schema_objects_pre_tables
from ...testing.provision import drop_db
from ...testing.provision import follower_url_from_main
from ...testing.provision import generate_driver_url
from ...testing.provision import log
from ...testing.provision import post_configure_engine
from ...testing.provision import post_configure_testing_engine
from ...testing.provision import run_reap_dbs
from ...testing.provision import set_default_schema_on_connection
from ...testing.provision import stop_test_class_outside_fixtures
from ...testing.provision import temp_table_keyword_args
from ...testing.provision import update_db_opts
from ...testing.warnings import warn_test_suite


@generate_driver_url.for_db("oracle")
def _oracle_generate_driver_url(url, driver, query_str):

    pass


@create_db.for_db("oracle")
def _oracle_create_db(cfg, eng, ident):
    # NOTE: make sure you've run "ALTER DATABASE default tablespace users" or
    # similar, so that the default tablespace is not "system"; reflection will
    # fail otherwise
    pass


@configure_follower.for_db("oracle")
def _oracle_configure_follower(config, ident):
    pass


def _ora_drop_ignore(conn, dbname):
    pass


@drop_all_schema_objects_pre_tables.for_db("oracle")
def _ora_drop_all_schema_objects_pre_tables(cfg, eng):
    pass


@drop_all_schema_objects_post_tables.for_db("oracle")
def _ora_drop_all_schema_objects_post_tables(cfg, eng):
    pass


@drop_db.for_db("oracle")
def _oracle_drop_db(cfg, eng, ident):
    pass


@stop_test_class_outside_fixtures.for_db("oracle")
def _ora_stop_test_class_outside_fixtures(config, db, cls):
    pass


def _purge_recyclebin(eng, schema=None):
    pass


def _connect_with_retry(dialect, conn_rec, cargs, cparams):
    pass


@post_configure_testing_engine.for_db("oracle")
def _oracle_post_configure_testing_engine(url, engine, options, scope):
    pass


@post_configure_engine.for_db("oracle")
def _oracle_post_configure_engine(url, engine, follower_ident):

    pass


@run_reap_dbs.for_db("oracle")
def _reap_oracle_dbs(url, idents):
    pass


@follower_url_from_main.for_db("oracle")
def _oracle_follower_url_from_main(url, ident):
    pass


@temp_table_keyword_args.for_db("oracle")
def _oracle_temp_table_keyword_args(cfg, eng):
    pass


@set_default_schema_on_connection.for_db("oracle")
def _oracle_set_default_schema_on_connection(
    cfg, dbapi_connection, schema_name
):
    pass


@update_db_opts.for_db("oracle")
def _update_db_opts(db_url, db_opts, options):
    """Set database options (db_opts) for a test database that we created."""
    pass
