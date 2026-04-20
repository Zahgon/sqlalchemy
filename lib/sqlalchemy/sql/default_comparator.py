# sql/default_comparator.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php

"""Default implementation of SQL comparison operations."""

from __future__ import annotations

import typing
from typing import Any
from typing import Callable
from typing import NoReturn
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union

from . import coercions
from . import functions
from . import operators
from . import roles
from . import type_api
from .elements import and_
from .elements import BinaryExpression
from .elements import ClauseElement
from .elements import CollationClause
from .elements import CollectionAggregate
from .elements import ExpressionClauseList
from .elements import False_
from .elements import Null
from .elements import OperatorExpression
from .elements import or_
from .elements import True_
from .elements import UnaryExpression
from .operators import OperatorType
from .. import exc
from .. import util

_T = typing.TypeVar("_T", bound=Any)

if typing.TYPE_CHECKING:
    from .elements import ColumnElement
    from .operators import custom_op
    from .type_api import TypeEngine


def _boolean_compare(
    expr: ColumnElement[Any],
    op: OperatorType,
    obj: Any,
    *,
    negate_op: Optional[OperatorType] = None,
    reverse: bool = False,
    _python_is_types: Tuple[Type[Any], ...] = (type(None), bool),
    result_type: Optional[TypeEngine[bool]] = None,
    **kwargs: Any,
) -> OperatorExpression[bool]:
    pass


def _custom_op_operate(
    expr: ColumnElement[Any],
    op: custom_op[Any],
    obj: Any,
    reverse: bool = False,
    result_type: Optional[TypeEngine[Any]] = None,
    **kw: Any,
) -> ColumnElement[Any]:
    pass


def _binary_operate(
    expr: ColumnElement[Any],
    op: OperatorType,
    obj: roles.BinaryElementRole[Any],
    *,
    reverse: bool = False,
    result_type: Optional[TypeEngine[_T]] = None,
    **kw: Any,
) -> OperatorExpression[_T]:
    pass


def _conjunction_operate(
    expr: ColumnElement[Any], op: OperatorType, other: Any, **kw: Any
) -> ColumnElement[Any]:
    pass


def _scalar(
    expr: ColumnElement[Any],
    op: OperatorType,
    fn: Callable[[ColumnElement[Any]], ColumnElement[Any]],
    **kw: Any,
) -> ColumnElement[Any]:
    pass


def _in_impl(
    expr: ColumnElement[Any],
    op: OperatorType,
    seq_or_selectable: ClauseElement,
    negate_op: OperatorType,
    **kw: Any,
) -> ColumnElement[Any]:
    pass


def _getitem_impl(
    expr: ColumnElement[Any], op: OperatorType, other: Any, **kw: Any
) -> ColumnElement[Any]:
    pass


def _unsupported_impl(
    expr: ColumnElement[Any], op: OperatorType, *arg: Any, **kw: Any
) -> NoReturn:
    raise NotImplementedError(
        "Operator '%s' is not supported on this expression" % op.__name__
    )


