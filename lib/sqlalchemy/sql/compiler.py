# sql/compiler.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: allow-untyped-defs, allow-untyped-calls

"""Base SQL and DDL compiler implementations.

Classes provided include:

:class:`.compiler.SQLCompiler` - renders SQL
strings

:class:`.compiler.DDLCompiler` - renders DDL
(data definition language) strings

:class:`.compiler.GenericTypeCompiler` - renders
type specification strings.

To generate user-defined SQL strings, see
:doc:`/ext/compiler`.

"""
from __future__ import annotations

import collections
import collections.abc as collections_abc
import contextlib
from enum import IntEnum
import functools
import itertools
import operator
import re
from time import perf_counter
import typing
from typing import Any
from typing import Callable
from typing import cast
from typing import ClassVar
from typing import Dict
from typing import Final
from typing import FrozenSet
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Literal
from typing import Mapping
from typing import MutableMapping
from typing import NamedTuple
from typing import NoReturn
from typing import Optional
from typing import Pattern
from typing import Protocol
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING
from typing import TypedDict
from typing import Union

from . import base
from . import coercions
from . import crud
from . import elements
from . import functions
from . import operators
from . import roles
from . import schema
from . import selectable
from . import sqltypes
from . import util as sql_util
from ._typing import is_column_element
from ._typing import is_dml
from .base import _de_clone
from .base import _from_objects
from .base import _NONE_NAME
from .base import _SentinelDefaultCharacterization
from .base import NO_ARG
from .elements import quoted_name
from .sqltypes import TupleType
from .visitors import prefix_anon_map
from .. import exc
from .. import util
from ..util import FastIntFlag
from ..util.typing import Self
from ..util.typing import TupleAny
from ..util.typing import Unpack

if typing.TYPE_CHECKING:
    from .annotation import _AnnotationDict
    from .base import _AmbiguousTableNameMap
    from .base import CompileState
    from .base import Executable
    from .base import ExecutableStatement
    from .cache_key import CacheKey
    from .ddl import _TableViaSelect
    from .ddl import CreateTableAs
    from .ddl import CreateView
    from .ddl import ExecutableDDLElement
    from .dml import Delete
    from .dml import Insert
    from .dml import Update
    from .dml import UpdateBase
    from .dml import UpdateDMLState
    from .dml import ValuesBase
    from .elements import _truncated_label
    from .elements import BinaryExpression
    from .elements import BindParameter
    from .elements import ClauseElement
    from .elements import ColumnClause
    from .elements import ColumnElement
    from .elements import False_
    from .elements import Label
    from .elements import Null
    from .elements import True_
    from .functions import Function
    from .schema import CheckConstraint
    from .schema import Column
    from .schema import Constraint
    from .schema import ForeignKeyConstraint
    from .schema import IdentityOptions
    from .schema import Index
    from .schema import PrimaryKeyConstraint
    from .schema import Table
    from .schema import UniqueConstraint
    from .selectable import _ColumnsClauseElement
    from .selectable import AliasedReturnsRows
    from .selectable import CompoundSelectState
    from .selectable import CTE
    from .selectable import FromClause
    from .selectable import NamedFromClause
    from .selectable import ReturnsRows
    from .selectable import Select
    from .selectable import SelectState
    from .type_api import _BindProcessorType
    from .type_api import TypeDecorator
    from .type_api import TypeEngine
    from .type_api import UserDefinedType
    from .visitors import Visitable
    from ..engine.cursor import CursorResultMetaData
    from ..engine.interfaces import _CoreSingleExecuteParams
    from ..engine.interfaces import _DBAPIAnyExecuteParams
    from ..engine.interfaces import _DBAPIMultiExecuteParams
    from ..engine.interfaces import _DBAPISingleExecuteParams
    from ..engine.interfaces import _ExecuteOptions
    from ..engine.interfaces import _GenericSetInputSizesType
    from ..engine.interfaces import _MutableCoreSingleExecuteParams
    from ..engine.interfaces import Dialect
    from ..engine.interfaces import SchemaTranslateMapType


_FromHintsType = Dict["FromClause", str]

RESERVED_WORDS = {
    "all",
    "analyse",
    "analyze",
    "and",
    "any",
    "array",
    "as",
    "asc",
    "asymmetric",
    "authorization",
    "between",
    "binary",
    "both",
    "case",
    "cast",
    "check",
    "collate",
    "column",
    "constraint",
    "create",
    "cross",
    "current_date",
    "current_role",
    "current_time",
    "current_timestamp",
    "current_user",
    "default",
    "deferrable",
    "desc",
    "distinct",
    "do",
    "else",
    "end",
    "except",
    "false",
    "for",
    "foreign",
    "freeze",
    "from",
    "full",
    "grant",
    "group",
    "having",
    "ilike",
    "in",
    "initially",
    "inner",
    "intersect",
    "into",
    "is",
    "isnull",
    "join",
    "leading",
    "left",
    "like",
    "limit",
    "localtime",
    "localtimestamp",
    "natural",
    "new",
    "not",
    "notnull",
    "null",
    "off",
    "offset",
    "old",
    "on",
    "only",
    "or",
    "order",
    "outer",
    "overlaps",
    "placing",
    "primary",
    "references",
    "right",
    "select",
    "session_user",
    "set",
    "similar",
    "some",
    "symmetric",
    "table",
    "then",
    "to",
    "trailing",
    "true",
    "union",
    "unique",
    "user",
    "using",
    "verbose",
    "when",
    "where",
}

LEGAL_CHARACTERS = re.compile(r"^[A-Z0-9_$]+$", re.I)
LEGAL_CHARACTERS_PLUS_SPACE = re.compile(r"^[A-Z0-9_ $]+$", re.I)
ILLEGAL_INITIAL_CHARACTERS = {str(x) for x in range(0, 10)}.union(["$"])

FK_ON_DELETE = re.compile(
    r"^(?:RESTRICT|CASCADE|SET NULL|NO ACTION|SET DEFAULT)$", re.I
)
FK_ON_UPDATE = re.compile(
    r"^(?:RESTRICT|CASCADE|SET NULL|NO ACTION|SET DEFAULT)$", re.I
)
FK_INITIALLY = re.compile(r"^(?:DEFERRED|IMMEDIATE)$", re.I)
_WINDOW_EXCLUDE_RE = re.compile(
    r"^(?:CURRENT ROW|GROUP|TIES|NO OTHERS)$", re.I
)
BIND_PARAMS = re.compile(r"(?<![:\w\$\x5c]):([\w\$]+)(?![:\w\$])", re.UNICODE)
BIND_PARAMS_ESC = re.compile(r"\x5c(:[\w\$]*)(?![:\w\$])", re.UNICODE)

_pyformat_template = "%%(%(name)s)s"
BIND_TEMPLATES = {
    "pyformat": _pyformat_template,
    "qmark": "?",
    "format": "%%s",
    "numeric": ":[_POSITION]",
    "numeric_dollar": "$[_POSITION]",
    "named": ":%(name)s",
}


OPERATORS = {
    # binary
    operators.and_: " AND ",
    operators.or_: " OR ",
    operators.add: " + ",
    operators.mul: " * ",
    operators.sub: " - ",
    operators.mod: " % ",
    operators.neg: "-",
    operators.lt: " < ",
    operators.le: " <= ",
    operators.ne: " != ",
    operators.gt: " > ",
    operators.ge: " >= ",
    operators.eq: " = ",
    operators.is_distinct_from: " IS DISTINCT FROM ",
    operators.is_not_distinct_from: " IS NOT DISTINCT FROM ",
    operators.concat_op: " || ",
    operators.match_op: " MATCH ",
    operators.not_match_op: " NOT MATCH ",
    operators.in_op: " IN ",
    operators.not_in_op: " NOT IN ",
    operators.comma_op: ", ",
    operators.from_: " FROM ",
    operators.as_: " AS ",
    operators.is_: " IS ",
    operators.is_not: " IS NOT ",
    operators.collate: " COLLATE ",
    # unary
    operators.exists: "EXISTS ",
    operators.distinct_op: "DISTINCT ",
    operators.inv: "NOT ",
    operators.any_op: "ANY ",
    operators.all_op: "ALL ",
    # modifiers
    operators.desc_op: " DESC",
    operators.asc_op: " ASC",
    operators.nulls_first_op: " NULLS FIRST",
    operators.nulls_last_op: " NULLS LAST",
    # bitwise
    operators.bitwise_xor_op: " ^ ",
    operators.bitwise_or_op: " | ",
    operators.bitwise_and_op: " & ",
    operators.bitwise_not_op: "~",
    operators.bitwise_lshift_op: " << ",
    operators.bitwise_rshift_op: " >> ",
}

FUNCTIONS: Dict[Type[Function[Any]], str] = {
    functions.coalesce: "coalesce",
    functions.current_date: "CURRENT_DATE",
    functions.current_time: "CURRENT_TIME",
    functions.current_timestamp: "CURRENT_TIMESTAMP",
    functions.current_user: "CURRENT_USER",
    functions.localtime: "LOCALTIME",
    functions.localtimestamp: "LOCALTIMESTAMP",
    functions.random: "random",
    functions.sysdate: "sysdate",
    functions.session_user: "SESSION_USER",
    functions.user: "USER",
    functions.cube: "CUBE",
    functions.rollup: "ROLLUP",
    functions.grouping_sets: "GROUPING SETS",
}


EXTRACT_MAP = {
    "month": "month",
    "day": "day",
    "year": "year",
    "second": "second",
    "hour": "hour",
    "doy": "doy",
    "minute": "minute",
    "quarter": "quarter",
    "dow": "dow",
    "week": "week",
    "epoch": "epoch",
    "milliseconds": "milliseconds",
    "microseconds": "microseconds",
    "timezone_hour": "timezone_hour",
    "timezone_minute": "timezone_minute",
}

COMPOUND_KEYWORDS = {
    selectable._CompoundSelectKeyword.UNION: "UNION",
    selectable._CompoundSelectKeyword.UNION_ALL: "UNION ALL",
    selectable._CompoundSelectKeyword.EXCEPT: "EXCEPT",
    selectable._CompoundSelectKeyword.EXCEPT_ALL: "EXCEPT ALL",
    selectable._CompoundSelectKeyword.INTERSECT: "INTERSECT",
    selectable._CompoundSelectKeyword.INTERSECT_ALL: "INTERSECT ALL",
}


class ResultColumnsEntry(NamedTuple):
    """Tracks a column expression that is expected to be represented
    in the result rows for this statement.

    This normally refers to the columns clause of a SELECT statement
    but may also refer to a RETURNING clause, as well as for dialect-specific
    emulations.

    """

    keyname: str
    """string name that's expected in cursor.description"""

    name: str
    """column name, may be labeled"""

    objects: Tuple[Any, ...]
    """sequence of objects that should be able to locate this column
    in a RowMapping.  This is typically string names and aliases
    as well as Column objects.

    """

    type: TypeEngine[Any]
    """Datatype to be associated with this column.   This is where
    the "result processing" logic directly links the compiled statement
    to the rows that come back from the cursor.

    """


class _ResultMapAppender(Protocol):
    def __call__(
        self,
        keyname: str,
        name: str,
        objects: Sequence[Any],
        type_: TypeEngine[Any],
    ) -> None: ...


# integer indexes into ResultColumnsEntry used by cursor.py.
# some profiling showed integer access faster than named tuple
RM_RENDERED_NAME: Literal[0] = 0
RM_NAME: Literal[1] = 1
RM_OBJECTS: Literal[2] = 2
RM_TYPE: Literal[3] = 3


class _BaseCompilerStackEntry(TypedDict):
    asfrom_froms: Set[FromClause]
    correlate_froms: Set[FromClause]
    selectable: ReturnsRows


class _CompilerStackEntry(_BaseCompilerStackEntry, total=False):
    compile_state: CompileState
    need_result_map_for_nested: bool
    need_result_map_for_compound: bool
    select_0: ReturnsRows
    insert_from_select: Select[Unpack[TupleAny]]


class ExpandedState(NamedTuple):
    """represents state to use when producing "expanded" and
    "post compile" bound parameters for a statement.

    "expanded" parameters are parameters that are generated at
    statement execution time to suit a number of parameters passed, the most
    prominent example being the individual elements inside of an IN expression.

    "post compile" parameters are parameters where the SQL literal value
    will be rendered into the SQL statement at execution time, rather than
    being passed as separate parameters to the driver.

    To create an :class:`.ExpandedState` instance, use the
    :meth:`.SQLCompiler.construct_expanded_state` method on any
    :class:`.SQLCompiler` instance.

    """

    statement: str
    """String SQL statement with parameters fully expanded"""

    parameters: _CoreSingleExecuteParams
    """Parameter dictionary with parameters fully expanded.

    For a statement that uses named parameters, this dictionary will map
    exactly to the names in the statement.  For a statement that uses
    positional parameters, the :attr:`.ExpandedState.positional_parameters`
    will yield a tuple with the positional parameter set.

    """

    processors: Mapping[str, _BindProcessorType[Any]]
    """mapping of bound value processors"""

    positiontup: Optional[Sequence[str]]
    """Sequence of string names indicating the order of positional
    parameters"""

    parameter_expansion: Mapping[str, List[str]]
    """Mapping representing the intermediary link from original parameter
    name to list of "expanded" parameter names, for those parameters that
    were expanded."""

    @property
    def positional_parameters(self) -> Tuple[Any, ...]:
        """Tuple of positional parameters, for statements that were compiled
        using a positional paramstyle.

        """
        pass

    @property
    def additional_parameters(self) -> _CoreSingleExecuteParams:
        """synonym for :attr:`.ExpandedState.parameters`."""
        pass


class _InsertManyValues(NamedTuple):
    """represents state to use for executing an "insertmanyvalues" statement.

    The primary consumers of this object are the
    :meth:`.SQLCompiler._deliver_insertmanyvalues_batches` and
    :meth:`.DefaultDialect._deliver_insertmanyvalues_batches` methods.

    .. versionadded:: 2.0

    """

    is_default_expr: bool
    """if True, the statement is of the form
    ``INSERT INTO TABLE DEFAULT VALUES``, and can't be rewritten as a "batch"

    """

    single_values_expr: str
    """The rendered "values" clause of the INSERT statement.

    This is typically the parenthesized section e.g. "(?, ?, ?)" or similar.
    The insertmanyvalues logic uses this string as a search and replace
    target.

    """

    insert_crud_params: List[crud._CrudParamElementStr]
    """List of Column / bind names etc. used while rewriting the statement"""

    num_positional_params_counted: int
    """the number of bound parameters in a single-row statement.

    This count may be larger or smaller than the actual number of columns
    targeted in the INSERT, as it accommodates for SQL expressions
    in the values list that may have zero or more parameters embedded
    within them.

    This count is part of what's used to organize rewritten parameter lists
    when batching.

    """

    sort_by_parameter_order: bool = False
    """if the deterministic_returnined_order parameter were used on the
    insert.

    All of the attributes following this will only be used if this is True.

    """

    includes_upsert_behaviors: bool = False
    """if True, we have to accommodate for upsert behaviors.

    This will in some cases downgrade "insertmanyvalues" that requests
    deterministic ordering.

    """

    sentinel_columns: Optional[Sequence[Column[Any]]] = None
    """List of sentinel columns that were located.

    This list is only here if the INSERT asked for
    sort_by_parameter_order=True,
    and dialect-appropriate sentinel columns were located.

    .. versionadded:: 2.0.10

    """

    num_sentinel_columns: int = 0
    """how many sentinel columns are in the above list, if any.

    This is the same as
    ``len(sentinel_columns) if sentinel_columns is not None else 0``

    """

    sentinel_param_keys: Optional[Sequence[str]] = None
    """parameter str keys in each param dictionary / tuple
    that would link to the client side "sentinel" values for that row, which
    we can use to match up parameter sets to result rows.

    This is only present if sentinel_columns is present and the INSERT
    statement actually refers to client side values for these sentinel
    columns.

    .. versionadded:: 2.0.10

    .. versionchanged:: 2.0.29 - the sequence is now string dictionary keys
       only, used against the "compiled parameteters" collection before
       the parameters were converted by bound parameter processors

    """

    implicit_sentinel: bool = False
    """if True, we have exactly one sentinel column and it uses a server side
    value, currently has to generate an incrementing integer value.

    The dialect in question would have asserted that it supports receiving
    these values back and sorting on that value as a means of guaranteeing
    correlation with the incoming parameter list.

    .. versionadded:: 2.0.10

    """

    has_upsert_bound_parameters: bool = False
    """if True, the upsert SET clause contains bound parameters that will
    receive their values from the parameters dict (i.e., parametrized
    bindparams where value is None and callable is None).

    This means we can't batch multiple rows in a single statement, since
    each row would need different values in the SET clause but there's only
    one SET clause per statement. See issue #13130.

    .. versionadded:: 2.0.37

    """

    embed_values_counter: bool = False
    """Whether to embed an incrementing integer counter in each parameter
    set within the VALUES clause as parameters are batched over.

    This is only used for a specific INSERT..SELECT..VALUES..RETURNING syntax
    where a subquery is used to produce value tuples.  Current support
    includes PostgreSQL, Microsoft SQL Server.

    .. versionadded:: 2.0.10

    """


