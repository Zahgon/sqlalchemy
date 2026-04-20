# orm/evaluator.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors

"""Evaluation functions used **INTERNALLY** by ORM DML use cases.


This module is **private, for internal use by SQLAlchemy**.

.. versionchanged:: 2.0.4 renamed ``EvaluatorCompiler`` to
   ``_EvaluatorCompiler``.

"""


from __future__ import annotations

from typing import Type

from . import exc as orm_exc
from .base import LoaderCallableStatus
from .base import PassiveFlag
from .. import exc
from .. import inspect
from ..sql import and_
from ..sql import operators
from ..sql.sqltypes import Concatenable
from ..sql.sqltypes import Integer
from ..sql.sqltypes import Numeric
from ..util import warn_deprecated


class UnevaluatableError(exc.InvalidRequestError):
    pass


class _NoObject(operators.ColumnOperators):
    def operate(self, *arg, **kw):
        return None

    def reverse_operate(self, *arg, **kw):
        pass


class _ExpiredObject(operators.ColumnOperators):
    def operate(self, *arg, **kw):
        return self

    def reverse_operate(self, *arg, **kw):
        pass


_NO_OBJECT = _NoObject()
_EXPIRED_OBJECT = _ExpiredObject()


class _EvaluatorCompiler:
    def __init__(self, target_cls=None):
        self.target_cls = target_cls

    def process(self, clause, *clauses):
        if clauses:
            clause = and_(clause, *clauses)

        meth = getattr(self, f"visit_{clause.__visit_name__}", None)
        if not meth:
            raise UnevaluatableError(
                f"Cannot evaluate {type(clause).__name__}"
            )
        return meth(clause)

    def visit_grouping(self, clause):
        pass

    def visit_null(self, clause):
        pass

    def visit_false(self, clause):
        return lambda obj: False

    def visit_true(self, clause):
        return lambda obj: True

    def visit_column(self, clause):
        pass

    def visit_tuple(self, clause):
        pass

    def visit_expression_clauselist(self, clause):
        pass

    def visit_clauselist(self, clause):
        pass

    def visit_binary(self, clause):
        pass

    def visit_or_clauselist_op(self, operator, evaluators, clause):
        pass

    def visit_and_clauselist_op(self, operator, evaluators, clause):
        pass

    def visit_comma_op_clauselist_op(self, operator, evaluators, clause):
        pass

    def visit_custom_op_binary_op(
        self, operator, eval_left, eval_right, clause
    ):
        pass

    def visit_is_binary_op(self, operator, eval_left, eval_right, clause):
        pass

    def visit_is_not_binary_op(self, operator, eval_left, eval_right, clause):
        pass

    def _straight_evaluate(self, operator, eval_left, eval_right, clause):
        pass

    def _straight_evaluate_numeric_only(
        self, operator, eval_left, eval_right, clause
    ):
        pass

    visit_add_binary_op = _straight_evaluate_numeric_only
    visit_mul_binary_op = _straight_evaluate_numeric_only
    visit_sub_binary_op = _straight_evaluate_numeric_only
    visit_mod_binary_op = _straight_evaluate_numeric_only
    visit_truediv_binary_op = _straight_evaluate_numeric_only
    visit_lt_binary_op = _straight_evaluate
    visit_le_binary_op = _straight_evaluate
    visit_ne_binary_op = _straight_evaluate
    visit_gt_binary_op = _straight_evaluate
    visit_ge_binary_op = _straight_evaluate
    visit_eq_binary_op = _straight_evaluate

    def visit_in_op_binary_op(self, operator, eval_left, eval_right, clause):
        pass

    def visit_not_in_op_binary_op(
        self, operator, eval_left, eval_right, clause
    ):
        pass

    def visit_concat_op_binary_op(
        self, operator, eval_left, eval_right, clause
    ):

        pass

    def visit_startswith_op_binary_op(
        self, operator, eval_left, eval_right, clause
    ):
        pass

    def visit_endswith_op_binary_op(
        self, operator, eval_left, eval_right, clause
    ):
        pass

    def visit_unary(self, clause):
        pass

    def visit_bindparam(self, clause):
        pass


def __getattr__(name: str) -> Type[_EvaluatorCompiler]:
    if name == "EvaluatorCompiler":
        warn_deprecated(
            "Direct use of 'EvaluatorCompiler' is not supported, and this "
            "name will be removed in a future release.  "
            "'_EvaluatorCompiler' is for internal use only",
            "2.0",
        )
        return _EvaluatorCompiler
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
