# orm/strategies.py
# Copyright (C) 2005-2026 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors


"""sqlalchemy.orm.interfaces.LoaderStrategy
implementations, and related MapperOptions."""

from __future__ import annotations

import collections
import itertools
from typing import Any
from typing import Dict
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import TYPE_CHECKING
from typing import Union

from . import attributes
from . import exc as orm_exc
from . import interfaces
from . import loading
from . import path_registry
from . import properties
from . import query
from . import relationships
from . import unitofwork
from . import util as orm_util
from .base import _DEFER_FOR_STATE
from .base import _RAISE_FOR_STATE
from .base import _SET_DEFERRED_EXPIRED
from .base import ATTR_WAS_SET
from .base import LoaderCallableStatus
from .base import PASSIVE_OFF
from .base import PassiveFlag
from .context import _column_descriptions
from .context import _ORMCompileState
from .context import _ORMSelectCompileState
from .context import QueryContext
from .interfaces import LoaderStrategy
from .interfaces import StrategizedProperty
from .session import _state_session
from .state import InstanceState
from .strategy_options import Load
from .util import _none_only_set
from .util import AliasedClass
from .. import event
from .. import exc as sa_exc
from .. import inspect
from .. import log
from .. import sql
from .. import util
from ..sql import util as sql_util
from ..sql import visitors
from ..sql.selectable import LABEL_STYLE_TABLENAME_PLUS_COL
from ..sql.selectable import Select

if TYPE_CHECKING:
    from .mapper import Mapper
    from .relationships import RelationshipProperty
    from ..sql.elements import ColumnElement


def _register_attribute(
    prop,
    mapper,
    useobject,
    compare_function=None,
    typecallable=None,
    callable_=None,
    proxy_property=None,
    active_history=False,
    impl_class=None,
    default_scalar_value=None,
    **kw,
):
    listen_hooks = []

    uselist = useobject and prop.uselist

    if useobject and prop.single_parent:
        listen_hooks.append(_single_parent_validator)

    if prop.key in prop.parent.validators:
        fn, opts = prop.parent.validators[prop.key]
        listen_hooks.append(
            lambda desc, prop: orm_util._validator_events(
                desc, prop.key, fn, **opts
            )
        )

    if useobject:
        listen_hooks.append(unitofwork._track_cascade_events)

    # need to assemble backref listeners
    # after the singleparentvalidator, mapper validator
    if useobject:
        backref = prop.back_populates
        if backref and prop._effective_sync_backref:
            listen_hooks.append(
                lambda desc, prop: attributes._backref_listeners(
                    desc, backref, uselist
                )
            )

    # a single MapperProperty is shared down a class inheritance
    # hierarchy, so we set up attribute instrumentation and backref event
    # for each mapper down the hierarchy.

    # typically, "mapper" is the same as prop.parent, due to the way
    # the configure_mappers() process runs, however this is not strongly
    # enforced, and in the case of a second configure_mappers() run the
    # mapper here might not be prop.parent; also, a subclass mapper may
    # be called here before a superclass mapper.  That is, can't depend
    # on mappers not already being set up so we have to check each one.

    for m in mapper.self_and_descendants:
        if prop is m._props.get(
            prop.key
        ) and not m.class_manager._attr_has_impl(prop.key):
            desc = attributes._register_attribute_impl(
                m.class_,
                prop.key,
                parent_token=prop,
                uselist=uselist,
                compare_function=compare_function,
                useobject=useobject,
                trackparent=useobject
                and (
                    prop.single_parent
                    or prop.direction is interfaces.ONETOMANY
                ),
                typecallable=typecallable,
                callable_=callable_,
                active_history=active_history,
                default_scalar_value=default_scalar_value,
                impl_class=impl_class,
                send_modified_events=not useobject or not prop.viewonly,
                doc=prop.doc,
                **kw,
            )

            for hook in listen_hooks:
                hook(desc, prop)


@properties.ColumnProperty.strategy_for(instrument=False, deferred=False)
class _UninstrumentedColumnLoader(LoaderStrategy):
    """Represent a non-instrumented MapperProperty.

    The polymorphic_on argument of mapper() often results in this,
    if the argument is against the with_polymorphic selectable.

    """

    __slots__ = ("columns",)

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.columns = self.parent_property.columns

    def setup_query(
        self,
        compile_state,
        query_entity,
        path,
        loadopt,
        adapter,
        column_collection=None,
        **kwargs,
    ):
        pass

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        pass


@log.class_logger
@properties.ColumnProperty.strategy_for(instrument=True, deferred=False)
class _ColumnLoader(LoaderStrategy):
    """Provide loading behavior for a :class:`.ColumnProperty`."""

    __slots__ = "columns", "is_composite"

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.columns = self.parent_property.columns
        self.is_composite = hasattr(self.parent_property, "composite_class")

    def setup_query(
        self,
        compile_state,
        query_entity,
        path,
        loadopt,
        adapter,
        column_collection,
        memoized_populators,
        check_for_adapt=False,
        **kwargs,
    ):
        pass

    def init_class_attribute(self, mapper):
        self.is_class_level = True
        coltype = self.columns[0].type
        # TODO: check all columns ?  check for foreign key as well?
        active_history = (
            self.parent_property.active_history
            or self.columns[0].primary_key
            or (
                mapper.version_id_col is not None
                and mapper._columntoproperty.get(mapper.version_id_col, None)
                is self.parent_property
            )
        )

        _register_attribute(
            self.parent_property,
            mapper,
            useobject=False,
            compare_function=coltype.compare_values,
            active_history=active_history,
            default_scalar_value=self.parent_property._default_scalar_value,
        )

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        # look through list of columns represented here
        # to see which, if any, is present in the row.

        for col in self.columns:
            if adapter:
                col = adapter.columns[col]
            getter = result._getter(col, False)
            if getter:
                populators["quick"].append((self.key, getter))
                break
        else:
            populators["expire"].append((self.key, True))


@log.class_logger
@properties.ColumnProperty.strategy_for(query_expression=True)
class _ExpressionColumnLoader(_ColumnLoader):
    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)

        # compare to the "default" expression that is mapped in
        # the column.   If it's sql.null, we don't need to render
        # unless an expr is passed in the options.
        null = sql.null().label(None)
        self._have_default_expression = any(
            not c.compare(null) for c in self.parent_property.columns
        )

    def setup_query(
        self,
        compile_state,
        query_entity,
        path,
        loadopt,
        adapter,
        column_collection,
        memoized_populators,
        **kwargs,
    ):
        pass

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        # look through list of columns represented here
        # to see which, if any, is present in the row.
        if loadopt and loadopt._extra_criteria:
            columns = loadopt._extra_criteria

            for col in columns:
                if adapter:
                    col = adapter.columns[col]
                getter = result._getter(col, False)
                if getter:
                    populators["quick"].append((self.key, getter))
                    break
            else:
                populators["expire"].append((self.key, True))

    def init_class_attribute(self, mapper):
        self.is_class_level = True

        _register_attribute(
            self.parent_property,
            mapper,
            useobject=False,
            compare_function=self.columns[0].type.compare_values,
            accepts_scalar_loader=False,
            default_scalar_value=self.parent_property._default_scalar_value,
        )