class _InsertManyValuesBatch(NamedTuple):
    """represents an individual batch SQL statement for insertmanyvalues.

    This is passed through the
    :meth:`.SQLCompiler._deliver_insertmanyvalues_batches` and
    :meth:`.DefaultDialect._deliver_insertmanyvalues_batches` methods out
    to the :class:`.Connection` within the
    :meth:`.Connection._exec_insertmany_context` method.

    .. versionadded:: 2.0.10

    """

    replaced_statement: str
    replaced_parameters: _DBAPIAnyExecuteParams
    processed_setinputsizes: Optional[_GenericSetInputSizesType]
    batch: Sequence[_DBAPISingleExecuteParams]
    sentinel_values: Sequence[Tuple[Any, ...]]
    current_batch_size: int
    batchnum: int
    total_batches: int
    rows_sorted: bool
    is_downgraded: bool


class InsertmanyvaluesSentinelOpts(FastIntFlag):
    """bitflag enum indicating styles of PK defaults
    which can work as implicit sentinel columns

    """

    NOT_SUPPORTED = 1
    AUTOINCREMENT = 2
    IDENTITY = 4
    SEQUENCE = 8
    MONOTONIC_FUNCTION = 16

    ANY_AUTOINCREMENT = (
        AUTOINCREMENT | IDENTITY | SEQUENCE | MONOTONIC_FUNCTION
    )
    _SUPPORTED_OR_NOT = NOT_SUPPORTED | ANY_AUTOINCREMENT

    USE_INSERT_FROM_SELECT = 32
    RENDER_SELECT_COL_CASTS = 64


class AggregateOrderByStyle(IntEnum):
    """Describes backend database's capabilities with ORDER BY for aggregate
    functions

    .. versionadded:: 2.1

    """

    NONE = 0
    """database has no ORDER BY for aggregate functions"""

    INLINE = 1
    """ORDER BY is rendered inside the function's argument list, typically as
    the last element"""

    WITHIN_GROUP = 2
    """the WITHIN GROUP (ORDER BY ...) phrase is used for all aggregate
    functions (not just the ordered set ones)"""


class CompilerState(IntEnum):
    COMPILING = 0
    """statement is present, compilation phase in progress"""

    STRING_APPLIED = 1
    """statement is present, string form of the statement has been applied.

    Additional processors by subclasses may still be pending.

    """

    NO_STATEMENT = 2
    """compiler does not have a statement to compile, is used
    for method access"""


class Linting(IntEnum):
    """represent preferences for the 'SQL linting' feature.

    this feature currently includes support for flagging cartesian products
    in SQL statements.

    """

    NO_LINTING = 0
    "Disable all linting."

    COLLECT_CARTESIAN_PRODUCTS = 1
    """Collect data on FROMs and cartesian products and gather into
    'self.from_linter'"""

    WARN_LINTING = 2
    "Emit warnings for linters that find problems"

    FROM_LINTING = COLLECT_CARTESIAN_PRODUCTS | WARN_LINTING
    """Warn for cartesian products; combines COLLECT_CARTESIAN_PRODUCTS
    and WARN_LINTING"""


NO_LINTING, COLLECT_CARTESIAN_PRODUCTS, WARN_LINTING, FROM_LINTING = tuple(
    Linting
)


class FromLinter(collections.namedtuple("FromLinter", ["froms", "edges"])):
    """represents current state for the "cartesian product" detection
    feature."""

    def lint(self, start=None):
        froms = self.froms
        if not froms:
            return None, None

        edges = set(self.edges)
        the_rest = set(froms)

        if start is not None:
            start_with = start
            the_rest.remove(start_with)
        else:
            start_with = the_rest.pop()

        stack = collections.deque([start_with])

        while stack and the_rest:
            node = stack.popleft()
            the_rest.discard(node)

            # comparison of nodes in edges here is based on hash equality, as
            # there are "annotated" elements that match the non-annotated ones.
            #   to remove the need for in-python hash() calls, use native
            # containment routines (e.g. "node in edge", "edge.index(node)")
            to_remove = {edge for edge in edges if node in edge}

            # appendleft the node in each edge that is not
            # the one that matched.
            stack.extendleft(edge[not edge.index(node)] for edge in to_remove)
            edges.difference_update(to_remove)

        # FROMS left over?  boom
        if the_rest:
            return the_rest, start_with
        else:
            return None, None

    def warn(self, stmt_type="SELECT"):
        the_rest, start_with = self.lint()

        # FROMS left over?  boom
        if the_rest:
            froms = the_rest
            if froms:
                template = (
                    "{stmt_type} statement has a cartesian product between "
                    "FROM element(s) {froms} and "
                    'FROM element "{start}".  Apply join condition(s) '
                    "between each element to resolve."
                )
                froms_str = ", ".join(
                    f'"{self.froms[from_]}"' for from_ in froms
                )
                message = template.format(
                    stmt_type=stmt_type,
                    froms=froms_str,
                    start=self.froms[start_with],
                )

                util.warn(message)


class Compiled:
    """Represent a compiled SQL or DDL expression.

    The ``__str__`` method of the ``Compiled`` object should produce
    the actual text of the statement.  ``Compiled`` objects are
    specific to their underlying database dialect, and also may
    or may not be specific to the columns referenced within a
    particular set of bind parameters.  In no case should the
    ``Compiled`` object be dependent on the actual values of those
    bind parameters, even though it may reference those values as
    defaults.
    """

    statement: Optional[ClauseElement] = None
    "The statement to compile."
    string: str = ""
    "The string representation of the ``statement``"

    state: CompilerState
    """description of the compiler's state"""

    is_sql = False
    is_ddl = False

    _cached_metadata: Optional[CursorResultMetaData] = None

    _result_columns: Optional[List[ResultColumnsEntry]] = None

    schema_translate_map: Optional[SchemaTranslateMapType] = None

    execution_options: _ExecuteOptions = util.EMPTY_DICT
    """
    Execution options propagated from the statement.   In some cases,
    sub-elements of the statement can modify these.
    """

    preparer: IdentifierPreparer

    _annotations: _AnnotationDict = util.EMPTY_DICT

    compile_state: Optional[CompileState] = None
    """Optional :class:`.CompileState` object that maintains additional
    state used by the compiler.

    Major executable objects such as :class:`_expression.Insert`,
    :class:`_expression.Update`, :class:`_expression.Delete`,
    :class:`_expression.Select` will generate this
    state when compiled in order to calculate additional information about the
    object.   For the top level object that is to be executed, the state can be
    stored here where it can also have applicability towards result set
    processing.

    .. versionadded:: 1.4

    """

    dml_compile_state: Optional[CompileState] = None
    """Optional :class:`.CompileState` assigned at the same point that
    .isinsert, .isupdate, or .isdelete is assigned.

    This will normally be the same object as .compile_state, with the
    exception of cases like the :class:`.ORMFromStatementCompileState`
    object.

    .. versionadded:: 1.4.40

    """

    cache_key: Optional[CacheKey] = None
    """The :class:`.CacheKey` that was generated ahead of creating this
    :class:`.Compiled` object.

    This is used for routines that need access to the original
    :class:`.CacheKey` instance generated when the :class:`.Compiled`
    instance was first cached, typically in order to reconcile
    the original list of :class:`.BindParameter` objects with a
    per-statement list that's generated on each call.

    """

    _gen_time: float
    """Generation time of this :class:`.Compiled`, used for reporting
    cache stats."""

    def __init__(
        self,
        dialect: Dialect,
        statement: Optional[ClauseElement],
        schema_translate_map: Optional[SchemaTranslateMapType] = None,
        render_schema_translate: bool = False,
        compile_kwargs: Mapping[str, Any] = util.immutabledict(),
    ):
        """Construct a new :class:`.Compiled` object.

        :param dialect: :class:`.Dialect` to compile against.

        :param statement: :class:`_expression.ClauseElement` to be compiled.

        :param schema_translate_map: dictionary of schema names to be
         translated when forming the resultant SQL

         .. seealso::

            :ref:`schema_translating`

        :param compile_kwargs: additional kwargs that will be
         passed to the initial call to :meth:`.Compiled.process`.


        """
        self.dialect = dialect
        self.preparer = self.dialect.identifier_preparer
        if schema_translate_map:
            self.schema_translate_map = schema_translate_map
            self.preparer = self.preparer._with_schema_translate(
                schema_translate_map
            )

        if statement is not None:
            self.state = CompilerState.COMPILING
            self.statement = statement
            self.can_execute = statement.supports_execution
            self._annotations = statement._annotations
            if self.can_execute:
                if TYPE_CHECKING:
                    assert isinstance(statement, Executable)
                self.execution_options = statement._execution_options
            self.string = self.process(self.statement, **compile_kwargs)

            if render_schema_translate:
                assert schema_translate_map is not None
                self.string = self.preparer._render_schema_translates(
                    self.string, schema_translate_map
                )

            self.state = CompilerState.STRING_APPLIED
        else:
            self.state = CompilerState.NO_STATEMENT

        self._gen_time = perf_counter()

    def __init_subclass__(cls) -> None:
        cls._init_compiler_cls()
        return super().__init_subclass__()

    @classmethod
    def _init_compiler_cls(cls):
        pass

    def visit_unsupported_compilation(self, element, err, **kw):
        raise exc.UnsupportedCompilationError(self, type(element)) from err

    @property
    def sql_compiler(self) -> SQLCompiler:
        """Return a Compiled that is capable of processing SQL expressions.

        If this compiler is one, it would likely just return 'self'.

        """

        raise NotImplementedError()

    def process(self, obj: Visitable, **kwargs: Any) -> str:
        return obj._compiler_dispatch(self, **kwargs)

    def __str__(self) -> str:
        """Return the string text of the generated SQL or DDL."""

        if self.state is CompilerState.STRING_APPLIED:
            return self.string
        else:
            return ""

    def construct_params(
        self,
        params: Optional[_CoreSingleExecuteParams] = None,
        extracted_parameters: Optional[Sequence[BindParameter[Any]]] = None,
        escape_names: bool = True,
    ) -> Optional[_MutableCoreSingleExecuteParams]:
        """Return the bind params for this compiled object.

        :param params: a dict of string/object pairs whose values will
                       override bind values compiled in to the
                       statement.
        """

        raise NotImplementedError()

    @property
    def params(self):
        """Return the bind params for this compiled object."""
        return self.construct_params()


class TypeCompiler(util.EnsureKWArg):
    """Produces DDL specification for TypeEngine objects."""

    ensure_kwarg = r"visit_\w+"

    def __init__(self, dialect: Dialect):
        self.dialect = dialect

    def process(self, type_: TypeEngine[Any], **kw: Any) -> str:
        if (
            type_._variant_mapping
            and self.dialect.name in type_._variant_mapping
        ):
            type_ = type_._variant_mapping[self.dialect.name]
        return type_._compiler_dispatch(self, **kw)

    def visit_unsupported_compilation(
        self, element: Any, err: Exception, **kw: Any
    ) -> NoReturn:
        raise exc.UnsupportedCompilationError(self, element) from err


# this was a Visitable, but to allow accurate detection of
# column elements this is actually a column element
class _CompileLabel(
    roles.BinaryElementRole[Any], elements.CompilerColumnElement
):
    """lightweight label object which acts as an expression.Label."""

    __visit_name__ = "label"
    __slots__ = "element", "name", "_alt_names"

    def __init__(self, col, name, alt_names=()):
        self.element = col
        self.name = name
        self._alt_names = (col,) + alt_names

    @property
    def proxy_set(self):
        pass

    @property
    def type(self):
        pass

    def self_group(self, **kw):
        return self


class aggregate_orderby_inline(
    roles.BinaryElementRole[Any], elements.CompilerColumnElement
):
    """produce ORDER BY inside of function argument lists"""

    __visit_name__ = "aggregate_orderby_inline"
    __slots__ = "element", "aggregate_order_by"

    def __init__(self, element, orderby):
        self.element = element
        self.aggregate_order_by = orderby

    def __iter__(self):
        return iter(self.element)

    @property
    def proxy_set(self):
        pass

    @property
    def type(self):
        pass

    def self_group(self, **kw):
        return self

    def _with_binary_element_type(self, type_):
        return aggregate_orderby_inline(
            self.element._with_binary_element_type(type_),
            self.aggregate_order_by,
        )


class ilike_case_insensitive(
    roles.BinaryElementRole[Any], elements.CompilerColumnElement
):
    """produce a wrapping element for a case-insensitive portion of
    an ILIKE construct.

    The construct usually renders the ``lower()`` function, but on
    PostgreSQL will pass silently with the assumption that "ILIKE"
    is being used.

    .. versionadded:: 2.0

    """

    __visit_name__ = "ilike_case_insensitive_operand"
    __slots__ = "element", "comparator"

    def __init__(self, element):
        self.element = element
        self.comparator = element.comparator

    @property
    def proxy_set(self):
        pass

    @property
    def type(self):
        pass

    def self_group(self, **kw):
        return self

    def _with_binary_element_type(self, type_):
        return ilike_case_insensitive(
            self.element._with_binary_element_type(type_)
        )


