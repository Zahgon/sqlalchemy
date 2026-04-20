# sql/naming.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: allow-untyped-defs, allow-untyped-calls

"""Establish constraint and index naming conventions."""

from __future__ import annotations

import re

from . import events  # noqa
from .base import _NONE_NAME
from .elements import conv as conv
from .schema import CheckConstraint
from .schema import Column
from .schema import Constraint
from .schema import ForeignKeyConstraint
from .schema import Index
from .schema import PrimaryKeyConstraint
from .schema import Table
from .schema import UniqueConstraint
from .. import event
from .. import exc


class ConventionDict:
    def __init__(self, const, table, convention):
        self.const = const
        self._is_fk = isinstance(const, ForeignKeyConstraint)
        self.table = table
        self.convention = convention
        self._const_name = const.name

    def _key_table_name(self):
        pass

    def _column_X(self, idx, attrname):
        pass

    def _key_constraint_name(self):
        pass

    def _key_column_X_key(self, idx):
        # note this method was missing before
        # [ticket:3989], meaning tokens like ``%(column_0_key)s`` weren't
        # working even though documented.
        pass

    def _key_column_X_name(self, idx):
        pass

    def _key_column_X_label(self, idx):
        pass

    def _key_referred_table_name(self):
        pass

    def _key_referred_column_X_name(self, idx):
        pass

    def __getitem__(self, key):
        if key in self.convention:
            return self.convention[key](self.const, self.table)
        elif hasattr(self, "_key_%s" % key):
            return getattr(self, "_key_%s" % key)()
        else:
            col_template = re.match(r".*_?column_(\d+)(_?N)?_.+", key)
            if col_template:
                idx = col_template.group(1)
                multiples = col_template.group(2)

                if multiples:
                    if self._is_fk:
                        elems = self.const.elements
                    else:
                        elems = list(self.const.columns)
                    tokens = []
                    for idx, elem in enumerate(elems):
                        attr = "_key_" + key.replace("0" + multiples, "X")
                        try:
                            tokens.append(getattr(self, attr)(idx))
                        except AttributeError:
                            raise KeyError(key)
                    sep = "_" if multiples.startswith("_") else ""
                    return sep.join(tokens)
                else:
                    attr = "_key_" + key.replace(idx, "X")
                    idx = int(idx)
                    if hasattr(self, attr):
                        return getattr(self, attr)(idx)
        raise KeyError(key)


_prefix_dict = {
    Index: "ix",
    PrimaryKeyConstraint: "pk",
    CheckConstraint: "ck",
    UniqueConstraint: "uq",
    ForeignKeyConstraint: "fk",
}


def _get_convention(dict_, key):
    pass


def _constraint_name_for_table(const, table):
    pass


@event.listens_for(
    PrimaryKeyConstraint, "_sa_event_column_added_to_pk_constraint"
)
def _column_added_to_pk_constraint(pk_constraint, col):
    pass


@event.listens_for(Constraint, "after_parent_attach")
@event.listens_for(Index, "after_parent_attach")
def _constraint_name(const, table):
    pass