@log.class_logger
@properties.ColumnProperty.strategy_for(deferred=True, instrument=True)
@properties.ColumnProperty.strategy_for(
    deferred=True, instrument=True, raiseload=True
)
@properties.ColumnProperty.strategy_for(do_nothing=True)
class _DeferredColumnLoader(LoaderStrategy):
    """Provide loading behavior for a deferred :class:`.ColumnProperty`."""

    __slots__ = "columns", "group", "raiseload"

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        if hasattr(self.parent_property, "composite_class"):
            raise NotImplementedError(
                "Deferred loading for composite types not implemented yet"
            )
        self.raiseload = self.strategy_opts.get("raiseload", False)
        self.columns = self.parent_property.columns
        self.group = self.parent_property.group

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        # for a DeferredColumnLoader, this method is only used during a
        # "row processor only" query; see test_deferred.py ->
        # tests with "rowproc_only" in their name.  As of the 1.0 series,
        # loading._instance_processor doesn't use a "row processing" function
        # to populate columns, instead it uses data in the "populators"
        # dictionary.  Normally, the DeferredColumnLoader.setup_query()
        # sets up that data in the "memoized_populators" dictionary
        # and "create_row_processor()" here is never invoked.

        if (
            context.refresh_state
            and context.query._compile_options._only_load_props
            and self.key in context.query._compile_options._only_load_props
        ):
            self.parent_property._get_strategy(
                (("deferred", False), ("instrument", True))
            ).create_row_processor(
                context,
                query_entity,
                path,
                loadopt,
                mapper,
                result,
                adapter,
                populators,
            )

        elif not self.is_class_level:
            if self.raiseload:
                set_deferred_for_local_state = (
                    self.parent_property._raise_column_loader
                )
            else:
                set_deferred_for_local_state = (
                    self.parent_property._deferred_column_loader
                )
            populators["new"].append((self.key, set_deferred_for_local_state))
        else:
            populators["expire"].append((self.key, False))

    def init_class_attribute(self, mapper):
        self.is_class_level = True

        _register_attribute(
            self.parent_property,
            mapper,
            useobject=False,
            compare_function=self.columns[0].type.compare_values,
            callable_=self._load_for_state,
            load_on_unexpire=False,
            default_scalar_value=self.parent_property._default_scalar_value,
        )

    def setup_query(
        self,
        compile_state,
        query_entity,
        path,
        loadopt,
        adapter,
        column_collection,
        memoized_populators,
        only_load_props=None,
        **kw,
    ):
        pass

    def _load_for_state(self, state, passive):
        pass

    def _invoke_raise_load(self, state, passive, lazy):
        raise sa_exc.InvalidRequestError(
            "'%s' is not available due to raiseload=True" % (self,)
        )


class _LoadDeferredColumns:
    """serializable loader object used by DeferredColumnLoader"""

    def __init__(self, key: str, raiseload: bool = False):
        self.key = key
        self.raiseload = raiseload

    def __call__(self, state, passive=attributes.PASSIVE_OFF):
        key = self.key

        localparent = state.manager.mapper
        prop = localparent._props[key]
        if self.raiseload:
            strategy_key = (
                ("deferred", True),
                ("instrument", True),
                ("raiseload", True),
            )
        else:
            strategy_key = (("deferred", True), ("instrument", True))
        strategy = prop._get_strategy(strategy_key)
        return strategy._load_for_state(state, passive)


class _AbstractRelationshipLoader(LoaderStrategy):
    """LoaderStratgies which deal with related objects."""

    __slots__ = "mapper", "target", "uselist", "entity"

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.mapper = self.parent_property.mapper
        self.entity = self.parent_property.entity
        self.target = self.parent_property.target
        self.uselist = self.parent_property.uselist

    def _immediateload_create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        return self.parent_property._get_strategy(
            (("lazy", "immediate"),)
        ).create_row_processor(
            context,
            query_entity,
            path,
            loadopt,
            mapper,
            result,
            adapter,
            populators,
        )


@log.class_logger
@relationships.RelationshipProperty.strategy_for(do_nothing=True)
class _DoNothingLoader(LoaderStrategy):
    """Relationship loader that makes no change to the object's state.

    Compared to NoLoader, this loader does not initialize the
    collection/attribute to empty/none; the usual default LazyLoader will
    take effect.

    """


@log.class_logger
@relationships.RelationshipProperty.strategy_for(lazy="noload")
@relationships.RelationshipProperty.strategy_for(lazy=None)
class _NoLoader(_AbstractRelationshipLoader):
    """Provide loading behavior for a :class:`.Relationship`
    with "lazy=None".

    """

    __slots__ = ()

    @util.deprecated(
        "2.1",
        "The ``noload`` loader strategy is deprecated and will be removed "
        "in a future release.  This option "
        "produces incorrect results by returning ``None`` for related "
        "items.",
    )
    def init_class_attribute(self, mapper):
        self.is_class_level = True

        _register_attribute(
            self.parent_property,
            mapper,
            useobject=True,
            typecallable=self.parent_property.collection_class,
        )

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        def invoke_no_load(state, dict_, row):
            if self.uselist:
                attributes.init_state_collection(state, dict_, self.key)
            else:
                dict_[self.key] = None

        populators["new"].append((self.key, invoke_no_load))