class SQLCompiler(Compiled):
    """Default implementation of :class:`.Compiled`.

    Compiles :class:`_expression.ClauseElement` objects into SQL strings.

    """

    extract_map = EXTRACT_MAP

    bindname_escape_characters: ClassVar[Mapping[str, str]] = (
        util.immutabledict(
            {
                "%": "P",
                "(": "A",
                ")": "Z",
                ":": "C",
                ".": "_",
                "[": "_",
                "]": "_",
                " ": "_",
            }
        )
    )
    """A mapping (e.g. dict or similar) containing a lookup of
    characters keyed to replacement characters which will be applied to all
    'bind names' used in SQL statements as a form of 'escaping'; the given
    characters are replaced entirely with the 'replacement' character when
    rendered in the SQL statement, and a similar translation is performed
    on the incoming names used in parameter dictionaries passed to methods
    like :meth:`_engine.Connection.execute`.

    This allows bound parameter names used in :func:`_sql.bindparam` and
    other constructs to have any arbitrary characters present without any
    concern for characters that aren't allowed at all on the target database.

    Third party dialects can establish their own dictionary here to replace the
    default mapping, which will ensure that the particular characters in the
    mapping will never appear in a bound parameter name.

    The dictionary is evaluated at **class creation time**, so cannot be
    modified at runtime; it must be present on the class when the class
    is first declared.

    Note that for dialects that have additional bound parameter rules such
    as additional restrictions on leading characters, the
    :meth:`_sql.SQLCompiler.bindparam_string` method may need to be augmented.
    See the cx_Oracle compiler for an example of this.

    .. versionadded:: 2.0.0rc1

    """

    _bind_translate_re: ClassVar[Pattern[str]]
    _bind_translate_chars: ClassVar[Mapping[str, str]]

    is_sql = True

    compound_keywords = COMPOUND_KEYWORDS

    isdelete: bool = False
    isinsert: bool = False
    isupdate: bool = False
    """class-level defaults which can be set at the instance
    level to define if this Compiled instance represents
    INSERT/UPDATE/DELETE
    """

    postfetch: Optional[List[Column[Any]]]
    """list of columns that can be post-fetched after INSERT or UPDATE to
    receive server-updated values"""

    insert_prefetch: Sequence[Column[Any]] = ()
    """list of columns for which default values should be evaluated before
    an INSERT takes place"""

    update_prefetch: Sequence[Column[Any]] = ()
    """list of columns for which onupdate default values should be evaluated
    before an UPDATE takes place"""

    implicit_returning: Optional[Sequence[ColumnElement[Any]]] = None
    """list of "implicit" returning columns for a toplevel INSERT or UPDATE
    statement, used to receive newly generated values of columns.

    .. versionadded:: 2.0  ``implicit_returning`` replaces the previous
       ``returning`` collection, which was not a generalized RETURNING
       collection and instead was in fact specific to the "implicit returning"
       feature.

    """

    isplaintext: bool = False

    binds: Dict[str, BindParameter[Any]]
    """a dictionary of bind parameter keys to BindParameter instances."""

    bind_names: Dict[BindParameter[Any], str]
    """a dictionary of BindParameter instances to "compiled" names
    that are actually present in the generated SQL"""

    stack: List[_CompilerStackEntry]
    """major statements such as SELECT, INSERT, UPDATE, DELETE are
    tracked in this stack using an entry format."""

    returning_precedes_values: bool = False
    """set to True classwide to generate RETURNING
    clauses before the VALUES or WHERE clause (i.e. MSSQL)
    """

    render_table_with_column_in_update_from: bool = False
    """set to True classwide to indicate the SET clause
    in a multi-table UPDATE statement should qualify
    columns with the table name (i.e. MySQL only)
    """

    ansi_bind_rules: bool = False
    """SQL 92 doesn't allow bind parameters to be used
    in the columns clause of a SELECT, nor does it allow
    ambiguous expressions like "? = ?".  A compiler
    subclass can set this flag to False if the target
    driver/DB enforces this
    """

    bindtemplate: str
    """template to render bound parameters based on paramstyle."""

    compilation_bindtemplate: str
    """template used by compiler to render parameters before positional
    paramstyle application"""

    _numeric_binds_identifier_char: str
    """Character that's used to as the identifier of a numerical bind param.
    For example if this char is set to ``$``, numerical binds will be rendered
    in the form ``$1, $2, $3``.
    """

    _result_columns: List[ResultColumnsEntry]
    """relates label names in the final SQL to a tuple of local
    column/label name, ColumnElement object (if any) and
    TypeEngine. CursorResult uses this for type processing and
    column targeting"""

    _textual_ordered_columns: bool = False
    """tell the result object that the column names as rendered are important,
    but they are also "ordered" vs. what is in the compiled object here.

    As of 1.4.42 this condition is only present when the statement is a
    TextualSelect, e.g. text("....").columns(...), where it is required
    that the columns are considered positionally and not by name.

    """

    _ad_hoc_textual: bool = False
    """tell the result that we encountered text() or '*' constructs in the
    middle of the result columns, but we also have compiled columns, so
    if the number of columns in cursor.description does not match how many
    expressions we have, that means we can't rely on positional at all and
    should match on name.

    """

    _ordered_columns: bool = True
    """
    if False, means we can't be sure the list of entries
    in _result_columns is actually the rendered order.  Usually
    True unless using an unordered TextualSelect.
    """

    _loose_column_name_matching: bool = False
    """tell the result object that the SQL statement is textual, wants to match
    up to Column objects, and may be using the ._tq_label in the SELECT rather
    than the base name.

    """

    _numeric_binds: bool = False
    """
    True if paramstyle is "numeric".  This paramstyle is trickier than
    all the others.

    """

    _render_postcompile: bool = False
    """
    whether to render out POSTCOMPILE params during the compile phase.

    This attribute is used only for end-user invocation of stmt.compile();
    it's never used for actual statement execution, where instead the
    dialect internals access and render the internal postcompile structure
    directly.

    """

    _post_compile_expanded_state: Optional[ExpandedState] = None
    """When render_postcompile is used, the ``ExpandedState`` used to create
    the "expanded" SQL is assigned here, and then used by the ``.params``
    accessor and ``.construct_params()`` methods for their return values.

    .. versionadded:: 2.0.0rc1

    """

    _pre_expanded_string: Optional[str] = None
    """Stores the original string SQL before 'post_compile' is applied,
    for cases where 'post_compile' were used.

    """

    _pre_expanded_positiontup: Optional[List[str]] = None

    _insertmanyvalues: Optional[_InsertManyValues] = None

    _insert_crud_params: Optional[crud._CrudParamSequence] = None

    literal_execute_params: FrozenSet[BindParameter[Any]] = frozenset()
    """bindparameter objects that are rendered as literal values at statement
    execution time.

    """

    post_compile_params: FrozenSet[BindParameter[Any]] = frozenset()
    """bindparameter objects that are rendered as bound parameter placeholders
    at statement execution time.

    """

    escaped_bind_names: util.immutabledict[str, str] = util.EMPTY_DICT
    """Late escaping of bound parameter names that has to be converted
    to the original name when looking in the parameter dictionary.

    """

    has_out_parameters = False
    """if True, there are bindparam() objects that have the isoutparam
    flag set."""

    postfetch_lastrowid = False
    """if True, and this in insert, use cursor.lastrowid to populate
    result.inserted_primary_key. """

    _cache_key_bind_match: Optional[
        Tuple[
            Dict[
                BindParameter[Any],
                List[BindParameter[Any]],
            ],
            Dict[
                str,
                BindParameter[Any],
            ],
        ]
    ] = None
    """a mapping that will relate the BindParameter object we compile
    to those that are part of the extracted collection of parameters
    in the cache key, if we were given a cache key.

    """

    positiontup: Optional[List[str]] = None
    """for a compiled construct that uses a positional paramstyle, will be
    a sequence of strings, indicating the names of bound parameters in order.

    This is used in order to render bound parameters in their correct order,
    and is combined with the :attr:`_sql.Compiled.params` dictionary to
    render parameters.

    This sequence always contains the unescaped name of the parameters.

    .. seealso::

        :ref:`faq_sql_expression_string` - includes a usage example for
        debugging use cases.

    """
    _values_bindparam: Optional[List[str]] = None

    _visited_bindparam: Optional[List[str]] = None

    inline: bool = False

    ctes: Optional[MutableMapping[CTE, str]]

    # Detect same CTE references - Dict[(level, name), cte]
    # Level is required for supporting nesting
    ctes_by_level_name: Dict[Tuple[int, str], CTE]

    # To retrieve key/level in ctes_by_level_name -
    # Dict[cte_reference, (level, cte_name, cte_opts)]
    level_name_by_cte: Dict[CTE, Tuple[int, str, selectable._CTEOpts]]

    ctes_recursive: bool

    _post_compile_pattern = re.compile(r"__\[POSTCOMPILE_(\S+?)(~~.+?~~)?\]")
    _pyformat_pattern = re.compile(r"%\(([^)]+?)\)s")
    _positional_pattern = re.compile(
        f"{_pyformat_pattern.pattern}|{_post_compile_pattern.pattern}"
    )
    _collect_params: Final[bool]
    _collected_params: util.immutabledict[str, Any]

    @classmethod
    def _init_compiler_cls(cls):
        cls._init_bind_translate()

    @classmethod
    def _init_bind_translate(cls):
        reg = re.escape("".join(cls.bindname_escape_characters))
        cls._bind_translate_re = re.compile(f"[{reg}]")
        cls._bind_translate_chars = cls.bindname_escape_characters

    def __init__(
        self,
        dialect: Dialect,
        statement: Optional[ClauseElement],
        cache_key: Optional[CacheKey] = None,
        column_keys: Optional[Sequence[str]] = None,
        for_executemany: bool = False,
        linting: Linting = NO_LINTING,
        _supporting_against: Optional[SQLCompiler] = None,
        **kwargs: Any,
    ):
        """Construct a new :class:`.SQLCompiler` object.

        :param dialect: :class:`.Dialect` to be used

        :param statement: :class:`_expression.ClauseElement` to be compiled

        :param column_keys:  a list of column names to be compiled into an
         INSERT or UPDATE statement.

        :param for_executemany: whether INSERT / UPDATE statements should
         expect that they are to be invoked in an "executemany" style,
         which may impact how the statement will be expected to return the
         values of defaults and autoincrement / sequences and similar.
         Depending on the backend and driver in use, support for retrieving
         these values may be disabled which means SQL expressions may
         be rendered inline, RETURNING may not be rendered, etc.

        :param kwargs: additional keyword arguments to be consumed by the
         superclass.

        """
        self.column_keys = column_keys

        self.cache_key = cache_key

        if cache_key:
            cksm = {b.key: b for b in cache_key[1]}
            ckbm = {b: [b] for b in cache_key[1]}
            self._cache_key_bind_match = (ckbm, cksm)

        # compile INSERT/UPDATE defaults/sequences to expect executemany
        # style execution, which may mean no pre-execute of defaults,
        # or no RETURNING
        self.for_executemany = for_executemany

        self.linting = linting

        # a dictionary of bind parameter keys to BindParameter
        # instances.
        self.binds = {}

        # a dictionary of BindParameter instances to "compiled" names
        # that are actually present in the generated SQL
        self.bind_names = util.column_dict()

        # stack which keeps track of nested SELECT statements
        self.stack = []

        self._result_columns = []

        # true if the paramstyle is positional
        self.positional = dialect.positional
        if self.positional:
            self._numeric_binds = nb = dialect.paramstyle.startswith("numeric")
            if nb:
                self._numeric_binds_identifier_char = (
                    "$" if dialect.paramstyle == "numeric_dollar" else ":"
                )

            self.compilation_bindtemplate = _pyformat_template
        else:
            self.compilation_bindtemplate = BIND_TEMPLATES[dialect.paramstyle]

        self.ctes = None

        self.label_length = (
            dialect.label_length or dialect.max_identifier_length
        )

        # a map which tracks "anonymous" identifiers that are created on
        # the fly here
        self.anon_map = prefix_anon_map()

        # a map which tracks "truncated" names based on
        # dialect.label_length or dialect.max_identifier_length
        self.truncated_names: Dict[Tuple[str, str], str] = {}
        self._truncated_counters: Dict[str, int] = {}
        if not cache_key:
            self._collect_params = True
            self._collected_params = util.EMPTY_DICT
        else:
            self._collect_params = False  # type: ignore[misc]

        Compiled.__init__(self, dialect, statement, **kwargs)

        if self.isinsert or self.isupdate or self.isdelete:
            if TYPE_CHECKING:
                assert isinstance(statement, UpdateBase)

            if self.isinsert or self.isupdate:
                if TYPE_CHECKING:
                    assert isinstance(statement, ValuesBase)
                if statement._inline:
                    self.inline = True
                elif self.for_executemany and (
                    not self.isinsert
                    or (
                        self.dialect.insert_executemany_returning
                        and statement._return_defaults
                    )
                ):
                    self.inline = True

        self.bindtemplate = BIND_TEMPLATES[dialect.paramstyle]

        if _supporting_against:
            self.__dict__.update(
                {
                    k: v
                    for k, v in _supporting_against.__dict__.items()
                    if k
                    not in {
                        "state",
                        "dialect",
                        "preparer",
                        "positional",
                        "_numeric_binds",
                        "compilation_bindtemplate",
                        "bindtemplate",
                    }
                }
            )

        if self.state is CompilerState.STRING_APPLIED:
            if self.positional:
                if self._numeric_binds:
                    self._process_numeric()
                else:
                    self._process_positional()

            if self._render_postcompile:
                parameters = self.construct_params(
                    escape_names=False,
                    _no_postcompile=True,
                )

                self._process_parameters_for_postcompile(
                    parameters, _populate_self=True
                )

    @property
    def insert_single_values_expr(self) -> Optional[str]:
        """When an INSERT is compiled with a single set of parameters inside
        a VALUES expression, the string is assigned here, where it can be
        used for insert batching schemes to rewrite the VALUES expression.

        .. versionchanged:: 2.0 This collection is no longer used by
           SQLAlchemy's built-in dialects, in favor of the currently
           internal ``_insertmanyvalues`` collection that is used only by
           :class:`.SQLCompiler`.

        """
        pass

    @util.ro_memoized_property
    def effective_returning(self) -> Optional[Sequence[ColumnElement[Any]]]:
        """The effective "returning" columns for INSERT, UPDATE or DELETE.

        This is either the so-called "implicit returning" columns which are
        calculated by the compiler on the fly, or those present based on what's
        present in ``self.statement._returning`` (expanded into individual
        columns using the ``._all_selected_columns`` attribute) i.e. those set
        explicitly using the :meth:`.UpdateBase.returning` method.

        .. versionadded:: 2.0

        """
        pass

    @property
    def returning(self):
        """backwards compatibility; returns the
        effective_returning collection.

        """
        return self.effective_returning

    @property
    def current_executable(self):
        """Return the current 'executable' that is being compiled.

        This is currently the :class:`_sql.Select`, :class:`_sql.Insert`,
        :class:`_sql.Update`, :class:`_sql.Delete`,
        :class:`_sql.CompoundSelect` object that is being compiled.
        Specifically it's assigned to the ``self.stack`` list of elements.

        When a statement like the above is being compiled, it normally
        is also assigned to the ``.statement`` attribute of the
        :class:`_sql.Compiler` object.   However, all SQL constructs are
        ultimately nestable, and this attribute should never be consulted
        by a ``visit_`` method, as it is not guaranteed to be assigned
        nor guaranteed to correspond to the current statement being compiled.

        """
        pass

    @property
    def prefetch(self):
        pass

    @util.memoized_property
    def _global_attributes(self) -> Dict[Any, Any]:
        pass

    def _add_to_params(self, item: ExecutableStatement) -> None:
        # assumes that this is called before traversing the statement
        # so the call happens outer to inner, meaning that existing params
        # take precedence
        pass

    @util.memoized_instancemethod
    def _init_cte_state(self) -> MutableMapping[CTE, str]:
        """Initialize collections related to CTEs only if
        a CTE is located, to save on the overhead of
        these collections otherwise.

        """
        pass

    @contextlib.contextmanager
    def _nested_result(self):
        """special API to support the use case of 'nested result sets'"""
        pass

    def _process_positional(self):
        assert not self.positiontup
        assert self.state is CompilerState.STRING_APPLIED
        assert not self._numeric_binds

        if self.dialect.paramstyle == "format":
            placeholder = "%s"
        else:
            assert self.dialect.paramstyle == "qmark"
            placeholder = "?"

        positions = []

        def find_position(m: re.Match[str]) -> str:
            normal_bind = m.group(1)
            if normal_bind:
                positions.append(normal_bind)
                return placeholder
            else:
                # this a post-compile bind
                positions.append(m.group(2))
                return m.group(0)

        self.string = re.sub(
            self._positional_pattern, find_position, self.string
        )

        if self.escaped_bind_names:
            reverse_escape = {v: k for k, v in self.escaped_bind_names.items()}
            assert len(self.escaped_bind_names) == len(reverse_escape)
            self.positiontup = [
                reverse_escape.get(name, name) for name in positions
            ]
        else:
            self.positiontup = positions

        if self._insertmanyvalues:
            positions = []

            single_values_expr = re.sub(
                self._positional_pattern,
                find_position,
                self._insertmanyvalues.single_values_expr,
            )
            insert_crud_params = [
                (
                    v[0],
                    v[1],
                    re.sub(self._positional_pattern, find_position, v[2]),
                    v[3],
                )
                for v in self._insertmanyvalues.insert_crud_params
            ]

            self._insertmanyvalues = self._insertmanyvalues._replace(
                single_values_expr=single_values_expr,
                insert_crud_params=insert_crud_params,
            )

    def _process_numeric(self):
        assert self._numeric_binds
        assert self.state is CompilerState.STRING_APPLIED

        num = 1
        param_pos: Dict[str, str] = {}
        order: Iterable[str]
        if self._insertmanyvalues and self._values_bindparam is not None:
            # bindparams that are not in values are always placed first.
            # this avoids the need of changing them when using executemany
            # values () ()
            order = itertools.chain(
                (
                    name
                    for name in self.bind_names.values()
                    if name not in self._values_bindparam
                ),
                self.bind_names.values(),
            )
        else:
            order = self.bind_names.values()

        for bind_name in order:
            if bind_name in param_pos:
                continue
            bind = self.binds[bind_name]
            if (
                bind in self.post_compile_params
                or bind in self.literal_execute_params
            ):
                # set to None to just mark the in positiontup, it will not
                # be replaced below.
                param_pos[bind_name] = None  # type: ignore
            else:
                ph = f"{self._numeric_binds_identifier_char}{num}"
                num += 1
                param_pos[bind_name] = ph

        self.next_numeric_pos = num

        self.positiontup = list(param_pos)
        if self.escaped_bind_names:
            len_before = len(param_pos)
            param_pos = {
                self.escaped_bind_names.get(name, name): pos
                for name, pos in param_pos.items()
            }
            assert len(param_pos) == len_before

        # Can't use format here since % chars are not escaped.
        self.string = self._pyformat_pattern.sub(
            lambda m: param_pos[m.group(1)], self.string
        )

        if self._insertmanyvalues:
            single_values_expr = (
                # format is ok here since single_values_expr includes only
                # place-holders
                self._insertmanyvalues.single_values_expr
                % param_pos
            )
            insert_crud_params = [
                (v[0], v[1], "%s", v[3])
                for v in self._insertmanyvalues.insert_crud_params
            ]

            self._insertmanyvalues = self._insertmanyvalues._replace(
                # This has the numbers (:1, :2)
                single_values_expr=single_values_expr,
                # The single binds are instead %s so they can be formatted
                insert_crud_params=insert_crud_params,
            )

    @util.memoized_property
    def _bind_processors(
        self,
    ) -> MutableMapping[
        str, Union[_BindProcessorType[Any], Sequence[_BindProcessorType[Any]]]
    ]:
        # mypy is not able to see the two value types as the above Union,
        # it just sees "object".  don't know how to resolve
        pass

    def is_subquery(self):
        return len(self.stack) > 1

    @property
    def sql_compiler(self) -> Self:
        pass

    def construct_expanded_state(
        self,
        params: Optional[_CoreSingleExecuteParams] = None,
        escape_names: bool = True,
    ) -> ExpandedState:
        """Return a new :class:`.ExpandedState` for a given parameter set.

        For queries that use "expanding" or other late-rendered parameters,
        this method will provide for both the finalized SQL string as well
        as the parameters that would be used for a particular parameter set.

        .. versionadded:: 2.0.0rc1

        """
        pass

    def construct_params(
        self,
        params: Optional[_CoreSingleExecuteParams] = None,
        extracted_parameters: Optional[Sequence[BindParameter[Any]]] = None,
        escape_names: bool = True,
        _group_number: Optional[int] = None,
        _check: bool = True,
        _no_postcompile: bool = False,
        _collected_params: _CoreSingleExecuteParams | None = None,
    ) -> _MutableCoreSingleExecuteParams:
        """return a dictionary of bind parameter keys and values"""
        if _collected_params is not None:
            assert not self._collect_params
        elif self._collect_params:
            _collected_params = self._collected_params

        if _collected_params:
            if not params:
                params = _collected_params
            else:
                params = {**_collected_params, **params}

        if self._render_postcompile and not _no_postcompile:
            assert self._post_compile_expanded_state is not None
            if not params:
                return dict(self._post_compile_expanded_state.parameters)
            else:
                raise exc.InvalidRequestError(
                    "can't construct new parameters when render_postcompile "
                    "is used; the statement is hard-linked to the original "
                    "parameters.  Use construct_expanded_state to generate a "
                    "new statement and parameters."
                )

        has_escaped_names = escape_names and bool(self.escaped_bind_names)

        if extracted_parameters:
            # related the bound parameters collected in the original cache key
            # to those collected in the incoming cache key.  They will not have
            # matching names but they will line up positionally in the same
            # way.   The parameters present in self.bind_names may be clones of
            # these original cache key params in the case of DML but the .key
            # will be guaranteed to match.
            if self.cache_key is None:
                raise exc.CompileError(
                    "This compiled object has no original cache key; "
                    "can't pass extracted_parameters to construct_params"
                )
            else:
                orig_extracted = self.cache_key[1]

            ckbm_tuple = self._cache_key_bind_match
            assert ckbm_tuple is not None
            ckbm, _ = ckbm_tuple
            resolved_extracted = {
                bind: extracted
                for b, extracted in zip(orig_extracted, extracted_parameters)
                for bind in ckbm[b]
            }
        else:
            resolved_extracted = None

        if params:
            pd = {}
            for bindparam, name in self.bind_names.items():
                escaped_name = (
                    self.escaped_bind_names.get(name, name)
                    if has_escaped_names
                    else name
                )

                if bindparam.key in params:
                    pd[escaped_name] = params[bindparam.key]
                elif name in params:
                    pd[escaped_name] = params[name]

                elif _check and bindparam.required:
                    if _group_number:
                        raise exc.InvalidRequestError(
                            "A value is required for bind parameter %r, "
                            "in parameter group %d"
                            % (bindparam.key, _group_number),
                            code="cd3x",
                        )
                    else:
                        raise exc.InvalidRequestError(
                            "A value is required for bind parameter %r"
                            % bindparam.key,
                            code="cd3x",
                        )
                else:
                    if resolved_extracted:
                        value_param = resolved_extracted.get(
                            bindparam, bindparam
                        )
                    else:
                        value_param = bindparam

                    if bindparam.callable:
                        pd[escaped_name] = value_param.effective_value
                    else:
                        pd[escaped_name] = value_param.value
            return pd
        else:
            pd = {}
            for bindparam, name in self.bind_names.items():
                escaped_name = (
                    self.escaped_bind_names.get(name, name)
                    if has_escaped_names
                    else name
                )

                if _check and bindparam.required:
                    if _group_number:
                        raise exc.InvalidRequestError(
                            "A value is required for bind parameter %r, "
                            "in parameter group %d"
                            % (bindparam.key, _group_number),
                            code="cd3x",
                        )
                    else:
                        raise exc.InvalidRequestError(
                            "A value is required for bind parameter %r"
                            % bindparam.key,
                            code="cd3x",
                        )

                if resolved_extracted:
                    value_param = resolved_extracted.get(bindparam, bindparam)
                else:
                    value_param = bindparam

                if bindparam.callable:
                    pd[escaped_name] = value_param.effective_value
                else:
                    pd[escaped_name] = value_param.value

            return pd

    @util.memoized_instancemethod
    def _get_set_input_sizes_lookup(self):
        dialect = self.dialect

        include_types = dialect.include_set_input_sizes
        exclude_types = dialect.exclude_set_input_sizes

        dbapi = dialect.dbapi

        def lookup_type(typ):
            dbtype = typ._unwrapped_dialect_impl(dialect).get_dbapi_type(dbapi)

            if (
                dbtype is not None
                and (exclude_types is None or dbtype not in exclude_types)
                and (include_types is None or dbtype in include_types)
            ):
                return dbtype
            else:
                return None

        inputsizes = {}

        literal_execute_params = self.literal_execute_params

        for bindparam in self.bind_names:
            if bindparam in literal_execute_params:
                continue

            if bindparam.type._is_tuple_type:
                inputsizes[bindparam] = [
                    lookup_type(typ)
                    for typ in cast(TupleType, bindparam.type).types
                ]
            else:
                inputsizes[bindparam] = lookup_type(bindparam.type)

        return inputsizes

    @property
    def params(self):
        """Return the bind param dictionary embedded into this
        compiled object, for those values that are present.

        .. seealso::

            :ref:`faq_sql_expression_string` - includes a usage example for
            debugging use cases.

        """
        return self.construct_params(_check=False)

    def _process_parameters_for_postcompile(
        self,
        parameters: _MutableCoreSingleExecuteParams,
        _populate_self: bool = False,
    ) -> ExpandedState:
        """handle special post compile parameters.

        These include:

        * "expanding" parameters -typically IN tuples that are rendered
          on a per-parameter basis for an otherwise fixed SQL statement string.

        * literal_binds compiled with the literal_execute flag.  Used for
          things like SQL Server "TOP N" where the driver does not accommodate
          N as a bound parameter.

        """

        expanded_parameters = {}
        new_positiontup: Optional[List[str]]

        pre_expanded_string = self._pre_expanded_string
        if pre_expanded_string is None:
            pre_expanded_string = self.string

        if self.positional:
            new_positiontup = []

            pre_expanded_positiontup = self._pre_expanded_positiontup
            if pre_expanded_positiontup is None:
                pre_expanded_positiontup = self.positiontup

        else:
            new_positiontup = pre_expanded_positiontup = None

        processors = self._bind_processors
        single_processors = cast(
            "Mapping[str, _BindProcessorType[Any]]", processors
        )
        tuple_processors = cast(
            "Mapping[str, Sequence[_BindProcessorType[Any]]]", processors
        )

        new_processors: Dict[str, _BindProcessorType[Any]] = {}

        replacement_expressions: Dict[str, Any] = {}
        to_update_sets: Dict[str, Any] = {}

        # notes:
        # *unescaped* parameter names in:
        # self.bind_names, self.binds, self._bind_processors, self.positiontup
        #
        # *escaped* parameter names in:
        # construct_params(), replacement_expressions

        numeric_positiontup: Optional[List[str]] = None

        if self.positional and pre_expanded_positiontup is not None:
            names: Iterable[str] = pre_expanded_positiontup
            if self._numeric_binds:
                numeric_positiontup = []
        else:
            names = self.bind_names.values()

        ebn = self.escaped_bind_names
        for name in names:
            escaped_name = ebn.get(name, name) if ebn else name
            parameter = self.binds[name]

            if parameter in self.literal_execute_params:
                if escaped_name not in replacement_expressions:
                    replacement_expressions[escaped_name] = (
                        self.render_literal_bindparam(
                            parameter,
                            render_literal_value=parameters.pop(escaped_name),
                        )
                    )
                continue

            if parameter in self.post_compile_params:
                if escaped_name in replacement_expressions:
                    to_update = to_update_sets[escaped_name]
                    values = None
                else:
                    # we are removing the parameter from parameters
                    # because it is a list value, which is not expected by
                    # TypeEngine objects that would otherwise be asked to
                    # process it. the single name is being replaced with
                    # individual numbered parameters for each value in the
                    # param.
                    #
                    # note we are also inserting *escaped* parameter names
                    # into the given dictionary.   default dialect will
                    # use these param names directly as they will not be
                    # in the escaped_bind_names dictionary.
                    values = parameters.pop(name)

                    leep_res = self._literal_execute_expanding_parameter(
                        escaped_name, parameter, values
                    )
                    (to_update, replacement_expr) = leep_res

                    to_update_sets[escaped_name] = to_update
                    replacement_expressions[escaped_name] = replacement_expr

                if not parameter.literal_execute:
                    parameters.update(to_update)
                    if parameter.type._is_tuple_type:
                        assert values is not None
                        new_processors.update(
                            (
                                "%s_%s_%s" % (name, i, j),
                                tuple_processors[name][j - 1],
                            )
                            for i, tuple_element in enumerate(values, 1)
                            for j, _ in enumerate(tuple_element, 1)
                            if name in tuple_processors
                            and tuple_processors[name][j - 1] is not None
                        )
                    else:
                        new_processors.update(
                            (key, single_processors[name])
                            for key, _ in to_update
                            if name in single_processors
                        )
                    if numeric_positiontup is not None:
                        numeric_positiontup.extend(
                            name for name, _ in to_update
                        )
                    elif new_positiontup is not None:
                        # to_update has escaped names, but that's ok since
                        # these are new names, that aren't in the
                        # escaped_bind_names dict.
                        new_positiontup.extend(name for name, _ in to_update)
                    expanded_parameters[name] = [
                        expand_key for expand_key, _ in to_update
                    ]
            elif new_positiontup is not None:
                new_positiontup.append(name)

        def process_expanding(m):
            key = m.group(1)
            expr = replacement_expressions[key]

            # if POSTCOMPILE included a bind_expression, render that
            # around each element
            if m.group(2):
                tok = m.group(2).split("~~")
                be_left, be_right = tok[1], tok[3]
                expr = ", ".join(
                    "%s%s%s" % (be_left, exp, be_right)
                    for exp in expr.split(", ")
                )
            return expr

        statement = re.sub(
            self._post_compile_pattern, process_expanding, pre_expanded_string
        )

        if numeric_positiontup is not None:
            assert new_positiontup is not None
            param_pos = {
                key: f"{self._numeric_binds_identifier_char}{num}"
                for num, key in enumerate(
                    numeric_positiontup, self.next_numeric_pos
                )
            }
            # Can't use format here since % chars are not escaped.
            statement = self._pyformat_pattern.sub(
                lambda m: param_pos[m.group(1)], statement
            )
            new_positiontup.extend(numeric_positiontup)

        expanded_state = ExpandedState(
            statement,
            parameters,
            new_processors,
            new_positiontup,
            expanded_parameters,
        )

        if _populate_self:
            # this is for the "render_postcompile" flag, which is not
            # otherwise used internally and is for end-user debugging and
            # special use cases.
            self._pre_expanded_string = pre_expanded_string
            self._pre_expanded_positiontup = pre_expanded_positiontup
            self.string = expanded_state.statement
            self.positiontup = (
                list(expanded_state.positiontup or ())
                if self.positional
                else None
            )
            self._post_compile_expanded_state = expanded_state

        return expanded_state

    @util.preload_module("sqlalchemy.engine.cursor")
    def _create_result_map(self):
        """utility method used for unit tests only."""
        pass

    # assigned by crud.py for insert/update statements
    _get_bind_name_for_col: _BindNameForColProtocol

    @util.memoized_property
    def _within_exec_param_key_getter(self) -> Callable[[Any], str]:
        pass

    @util.memoized_property
    @util.preload_module("sqlalchemy.engine.result")
    def _inserted_primary_key_from_lastrowid_getter(self):
        pass

    @util.memoized_property
    @util.preload_module("sqlalchemy.engine.result")
    def _inserted_primary_key_from_returning_getter(self):
        pass

    def default_from(self) -> str:
        """Called when a SELECT statement has no froms, and no FROM clause is
        to be appended.

        Gives Oracle Database a chance to tack on a ``FROM DUAL`` to the string
        output.

        """
        pass

    def visit_override_binds(self, override_binds, **kw):
        """SQL compile the nested element of an _OverrideBinds with
        bindparams swapped out.

        The _OverrideBinds is not normally expected to be compiled; it
        is meant to be used when an already cached statement is to be used,
        the compilation was already performed, and only the bound params should
        be swapped in at execution time.

        However, there are test cases that exericise this object, and
        additionally the ORM subquery loader is known to feed in expressions
        which include this construct into new queries (discovered in #11173),
        so it has to do the right thing at compile time as well.

        """
        pass

    def visit_grouping(self, grouping, asfrom=False, **kwargs):
        pass

    def visit_select_statement_grouping(self, grouping, **kwargs):
        pass

    def visit_label_reference(
        self, element, within_columns_clause=False, **kwargs
    ):
        pass

    def visit_textual_label_reference(
        self, element, within_columns_clause=False, **kwargs
    ):
        pass

    def visit_label(
        self,
        label,
        add_to_result_map=None,
        within_label_clause=False,
        within_columns_clause=False,
        render_label_as_label=None,
        result_map_targets=(),
        within_tstring=False,
        **kw,
    ):
        pass

    def _fallback_column_name(self, column):
        raise exc.CompileError(
            "Cannot compile Column object until its 'name' is assigned."
        )

    def visit_lambda_element(self, element, **kw):
        pass

    def visit_column(
        self,
        column: ColumnClause[Any],
        add_to_result_map: Optional[_ResultMapAppender] = None,
        include_table: bool = True,
        result_map_targets: Tuple[Any, ...] = (),
        ambiguous_table_name_map: Optional[_AmbiguousTableNameMap] = None,
        **kwargs: Any,
    ) -> str:
        pass

    def visit_collation(self, element, **kw):
        pass

    def visit_fromclause(self, fromclause, **kwargs):
        pass

    def visit_index(self, index, **kwargs):
        pass

    def visit_typeclause(self, typeclause, **kw):
        pass

    def post_process_text(self, text):
        pass

    def escape_literal_column(self, text):
        pass

    def visit_textclause(self, textclause, add_to_result_map=None, **kw):
        pass

    def visit_tstring(self, tstring, add_to_result_map=None, **kw):
        pass

    def visit_textual_select(
        self, taf, compound_index=None, asfrom=False, **kw
    ):
        pass

    def visit_null(self, expr: Null, **kw: Any) -> str:
        pass

    def visit_true(self, expr: True_, **kw: Any) -> str:
        if self.dialect.supports_native_boolean:
            return "true"
        else:
            return "1"

    def visit_false(self, expr: False_, **kw: Any) -> str:
        if self.dialect.supports_native_boolean:
            return "false"
        else:
            return "0"

    def _generate_delimited_list(self, elements, separator, **kw):
        pass

    def _generate_delimited_and_list(self, clauses, **kw):
        pass

    def visit_tuple(self, clauselist, **kw):
        pass

    def visit_element_list(self, element, **kw):
        pass

    def visit_order_by_list(self, element, **kw):
        pass

    def visit_clauselist(self, clauselist, **kw):
        pass

    def visit_expression_clauselist(self, clauselist, **kw):
        pass

    def visit_case(self, clause, **kwargs):
        pass

    def visit_type_coerce(self, type_coerce, **kw):
        pass

    def visit_cast(self, cast, **kwargs):
        pass

    def visit_frame_clause(self, frameclause, **kw):

        pass

    def visit_over(self, over, **kwargs):
        pass

    def visit_withingroup(self, withingroup, **kwargs):
        pass

    def visit_funcfilter(self, funcfilter, **kwargs):
        pass

    def visit_aggregateorderby(self, aggregateorderby, **kwargs):
        pass

    def visit_aggregate_orderby_inline(self, element, **kw):
        pass

    def visit_aggregate_strings_func(self, fn, *, use_function_name, **kw):
        # aggreagate_order_by attribute is present if visit_function
        # gave us a Function with aggregate_orderby_inline() as the inner
        # contents
        pass

    def visit_extract(self, extract, **kwargs):
        pass

    def visit_scalar_function_column(self, element, **kw):
        pass

    def visit_function(
        self,
        func: Function[Any],
        add_to_result_map: Optional[_ResultMapAppender] = None,
        **kwargs: Any,
    ) -> str:
        pass

    def visit_next_value_func(self, next_value, **kw):
        pass

    def visit_sequence(self, sequence, **kw):
        raise NotImplementedError(
            "Dialect '%s' does not support sequence increments."
            % self.dialect.name
        )

    def function_argspec(self, func: Function[Any], **kwargs: Any) -> str:
        pass

    def visit_compound_select(
        self, cs, asfrom=False, compound_index=None, **kwargs
    ):
        pass

    def _row_limit_clause(self, cs, **kwargs):
        pass

    def _get_operator_dispatch(self, operator_, qualifier1, qualifier2):
        pass

    def _get_custom_operator_dispatch(self, operator_, qualifier1):
        pass

    def visit_unary(
        self, unary, add_to_result_map=None, result_map_targets=(), **kw
    ):
        pass

    def visit_truediv_binary(self, binary, operator, **kw):
        pass

    def visit_floordiv_binary(self, binary, operator, **kw):
        pass

    def visit_is_true_unary_operator(self, element, operator, **kw):
        pass

    def visit_is_false_unary_operator(self, element, operator, **kw):
        pass

    def visit_not_match_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_in_op_binary(self, binary, operator, **kw):
        # The brackets are required in the NOT IN operation because the empty
        # case is handled using the form "(col NOT IN (null) OR 1 = 1)".
        # The presence of the OR makes the brackets required.
        pass

    def visit_empty_set_op_expr(self, type_, expand_op, **kw):
        if expand_op is operators.not_in_op:
            if len(type_) > 1:
                return "(%s)) OR (1 = 1" % (
                    ", ".join("NULL" for element in type_)
                )
            else:
                return "NULL) OR (1 = 1"
        elif expand_op is operators.in_op:
            if len(type_) > 1:
                return "(%s)) AND (1 != 1" % (
                    ", ".join("NULL" for element in type_)
                )
            else:
                return "NULL) AND (1 != 1"
        else:
            return self.visit_empty_set_expr(type_)

    def visit_empty_set_expr(self, element_types, **kw):
        raise NotImplementedError(
            "Dialect '%s' does not support empty set expression."
            % self.dialect.name
        )

    def _literal_execute_expanding_parameter_literal_binds(
        self, parameter, values, bind_expression_template=None
    ):
        typ_dialect_impl = parameter.type._unwrapped_dialect_impl(self.dialect)

        if not values:
            # empty IN expression.  note we don't need to use
            # bind_expression_template here because there are no
            # expressions to render.

            if typ_dialect_impl._is_tuple_type:
                replacement_expression = (
                    "VALUES " if self.dialect.tuple_in_values else ""
                ) + self.visit_empty_set_op_expr(
                    parameter.type.types, parameter.expand_op
                )

            else:
                replacement_expression = self.visit_empty_set_op_expr(
                    [parameter.type], parameter.expand_op
                )

        elif typ_dialect_impl._is_tuple_type or (
            typ_dialect_impl._isnull
            and isinstance(values[0], collections_abc.Sequence)
            and not isinstance(values[0], (str, bytes))
        ):
            if typ_dialect_impl._has_bind_expression:
                raise NotImplementedError(
                    "bind_expression() on TupleType not supported with "
                    "literal_binds"
                )

            replacement_expression = (
                "VALUES " if self.dialect.tuple_in_values else ""
            ) + ", ".join(
                "(%s)"
                % (
                    ", ".join(
                        self.render_literal_value(value, param_type)
                        for value, param_type in zip(
                            tuple_element, parameter.type.types
                        )
                    )
                )
                for i, tuple_element in enumerate(values)
            )
        else:
            if bind_expression_template:
                post_compile_pattern = self._post_compile_pattern
                m = post_compile_pattern.search(bind_expression_template)
                assert m and m.group(
                    2
                ), "unexpected format for expanding parameter"

                tok = m.group(2).split("~~")
                be_left, be_right = tok[1], tok[3]
                replacement_expression = ", ".join(
                    "%s%s%s"
                    % (
                        be_left,
                        self.render_literal_value(value, parameter.type),
                        be_right,
                    )
                    for value in values
                )
            else:
                replacement_expression = ", ".join(
                    self.render_literal_value(value, parameter.type)
                    for value in values
                )

        return (), replacement_expression

    def _literal_execute_expanding_parameter(self, name, parameter, values):
        if parameter.literal_execute:
            return self._literal_execute_expanding_parameter_literal_binds(
                parameter, values
            )

        dialect = self.dialect
        typ_dialect_impl = parameter.type._unwrapped_dialect_impl(dialect)

        if self._numeric_binds:
            bind_template = self.compilation_bindtemplate
        else:
            bind_template = self.bindtemplate

        if (
            self.dialect._bind_typing_render_casts
            and typ_dialect_impl.render_bind_cast
        ):

            def _render_bindtemplate(name):
                return self.render_bind_cast(
                    parameter.type,
                    typ_dialect_impl,
                    bind_template % {"name": name},
                )

        else:

            def _render_bindtemplate(name):
                return bind_template % {"name": name}

        if not values:
            to_update = []
            if typ_dialect_impl._is_tuple_type:
                replacement_expression = self.visit_empty_set_op_expr(
                    parameter.type.types, parameter.expand_op
                )
            else:
                replacement_expression = self.visit_empty_set_op_expr(
                    [parameter.type], parameter.expand_op
                )

        elif typ_dialect_impl._is_tuple_type or (
            typ_dialect_impl._isnull
            and isinstance(values[0], collections_abc.Sequence)
            and not isinstance(values[0], (str, bytes))
        ):
            assert not typ_dialect_impl._is_array
            to_update = [
                ("%s_%s_%s" % (name, i, j), value)
                for i, tuple_element in enumerate(values, 1)
                for j, value in enumerate(tuple_element, 1)
            ]

            replacement_expression = (
                "VALUES " if dialect.tuple_in_values else ""
            ) + ", ".join(
                "(%s)"
                % (
                    ", ".join(
                        _render_bindtemplate(
                            to_update[i * len(tuple_element) + j][0]
                        )
                        for j, value in enumerate(tuple_element)
                    )
                )
                for i, tuple_element in enumerate(values)
            )
        else:
            to_update = [
                ("%s_%s" % (name, i), value)
                for i, value in enumerate(values, 1)
            ]
            replacement_expression = ", ".join(
                _render_bindtemplate(key) for key, value in to_update
            )

        return to_update, replacement_expression

    def visit_binary(
        self,
        binary,
        override_operator=None,
        eager_grouping=False,
        from_linter=None,
        lateral_from_linter=None,
        **kw,
    ):
        pass

    def visit_function_as_comparison_op_binary(self, element, operator, **kw):
        pass

    def visit_mod_binary(self, binary, operator, **kw):
        pass

    def visit_custom_op_binary(self, element, operator, **kw):
        pass

    def visit_custom_op_unary_operator(self, element, operator, **kw):
        pass

    def visit_custom_op_unary_modifier(self, element, operator, **kw):
        pass

    def _generate_generic_binary(
        self,
        binary: BinaryExpression[Any],
        opstring: str,
        eager_grouping: bool = False,
        **kw: Any,
    ) -> str:
        pass

    def _generate_generic_unary_operator(self, unary, opstring, **kw):
        pass

    def _generate_generic_unary_modifier(self, unary, opstring, **kw):
        pass

    @util.memoized_property
    def _like_percent_literal(self):
        pass

    def visit_ilike_case_insensitive_operand(self, element, **kw):
        pass

    def visit_contains_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_contains_op_binary(self, binary, operator, **kw):
        pass

    def visit_icontains_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_icontains_op_binary(self, binary, operator, **kw):
        pass

    def visit_startswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_startswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_istartswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_istartswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_endswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_endswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_iendswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_iendswith_op_binary(self, binary, operator, **kw):
        pass

    def visit_like_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_like_op_binary(self, binary, operator, **kw):
        pass

    def visit_ilike_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_ilike_op_binary(self, binary, operator, **kw):
        pass

    def visit_between_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_between_op_binary(self, binary, operator, **kw):
        pass

    def visit_regexp_match_op_binary(
        self, binary: BinaryExpression[Any], operator: Any, **kw: Any
    ) -> str:
        raise exc.CompileError(
            "%s dialect does not support regular expressions"
            % self.dialect.name
        )

    def visit_not_regexp_match_op_binary(
        self, binary: BinaryExpression[Any], operator: Any, **kw: Any
    ) -> str:
        raise exc.CompileError(
            "%s dialect does not support regular expressions"
            % self.dialect.name
        )

    def visit_regexp_replace_op_binary(
        self, binary: BinaryExpression[Any], operator: Any, **kw: Any
    ) -> str:
        raise exc.CompileError(
            "%s dialect does not support regular expression replacements"
            % self.dialect.name
        )

    def visit_dmltargetcopy(self, element, *, bindmarkers=None, **kw):
        pass

    def visit_bindparam(
        self,
        bindparam,
        within_columns_clause=False,
        literal_binds=False,
        skip_bind_expression=False,
        literal_execute=False,
        render_postcompile=False,
        is_upsert_set=False,
        **kwargs,
    ):
        # Detect parametrized bindparams in upsert SET clause for issue #13130
        pass

    def render_bind_cast(self, type_, dbapi_type, sqltext):
        raise NotImplementedError()

    def render_literal_bindparam(
        self,
        bindparam,
        render_literal_value=NO_ARG,
        bind_expression_template=None,
        **kw,
    ):
        if render_literal_value is not NO_ARG:
            value = render_literal_value
        else:
            if bindparam.value is None and bindparam.callable is None:
                op = kw.get("_binary_op", None)
                if op and op not in (operators.is_, operators.is_not):
                    util.warn_limited(
                        "Bound parameter '%s' rendering literal NULL in a SQL "
                        "expression; comparisons to NULL should not use "
                        "operators outside of 'is' or 'is not'",
                        (bindparam.key,),
                    )
                return self.process(sqltypes.NULLTYPE, **kw)
            value = bindparam.effective_value

        if bindparam.expanding:
            leep = self._literal_execute_expanding_parameter_literal_binds
            to_update, replacement_expr = leep(
                bindparam,
                value,
                bind_expression_template=bind_expression_template,
            )
            return replacement_expr
        else:
            return self.render_literal_value(value, bindparam.type)

    def render_literal_value(
        self, value: Any, type_: sqltypes.TypeEngine[Any]
    ) -> str:
        """Render the value of a bind parameter as a quoted literal.

        This is used for statement sections that do not accept bind parameters
        on the target driver/database.

        This should be implemented by subclasses using the quoting services
        of the DBAPI.

        """

        if value is None and not type_.should_evaluate_none:
            # issue #10535 - handle NULL in the compiler without placing
            # this onto each type, except for "evaluate None" types
            # (e.g. JSON)
            return self.process(elements.Null._instance())

        processor = type_._cached_literal_processor(self.dialect)
        if processor:
            try:
                return processor(value)
            except Exception as e:
                raise exc.CompileError(
                    f"Could not render literal value "
                    f'"{sql_util._repr_single_value(value)}" '
                    f"with datatype "
                    f"{type_}; see parent stack trace for "
                    "more detail."
                ) from e

        else:
            raise exc.CompileError(
                f"No literal value renderer is available for literal value "
                f'"{sql_util._repr_single_value(value)}" '
                f"with datatype {type_}"
            )

    def _truncate_bindparam(self, bindparam):
        pass

    def _truncated_identifier(
        self, ident_class: str, name: _truncated_label
    ) -> str:
        pass

    def _anonymize(self, name: str) -> str:
        pass

    def bindparam_string(
        self,
        name: str,
        post_compile: bool = False,
        expanding: bool = False,
        escaped_from: Optional[str] = None,
        bindparam_type: Optional[TypeEngine[Any]] = None,
        accumulate_bind_names: Optional[Set[str]] = None,
        visited_bindparam: Optional[List[str]] = None,
        **kw: Any,
    ) -> str:
        # TODO: accumulate_bind_names is passed by crud.py to gather
        # names on a per-value basis, visited_bindparam is passed by
        # visit_insert() to collect all parameters in the statement.
        # see if this gathering can be simplified somehow
        pass

    def _dispatch_independent_ctes(self, stmt, kw):
        pass

    def visit_cte(
        self,
        cte: CTE,
        asfrom: bool = False,
        ashint: bool = False,
        fromhints: Optional[_FromHintsType] = None,
        visiting_cte: Optional[CTE] = None,
        from_linter: Optional[FromLinter] = None,
        cte_opts: selectable._CTEOpts = selectable._CTEOpts(False),
        **kwargs: Any,
    ) -> Optional[str]:
        pass

    def visit_table_valued_alias(self, element, **kw):
        pass

    def visit_table_valued_column(self, element, **kw):
        pass

    def visit_alias(
        self,
        alias,
        asfrom=False,
        ashint=False,
        iscrud=False,
        fromhints=None,
        subquery=False,
        lateral=False,
        enclosing_alias=None,
        from_linter=None,
        within_tstring=False,
        **kwargs,
    ):
        pass

    def visit_subquery(self, subquery, **kw):
        pass

    def visit_lateral(self, lateral_, **kw):
        pass

    def visit_tablesample(self, tablesample, asfrom=False, **kw):
        pass

    def _render_values(self, element, **kw):
        pass

    def visit_values(
        self, element, asfrom=False, from_linter=None, visiting_cte=None, **kw
    ):

        pass

    def visit_scalar_values(self, element, **kw):
        pass

    def get_render_as_alias_suffix(self, alias_name_text):
        pass

    def _add_to_result_map(
        self,
        keyname: str,
        name: str,
        objects: Tuple[Any, ...],
        type_: TypeEngine[Any],
    ) -> None:

        # note objects must be non-empty for cursor.py to handle the
        # collection properly
        pass

    def _label_returning_column(
        self, stmt, column, populate_result_map, column_clause_args=None, **kw
    ):
        """Render a column with necessary labels inside of a RETURNING clause.

        This method is provided for individual dialects in place of calling
        the _label_select_column method directly, so that the two use cases
        of RETURNING vs. SELECT can be disambiguated going forward.

        .. versionadded:: 1.4.21

        """
        pass

    def _label_select_column(
        self,
        select,
        column,
        populate_result_map,
        asfrom,
        column_clause_args,
        name=None,
        proxy_name=None,
        fallback_label_name=None,
        within_columns_clause=True,
        column_is_repeated=False,
        need_column_expressions=False,
        include_table=True,
    ):
        """produce labeled columns present in a select()."""
        pass

    def format_from_hint_text(self, sqltext, table, hint, iscrud):
        pass

    def get_select_hint_text(self, byfroms):
        pass

    def get_from_hint_text(
        self, table: FromClause, text: Optional[str]
    ) -> Optional[str]:
        pass

    def get_crud_hint_text(self, table, text):
        pass

    def get_statement_hint_text(self, hint_texts):
        pass

    _default_stack_entry: _CompilerStackEntry

    if not typing.TYPE_CHECKING:
        _default_stack_entry = util.immutabledict(
            [("correlate_froms", frozenset()), ("asfrom_froms", frozenset())]
        )

    def _display_froms_for_select(
        self, select_stmt, asfrom, lateral=False, **kw
    ):
        # utility method to help external dialects
        # get the correct from list for a select.
        # specifically the oracle dialect needs this feature
        # right now.
        pass

    translate_select_structure: Any = None
    """if not ``None``, should be a callable which accepts ``(select_stmt,
    **kw)`` and returns a select object.   this is used for structural changes
    mostly to accommodate for LIMIT/OFFSET schemes

    """

    def visit_select(
        self,
        select_stmt,
        asfrom=False,
        insert_into=False,
        fromhints=None,
        compound_index=None,
        select_wraps_for=None,
        lateral=False,
        from_linter=None,
        **kwargs,
    ):
        pass

    def _setup_select_hints(
        self, select: Select[Unpack[TupleAny]]
    ) -> Tuple[str, _FromHintsType]:
        pass

    def _setup_select_stack(
        self, select, compile_state, entry, asfrom, lateral, compound_index
    ):
        pass

    def _compose_select_body(
        self,
        text,
        select,
        compile_state,
        inner_columns,
        froms,
        byfrom,
        toplevel,
        kwargs,
    ):
        pass

    def _generate_prefixes(self, stmt, prefixes, **kw):
        pass

    def _render_cte_clause(
        self,
        nesting_level=None,
        include_following_stack=False,
    ):
        """
        include_following_stack
            Also render the nesting CTEs on the next stack. Useful for
            SQL structures like UNION or INSERT that can wrap SELECT
            statements containing nesting CTEs.
        """
        pass

    def get_cte_preamble(self, recursive):
        pass

    def get_select_precolumns(self, select: Select[Any], **kw: Any) -> str:
        """Called when building a ``SELECT`` statement, position is just
        before column list.

        """
        pass

    def group_by_clause(self, select, **kw):
        """allow dialects to customize how GROUP BY is rendered."""
        pass

    def order_by_clause(self, select, **kw):
        """allow dialects to customize how ORDER BY is rendered."""
        pass

    def for_update_clause(self, select, **kw):
        pass

    def returning_clause(
        self,
        stmt: UpdateBase,
        returning_cols: Sequence[_ColumnsClauseElement],
        *,
        populate_result_map: bool,
        **kw: Any,
    ) -> str:
        pass

    def limit_clause(self, select, **kw):
        pass

    def fetch_clause(
        self,
        select,
        fetch_clause=None,
        require_offset=False,
        use_literal_execute_for_simple_int=False,
        **kw,
    ):
        pass

    def visit_table(
        self,
        table,
        asfrom=False,
        iscrud=False,
        ashint=False,
        fromhints=None,
        use_schema=True,
        from_linter=None,
        ambiguous_table_name_map=None,
        enclosing_alias=None,
        within_tstring=False,
        **kwargs,
    ):
        pass

    def visit_join(self, join, asfrom=False, from_linter=None, **kwargs):
        pass

    def _setup_crud_hints(self, stmt, table_text):
        pass

    # within the realm of "insertmanyvalues sentinel columns",
    # these lookups match different kinds of Column() configurations
    # to specific backend capabilities.  they are broken into two
    # lookups, one for autoincrement columns and the other for non
    # autoincrement columns
    _sentinel_col_non_autoinc_lookup = util.immutabledict(
        {
            _SentinelDefaultCharacterization.CLIENTSIDE: (
                InsertmanyvaluesSentinelOpts._SUPPORTED_OR_NOT
            ),
            _SentinelDefaultCharacterization.SENTINEL_DEFAULT: (
                InsertmanyvaluesSentinelOpts._SUPPORTED_OR_NOT
            ),
            _SentinelDefaultCharacterization.NONE: (
                InsertmanyvaluesSentinelOpts._SUPPORTED_OR_NOT
            ),
            _SentinelDefaultCharacterization.IDENTITY: (
                InsertmanyvaluesSentinelOpts.IDENTITY
            ),
            _SentinelDefaultCharacterization.SEQUENCE: (
                InsertmanyvaluesSentinelOpts.SEQUENCE
            ),
            _SentinelDefaultCharacterization.MONOTONIC_FUNCTION: (
                InsertmanyvaluesSentinelOpts.MONOTONIC_FUNCTION
            ),
        }
    )
    _sentinel_col_autoinc_lookup = _sentinel_col_non_autoinc_lookup.union(
        {
            _SentinelDefaultCharacterization.NONE: (
                InsertmanyvaluesSentinelOpts.AUTOINCREMENT
            ),
        }
    )

    def _get_sentinel_column_for_table(
        self, table: Table
    ) -> Optional[Sequence[Column[Any]]]:
        """given a :class:`.Table`, return a usable sentinel column or
        columns for this dialect if any.

        Return None if no sentinel columns could be identified, or raise an
        error if a column was marked as a sentinel explicitly but isn't
        compatible with this dialect.

        """
        pass

    def _deliver_insertmanyvalues_batches(
        self,
        statement: str,
        parameters: _DBAPIMultiExecuteParams,
        compiled_parameters: List[_MutableCoreSingleExecuteParams],
        generic_setinputsizes: Optional[_GenericSetInputSizesType],
        batch_size: int,
        sort_by_parameter_order: bool,
        schema_translate_map: Optional[SchemaTranslateMapType],
    ) -> Iterator[_InsertManyValuesBatch]:
        imv = self._insertmanyvalues
        assert imv is not None

        if not imv.sentinel_param_keys:
            _sentinel_from_params = None
        else:
            _sentinel_from_params = operator.itemgetter(
                *imv.sentinel_param_keys
            )

        lenparams = len(parameters)
        if imv.is_default_expr and not self.dialect.supports_default_metavalue:
            # backend doesn't support
            # INSERT INTO table (pk_col) VALUES (DEFAULT), (DEFAULT), ...
            # at the moment this is basically SQL Server due to
            # not being able to use DEFAULT for identity column
            # just yield out that many single statements!  still
            # faster than a whole connection.execute() call ;)
            #
            # note we still are taking advantage of the fact that we know
            # we are using RETURNING.   The generalized approach of fetching
            # cursor.lastrowid etc. still goes through the more heavyweight
            # "ExecutionContext per statement" system as it isn't usable
            # as a generic "RETURNING" approach
            use_row_at_a_time = True
            downgraded = False
        elif not self.dialect.supports_multivalues_insert or (
            sort_by_parameter_order
            and self._result_columns
            and (
                imv.sentinel_columns is None
                or (
                    imv.includes_upsert_behaviors
                    and not imv.embed_values_counter
                )
            )
        ):
            # deterministic order was requested and the compiler could
            # not organize sentinel columns for this dialect/statement.
            # use row at a time.  Note: if embed_values_counter is True,
            # the counter itself provides the ordering capability we need,
            # so we can use batch mode even with upsert behaviors.
            use_row_at_a_time = True
            downgraded = True
        elif (
            imv.has_upsert_bound_parameters
            and not imv.embed_values_counter
            and self._result_columns
        ):
            # For upsert behaviors (ON CONFLICT DO UPDATE, etc.) with RETURNING
            # and parametrized bindparams in the SET clause, we must use
            # row-at-a-time. Batching multiple rows in a single statement
            # doesn't work when the SET clause contains bound parameters that
            # will receive different values per row, as there's only one SET
            # clause per statement. See issue #13130.
            use_row_at_a_time = True
            downgraded = True
        else:
            use_row_at_a_time = False
            downgraded = False

        if use_row_at_a_time:
            for batchnum, (param, compiled_param) in enumerate(
                cast(
                    "Sequence[Tuple[_DBAPISingleExecuteParams, _MutableCoreSingleExecuteParams]]",  # noqa: E501
                    zip(parameters, compiled_parameters),
                ),
                1,
            ):
                yield _InsertManyValuesBatch(
                    statement,
                    param,
                    generic_setinputsizes,
                    [param],
                    (
                        [_sentinel_from_params(compiled_param)]
                        if _sentinel_from_params
                        else []
                    ),
                    1,
                    batchnum,
                    lenparams,
                    sort_by_parameter_order,
                    downgraded,
                )
            return

        if schema_translate_map:
            rst = functools.partial(
                self.preparer._render_schema_translates,
                schema_translate_map=schema_translate_map,
            )
        else:
            rst = None

        imv_single_values_expr = imv.single_values_expr
        if rst:
            imv_single_values_expr = rst(imv_single_values_expr)

        executemany_values = f"({imv_single_values_expr})"
        statement = statement.replace(executemany_values, "__EXECMANY_TOKEN__")

        # Use optional insertmanyvalues_max_parameters
        # to further shrink the batch size so that there are no more than
        # insertmanyvalues_max_parameters params.
        # Currently used by SQL Server, which limits statements to 2100 bound
        # parameters (actually 2099).
        max_params = self.dialect.insertmanyvalues_max_parameters
        if max_params:
            total_num_of_params = len(self.bind_names)
            num_params_per_batch = len(imv.insert_crud_params)
            num_params_outside_of_batch = (
                total_num_of_params - num_params_per_batch
            )
            batch_size = min(
                batch_size,
                (
                    (max_params - num_params_outside_of_batch)
                    // num_params_per_batch
                ),
            )

        batches = cast("List[Sequence[Any]]", list(parameters))
        compiled_batches = cast(
            "List[Sequence[Any]]", list(compiled_parameters)
        )

        processed_setinputsizes: Optional[_GenericSetInputSizesType] = None
        batchnum = 1
        total_batches = lenparams // batch_size + (
            1 if lenparams % batch_size else 0
        )

        insert_crud_params = imv.insert_crud_params
        assert insert_crud_params is not None

        if rst:
            insert_crud_params = [
                (col, key, rst(expr), st)
                for col, key, expr, st in insert_crud_params
            ]

        escaped_bind_names: Mapping[str, str]
        expand_pos_lower_index = expand_pos_upper_index = 0

        if not self.positional:
            if self.escaped_bind_names:
                escaped_bind_names = self.escaped_bind_names
            else:
                escaped_bind_names = {}

            all_keys = set(parameters[0])

            def apply_placeholders(keys, formatted):
                for key in keys:
                    key = escaped_bind_names.get(key, key)
                    formatted = formatted.replace(
                        self.bindtemplate % {"name": key},
                        self.bindtemplate
                        % {"name": f"{key}__EXECMANY_INDEX__"},
                    )
                return formatted

            if imv.embed_values_counter:
                imv_values_counter = ", _IMV_VALUES_COUNTER"
            else:
                imv_values_counter = ""
            formatted_values_clause = f"""({', '.join(
                apply_placeholders(bind_keys, formatted)
                for _, _, formatted, bind_keys in insert_crud_params
            )}{imv_values_counter})"""

            keys_to_replace = all_keys.intersection(
                escaped_bind_names.get(key, key)
                for _, _, _, bind_keys in insert_crud_params
                for key in bind_keys
            )
            base_parameters = {
                key: parameters[0][key]
                for key in all_keys.difference(keys_to_replace)
            }

            executemany_values_w_comma = ""
        else:
            formatted_values_clause = ""
            keys_to_replace = set()
            base_parameters = {}

            if imv.embed_values_counter:
                executemany_values_w_comma = (
                    f"({imv_single_values_expr}, _IMV_VALUES_COUNTER), "
                )
            else:
                executemany_values_w_comma = f"({imv_single_values_expr}), "

            all_names_we_will_expand: Set[str] = set()
            for elem in imv.insert_crud_params:
                all_names_we_will_expand.update(elem[3])

            # get the start and end position in a particular list
            # of parameters where we will be doing the "expanding".
            # statements can have params on either side or both sides,
            # given RETURNING and CTEs
            if all_names_we_will_expand:
                positiontup = self.positiontup
                assert positiontup is not None

                all_expand_positions = {
                    idx
                    for idx, name in enumerate(positiontup)
                    if name in all_names_we_will_expand
                }
                expand_pos_lower_index = min(all_expand_positions)
                expand_pos_upper_index = max(all_expand_positions) + 1
                assert (
                    len(all_expand_positions)
                    == expand_pos_upper_index - expand_pos_lower_index
                )

            if self._numeric_binds:
                escaped = re.escape(self._numeric_binds_identifier_char)
                executemany_values_w_comma = re.sub(
                    rf"{escaped}\d+", "%s", executemany_values_w_comma
                )

        while batches:
            batch = batches[0:batch_size]
            compiled_batch = compiled_batches[0:batch_size]

            batches[0:batch_size] = []
            compiled_batches[0:batch_size] = []

            if batches:
                current_batch_size = batch_size
            else:
                current_batch_size = len(batch)

            if generic_setinputsizes:
                # if setinputsizes is present, expand this collection to
                # suit the batch length as well
                # currently this will be mssql+pyodbc for internal dialects
                processed_setinputsizes = [
                    (new_key, len_, typ)
                    for new_key, len_, typ in (
                        (f"{key}_{index}", len_, typ)
                        for index in range(current_batch_size)
                        for key, len_, typ in generic_setinputsizes
                    )
                ]

            replaced_parameters: Any
            if self.positional:
                num_ins_params = imv.num_positional_params_counted

                batch_iterator: Iterable[Sequence[Any]]
                extra_params_left: Sequence[Any]
                extra_params_right: Sequence[Any]

                if num_ins_params == len(batch[0]):
                    extra_params_left = extra_params_right = ()
                    batch_iterator = batch
                else:
                    extra_params_left = batch[0][:expand_pos_lower_index]
                    extra_params_right = batch[0][expand_pos_upper_index:]
                    batch_iterator = (
                        b[expand_pos_lower_index:expand_pos_upper_index]
                        for b in batch
                    )

                if imv.embed_values_counter:
                    expanded_values_string = (
                        "".join(
                            executemany_values_w_comma.replace(
                                "_IMV_VALUES_COUNTER", str(i)
                            )
                            for i, _ in enumerate(batch)
                        )
                    )[:-2]
                else:
                    expanded_values_string = (
                        (executemany_values_w_comma * current_batch_size)
                    )[:-2]

                if self._numeric_binds and num_ins_params > 0:
                    # numeric will always number the parameters inside of
                    # VALUES (and thus order self.positiontup) to be higher
                    # than non-VALUES parameters, no matter where in the
                    # statement those non-VALUES parameters appear (this is
                    # ensured in _process_numeric by numbering first all
                    # params that are not in _values_bindparam)
                    # therefore all extra params are always
                    # on the left side and numbered lower than the VALUES
                    # parameters
                    assert not extra_params_right

                    start = expand_pos_lower_index + 1
                    end = num_ins_params * (current_batch_size) + start

                    # need to format here, since statement may contain
                    # unescaped %, while values_string contains just (%s, %s)
                    positions = tuple(
                        f"{self._numeric_binds_identifier_char}{i}"
                        for i in range(start, end)
                    )
                    expanded_values_string = expanded_values_string % positions

                replaced_statement = statement.replace(
                    "__EXECMANY_TOKEN__", expanded_values_string
                )

                replaced_parameters = tuple(
                    itertools.chain.from_iterable(batch_iterator)
                )

                replaced_parameters = (
                    extra_params_left
                    + replaced_parameters
                    + extra_params_right
                )

            else:
                replaced_values_clauses = []
                replaced_parameters = base_parameters.copy()

                for i, param in enumerate(batch):
                    fmv = formatted_values_clause.replace(
                        "EXECMANY_INDEX__", str(i)
                    )
                    if imv.embed_values_counter:
                        fmv = fmv.replace("_IMV_VALUES_COUNTER", str(i))

                    replaced_values_clauses.append(fmv)
                    replaced_parameters.update(
                        {f"{key}__{i}": param[key] for key in keys_to_replace}
                    )

                replaced_statement = statement.replace(
                    "__EXECMANY_TOKEN__",
                    ", ".join(replaced_values_clauses),
                )

            yield _InsertManyValuesBatch(
                replaced_statement,
                replaced_parameters,
                processed_setinputsizes,
                batch,
                (
                    [_sentinel_from_params(cb) for cb in compiled_batch]
                    if _sentinel_from_params
                    else []
                ),
                current_batch_size,
                batchnum,
                total_batches,
                sort_by_parameter_order,
                False,
            )
            batchnum += 1

    def visit_insert(
        self, insert_stmt, visited_bindparam=None, visiting_cte=None, **kw
    ):
        pass

    def update_tables_clause(self, update_stmt, from_table, extra_froms, **kw):
        """Provide a hook to override the initial table clause
        in an UPDATE statement.

        MySQL overrides this.

        """
        pass

    def update_from_clause(
        self, update_stmt, from_table, extra_froms, from_hints, **kw
    ):
        """Provide a hook to override the generation of an
        UPDATE..FROM clause.
        MySQL and MSSQL override this.
        """
        raise NotImplementedError(
            "This backend does not support multiple-table "
            "criteria within UPDATE"
        )

    def update_post_criteria_clause(
        self, update_stmt: Update, **kw: Any
    ) -> Optional[str]:
        """provide a hook to override generation after the WHERE criteria
        in an UPDATE statement

        .. versionadded:: 2.1

        """
        pass

    def delete_post_criteria_clause(
        self, delete_stmt: Delete, **kw: Any
    ) -> Optional[str]:
        """provide a hook to override generation after the WHERE criteria
        in a DELETE statement

        .. versionadded:: 2.1

        """
        pass

    def visit_update(
        self,
        update_stmt: Update,
        visiting_cte: Optional[CTE] = None,
        **kw: Any,
    ) -> str:
        pass

    def delete_extra_from_clause(
        self, delete_stmt, from_table, extra_froms, from_hints, **kw
    ):
        """Provide a hook to override the generation of an
        DELETE..FROM clause.

        This can be used to implement DELETE..USING for example.

        MySQL and MSSQL override this.

        """
        raise NotImplementedError(
            "This backend does not support multiple-table "
            "criteria within DELETE"
        )

    def delete_table_clause(self, delete_stmt, from_table, extra_froms, **kw):
        pass

    def visit_delete(self, delete_stmt, visiting_cte=None, **kw):
        pass

    def visit_savepoint(self, savepoint_stmt, **kw):
        pass

    def visit_rollback_to_savepoint(self, savepoint_stmt, **kw):
        pass

    def visit_release_savepoint(self, savepoint_stmt, **kw):
        pass


