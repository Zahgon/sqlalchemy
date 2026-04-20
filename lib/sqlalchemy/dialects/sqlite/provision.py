# dialects/sqlite/provision.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

import os
import re

from ... import event
from ... import exc
from ...engine import url as sa_url
from ...testing import config
from ...testing.provision import create_db
from ...testing.provision import drop_db
from ...testing.provision import follower_url_from_main
from ...testing.provision import generate_driver_url
from ...testing.provision import log
from ...testing.provision import post_configure_engine
from ...testing.provision import post_configure_testing_engine
from ...testing.provision import run_reap_dbs
from ...testing.provision import stop_test_class_outside_fixtures
from ...testing.provision import temp_table_keyword_args
from ...testing.provision import upsert

# TODO: I can't get this to build dynamically with pytest-xdist procs
_drivernames = {
    "pysqlite",
    "aiosqlite",
    "pysqlcipher",
    "pysqlite_numeric",
    "pysqlite_dollar",
}


def _format_url(url, driver, ident):
    """given a sqlite url + desired driver + ident, make a canonical
    URL out of it

    """
    url = sa_url.make_url(url)

    if driver is None:
        driver = url.get_driver_name()

    filename = url.database

    needs_enc = driver == "pysqlcipher"
    name_token = None

    if filename and filename != ":memory:":
        assert "test_schema" not in filename
        tokens = re.split(r"[_\.]", filename)

        for token in tokens:
            if token in _drivernames:
                if driver is None:
                    driver = token
                continue
            elif token in ("db", "enc"):
                continue
            elif name_token is None:
                name_token = token.strip("_")

        assert name_token, f"sqlite filename has no name token: {url.database}"

        new_filename = f"{name_token}_{driver}"
        if ident:
            new_filename += f"_{ident}"
        new_filename += ".db"
        if needs_enc:
            new_filename += ".enc"
        url = url.set(database=new_filename)

    if needs_enc:
        url = url.set(password="test")

    url = url.set(drivername="sqlite+%s" % (driver,))

    return url


@generate_driver_url.for_db("sqlite")
def generate_driver_url(url, driver, query_str):
    url = _format_url(url, driver, None)

    try:
        url.get_dialect()
    except exc.NoSuchModuleError:
        return None
    else:
        return url


@follower_url_from_main.for_db("sqlite")
def _sqlite_follower_url_from_main(url, ident):
    pass


@post_configure_engine.for_db("sqlite")
def _sqlite_post_configure_engine(url, engine, follower_ident):
    pass


@post_configure_testing_engine.for_db("sqlite")
def _sqlite_post_configure_testing_engine(url, engine, options, scope):

    pass


@create_db.for_db("sqlite")
def _sqlite_create_db(cfg, eng, ident):
    pass


@drop_db.for_db("sqlite")
def _sqlite_drop_db(cfg, eng, ident):
    pass


def _drop_dbs_w_ident(databasename, driver, ident):
    pass


@stop_test_class_outside_fixtures.for_db("sqlite")
def stop_test_class_outside_fixtures(config, db, cls):
    db.dispose()


@temp_table_keyword_args.for_db("sqlite")
def _sqlite_temp_table_keyword_args(cfg, eng):
    pass


@run_reap_dbs.for_db("sqlite")
def _reap_sqlite_dbs(url, idents):
    pass


@upsert.for_db("sqlite")
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