@log.class_logger
@relationships.RelationshipProperty.strategy_for(lazy=True)
@relationships.RelationshipProperty.strategy_for(lazy="select")
@relationships.RelationshipProperty.strategy_for(lazy="raise")
@relationships.RelationshipProperty.strategy_for(lazy="raise_on_sql")
@relationships.RelationshipProperty.strategy_for(lazy="baked_select")
class _LazyLoader(
    _AbstractRelationshipLoader, util.MemoizedSlots, log.Identified
):
    """Provide loading behavior for a :class:`.Relationship`
    with "lazy=True", that is loads when first accessed.

    """

    __slots__ = (
        "_lazywhere",
        "_rev_lazywhere",
        "_lazyload_reverse_option",
        "_order_by",
        "use_get",
        "is_aliased_class",
        "_bind_to_col",
        "_equated_columns",
        "_rev_bind_to_col",
        "_rev_equated_columns",
        "_simple_lazy_clause",
        "_raise_always",
        "_raise_on_sql",
    )

    _lazywhere: ColumnElement[bool]
    _bind_to_col: Dict[str, ColumnElement[Any]]
    _rev_lazywhere: ColumnElement[bool]
    _rev_bind_to_col: Dict[str, ColumnElement[Any]]

    parent_property: RelationshipProperty[Any]

    def __init__(
        self, parent: RelationshipProperty[Any], strategy_key: Tuple[Any, ...]
    ):
        super().__init__(parent, strategy_key)
        self._raise_always = self.strategy_opts["lazy"] == "raise"
        self._raise_on_sql = self.strategy_opts["lazy"] == "raise_on_sql"

        self.is_aliased_class = inspect(self.entity).is_aliased_class

        join_condition = self.parent_property._join_condition
        (
            self._lazywhere,
            self._bind_to_col,
            self._equated_columns,
        ) = join_condition.create_lazy_clause()

        (
            self._rev_lazywhere,
            self._rev_bind_to_col,
            self._rev_equated_columns,
        ) = join_condition.create_lazy_clause(reverse_direction=True)

        if self.parent_property.order_by:
            self._order_by = util.to_list(self.parent_property.order_by)
        else:
            self._order_by = None

        self.logger.info("%s lazy loading clause %s", self, self._lazywhere)

        # determine if our "lazywhere" clause is the same as the mapper's
        # get() clause.  then we can just use mapper.get()
        #
        # TODO: the "not self.uselist" can be taken out entirely; a m2o
        # load that populates for a list (very unusual, but is possible with
        # the API) can still set for "None" and the attribute system will
        # populate as an empty list.
        self.use_get = (
            not self.is_aliased_class
            and not self.uselist
            and self.entity._get_clause[0].compare(
                self._lazywhere,
                use_proxies=True,
                compare_keys=False,
                equivalents=self.mapper._equivalent_columns,
            )
        )

        if self.use_get:
            for col in list(self._equated_columns):
                if col in self.mapper._equivalent_columns:
                    for c in self.mapper._equivalent_columns[col]:
                        self._equated_columns[c] = self._equated_columns[col]

            self.logger.info(
                "%s will use Session.get() to optimize instance loads", self
            )

    def init_class_attribute(self, mapper):
        self.is_class_level = True

        _legacy_inactive_history_style = (
            self.parent_property._legacy_inactive_history_style
        )

        if self.parent_property.active_history:
            active_history = True
            _deferred_history = False

        elif (
            self.parent_property.direction is not interfaces.MANYTOONE
            or not self.use_get
        ):
            if _legacy_inactive_history_style:
                active_history = True
                _deferred_history = False
            else:
                active_history = False
                _deferred_history = True
        else:
            active_history = _deferred_history = False

        _register_attribute(
            self.parent_property,
            mapper,
            useobject=True,
            callable_=self._load_for_state,
            typecallable=self.parent_property.collection_class,
            active_history=active_history,
            _deferred_history=_deferred_history,
        )

    def _memoized_attr__simple_lazy_clause(self):
        pass

    def _generate_lazy_clause(self, state, passive):
        pass

    def _invoke_raise_load(self, state, passive, lazy):
        raise sa_exc.InvalidRequestError(
            "'%s' is not available due to lazy='%s'" % (self, lazy)
        )

    def _load_for_state(
        self,
        state,
        passive,
        loadopt=None,
        extra_criteria=(),
        extra_options=(),
        alternate_effective_path=None,
        execution_options=util.EMPTY_DICT,
    ):
        pass

    def _get_ident_for_use_get(self, session, state, passive):
        pass

    @util.preload_module("sqlalchemy.orm.strategy_options")
    def _emit_lazyload(
        self,
        session,
        state,
        primary_key_identity,
        passive,
        loadopt,
        extra_criteria,
        extra_options,
        alternate_effective_path,
        execution_options,
    ):
        pass

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        key = self.key

        if (
            context.load_options._is_user_refresh
            and context.query._compile_options._only_load_props
            and self.key in context.query._compile_options._only_load_props
        ):
            return self._immediateload_create_row_processor(
                context,
                query_entity,
                path,
                loadopt,
                mapper,
                result,
                adapter,
                populators,
            )

        if not self.is_class_level or (loadopt and loadopt._extra_criteria):
            # we are not the primary manager for this attribute
            # on this class - set up a
            # per-instance lazyloader, which will override the
            # class-level behavior.
            # this currently only happens when using a
            # "lazyload" option on a "no load"
            # attribute - "eager" attributes always have a
            # class-level lazyloader installed.
            set_lazy_callable = (
                InstanceState._instance_level_callable_processor
            )(
                mapper.class_manager,
                _LoadLazyAttribute(
                    key,
                    self,
                    loadopt,
                    (
                        loadopt._generate_extra_criteria(context)
                        if loadopt._extra_criteria
                        else None
                    ),
                ),
                key,
            )

            populators["new"].append((self.key, set_lazy_callable))
        elif context.populate_existing or mapper.always_refresh:

            def reset_for_lazy_callable(state, dict_, row):
                # we are the primary manager for this attribute on
                # this class - reset its
                # per-instance attribute state, so that the class-level
                # lazy loader is
                # executed when next referenced on this instance.
                # this is needed in
                # populate_existing() types of scenarios to reset
                # any existing state.
                state._reset(dict_, key)

            populators["new"].append((self.key, reset_for_lazy_callable))


class _LoadLazyAttribute:
    """semi-serializable loader object used by LazyLoader

    Historically, this object would be carried along with instances that
    needed to run lazyloaders, so it had to be serializable to support
    cached instances.

    this is no longer a general requirement, and the case where this object
    is used is exactly the case where we can't really serialize easily,
    which is when extra criteria in the loader option is present.

    We can't reliably serialize that as it refers to mapped entities and
    AliasedClass objects that are local to the current process, which would
    need to be matched up on deserialize e.g. the sqlalchemy.ext.serializer
    approach.

    """

    def __init__(self, key, initiating_strategy, loadopt, extra_criteria):
        self.key = key
        self.strategy_key = initiating_strategy.strategy_key
        self.loadopt = loadopt
        self.extra_criteria = extra_criteria

    def __getstate__(self):
        if self.extra_criteria is not None:
            util.warn(
                "Can't reliably serialize a lazyload() option that "
                "contains additional criteria; please use eager loading "
                "for this case"
            )
        return {
            "key": self.key,
            "strategy_key": self.strategy_key,
            "loadopt": self.loadopt,
            "extra_criteria": (),
        }

    def __call__(self, state, passive=attributes.PASSIVE_OFF):
        key = self.key
        instance_mapper = state.manager.mapper
        prop = instance_mapper._props[key]
        strategy = prop._strategies[self.strategy_key]

        return strategy._load_for_state(
            state,
            passive,
            loadopt=self.loadopt,
            extra_criteria=self.extra_criteria,
        )