class StrSQLCompiler(SQLCompiler):
    """A :class:`.SQLCompiler` subclass which allows a small selection
    of non-standard SQL features to render into a string value.

    The :class:`.StrSQLCompiler` is invoked whenever a Core expression
    element is directly stringified without calling upon the
    :meth:`_expression.ClauseElement.compile` method.
    It can render a limited set
    of non-standard SQL constructs to assist in basic stringification,
    however for more substantial custom or dialect-specific SQL constructs,
    it will be necessary to make use of
    :meth:`_expression.ClauseElement.compile`
    directly.

    .. seealso::

        :ref:`faq_sql_expression_string`

    """

    def _fallback_column_name(self, column):
        pass

    @util.preload_module("sqlalchemy.engine.url")
    def visit_unsupported_compilation(self, element, err, **kw):
        if element.stringify_dialect != "default":
            url = util.preloaded.engine_url
            dialect = url.URL.create(element.stringify_dialect).get_dialect()()

            compiler = dialect.statement_compiler(
                dialect, None, _supporting_against=self
            )
            if not isinstance(compiler, StrSQLCompiler):
                return compiler.process(element, **kw)

        return super().visit_unsupported_compilation(element, err)

    def visit_getitem_binary(self, binary, operator, **kw):
        pass

    def visit_json_getitem_op_binary(self, binary, operator, **kw):
        pass

    def visit_json_path_getitem_op_binary(self, binary, operator, **kw):
        pass

    def visit_sequence(self, sequence, **kw):
        pass

    def returning_clause(
        self,
        stmt: UpdateBase,
        returning_cols: Sequence[_ColumnsClauseElement],
        *,
        populate_result_map: bool,
        **kw: Any,
    ) -> str:
        pass

    def update_from_clause(
        self, update_stmt, from_table, extra_froms, from_hints, **kw
    ):
        pass

    def delete_extra_from_clause(
        self, delete_stmt, from_table, extra_froms, from_hints, **kw
    ):
        pass

    def visit_empty_set_expr(self, element_types, **kw):
        return "SELECT 1 WHERE 1!=1"

    def get_from_hint_text(self, table, text):
        pass

    def visit_regexp_match_op_binary(self, binary, operator, **kw):
        pass

    def visit_not_regexp_match_op_binary(self, binary, operator, **kw):
        pass

    def visit_regexp_replace_op_binary(self, binary, operator, **kw):
        pass

    def visit_try_cast(self, cast, **kwargs):
        pass


