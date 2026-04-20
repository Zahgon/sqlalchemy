# sql/traversals.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: allow-untyped-defs, allow-untyped-calls

from __future__ import annotations

from collections import deque
import collections.abc as collections_abc
import itertools
from itertools import zip_longest
import operator
import typing
from typing import Any
from typing import Callable
from typing import Deque
from typing import Dict
from typing import Iterable
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type

from . import operators
from .cache_key import HasCacheKey
from .visitors import _TraverseInternalsType
from .visitors import anon_map
from .visitors import ExternallyTraversible
from .visitors import HasTraversalDispatch
from .visitors import HasTraverseInternals
from .. import util
from ..util import langhelpers
from ..util.typing import Self


SKIP_TRAVERSE = util.symbol("skip_traverse")
COMPARE_FAILED = False
COMPARE_SUCCEEDED = True


def compare(obj1: Any, obj2: Any, **kw: Any) -> bool:
    strategy: TraversalComparatorStrategy
    if kw.get("use_proxies", False):
        strategy = ColIdentityComparatorStrategy()
    else:
        strategy = TraversalComparatorStrategy()

    return strategy.compare(obj1, obj2, **kw)


def _preconfigure_traversals(target_hierarchy: Type[Any]) -> None:
    pass


class HasShallowCopy(HasTraverseInternals):
    """attribute-wide operations that are useful for classes that use
    __slots__ and therefore can't operate on their attributes in a dictionary.


    """

    __slots__ = ()

    if typing.TYPE_CHECKING:

        def _generated_shallow_copy_traversal(self, other: Self) -> None: ...

        def _generated_shallow_from_dict_traversal(
            self, d: Dict[str, Any]
        ) -> None: ...

        def _generated_shallow_to_dict_traversal(self) -> Dict[str, Any]: ...

    @classmethod
    def _generate_shallow_copy(
        cls,
        internal_dispatch: _TraverseInternalsType,
        method_name: str,
    ) -> Callable[[Self, Self], None]:
        code = "\n".join(
            f"    other.{attrname} = self.{attrname}"
            for attrname, _ in internal_dispatch
        )
        meth_text = f"def {method_name}(self, other):\n{code}\n"
        return langhelpers._exec_code_in_env(meth_text, {}, method_name)

    @classmethod
    def _generate_shallow_to_dict(
        cls,
        internal_dispatch: _TraverseInternalsType,
        method_name: str,
    ) -> Callable[[Self], Dict[str, Any]]:
        pass

    @classmethod
    def _generate_shallow_from_dict(
        cls,
        internal_dispatch: _TraverseInternalsType,
        method_name: str,
    ) -> Callable[[Self, Dict[str, Any]], None]:
        pass

    def _shallow_from_dict(self, d: Dict[str, Any]) -> None:
        pass

    def _shallow_to_dict(self) -> Dict[str, Any]:
        pass

    def _shallow_copy_to(self, other: Self) -> None:
        cls = self.__class__

        shallow_copy: Callable[[Self, Self], None]
        try:
            shallow_copy = cls.__dict__["_generated_shallow_copy_traversal"]
        except KeyError:
            shallow_copy = self._generate_shallow_copy(
                cls._traverse_internals, "_generated_shallow_copy_traversal"
            )

            cls._generated_shallow_copy_traversal = shallow_copy  # type: ignore  # noqa: E501
        shallow_copy(self, other)

    def _clone(self, **kw: Any) -> Self:
        """Create a shallow copy"""
        c = self.__class__.__new__(self.__class__)
        self._shallow_copy_to(c)
        return c


class GenerativeOnTraversal(HasShallowCopy):
    """Supplies Generative behavior but making use of traversals to shallow
    copy.

    .. seealso::

        :class:`sqlalchemy.sql.base.Generative`


    """

    __slots__ = ()

    def _generate(self) -> Self:
        cls = self.__class__
        s = cls.__new__(cls)
        self._shallow_copy_to(s)
        return s


def _clone(element, **kw):
    return element._clone()