class _PostLoader(_AbstractRelationshipLoader):
    """A relationship loader that emits a second SELECT statement."""

    __slots__ = ()

    def _setup_for_recursion(self, context, path, loadopt, join_depth=None):
        effective_path = (
            context.compile_state.current_path or orm_util.PathRegistry.root
        ) + path

        top_level_context = context._get_top_level_context()
        execution_options = util.immutabledict(
            {"sa_top_level_orm_context": top_level_context}
        )

        if loadopt:
            recursion_depth = loadopt.local_opts.get("recursion_depth", None)
            unlimited_recursion = recursion_depth == -1
        else:
            recursion_depth = None
            unlimited_recursion = False

        if recursion_depth is not None:
            if not self.parent_property._is_self_referential:
                raise sa_exc.InvalidRequestError(
                    f"recursion_depth option on relationship "
                    f"{self.parent_property} not valid for "
                    "non-self-referential relationship"
                )
            recursion_depth = context.execution_options.get(
                f"_recursion_depth_{id(self)}", recursion_depth
            )

            if not unlimited_recursion and recursion_depth < 0:
                return (
                    effective_path,
                    False,
                    execution_options,
                    recursion_depth,
                )

            if not unlimited_recursion:
                execution_options = execution_options.union(
                    {
                        f"_recursion_depth_{id(self)}": recursion_depth - 1,
                    }
                )

        if loading._PostLoad.path_exists(
            context, effective_path, self.parent_property
        ):
            return effective_path, False, execution_options, recursion_depth

        path_w_prop = path[self.parent_property]
        effective_path_w_prop = effective_path[self.parent_property]

        if not path_w_prop.contains(context.attributes, "loader"):
            if join_depth:
                if effective_path_w_prop.length / 2 > join_depth:
                    return (
                        effective_path,
                        False,
                        execution_options,
                        recursion_depth,
                    )
            elif effective_path_w_prop.contains_mapper(self.mapper):
                return (
                    effective_path,
                    False,
                    execution_options,
                    recursion_depth,
                )

        return effective_path, True, execution_options, recursion_depth


@relationships.RelationshipProperty.strategy_for(lazy="immediate")
class _ImmediateLoader(_PostLoader):
    __slots__ = ("join_depth",)

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.join_depth = self.parent_property.join_depth

    def init_class_attribute(self, mapper):
        self.parent_property._get_strategy(
            (("lazy", "select"),)
        ).init_class_attribute(mapper)

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        if not context.compile_state.compile_options._enable_eagerloads:
            return

        (
            effective_path,
            run_loader,
            execution_options,
            recursion_depth,
        ) = self._setup_for_recursion(context, path, loadopt, self.join_depth)

        if not run_loader:
            # this will not emit SQL and will only emit for a many-to-one
            # "use get" load.   the "_RELATED" part means it may return
            # instance even if its expired, since this is a mutually-recursive
            # load operation.
            flags = attributes.PASSIVE_NO_FETCH_RELATED | PassiveFlag.NO_RAISE
        else:
            flags = attributes.PASSIVE_OFF | PassiveFlag.NO_RAISE

        loading._PostLoad.callable_for_path(
            context,
            effective_path,
            self.parent,
            self.parent_property,
            self._load_for_path,
            loadopt,
            flags,
            recursion_depth,
            execution_options,
        )

    def _load_for_path(
        self,
        context,
        path,
        states,
        load_only,
        loadopt,
        flags,
        recursion_depth,
        execution_options,
    ):
        pass