def _inv_impl(
    expr: ColumnElement[Any], op: OperatorType, **kw: Any
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.__inv__`."""
    pass


def _neg_impl(
    expr: ColumnElement[Any], op: OperatorType, **kw: Any
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.__neg__`."""
    pass


def _bitwise_not_impl(
    expr: ColumnElement[Any], op: OperatorType, **kw: Any
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.bitwise_not`."""
    pass


def _match_impl(
    expr: ColumnElement[Any], op: OperatorType, other: Any, **kw: Any
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.match`."""
    pass


def _distinct_impl(
    expr: ColumnElement[Any], op: OperatorType, **kw: Any
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.distinct`."""
    pass


def _between_impl(
    expr: ColumnElement[Any],
    op: OperatorType,
    cleft: Any,
    cright: Any,
    **kw: Any,
) -> ColumnElement[Any]:
    """See :meth:`.ColumnOperators.between`."""
    pass


def _pow_impl(
    expr: ColumnElement[Any],
    op: OperatorType,
    other: Any,
    reverse: bool = False,
    **kw: Any,
) -> ColumnElement[Any]:
    pass


def _collate_impl(
    expr: ColumnElement[str], op: OperatorType, collation: str, **kw: Any
) -> ColumnElement[str]:
    pass


def _regexp_match_impl(
    expr: ColumnElement[str],
    op: OperatorType,
    pattern: Any,
    flags: Optional[str],
    **kw: Any,
) -> ColumnElement[Any]:
    pass


def _regexp_replace_impl(
    expr: ColumnElement[Any],
    op: OperatorType,
    pattern: Any,
    replacement: Any,
    flags: Optional[str],
    **kw: Any,
) -> ColumnElement[Any]:
    pass


operator_lookup: util.immutabledict[
    str,
    Tuple[
        Callable[..., "ColumnElement[Any]"],
        util.immutabledict[
            str, Union["OperatorType", Callable[..., "ColumnElement[Any]"]]
        ],
    ],
] = util.immutabledict(
    {
        "any_op": (
            _scalar,
            util.immutabledict({"fn": CollectionAggregate._create_any}),
        ),
        "all_op": (
            _scalar,
            util.immutabledict({"fn": CollectionAggregate._create_all}),
        ),
        "lt": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.ge}),
        ),
        "le": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.gt}),
        ),
        "ne": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.eq}),
        ),
        "gt": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.le}),
        ),
        "ge": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.lt}),
        ),
        "eq": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.ne}),
        ),
        "is_distinct_from": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.is_not_distinct_from}),
        ),
        "is_not_distinct_from": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.is_distinct_from}),
        ),
        "in_op": (
            _in_impl,
            util.immutabledict({"negate_op": operators.not_in_op}),
        ),
        "not_in_op": (
            _in_impl,
            util.immutabledict({"negate_op": operators.in_op}),
        ),
        "is_": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.is_}),
        ),
        "is_not": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.is_not}),
        ),
        "between_op": (
            _between_impl,
            util.EMPTY_DICT,
        ),
        "not_between_op": (
            _between_impl,
            util.EMPTY_DICT,
        ),
        "desc_op": (
            _scalar,
            util.immutabledict({"fn": UnaryExpression._create_desc}),
        ),
        "asc_op": (
            _scalar,
            util.immutabledict({"fn": UnaryExpression._create_asc}),
        ),
        "nulls_first_op": (
            _scalar,
            util.immutabledict({"fn": UnaryExpression._create_nulls_first}),
        ),
        "nulls_last_op": (
            _scalar,
            util.immutabledict({"fn": UnaryExpression._create_nulls_last}),
        ),
        "distinct_op": (
            _distinct_impl,
            util.EMPTY_DICT,
        ),
        "null_op": (_binary_operate, util.EMPTY_DICT),
        "custom_op": (_custom_op_operate, util.EMPTY_DICT),
        "and_": (
            _conjunction_operate,
            util.EMPTY_DICT,
        ),
        "or_": (
            _conjunction_operate,
            util.EMPTY_DICT,
        ),
        "inv": (
            _inv_impl,
            util.EMPTY_DICT,
        ),
        "add": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "concat_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "getitem": (_getitem_impl, util.EMPTY_DICT),
        "contains_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_contains_op}),
        ),
        "icontains_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_icontains_op}),
        ),
        "contains": (
            _unsupported_impl,
            util.EMPTY_DICT,
        ),
        "like_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_like_op}),
        ),
        "ilike_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_ilike_op}),
        ),
        "not_like_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.like_op}),
        ),
        "not_ilike_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.ilike_op}),
        ),
        "startswith_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_startswith_op}),
        ),
        "istartswith_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_istartswith_op}),
        ),
        "endswith_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_endswith_op}),
        ),
        "iendswith_op": (
            _boolean_compare,
            util.immutabledict({"negate_op": operators.not_iendswith_op}),
        ),
        "collate": (
            _collate_impl,
            util.EMPTY_DICT,
        ),
        "match_op": (_match_impl, util.EMPTY_DICT),
        "not_match_op": (
            _match_impl,
            util.EMPTY_DICT,
        ),
        "regexp_match_op": (
            _regexp_match_impl,
            util.EMPTY_DICT,
        ),
        "not_regexp_match_op": (
            _regexp_match_impl,
            util.EMPTY_DICT,
        ),
        "regexp_replace_op": (
            _regexp_replace_impl,
            util.EMPTY_DICT,
        ),
        "lshift": (_unsupported_impl, util.EMPTY_DICT),
        "rshift": (_unsupported_impl, util.EMPTY_DICT),
        "bitwise_xor_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "bitwise_or_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "bitwise_and_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "bitwise_not_op": (
            _bitwise_not_impl,
            util.EMPTY_DICT,
        ),
        "bitwise_lshift_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "bitwise_rshift_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "matmul": (_unsupported_impl, util.EMPTY_DICT),
        "pow": (_pow_impl, util.EMPTY_DICT),
        "neg": (_neg_impl, util.EMPTY_DICT),
        "mul": (_binary_operate, util.EMPTY_DICT),
        "sub": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "div": (_binary_operate, util.EMPTY_DICT),
        "mod": (_binary_operate, util.EMPTY_DICT),
        "truediv": (_binary_operate, util.EMPTY_DICT),
        "floordiv": (_binary_operate, util.EMPTY_DICT),
        "json_path_getitem_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
        "json_getitem_op": (
            _binary_operate,
            util.EMPTY_DICT,
        ),
    }
)