class DDLCompiler(Compiled):
    is_ddl = True

    if TYPE_CHECKING:

        def __init__(
            self,
            dialect: Dialect,
            statement: ExecutableDDLElement,
            schema_translate_map: Optional[SchemaTranslateMapType] = ...,
            render_schema_translate: bool = ...,
            compile_kwargs: Mapping[str, Any] = ...,
        ): ...

    @util.ro_memoized_property
    def sql_compiler(self) -> SQLCompiler:
        pass

    @util.memoized_property
    def type_compiler(self):
        pass

    def construct_params(
        self,
        params: Optional[_CoreSingleExecuteParams] = None,
        extracted_parameters: Optional[Sequence[BindParameter[Any]]] = None,
        escape_names: bool = True,
    ) -> Optional[_MutableCoreSingleExecuteParams]:
        return None

    def visit_ddl(self, ddl, **kwargs):
        # table events can substitute table and schema name
        pass

    def visit_create_schema(self, create, **kw):
        pass

    def visit_drop_schema(self, drop, **kw):
        pass

    def visit_create_table(self, create, **kw):
        pass

    def visit_create_view(self, element: CreateView, **kw: Any) -> str:
        pass

    def visit_create_table_as(self, element: CreateTableAs, **kw: Any) -> str:
        pass

    def _generate_table_select(
        self,
        element: _TableViaSelect,
        type_: str,
        if_not_exists: Optional[bool] = None,
        **kw: Any,
    ) -> str:
        pass

    def visit_create_column(self, create, first_pk=False, **kw):
        pass

    def create_table_constraints(
        self, table, _include_foreign_key_constraints=None, **kw
    ):
        # On some DB order is significant: visit PK first, then the
        # other constraints (engine.ReflectionTest.testbasic failed on FB2)
        pass

    def visit_drop_table(self, drop, **kw):
        pass

    def visit_drop_view(self, drop, **kw):
        pass

    def _verify_index_table(self, index: Index) -> None:
        pass

    def visit_create_index(
        self, create, include_schema=False, include_table_schema=True, **kw
    ):
        pass

    def visit_drop_index(self, drop, **kw):
        pass

    def _prepared_index_name(
        self, index: Index, include_schema: bool = False
    ) -> str:
        pass

    def visit_add_constraint(self, create, **kw):
        pass

    def visit_set_table_comment(self, create, **kw):
        pass

    def visit_drop_table_comment(self, drop, **kw):
        pass

    def visit_set_column_comment(self, create, **kw):
        pass

    def visit_drop_column_comment(self, drop, **kw):
        pass

    def visit_set_constraint_comment(self, create, **kw):
        raise exc.UnsupportedCompilationError(self, type(create))

    def visit_drop_constraint_comment(self, drop, **kw):
        raise exc.UnsupportedCompilationError(self, type(drop))

    def get_identity_options(self, identity_options: IdentityOptions) -> str:
        pass

    def visit_create_sequence(self, create, prefix=None, **kw):
        pass

    def visit_drop_sequence(self, drop, **kw):
        pass

    def visit_drop_constraint(self, drop, **kw):
        pass

    def get_column_specification(
        self, column: Column[Any], **kwargs: Any
    ) -> str:
        pass

    def create_table_suffix(self, table: Table) -> str:
        pass

    def post_create_table(self, table: Table) -> str:
        pass

    def get_column_default_string(self, column: Column[Any]) -> Optional[str]:
        pass

    def render_default_string(self, default: Union[Visitable, str]) -> str:
        pass

    def visit_table_or_column_check_constraint(self, constraint, **kw):
        pass

    def visit_check_constraint(self, constraint, **kw):
        pass

    def visit_column_check_constraint(self, constraint, **kw):
        pass

    def visit_primary_key_constraint(
        self, constraint: PrimaryKeyConstraint, **kw: Any
    ) -> str:
        pass

    def visit_foreign_key_constraint(
        self, constraint: ForeignKeyConstraint, **kw: Any
    ) -> str:
        pass

    def define_constraint_remote_table(self, constraint, table, preparer):
        """Format the remote table clause of a CREATE CONSTRAINT clause."""
        pass

    def visit_unique_constraint(
        self, constraint: UniqueConstraint, **kw: Any
    ) -> str:
        pass

    def define_constraint_preamble(
        self, constraint: Constraint, **kw: Any
    ) -> str:
        pass

    def define_primary_key_body(
        self, constraint: PrimaryKeyConstraint, **kw: Any
    ) -> str:
        pass

    def define_foreign_key_body(
        self, constraint: ForeignKeyConstraint, **kw: Any
    ) -> str:
        pass

    def define_unique_body(
        self, constraint: UniqueConstraint, **kw: Any
    ) -> str:
        pass

    def define_check_body(self, constraint: CheckConstraint, **kw: Any) -> str:
        pass

    def define_unique_constraint_distinct(
        self, constraint: UniqueConstraint, **kw: Any
    ) -> str:
        pass

    def define_constraint_cascades(
        self, constraint: ForeignKeyConstraint
    ) -> str:
        pass

    def define_constraint_ondelete_cascade(
        self, constraint: ForeignKeyConstraint
    ) -> str:
        pass

    def define_constraint_onupdate_cascade(
        self, constraint: ForeignKeyConstraint
    ) -> str:
        pass

    def define_constraint_deferrability(self, constraint: Constraint) -> str:
        pass

    def define_constraint_match(self, constraint: ForeignKeyConstraint) -> str:
        pass

    def visit_computed_column(self, generated, **kw):
        pass

    def visit_identity_column(self, identity, **kw):
        pass


