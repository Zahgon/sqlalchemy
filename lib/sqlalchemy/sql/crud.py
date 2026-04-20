# sql/crud.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: allow-untyped-defs, allow-untyped-calls

"""Functions used by compiler.py to determine the parameters rendered
within INSERT and UPDATE statements.

"""
from __future__ import annotations

import functools
import operator
import re
from typing import Any
from typing import Callable
from typing import cast
from typing import Dict
from typing import Iterable
from typing import List
from typing import Literal
from typing import MutableMapping
from typing import NamedTuple
from typing import Optional
from typing import overload
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import TYPE_CHECKING
from typing import Union

from . import coercions
from . import dml
from . import elements
from . import roles
from .base import _DefaultDescriptionTuple
from .dml import isinsert as _compile_state_isinsert
from .elements import ColumnClause
from .schema import default_is_clause_element
from .schema import default_is_sequence
from .selectable import Select
from .selectable import TableClause
from .. import exc
from .. import util

if TYPE_CHECKING:
    from .compiler import _BindNameForColProtocol
    from .compiler import SQLCompiler
    from .dml import _DMLColumnElement
    from .dml import DMLState
    from .dml import ValuesBase
    from .elements import ColumnElement
    from .elements import DMLTargetCopy
    from .elements import KeyedColumnElement
    from .schema import _SQLExprDefault
    from .schema import Column

REQUIRED = util.symbol(
    "REQUIRED",
    """
Placeholder for the value within a :class:`.BindParameter`
which is required to be present when the statement is passed
to :meth:`_engine.Connection.execute`.

This symbol is typically used when a :func:`_expression.insert`
or :func:`_expression.update` statement is compiled without parameter
values present.

""",
)


def _as_dml_column(c: ColumnElement[Any]) -> ColumnClause[Any]:
    pass


_CrudParamElement = Tuple[
    "ColumnElement[Any]",
    str,  # column name
    Optional[
        Union[str, "_SQLExprDefault"]
    ],  # bound parameter string or SQL expression to apply
    Iterable[str],
]
_CrudParamElementStr = Tuple[
    "KeyedColumnElement[Any]",
    str,  # column name
    str,  # bound parameter string
    Iterable[str],
]
_CrudParamElementSQLExpr = Tuple[
    "ColumnClause[Any]",
    str,
    "_SQLExprDefault",  # SQL expression to apply
    Iterable[str],
]

_CrudParamSequence = List[_CrudParamElement]


class _CrudParams(NamedTuple):
    single_params: List[_CrudParamElementStr]
    all_multi_params: List[Sequence[_CrudParamElementStr]]
    is_default_metavalue_only: bool = False
    use_insertmanyvalues: bool = False
    use_sentinel_columns: Optional[Sequence[Column[Any]]] = None


def _get_crud_params(
    compiler: SQLCompiler,
    stmt: ValuesBase,
    compile_state: DMLState,
    toplevel: bool,
    **kw: Any,
) -> _CrudParams:
    """create a set of tuples representing column/string pairs for use
    in an INSERT or UPDATE statement.

    Also generates the Compiled object's postfetch, prefetch, and
    returning column collections, used for default handling and ultimately
    populating the CursorResult's prefetch_cols() and postfetch_cols()
    collections.

    """
    pass


def _replace_bindmarkers(
    compiler, _column_as_key, bindmarkers, compile_state, values, kw
):
    pass


@overload
def _create_bind_param(
    compiler: SQLCompiler,
    col: ColumnElement[Any],
    value: Any,
    process: Literal[True] = ...,
    required: bool = False,
    name: Optional[str] = None,
    force_anonymous: bool = False,
    **kw: Any,
) -> str: ...


@overload
def _create_bind_param(
    compiler: SQLCompiler,
    col: ColumnElement[Any],
    value: Any,
    **kw: Any,
) -> str: ...


def _create_bind_param(
    compiler: SQLCompiler,
    col: ColumnElement[Any],
    value: Any,
    process: bool = True,
    required: bool = False,
    name: Optional[str] = None,
    force_anonymous: bool = False,
    **kw: Any,
) -> Union[str, elements.BindParameter[Any]]:
    pass


def _handle_values_anonymous_param(compiler, col, value, name, **kw):
    # the insert() and update() constructs as of 1.4 will now produce anonymous
    # bindparam() objects in the values() collections up front when given plain
    # literal values.  This is so that cache key behaviors, which need to
    # produce bound parameters in deterministic order without invoking any
    # compilation here, can be applied to these constructs when they include
    # values() (but not yet multi-values, which are not included in caching
    # right now).
    #
    # in order to produce the desired "crud" style name for these parameters,
    # which will also be targetable in engine/default.py through the usual
    # conventions, apply our desired name to these unique parameters by
    # populating the compiler truncated names cache with the desired name,
    # rather than having
    # compiler.visit_bindparam()->compiler._truncated_identifier make up a
    # name.  Saves on call counts also.

    # for INSERT/UPDATE that's a CTE, we don't need names to match to
    # external parameters and these would also conflict in the case where
    # multiple insert/update are combined together using CTEs
    pass


def _key_getters_for_crud_column(
    compiler: SQLCompiler, stmt: ValuesBase, compile_state: DMLState
) -> Tuple[
    Callable[[Union[str, ColumnClause[Any]]], Union[str, Tuple[str, str]]],
    Callable[[ColumnClause[Any]], Union[str, Tuple[str, str]]],
    _BindNameForColProtocol,
]:
    pass