class HasCopyInternals(HasTraverseInternals):
    __slots__ = ()

    def _clone(self, **kw):
        raise NotImplementedError()

    def _copy_internals(
        self, *, omit_attrs: Iterable[str] = (), **kw: Any
    ) -> None:
        """Reassign internal elements to be clones of themselves.

        Called during a copy-and-traverse operation on newly
        shallow-copied elements to create a deep copy.

        The given clone function should be used, which may be applying
        additional transformations to the element (i.e. replacement
        traversal, cloned traversal, annotations).

        """

        try:
            traverse_internals = self._traverse_internals
        except AttributeError:
            # user-defined classes may not have a _traverse_internals
            return

        for attrname, obj, meth in _copy_internals.run_generated_dispatch(
            self, traverse_internals, "_generated_copy_internals_traversal"
        ):
            if attrname in omit_attrs:
                continue

            if obj is not None:
                result = meth(attrname, self, obj, **kw)
                if result is not None:
                    setattr(self, attrname, result)


class _CopyInternalsTraversal(HasTraversalDispatch):
    """Generate a _copy_internals internal traversal dispatch for classes
    with a _traverse_internals collection."""

    def visit_clauseelement(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_clauseelement_list(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_clauseelement_tuple(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_executable_options(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_clauseelement_unordered_set(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_clauseelement_tuples(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_string_clauseelement_dict(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_setup_join_tuple(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass

    def visit_memoized_select_entities(self, attrname, parent, element, **kw):
        pass

    def visit_dml_ordered_values(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        # sequence of 2-tuples
        pass

    def visit_dml_values(self, attrname, parent, element, clone=_clone, **kw):
        pass

    def visit_dml_multi_values(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        # sequence of sequences, each sequence contains a list/dict/tuple

        pass

    def visit_propagate_attrs(
        self, attrname, parent, element, clone=_clone, **kw
    ):
        pass


_copy_internals = _CopyInternalsTraversal()


def _flatten_clauseelement(element):
    pass


class _GetChildrenTraversal(HasTraversalDispatch):
    """Generate a _children_traversal internal traversal dispatch for classes
    with a _traverse_internals collection."""

    def visit_has_cache_key(self, element, **kw):
        # the GetChildren traversal refers explicitly to ClauseElement
        # structures.  Within these, a plain HasCacheKey is not a
        # ClauseElement, so don't include these.
        pass

    def visit_clauseelement(self, element, **kw):
        pass

    def visit_clauseelement_list(self, element, **kw):
        pass

    def visit_clauseelement_tuple(self, element, **kw):
        pass

    def visit_clauseelement_tuples(self, element, **kw):
        pass

    def visit_fromclause_canonical_column_collection(self, element, **kw):
        pass

    def visit_string_clauseelement_dict(self, element, **kw):
        pass

    def visit_fromclause_ordered_set(self, element, **kw):
        pass

    def visit_clauseelement_unordered_set(self, element, **kw):
        pass

    def visit_setup_join_tuple(self, element, **kw):
        pass

    def visit_memoized_select_entities(self, element, **kw):
        pass

    def visit_dml_ordered_values(self, element, **kw):
        pass

    def visit_dml_values(self, element, **kw):
        pass

    def visit_dml_multi_values(self, element, **kw):
        pass

    def visit_propagate_attrs(self, element, **kw):
        pass


_get_children = _GetChildrenTraversal()


@util.preload_module("sqlalchemy.sql.elements")
def _resolve_name_for_compare(element, name, anon_map, **kw):
    pass


class TraversalComparatorStrategy(HasTraversalDispatch, util.MemoizedSlots):
    __slots__ = "stack", "cache", "anon_map"

    def __init__(self):
        self.stack: Deque[
            Tuple[
                Optional[ExternallyTraversible],
                Optional[ExternallyTraversible],
            ]
        ] = deque()
        self.cache = set()

    def _memoized_attr_anon_map(self):
        pass

    def compare(
        self,
        obj1: ExternallyTraversible,
        obj2: ExternallyTraversible,
        **kw: Any,
    ) -> bool:
        stack = self.stack
        cache = self.cache

        compare_annotations = kw.get("compare_annotations", False)

        stack.append((obj1, obj2))

        while stack:
            left, right = stack.popleft()

            if left is right:
                continue
            elif left is None or right is None:
                # we know they are different so no match
                return False
            elif (left, right) in cache:
                continue
            cache.add((left, right))

            visit_name = left.__visit_name__
            if visit_name != right.__visit_name__:
                return False

            meth = getattr(self, "compare_%s" % visit_name, None)

            if meth:
                attributes_compared = meth(left, right, **kw)
                if attributes_compared is COMPARE_FAILED:
                    return False
                elif attributes_compared is SKIP_TRAVERSE:
                    continue

                # attributes_compared is returned as a list of attribute
                # names that were "handled" by the comparison method above.
                # remaining attribute names in the _traverse_internals
                # will be compared.
            else:
                attributes_compared = ()

            for (
                (left_attrname, left_visit_sym),
                (right_attrname, right_visit_sym),
            ) in zip_longest(
                left._traverse_internals,
                right._traverse_internals,
                fillvalue=(None, None),
            ):
                if not compare_annotations and (
                    (left_attrname == "_annotations")
                    or (right_attrname == "_annotations")
                ):
                    continue

                if (
                    left_attrname != right_attrname
                    or left_visit_sym is not right_visit_sym
                ):
                    return False
                elif left_attrname in attributes_compared:
                    continue

                assert left_visit_sym is not None
                assert left_attrname is not None
                assert right_attrname is not None

                dispatch = self.dispatch(left_visit_sym)
                assert dispatch is not None, (
                    f"{self.__class__} has no dispatch for "
                    f"'{self._dispatch_lookup[left_visit_sym]}'"
                )
                left_child = operator.attrgetter(left_attrname)(left)
                right_child = operator.attrgetter(right_attrname)(right)
                if left_child is None:
                    if right_child is not None:
                        return False
                    else:
                        continue
                elif right_child is None:
                    return False

                comparison = dispatch(
                    left_attrname, left, left_child, right, right_child, **kw
                )
                if comparison is COMPARE_FAILED:
                    return False

        return True

    def compare_inner(self, obj1, obj2, **kw):
        pass

    def visit_has_cache_key(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_propagate_attrs(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_has_cache_key_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_executable_options(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_clauseelement(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_fromclause_canonical_column_collection(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_fromclause_derived_column_collection(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_string_clauseelement_dict(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_clauseelement_tuples(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_multi_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_clauseelement_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_clauseelement_tuple(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def _compare_unordered_sequences(self, seq1, seq2, **kw):
        pass

    def visit_clauseelement_unordered_set(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_fromclause_ordered_set(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_string(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_string_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_string_multi_dict(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_multi(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_anon_name(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_boolean(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_operator(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_type(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_plain_dict(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_dialect_options(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_annotations_key(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_compile_state_funcs(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_plain_obj(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_named_ddl_element(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_prefix_sequence(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_setup_join_tuple(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        # TODO: look at attrname for "legacy_join" and use different structure
        pass

    def visit_memoized_select_entities(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_table_hint_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_statement_hint_list(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_unknown_structure(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        raise NotImplementedError()

    def visit_dml_ordered_values(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        # sequence of tuple pairs

        pass

    def _compare_dml_values_or_ce(self, lv, rv, **kw):
        pass

    def visit_dml_values(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_dml_multi_values(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def visit_params(
        self, attrname, left_parent, left, right_parent, right, **kw
    ):
        pass

    def compare_expression_clauselist(self, left, right, **kw):
        pass

    def compare_clauselist(self, left, right, **kw):
        pass

    def compare_binary(self, left, right, **kw):
        pass

    def compare_bindparam(self, left, right, **kw):
        pass


class ColIdentityComparatorStrategy(TraversalComparatorStrategy):
    def compare_column_element(
        self, left, right, use_proxies=True, equivalents=(), **kw
    ):
        """Compare ColumnElements using proxies and equivalent collections.

        This is a comparison strategy specific to the ORM.
        """
        pass

    def compare_column(self, left, right, **kw):
        pass

    def compare_label(self, left, right, **kw):
        pass

    def compare_table(self, left, right, **kw):
        # tables compare on identity, since it's not really feasible to
        # compare them column by column with the above rules
        pass