class GenericTypeCompiler(TypeCompiler):
    def visit_FLOAT(self, type_: sqltypes.Float[Any], **kw: Any) -> str:
        pass

    def visit_DOUBLE(self, type_: sqltypes.Double[Any], **kw: Any) -> str:
        pass

    def visit_DOUBLE_PRECISION(
        self, type_: sqltypes.DOUBLE_PRECISION[Any], **kw: Any
    ) -> str:
        pass

    def visit_REAL(self, type_: sqltypes.REAL[Any], **kw: Any) -> str:
        pass

    def visit_NUMERIC(self, type_: sqltypes.Numeric[Any], **kw: Any) -> str:
        pass

    def visit_DECIMAL(self, type_: sqltypes.DECIMAL[Any], **kw: Any) -> str:
        pass

    def visit_INTEGER(self, type_: sqltypes.Integer, **kw: Any) -> str:
        pass

    def visit_SMALLINT(self, type_: sqltypes.SmallInteger, **kw: Any) -> str:
        pass

    def visit_BIGINT(self, type_: sqltypes.BigInteger, **kw: Any) -> str:
        pass

    def visit_TIMESTAMP(self, type_: sqltypes.TIMESTAMP, **kw: Any) -> str:
        pass

    def visit_DATETIME(self, type_: sqltypes.DateTime, **kw: Any) -> str:
        pass

    def visit_DATE(self, type_: sqltypes.Date, **kw: Any) -> str:
        pass

    def visit_TIME(self, type_: sqltypes.Time, **kw: Any) -> str:
        pass

    def visit_CLOB(self, type_: sqltypes.CLOB, **kw: Any) -> str:
        pass

    def visit_NCLOB(self, type_: sqltypes.Text, **kw: Any) -> str:
        pass

    def _render_string_type(
        self, name: str, length: Optional[int], collation: Optional[str]
    ) -> str:
        pass

    def visit_CHAR(self, type_: sqltypes.CHAR, **kw: Any) -> str:
        pass

    def visit_NCHAR(self, type_: sqltypes.NCHAR, **kw: Any) -> str:
        pass

    def visit_VARCHAR(self, type_: sqltypes.String, **kw: Any) -> str:
        pass

    def visit_NVARCHAR(self, type_: sqltypes.NVARCHAR, **kw: Any) -> str:
        pass

    def visit_TEXT(self, type_: sqltypes.Text, **kw: Any) -> str:
        pass

    def visit_UUID(self, type_: sqltypes.Uuid[Any], **kw: Any) -> str:
        pass

    def visit_BLOB(self, type_: sqltypes.LargeBinary, **kw: Any) -> str:
        pass

    def visit_BINARY(self, type_: sqltypes.BINARY, **kw: Any) -> str:
        pass

    def visit_VARBINARY(self, type_: sqltypes.VARBINARY, **kw: Any) -> str:
        pass

    def visit_BOOLEAN(self, type_: sqltypes.Boolean, **kw: Any) -> str:
        pass

    def visit_uuid(self, type_: sqltypes.Uuid[Any], **kw: Any) -> str:
        pass

    def visit_large_binary(
        self, type_: sqltypes.LargeBinary, **kw: Any
    ) -> str:
        pass

    def visit_boolean(self, type_: sqltypes.Boolean, **kw: Any) -> str:
        pass

    def visit_time(self, type_: sqltypes.Time, **kw: Any) -> str:
        pass

    def visit_datetime(self, type_: sqltypes.DateTime, **kw: Any) -> str:
        pass

    def visit_date(self, type_: sqltypes.Date, **kw: Any) -> str:
        pass

    def visit_big_integer(self, type_: sqltypes.BigInteger, **kw: Any) -> str:
        pass

    def visit_small_integer(
        self, type_: sqltypes.SmallInteger, **kw: Any
    ) -> str:
        pass

    def visit_integer(self, type_: sqltypes.Integer, **kw: Any) -> str:
        pass

    def visit_real(self, type_: sqltypes.REAL[Any], **kw: Any) -> str:
        pass

    def visit_float(self, type_: sqltypes.Float[Any], **kw: Any) -> str:
        pass

    def visit_double(self, type_: sqltypes.Double[Any], **kw: Any) -> str:
        pass

    def visit_numeric(self, type_: sqltypes.Numeric[Any], **kw: Any) -> str:
        pass

    def visit_string(self, type_: sqltypes.String, **kw: Any) -> str:
        pass

    def visit_unicode(self, type_: sqltypes.Unicode, **kw: Any) -> str:
        pass

    def visit_text(self, type_: sqltypes.Text, **kw: Any) -> str:
        pass

    def visit_unicode_text(
        self, type_: sqltypes.UnicodeText, **kw: Any
    ) -> str:
        pass

    def visit_enum(self, type_: sqltypes.Enum, **kw: Any) -> str:
        pass

    def visit_null(self, type_, **kw):
        raise exc.CompileError(
            "Can't generate DDL for %r; "
            "did you forget to specify a "
            "type on this Column?" % type_
        )

    def visit_type_decorator(
        self, type_: TypeDecorator[Any], **kw: Any
    ) -> str:
        pass

    def visit_user_defined(
        self, type_: UserDefinedType[Any], **kw: Any
    ) -> str:
        pass