@log.class_logger
@relationships.RelationshipProperty.strategy_for(lazy="subquery")
class _SubqueryLoader(_PostLoader):
    __slots__ = ("join_depth",)

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.join_depth = self.parent_property.join_depth

    def init_class_attribute(self, mapper):
        self.parent_property._get_strategy(
            (("lazy", "select"),)
        ).init_class_attribute(mapper)

    def _get_leftmost(
        self,
        orig_query_entity_index,
        subq_path,
        current_compile_state,
        is_root,
    ):
        given_subq_path = subq_path
        subq_path = subq_path.path
        subq_mapper = orm_util._class_to_mapper(subq_path[0])

        # determine attributes of the leftmost mapper
        if (
            self.parent.isa(subq_mapper)
            and self.parent_property is subq_path[1]
        ):
            leftmost_mapper, leftmost_prop = self.parent, self.parent_property
        else:
            leftmost_mapper, leftmost_prop = subq_mapper, subq_path[1]

        if is_root:
            # the subq_path is also coming from cached state, so when we start
            # building up this path, it has to also be converted to be in terms
            # of the current state. this is for the specific case of the entity
            # is an AliasedClass against a subquery that's not otherwise going
            # to adapt
            new_subq_path = current_compile_state._entities[
                orig_query_entity_index
            ].entity_zero._path_registry[leftmost_prop]
            additional = len(subq_path) - len(new_subq_path)
            if additional:
                new_subq_path += path_registry.PathRegistry.coerce(
                    subq_path[-additional:]
                )
        else:
            new_subq_path = given_subq_path

        leftmost_cols = leftmost_prop.local_columns

        leftmost_attr = [
            getattr(
                new_subq_path.path[0].entity,
                leftmost_mapper._columntoproperty[c].key,
            )
            for c in leftmost_cols
        ]

        return leftmost_mapper, leftmost_attr, leftmost_prop, new_subq_path

    def _generate_from_original_query(
        self,
        orig_compile_state,
        orig_query,
        leftmost_mapper,
        leftmost_attr,
        leftmost_relationship,
        orig_entity,
    ):
        # reformat the original query
        # to look only for significant columns
        q = orig_query._clone().correlate(None)

        # LEGACY: make a Query back from the select() !!
        # This suits at least two legacy cases:
        # 1. applications which expect before_compile() to be called
        #    below when we run .subquery() on this query (Keystone)
        # 2. applications which are doing subqueryload with complex
        #    from_self() queries, as query.subquery() / .statement
        #    has to do the full compile context for multiply-nested
        #    from_self() (Neutron) - see test_subqload_from_self
        #    for demo.
        q2 = query.Query.__new__(query.Query)
        q2.__dict__.update(q.__dict__)
        q = q2

        # set the query's "FROM" list explicitly to what the
        # FROM list would be in any case, as we will be limiting
        # the columns in the SELECT list which may no longer include
        # all entities mentioned in things like WHERE, JOIN, etc.
        if not q._from_obj:
            q._enable_assertions = False
            q.select_from.non_generative(
                q,
                *{
                    ent["entity"]
                    for ent in _column_descriptions(
                        orig_query, compile_state=orig_compile_state
                    )
                    if ent["entity"] is not None
                },
            )

        # select from the identity columns of the outer (specifically, these
        # are the 'local_cols' of the property).  This will remove other
        # columns from the query that might suggest the right entity which is
        # why we do set select_from above.   The attributes we have are
        # coerced and adapted using the original query's adapter, which is
        # needed only for the case of adapting a subclass column to
        # that of a polymorphic selectable, e.g. we have
        # Engineer.primary_language and the entity is Person.  All other
        # adaptations, e.g. from_self, select_entity_from(), will occur
        # within the new query when it compiles, as the compile_state we are
        # using here is only a partial one.  If the subqueryload is from a
        # with_polymorphic() or other aliased() object, left_attr will already
        # be the correct attributes so no adaptation is needed.
        target_cols = orig_compile_state._adapt_col_list(
            [
                sql.coercions.expect(sql.roles.ColumnsClauseRole, o)
                for o in leftmost_attr
            ],
            orig_compile_state._get_current_adapter(),
        )
        q._raw_columns = target_cols

        distinct_target_key = leftmost_relationship.distinct_target_key

        if distinct_target_key is True:
            q._distinct = True
        elif distinct_target_key is None:
            # if target_cols refer to a non-primary key or only
            # part of a composite primary key, set the q as distinct
            for t in {c.table for c in target_cols}:
                if not set(target_cols).issuperset(t.primary_key):
                    q._distinct = True
                    break

        # don't need ORDER BY if no limit/offset
        if not q._has_row_limiting_clause:
            q._order_by_clauses = ()

        if q._distinct is True and q._order_by_clauses:
            # the logic to automatically add the order by columns to the query
            # when distinct is True is deprecated in the query
            to_add = sql_util.expand_column_list_from_order_by(
                target_cols, q._order_by_clauses
            )
            if to_add:
                q._set_entities(target_cols + to_add)

        # the original query now becomes a subquery
        # which we'll join onto.
        # LEGACY: as "q" is a Query, the before_compile() event is invoked
        # here.
        embed_q = q.set_label_style(LABEL_STYLE_TABLENAME_PLUS_COL).subquery()
        left_alias = orm_util.AliasedClass(
            leftmost_mapper, embed_q, use_mapper_path=True
        )
        return left_alias

    def _prep_for_joins(self, left_alias, subq_path):
        # figure out what's being joined.  a.k.a. the fun part
        to_join = []
        pairs = list(subq_path.pairs())

        for i, (mapper, prop) in enumerate(pairs):
            if i > 0:
                # look at the previous mapper in the chain -
                # if it is as or more specific than this prop's
                # mapper, use that instead.
                # note we have an assumption here that
                # the non-first element is always going to be a mapper,
                # not an AliasedClass

                prev_mapper = pairs[i - 1][1].mapper
                to_append = prev_mapper if prev_mapper.isa(mapper) else mapper
            else:
                to_append = mapper

            to_join.append((to_append, prop.key))

        # determine the immediate parent class we are joining from,
        # which needs to be aliased.

        if len(to_join) < 2:
            # in the case of a one level eager load, this is the
            # leftmost "left_alias".
            parent_alias = left_alias
        else:
            info = inspect(to_join[-1][0])
            if info.is_aliased_class:
                parent_alias = info.entity
            else:
                # alias a plain mapper as we may be
                # joining multiple times
                parent_alias = orm_util.AliasedClass(
                    info.entity, use_mapper_path=True
                )

        local_cols = self.parent_property.local_columns

        local_attr = [
            getattr(parent_alias, self.parent._columntoproperty[c].key)
            for c in local_cols
        ]
        return to_join, local_attr, parent_alias

    def _apply_joins(
        self, q, to_join, left_alias, parent_alias, effective_entity
    ):
        ltj = len(to_join)
        if ltj == 1:
            to_join = [
                getattr(left_alias, to_join[0][1]).of_type(effective_entity)
            ]
        elif ltj == 2:
            to_join = [
                getattr(left_alias, to_join[0][1]).of_type(parent_alias),
                getattr(parent_alias, to_join[-1][1]).of_type(
                    effective_entity
                ),
            ]
        elif ltj > 2:
            middle = [
                (
                    (
                        orm_util.AliasedClass(item[0])
                        if not inspect(item[0]).is_aliased_class
                        else item[0].entity
                    ),
                    item[1],
                )
                for item in to_join[1:-1]
            ]
            inner = []

            while middle:
                item = middle.pop(0)
                attr = getattr(item[0], item[1])
                if middle:
                    attr = attr.of_type(middle[0][0])
                else:
                    attr = attr.of_type(parent_alias)

                inner.append(attr)

            to_join = (
                [getattr(left_alias, to_join[0][1]).of_type(inner[0].parent)]
                + inner
                + [
                    getattr(parent_alias, to_join[-1][1]).of_type(
                        effective_entity
                    )
                ]
            )

        for attr in to_join:
            q = q.join(attr)

        return q

    def _setup_options(
        self,
        context,
        q,
        subq_path,
        rewritten_path,
        orig_query,
        effective_entity,
        loadopt,
    ):
        # note that because the subqueryload object
        # does not reuse the cached query, instead always making
        # use of the current invoked query, while we have two queries
        # here (orig and context.query), they are both non-cached
        # queries and we can transfer the options as is without
        # adjusting for new criteria.   Some work on #6881 / #6889
        # brought this into question.
        new_options = orig_query._with_options

        if loadopt and loadopt._extra_criteria:
            new_options += (
                orm_util.LoaderCriteriaOption(
                    self.entity,
                    loadopt._generate_extra_criteria(context),
                ),
            )

        # propagate loader options etc. to the new query.
        # these will fire relative to subq_path.
        q = q._with_current_path(rewritten_path)
        q = q.options(*new_options)

        return q

    def _setup_outermost_orderby(self, q):
        if self.parent_property.order_by:

            def _setup_outermost_orderby(compile_context):
                compile_context.eager_order_by += tuple(
                    util.to_list(self.parent_property.order_by)
                )

            q = q._add_compile_state_func(
                _setup_outermost_orderby, self.parent_property
            )

        return q

    class _SubqCollections:
        """Given a :class:`_query.Query` used to emit the "subquery load",
        provide a load interface that executes the query at the
        first moment a value is needed.

        """

        __slots__ = (
            "session",
            "execution_options",
            "load_options",
            "params",
            "subq",
            "_data",
        )

        def __init__(self, context, subq):
            # avoid creating a cycle by storing context
            # even though that's preferable
            self.session = context.session
            self.execution_options = context.execution_options
            self.load_options = context.load_options
            self.params = context.params or {}
            self.subq = subq
            self._data = None

        def get(self, key, default):
            if self._data is None:
                self._load()
            return self._data.get(key, default)

        def _load(self):
            self._data = collections.defaultdict(list)

            q = self.subq
            assert q.session is None

            q = q.with_session(self.session)

            if self.load_options._populate_existing:
                q = q.populate_existing()
            # to work with baked query, the parameters may have been
            # updated since this query was created, so take these into account

            rows = list(q.params(self.params))
            for k, v in itertools.groupby(rows, lambda x: x[1:]):
                self._data[k].extend(vv[0] for vv in v)

        def loader(self, state, dict_, row):
            if self._data is None:
                self._load()

    def _setup_query_from_rowproc(
        self,
        context,
        query_entity,
        path,
        entity,
        loadopt,
        adapter,
    ):
        compile_state = context.compile_state
        if (
            not compile_state.compile_options._enable_eagerloads
            or compile_state.compile_options._for_refresh_state
        ):
            return

        orig_query_entity_index = compile_state._entities.index(query_entity)
        context.loaders_require_buffering = True

        path = path[self.parent_property]

        # build up a path indicating the path from the leftmost
        # entity to the thing we're subquery loading.
        with_poly_entity = path.get(
            compile_state.attributes, "path_with_polymorphic", None
        )
        if with_poly_entity is not None:
            effective_entity = with_poly_entity
        else:
            effective_entity = self.entity

        subq_path, rewritten_path = context.query._execution_options.get(
            ("subquery_paths", None),
            (orm_util.PathRegistry.root, orm_util.PathRegistry.root),
        )
        is_root = subq_path is orm_util.PathRegistry.root
        subq_path = subq_path + path
        rewritten_path = rewritten_path + path

        # use the current query being invoked, not the compile state
        # one.  this is so that we get the current parameters.  however,
        # it means we can't use the existing compile state, we have to make
        # a new one.    other approaches include possibly using the
        # compiled query but swapping the params, seems only marginally
        # less time spent but more complicated
        orig_query = context.query._execution_options.get(
            ("orig_query", _SubqueryLoader), context.query
        )

        # make a new compile_state for the query that's probably cached, but
        # we're sort of undoing a bit of that caching :(
        compile_state_cls = _ORMCompileState._get_plugin_class_for_plugin(
            orig_query, "orm"
        )

        if orig_query._is_lambda_element:
            if context.load_options._lazy_loaded_from is None:
                util.warn(
                    'subqueryloader for "%s" must invoke lambda callable '
                    "at %r in "
                    "order to produce a new query, decreasing the efficiency "
                    "of caching for this statement.  Consider using "
                    "selectinload() for more effective full-lambda caching"
                    % (self, orig_query)
                )
            orig_query = orig_query._resolved

        # this is the more "quick" version, however it's not clear how
        # much of this we need.    in particular I can't get a test to
        # fail if the "set_base_alias" is missing and not sure why that is.
        orig_compile_state = compile_state_cls._create_entities_collection(
            orig_query, legacy=False
        )

        (
            leftmost_mapper,
            leftmost_attr,
            leftmost_relationship,
            rewritten_path,
        ) = self._get_leftmost(
            orig_query_entity_index,
            rewritten_path,
            orig_compile_state,
            is_root,
        )

        # generate a new Query from the original, then
        # produce a subquery from it.
        left_alias = self._generate_from_original_query(
            orig_compile_state,
            orig_query,
            leftmost_mapper,
            leftmost_attr,
            leftmost_relationship,
            entity,
        )

        # generate another Query that will join the
        # left alias to the target relationships.
        # basically doing a longhand
        # "from_self()".  (from_self() itself not quite industrial
        # strength enough for all contingencies...but very close)

        q = query.Query(effective_entity)

        q._execution_options = context.query._execution_options.merge_with(
            context.execution_options,
            {
                ("orig_query", _SubqueryLoader): orig_query,
                ("subquery_paths", None): (subq_path, rewritten_path),
            },
        )

        q = q._set_enable_single_crit(False)
        to_join, local_attr, parent_alias = self._prep_for_joins(
            left_alias, subq_path
        )

        q = q.add_columns(*local_attr)
        q = self._apply_joins(
            q, to_join, left_alias, parent_alias, effective_entity
        )

        q = self._setup_options(
            context,
            q,
            subq_path,
            rewritten_path,
            orig_query,
            effective_entity,
            loadopt,
        )
        q = self._setup_outermost_orderby(q)

        return q

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        if (
            loadopt
            and context.compile_state.statement is not None
            and context.compile_state.statement.is_dml
        ):
            util.warn_deprecated(
                "The subqueryload loader option is not compatible with DML "
                "statements such as INSERT, UPDATE.  Only SELECT may be used."
                "This warning will become an exception in a future release.",
                "2.0",
            )

        if context.refresh_state:
            return self._immediateload_create_row_processor(
                context,
                query_entity,
                path,
                loadopt,
                mapper,
                result,
                adapter,
                populators,
            )

        _, run_loader, _, _ = self._setup_for_recursion(
            context, path, loadopt, self.join_depth
        )
        if not run_loader:
            return

        if not isinstance(context.compile_state, _ORMSelectCompileState):
            # issue 7505 - subqueryload() in 1.3 and previous would silently
            # degrade for from_statement() without warning. this behavior
            # is restored here
            return

        if not self.parent.class_manager[self.key].impl.supports_population:
            raise sa_exc.InvalidRequestError(
                "'%s' does not support object "
                "population - eager loading cannot be applied." % self
            )

        # a little dance here as the "path" is still something that only
        # semi-tracks the exact series of things we are loading, still not
        # telling us about with_polymorphic() and stuff like that when it's at
        # the root..  the initial MapperEntity is more accurate for this case.
        if len(path) == 1:
            if not orm_util._entity_isa(query_entity.entity_zero, self.parent):
                return
        elif not orm_util._entity_isa(
            path[-1], self.parent
        ) and not self.parent.isa(path[-1].mapper):
            # second check accommodates a polymorphic entity where
            # the path has been normalized to the base mapper but
            # self.parent is a subclass mapper.  Fixes #13209.
            return

        subq = self._setup_query_from_rowproc(
            context,
            query_entity,
            path,
            path[-1],
            loadopt,
            adapter,
        )

        if subq is None:
            return

        assert subq.session is None

        path = path[self.parent_property]

        local_cols = self.parent_property.local_columns

        # cache the loaded collections in the context
        # so that inheriting mappers don't re-load when they
        # call upon create_row_processor again
        collections = path.get(context.attributes, "collections")
        if collections is None:
            collections = self._SubqCollections(context, subq)
            path.set(context.attributes, "collections", collections)

        if adapter:
            local_cols = [adapter.columns[c] for c in local_cols]

        if self.uselist:
            self._create_collection_loader(
                context, result, collections, local_cols, populators
            )
        else:
            self._create_scalar_loader(
                context, result, collections, local_cols, populators
            )

    def _create_collection_loader(
        self, context, result, collections, local_cols, populators
    ):
        tuple_getter = result._tuple_getter(local_cols)

        def load_collection_from_subq(state, dict_, row):
            collection = collections.get(tuple_getter(row), ())
            state.get_impl(self.key).set_committed_value(
                state, dict_, collection
            )

        def load_collection_from_subq_existing_row(state, dict_, row):
            if self.key not in dict_:
                load_collection_from_subq(state, dict_, row)

        populators["new"].append((self.key, load_collection_from_subq))
        populators["existing"].append(
            (self.key, load_collection_from_subq_existing_row)
        )

        if context.invoke_all_eagers:
            populators["eager"].append((self.key, collections.loader))

    def _create_scalar_loader(
        self, context, result, collections, local_cols, populators
    ):
        tuple_getter = result._tuple_getter(local_cols)

        def load_scalar_from_subq(state, dict_, row):
            collection = collections.get(tuple_getter(row), (None,))
            if len(collection) > 1:
                util.warn(
                    "Multiple rows returned with "
                    "uselist=False for eagerly-loaded attribute '%s' " % self
                )

            scalar = collection[0]
            state.get_impl(self.key).set_committed_value(state, dict_, scalar)

        def load_scalar_from_subq_existing_row(state, dict_, row):
            if self.key not in dict_:
                load_scalar_from_subq(state, dict_, row)

        populators["new"].append((self.key, load_scalar_from_subq))
        populators["existing"].append(
            (self.key, load_scalar_from_subq_existing_row)
        )
        if context.invoke_all_eagers:
            populators["eager"].append((self.key, collections.loader))