def _scan_insert_from_select_cols(
    compiler,
    stmt,
    compile_state,
    parameters,
    _getattr_col_key,
    _column_as_key,
    _col_bind_name,
    check_columns,
    values,
    toplevel,
    kw,
):
    pass


def _scan_cols(
    compiler,
    stmt,
    compile_state,
    parameters,
    _getattr_col_key,
    _column_as_key,
    _col_bind_name,
    check_columns,
    values,
    toplevel,
    kw,
):
    pass


def _setup_delete_return_defaults(
    compiler,
    stmt,
    compile_state,
    parameters,
    _getattr_col_key,
    _column_as_key,
    _col_bind_name,
    check_columns,
    values,
    toplevel,
    kw,
):
    pass


def _append_param_parameter(
    compiler,
    stmt,
    compile_state,
    c,
    col_key,
    parameters,
    _col_bind_name,
    implicit_returning,
    implicit_return_defaults,
    postfetch_lastrowid,
    values,
    autoincrement_col,
    insert_null_pk_still_autoincrements,
    kw,
):
    pass


def _append_param_insert_pk_returning(compiler, stmt, c, values, kw):
    """Create a primary key expression in the INSERT statement where
    we want to populate result.inserted_primary_key and RETURNING
    is available.

    """
    pass


def _append_param_insert_pk_no_returning(compiler, stmt, c, values, kw):
    """Create a primary key expression in the INSERT statement where
    we want to populate result.inserted_primary_key and we cannot use
    RETURNING.

    Depending on the kind of default here we may create a bound parameter
    in the INSERT statement and pre-execute a default generation function,
    or we may use cursor.lastrowid if supported by the dialect.


    """
    pass


def _append_param_insert_hasdefault(
    compiler, stmt, c, implicit_return_defaults, values, kw
):
    pass


def _append_param_insert_select_hasdefault(
    compiler: SQLCompiler,
    stmt: ValuesBase,
    c: ColumnClause[Any],
    values: List[_CrudParamElementSQLExpr],
    kw: Dict[str, Any],
) -> None:
    pass


def _append_param_update(
    compiler, compile_state, stmt, c, implicit_return_defaults, values, kw
):
    pass


@overload
def _create_insert_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: Literal[True] = ...,
    **kw: Any,
) -> str: ...


@overload
def _create_insert_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: Literal[False],
    **kw: Any,
) -> elements.BindParameter[Any]: ...


def _create_insert_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: bool = True,
    name: Optional[str] = None,
    **kw: Any,
) -> Union[elements.BindParameter[Any], str]:
    pass


@overload
def _create_update_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: Literal[True] = ...,
    **kw: Any,
) -> str: ...


@overload
def _create_update_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: Literal[False],
    **kw: Any,
) -> elements.BindParameter[Any]: ...


def _create_update_prefetch_bind_param(
    compiler: SQLCompiler,
    c: ColumnElement[Any],
    process: bool = True,
    name: Optional[str] = None,
    **kw: Any,
) -> Union[elements.BindParameter[Any], str]:
    pass


class _multiparam_column(elements.ColumnElement[Any]):
    _is_multiparam_column = True

    def __init__(self, original, index):
        self.index = index
        self.key = "%s_m%d" % (original.key, index + 1)
        self.original = original
        self.default = original.default
        self.type = original.type

    def compare(self, other, **kw):
        raise NotImplementedError()

    def _copy_internals(self, **kw):
        raise NotImplementedError()

    def __eq__(self, other):
        return (
            isinstance(other, _multiparam_column)
            and other.key == self.key
            and other.original == self.original
        )

    @util.memoized_property
    def _default_description_tuple(self) -> _DefaultDescriptionTuple:
        """used by default.py -> _process_execute_defaults()"""
        pass

    @util.memoized_property
    def _onupdate_description_tuple(self) -> _DefaultDescriptionTuple:
        """used by default.py -> _process_execute_defaults()"""
        pass


def _process_multiparam_default_bind(
    compiler: SQLCompiler,
    stmt: ValuesBase,
    c: KeyedColumnElement[Any],
    index: int,
    kw: Dict[str, Any],
) -> str:
    pass


def _get_update_multitable_params(
    compiler,
    stmt,
    compile_state,
    stmt_parameter_tuples,
    check_columns,
    _col_bind_name,
    _getattr_col_key,
    values,
    kw,
):
    pass


def _extend_values_for_multiparams(
    compiler: SQLCompiler,
    stmt: ValuesBase,
    compile_state: DMLState,
    initial_values: Sequence[_CrudParamElementStr],
    _column_as_key: Callable[..., str],
    kw: Dict[str, Any],
) -> List[Sequence[_CrudParamElementStr]]:
    pass


def _get_stmt_parameter_tuples_params(
    compiler,
    compile_state,
    parameters,
    stmt_parameter_tuples,
    _column_as_key,
    values,
    kw,
):
    pass


def _get_returning_modifiers(compiler, stmt, compile_state, toplevel):
    """determines RETURNING strategy, if any, for the statement.

    This is where it's determined what we need to fetch from the
    INSERT or UPDATE statement after it's invoked.

    """
    pass


def _warn_pk_with_no_anticipated_value(c):
    pass