class StrSQLTypeCompiler(GenericTypeCompiler):
    def process(self, type_, **kw):
        try:
            _compiler_dispatch = type_._compiler_dispatch
        except AttributeError:
            return self._visit_unknown(type_, **kw)
        else:
            return _compiler_dispatch(self, **kw)

    def __getattr__(self, key):
        if key.startswith("visit_"):
            return self._visit_unknown
        else:
            raise AttributeError(key)

    def _visit_unknown(self, type_, **kw):
        if type_.__class__.__name__ == type_.__class__.__name__.upper():
            return type_.__class__.__name__
        else:
            return repr(type_)

    def visit_null(self, type_, **kw):
        pass

    def visit_user_defined(self, type_, **kw):
        pass


class _SchemaForObjectCallable(Protocol):
    def __call__(self, obj: Any, /) -> str: ...


class _BindNameForColProtocol(Protocol):
    def __call__(self, col: ColumnClause[Any]) -> str: ...


class IdentifierPreparer:
    """Handle quoting and case-folding of identifiers based on options."""

    reserved_words = RESERVED_WORDS

    legal_characters = LEGAL_CHARACTERS

    illegal_initial_characters = ILLEGAL_INITIAL_CHARACTERS

    initial_quote: str

    final_quote: str

    _strings: MutableMapping[str, str]

    schema_for_object: _SchemaForObjectCallable = operator.attrgetter("schema")
    """Return the .schema attribute for an object.

    For the default IdentifierPreparer, the schema for an object is always
    the value of the ".schema" attribute.   if the preparer is replaced
    with one that has a non-empty schema_translate_map, the value of the
    ".schema" attribute is rendered a symbol that will be converted to a
    real schema name from the mapping post-compile.

    """

    _includes_none_schema_translate: bool = False

    def __init__(
        self,
        dialect: Dialect,
        initial_quote: str = '"',
        final_quote: Optional[str] = None,
        escape_quote: str = '"',
        quote_case_sensitive_collations: bool = True,
        omit_schema: bool = False,
    ):
        """Construct a new ``IdentifierPreparer`` object.

        initial_quote
          Character that begins a delimited identifier.

        final_quote
          Character that ends a delimited identifier. Defaults to
          `initial_quote`.

        omit_schema
          Prevent prepending schema name. Useful for databases that do
          not support schemae.
        """

        self.dialect = dialect
        self.initial_quote = initial_quote
        self.final_quote = final_quote or self.initial_quote
        self.escape_quote = escape_quote
        self.escape_to_quote = self.escape_quote * 2
        self.omit_schema = omit_schema
        self.quote_case_sensitive_collations = quote_case_sensitive_collations
        self._strings = {}
        self._double_percents = self.dialect.paramstyle in (
            "format",
            "pyformat",
        )

    def _with_schema_translate(self, schema_translate_map):
        prep = self.__class__.__new__(self.__class__)
        prep.__dict__.update(self.__dict__)

        includes_none = None in schema_translate_map

        def symbol_getter(obj):
            name = obj.schema
            if obj._use_schema_map and (name is not None or includes_none):
                if name is not None and ("[" in name or "]" in name):
                    raise exc.CompileError(
                        "Square bracket characters ([]) not supported "
                        "in schema translate name '%s'" % name
                    )
                return quoted_name(
                    "__[SCHEMA_%s]" % (name or "_none"), quote=False
                )
            else:
                return obj.schema

        prep.schema_for_object = symbol_getter
        prep._includes_none_schema_translate = includes_none
        return prep

    def _render_schema_translates(
        self, statement: str, schema_translate_map: SchemaTranslateMapType
    ) -> str:
        d = schema_translate_map
        if None in d:
            if not self._includes_none_schema_translate:
                raise exc.InvalidRequestError(
                    "schema translate map which previously did not have "
                    "`None` present as a key now has `None` present; compiled "
                    "statement may lack adequate placeholders.  Please use "
                    "consistent keys in successive "
                    "schema_translate_map dictionaries."
                )

            d["_none"] = d[None]  # type: ignore[index]

        def replace(m):
            name = m.group(2)
            if name in d:
                effective_schema = d[name]
            else:
                if name in (None, "_none"):
                    raise exc.InvalidRequestError(
                        "schema translate map which previously had `None` "
                        "present as a key now no longer has it present; don't "
                        "know how to apply schema for compiled statement. "
                        "Please use consistent keys in successive "
                        "schema_translate_map dictionaries."
                    )
                effective_schema = name

            if not effective_schema:
                effective_schema = self.dialect.default_schema_name
                if not effective_schema:
                    # TODO: no coverage here
                    raise exc.CompileError(
                        "Dialect has no default schema name; can't "
                        "use None as dynamic schema target."
                    )
            return self.quote_schema(effective_schema)

        return re.sub(r"(__\[SCHEMA_([^\]]+)\])", replace, statement)

    def _escape_identifier(self, value: str) -> str:
        """Escape an identifier.

        Subclasses should override this to provide database-dependent
        escaping behavior.
        """

        value = value.replace(self.escape_quote, self.escape_to_quote)
        if self._double_percents:
            value = value.replace("%", "%%")
        return value

    def _unescape_identifier(self, value: str) -> str:
        """Canonicalize an escaped identifier.

        Subclasses should override this to provide database-dependent
        unescaping behavior that reverses _escape_identifier.
        """

        return value.replace(self.escape_to_quote, self.escape_quote)

    def validate_sql_phrase(self, element, reg):
        """keyword sequence filter.

        a filter for elements that are intended to represent keyword sequences,
        such as "INITIALLY", "INITIALLY DEFERRED", etc.   no special characters
        should be present.

        """
        pass

    def quote_identifier(self, value: str) -> str:
        """Quote an identifier.

        Subclasses should override this to provide database-dependent
        quoting behavior.
        """

        return (
            self.initial_quote
            + self._escape_identifier(value)
            + self.final_quote
        )

    def _requires_quotes(self, value: str) -> bool:
        """Return True if the given identifier requires quoting."""
        lc_value = value.lower()
        return (
            lc_value in self.reserved_words
            or value[0] in self.illegal_initial_characters
            or not self.legal_characters.match(str(value))
            or (lc_value != value)
        )

    def _requires_quotes_illegal_chars(self, value):
        """Return True if the given identifier requires quoting, but
        not taking case convention into account."""
        pass

    def quote_schema(self, schema: str) -> str:
        """Conditionally quote a schema name.


        The name is quoted if it is a reserved word, contains quote-necessary
        characters, or is an instance of :class:`.quoted_name` which includes
        ``quote`` set to ``True``.

        Subclasses can override this to provide database-dependent
        quoting behavior for schema names.

        :param schema: string schema name
        """
        return self.quote(schema)

    def quote(self, ident: str) -> str:
        """Conditionally quote an identifier.

        The identifier is quoted if it is a reserved word, contains
        quote-necessary characters, or is an instance of
        :class:`.quoted_name` which includes ``quote`` set to ``True``.

        Subclasses can override this to provide database-dependent
        quoting behavior for identifier names.

        :param ident: string identifier
        """
        force = getattr(ident, "quote", None)

        if force is None:
            if ident in self._strings:
                return self._strings[ident]
            else:
                if self._requires_quotes(ident):
                    self._strings[ident] = self.quote_identifier(ident)
                else:
                    self._strings[ident] = ident
                return self._strings[ident]
        elif force:
            return self.quote_identifier(ident)
        else:
            return ident

    def format_collation(self, collation_name):
        pass

    def format_sequence(
        self, sequence: schema.Sequence, use_schema: bool = True
    ) -> str:
        pass

    def format_label(
        self, label: Label[Any], name: Optional[str] = None
    ) -> str:
        pass

    def format_alias(
        self, alias: Optional[AliasedReturnsRows], name: Optional[str] = None
    ) -> str:
        pass

    def format_savepoint(self, savepoint, name=None):
        # Running the savepoint name through quoting is unnecessary
        # for all known dialects.  This is here to support potential
        # third party use cases
        pass

    @util.preload_module("sqlalchemy.sql.naming")
    def format_constraint(
        self, constraint: Union[Constraint, Index], _alembic_quote: bool = True
    ) -> Optional[str]:
        pass

    def truncate_and_render_index_name(
        self, name: str, _alembic_quote: bool = True
    ) -> str:
        # calculate these at format time so that ad-hoc changes
        # to dialect.max_identifier_length etc. can be reflected
        # as IdentifierPreparer is long lived
        pass

    def truncate_and_render_constraint_name(
        self, name: str, _alembic_quote: bool = True
    ) -> str:
        # calculate these at format time so that ad-hoc changes
        # to dialect.max_identifier_length etc. can be reflected
        # as IdentifierPreparer is long lived
        pass

    def _truncate_and_render_maxlen_name(
        self, name: str, max_: int, _alembic_quote: bool
    ) -> str:
        pass

    def format_index(self, index: Index) -> str:
        pass

    def format_table(
        self,
        table: FromClause,
        use_schema: bool = True,
        name: Optional[str] = None,
    ) -> str:
        """Prepare a quoted table and schema name."""
        if name is None:
            if TYPE_CHECKING:
                assert isinstance(table, NamedFromClause)
            name = table.name

        result = self.quote(name)

        effective_schema = self.schema_for_object(table)

        if not self.omit_schema and use_schema and effective_schema:
            result = self.quote_schema(effective_schema) + "." + result
        return result

    def format_schema(self, name):
        """Prepare a quoted schema name."""
        pass

    def format_label_name(
        self,
        name,
        anon_map=None,
    ):
        """Prepare a quoted column name."""
        pass

    def format_column(
        self,
        column: ColumnElement[Any],
        use_table: bool = False,
        name: Optional[str] = None,
        table_name: Optional[str] = None,
        use_schema: bool = False,
        anon_map: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Prepare a quoted column name."""
        pass

    def format_table_seq(self, table, use_schema=True):
        """Format table name and schema as a tuple."""
        pass

    @util.memoized_property
    def _r_identifiers(self):
        pass

    def unformat_identifiers(self, identifiers: str) -> Sequence[str]:
        """Unpack 'schema.table.column'-like strings into components."""

        r = self._r_identifiers
        return [
            self._unescape_identifier(i)
            for i in [a or b for a, b in r.findall(identifiers)]
        ]