@log.class_logger
@relationships.RelationshipProperty.strategy_for(lazy="joined")
@relationships.RelationshipProperty.strategy_for(lazy=False)
class _JoinedLoader(_AbstractRelationshipLoader):
    """Provide loading behavior for a :class:`.Relationship`
    using joined eager loading.

    """

    __slots__ = "join_depth"

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.join_depth = self.parent_property.join_depth

    def init_class_attribute(self, mapper):
        self.parent_property._get_strategy(
            (("lazy", "select"),)
        ).init_class_attribute(mapper)

    def setup_query(
        self,
        compile_state,
        query_entity,
        path,
        loadopt,
        adapter,
        column_collection=None,
        parentmapper=None,
        chained_from_outerjoin=False,
        **kwargs,
    ):
        """Add a left outer join to the statement that's being constructed."""
        pass

    def _init_user_defined_eager_proc(
        self, loadopt, compile_state, target_attributes
    ):
        # check if the opt applies at all
        if "eager_from_alias" not in loadopt.local_opts:
            # nope
            return False

        path = loadopt.path.parent

        # the option applies.  check if the "user_defined_eager_row_processor"
        # has been built up.
        adapter = path.get(
            compile_state.attributes, "user_defined_eager_row_processor", False
        )
        if adapter is not False:
            # just return it
            return adapter

        # otherwise figure it out.
        alias = loadopt.local_opts["eager_from_alias"]
        root_mapper, prop = path[-2:]

        if alias is not None:
            if isinstance(alias, str):
                alias = prop.target.alias(alias)
            adapter = orm_util.ORMAdapter(
                orm_util._TraceAdaptRole.JOINEDLOAD_USER_DEFINED_ALIAS,
                prop.mapper,
                selectable=alias,
                equivalents=prop.mapper._equivalent_columns,
                limit_on_entity=False,
            )
        else:
            if path.contains(
                compile_state.attributes, "path_with_polymorphic"
            ):
                with_poly_entity = path.get(
                    compile_state.attributes, "path_with_polymorphic"
                )
                adapter = orm_util.ORMAdapter(
                    orm_util._TraceAdaptRole.JOINEDLOAD_PATH_WITH_POLYMORPHIC,
                    with_poly_entity,
                    equivalents=prop.mapper._equivalent_columns,
                )
            else:
                adapter = compile_state._polymorphic_adapters.get(
                    prop.mapper, None
                )
        path.set(
            target_attributes,
            "user_defined_eager_row_processor",
            adapter,
        )

        return adapter

    def _setup_query_on_user_defined_adapter(
        self, context, entity, path, adapter, user_defined_adapter
    ):
        # apply some more wrapping to the "user defined adapter"
        # if we are setting up the query for SQL render.
        pass

    def _generate_row_adapter(
        self,
        compile_state,
        entity,
        path,
        loadopt,
        adapter,
        column_collection,
        parentmapper,
        chained_from_outerjoin,
    ):
        pass

    def _create_eager_join(
        self,
        compile_state,
        query_entity,
        path,
        adapter,
        parentmapper,
        clauses,
        innerjoin,
        chained_from_outerjoin,
        extra_criteria,
    ):
        pass

    def _splice_nested_inner_join(
        self,
        path,
        entity_we_want_to_splice_onto,
        join_obj,
        clauses,
        onclause,
        extra_criteria,
        entity_inside_join_structure: Union[
            Mapper, None, Literal[False]
        ] = False,
        detected_existing_path: Optional[path_registry.PathRegistry] = None,
    ):
        # recursive fn to splice a nested join into an existing one.
        # entity_inside_join_structure=False means this is the outermost call,
        # and it should return a value.  entity_inside_join_structure=<mapper>
        # indicates we've descended into a join and are looking at a FROM
        # clause representing this mapper; if this is not
        # entity_we_want_to_splice_onto then return None to end the recursive
        # branch

        pass

    def _create_eager_adapter(self, context, result, adapter, path, loadopt):
        compile_state = context.compile_state

        user_defined_adapter = (
            self._init_user_defined_eager_proc(
                loadopt, compile_state, context.attributes
            )
            if loadopt
            else False
        )

        if user_defined_adapter is not False:
            decorator = user_defined_adapter
            # user defined eagerloads are part of the "primary"
            # portion of the load.
            # the adapters applied to the Query should be honored.
            if compile_state.compound_eager_adapter and decorator:
                decorator = decorator.wrap(
                    compile_state.compound_eager_adapter
                )
            elif compile_state.compound_eager_adapter:
                decorator = compile_state.compound_eager_adapter
        else:
            decorator = path.get(
                compile_state.attributes, "eager_row_processor"
            )
            if decorator is None:
                return False

        if self.mapper._result_has_identity_key(result, decorator):
            return decorator
        else:
            # no identity key - don't return a row
            # processor, will cause a degrade to lazy
            return False

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):

        if not context.compile_state.compile_options._enable_eagerloads:
            return

        if not self.parent.class_manager[self.key].impl.supports_population:
            raise sa_exc.InvalidRequestError(
                "'%s' does not support object "
                "population - eager loading cannot be applied." % self
            )

        if self.uselist:
            context.loaders_require_uniquing = True

        our_path = path[self.parent_property]

        eager_adapter = self._create_eager_adapter(
            context, result, adapter, our_path, loadopt
        )

        if eager_adapter is not False:
            key = self.key

            _instance = loading._instance_processor(
                query_entity,
                self.mapper,
                context,
                result,
                our_path[self.entity],
                eager_adapter,
            )

            if not self.uselist:
                self._create_scalar_loader(context, key, _instance, populators)
            else:
                self._create_collection_loader(
                    context, key, _instance, populators
                )
        else:
            self.parent_property._get_strategy(
                (("lazy", "select"),)
            ).create_row_processor(
                context,
                query_entity,
                path,
                loadopt,
                mapper,
                result,
                adapter,
                populators,
            )

    def _create_collection_loader(self, context, key, _instance, populators):
        def load_collection_from_joined_new_row(state, dict_, row):
            # note this must unconditionally clear out any existing collection.
            # an existing collection would be present only in the case of
            # populate_existing().
            collection = attributes.init_state_collection(state, dict_, key)
            result_list = util.UniqueAppender(
                collection, "append_without_event"
            )
            context.attributes[(state, key)] = result_list
            inst = _instance(row)
            if inst is not None:
                result_list.append(inst)

        def load_collection_from_joined_existing_row(state, dict_, row):
            if (state, key) in context.attributes:
                result_list = context.attributes[(state, key)]
            else:
                # appender_key can be absent from context.attributes
                # with isnew=False when self-referential eager loading
                # is used; the same instance may be present in two
                # distinct sets of result columns
                collection = attributes.init_state_collection(
                    state, dict_, key
                )
                result_list = util.UniqueAppender(
                    collection, "append_without_event"
                )
                context.attributes[(state, key)] = result_list
            inst = _instance(row)
            if inst is not None:
                result_list.append(inst)

        def load_collection_from_joined_exec(state, dict_, row):
            _instance(row)

        populators["new"].append(
            (self.key, load_collection_from_joined_new_row)
        )
        populators["existing"].append(
            (self.key, load_collection_from_joined_existing_row)
        )
        if context.invoke_all_eagers:
            populators["eager"].append(
                (self.key, load_collection_from_joined_exec)
            )

    def _create_scalar_loader(self, context, key, _instance, populators):
        def load_scalar_from_joined_new_row(state, dict_, row):
            # set a scalar object instance directly on the parent
            # object, bypassing InstrumentedAttribute event handlers.
            dict_[key] = _instance(row)

        def load_scalar_from_joined_existing_row(state, dict_, row):
            # call _instance on the row, even though the object has
            # been created, so that we further descend into properties
            existing = _instance(row)

            # conflicting value already loaded, this shouldn't happen
            if key in dict_:
                if existing is not dict_[key]:
                    util.warn(
                        "Multiple rows returned with "
                        "uselist=False for eagerly-loaded attribute '%s' "
                        % self
                    )
            else:
                # this case is when one row has multiple loads of the
                # same entity (e.g. via aliasing), one has an attribute
                # that the other doesn't.
                dict_[key] = existing

        def load_scalar_from_joined_exec(state, dict_, row):
            _instance(row)

        populators["new"].append((self.key, load_scalar_from_joined_new_row))
        populators["existing"].append(
            (self.key, load_scalar_from_joined_existing_row)
        )
        if context.invoke_all_eagers:
            populators["eager"].append(
                (self.key, load_scalar_from_joined_exec)
            )


@log.class_logger
@relationships.RelationshipProperty.strategy_for(lazy="selectin")
class _SelectInLoader(_PostLoader, util.MemoizedSlots):
    __slots__ = (
        "join_depth",
        "omit_join",
        "_parent_alias",
        "_query_info",
        "_fallback_query_info",
    )

    query_info = collections.namedtuple(
        "queryinfo",
        [
            "load_only_child",
            "load_with_join",
            "in_expr",
            "pk_cols",
            "zero_idx",
            "child_lookup_cols",
        ],
    )

    _chunksize = 500

    def __init__(self, parent, strategy_key):
        super().__init__(parent, strategy_key)
        self.join_depth = self.parent_property.join_depth
        is_m2o = self.parent_property.direction is interfaces.MANYTOONE

        if self.parent_property.omit_join is not None:
            self.omit_join = self.parent_property.omit_join
        else:
            lazyloader = self.parent_property._get_strategy(
                (("lazy", "select"),)
            )
            if is_m2o:
                self.omit_join = lazyloader.use_get
            else:
                self.omit_join = self.parent._get_clause[0].compare(
                    lazyloader._rev_lazywhere,
                    use_proxies=True,
                    compare_keys=False,
                    equivalents=self.parent._equivalent_columns,
                )

        if self.omit_join:
            if is_m2o:
                self._query_info = self._init_for_omit_join_m2o()
                self._fallback_query_info = self._init_for_join()
            else:
                self._query_info = self._init_for_omit_join()
        else:
            self._query_info = self._init_for_join()

    def _init_for_omit_join(self):
        pk_to_fk = dict(
            self.parent_property._join_condition.local_remote_pairs
        )
        pk_to_fk.update(
            (equiv, pk_to_fk[k])
            for k in list(pk_to_fk)
            for equiv in self.parent._equivalent_columns.get(k, ())
        )

        pk_cols = fk_cols = [
            pk_to_fk[col] for col in self.parent.primary_key if col in pk_to_fk
        ]
        if len(fk_cols) > 1:
            in_expr = sql.tuple_(*fk_cols)
            zero_idx = False
        else:
            in_expr = fk_cols[0]
            zero_idx = True

        return self.query_info(False, False, in_expr, pk_cols, zero_idx, None)

    def _init_for_omit_join_m2o(self):
        pk_cols = self.mapper.primary_key
        if len(pk_cols) > 1:
            in_expr = sql.tuple_(*pk_cols)
            zero_idx = False
        else:
            in_expr = pk_cols[0]
            zero_idx = True

        lazyloader = self.parent_property._get_strategy((("lazy", "select"),))
        lookup_cols = [lazyloader._equated_columns[pk] for pk in pk_cols]

        return self.query_info(
            True, False, in_expr, pk_cols, zero_idx, lookup_cols
        )

    def _init_for_join(self):
        self._parent_alias = AliasedClass(self.parent.class_)
        pa_insp = inspect(self._parent_alias)
        pk_cols = [
            pa_insp._adapt_element(col) for col in self.parent.primary_key
        ]
        if len(pk_cols) > 1:
            in_expr = sql.tuple_(*pk_cols)
            zero_idx = False
        else:
            in_expr = pk_cols[0]
            zero_idx = True
        return self.query_info(False, True, in_expr, pk_cols, zero_idx, None)

    def init_class_attribute(self, mapper):
        self.parent_property._get_strategy(
            (("lazy", "select"),)
        ).init_class_attribute(mapper)

    def create_row_processor(
        self,
        context,
        query_entity,
        path,
        loadopt,
        mapper,
        result,
        adapter,
        populators,
    ):
        if context.refresh_state:
            return self._immediateload_create_row_processor(
                context,
                query_entity,
                path,
                loadopt,
                mapper,
                result,
                adapter,
                populators,
            )

        (
            effective_path,
            run_loader,
            execution_options,
            recursion_depth,
        ) = self._setup_for_recursion(
            context, path, loadopt, join_depth=self.join_depth
        )

        if not run_loader:
            return

        if not context.compile_state.compile_options._enable_eagerloads:
            return

        if not self.parent.class_manager[self.key].impl.supports_population:
            raise sa_exc.InvalidRequestError(
                "'%s' does not support object "
                "population - eager loading cannot be applied." % self
            )

        # a little dance here as the "path" is still something that only
        # semi-tracks the exact series of things we are loading, still not
        # telling us about with_polymorphic() and stuff like that when it's at
        # the root..  the initial MapperEntity is more accurate for this case.
        if len(path) == 1:
            if not orm_util._entity_isa(query_entity.entity_zero, self.parent):
                return
        elif not orm_util._entity_isa(
            path[-1], self.parent
        ) and not self.parent.isa(path[-1].mapper):
            # second check accommodates a polymorphic entity where
            # the path has been normalized to the base mapper but
            # self.parent is a subclass mapper, e.g.
            # joinedload(A.b.of_type(poly)).selectinload(poly.Sub.rel)
            # Fixes #13209.
            return

        selectin_path = effective_path

        path_w_prop = path[self.parent_property]

        # build up a path indicating the path from the leftmost
        # entity to the thing we're subquery loading.
        with_poly_entity = path_w_prop.get(
            context.attributes, "path_with_polymorphic", None
        )
        if with_poly_entity is not None:
            effective_entity = inspect(with_poly_entity)
        else:
            effective_entity = self.entity

        loading._PostLoad.callable_for_path(
            context,
            selectin_path,
            self.parent,
            self.parent_property,
            self._load_for_path,
            effective_entity,
            loadopt,
            recursion_depth,
            execution_options,
        )

    def _load_for_path(
        self,
        context,
        path,
        states,
        load_only,
        effective_entity,
        loadopt,
        recursion_depth,
        execution_options,
    ):
        pass

    def _load_via_child(
        self,
        our_states,
        none_states,
        query_info,
        q,
        context,
        execution_options,
    ):
        pass

    def _load_via_parent(
        self, our_states, query_info, q, context, execution_options
    ):
        pass


def _single_parent_validator(desc, prop):
    pass
