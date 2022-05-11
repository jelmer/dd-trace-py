import re
from typing import Union

import graphql
from graphql.language.source import Source

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.utils import get_argument_value
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...ext import SpanTypes


config._add("graphql", dict(_default_service="graphql"))


graphql_version_str = getattr(graphql, "__version__", "0.0.0")
graphql_version = tuple([int(i) for i in graphql_version_str.split(".")])


def patch():
    if getattr(graphql, "_datadog_patch", False) or graphql_version < (2, 0):
        return

    setattr(graphql, "_datadog_patch", True)

    graphql_module = "graphql.graphql"
    graphql_func = "graphql_impl"
    if graphql_version < (3, 0):
        graphql_module = "graphql"
        graphql_func = "graphql"

    parse_execute_module = "graphql.graphql"
    if (2, 1) <= graphql_version < (3, 0):
        parse_execute_module = "graphql.backend.core"

    validate_module = "graphql.validation"
    if graphql_version < (2, 1):
        validate_module = "graphql.graphql"
    elif (2, 1) <= graphql_version < (3, 0):
        validate_module = "graphql.backend.core"

    resolve_module = "graphql.execution.execute"
    # ExecutionContext.resolve_field was renamed to execute_field in graphql-core 3.2
    resolve_func = "ExecutionContext.execute_field"
    if graphql_version < (3, 0):
        resolve_module = "graphql.execution.executor"
        resolve_func = "resolve_field"
    elif graphql_version < (3, 2):
        resolve_func = "ExecutionContext.resolve_field"

    _w(graphql_module, graphql_func, _traced_graphql)
    _w(parse_execute_module, "parse", _traced_operation)
    _w(validate_module, "validate", _traced_operation)
    _w(parse_execute_module, "execute", _traced_operation)
    _w(resolve_module, resolve_func, _traced_resolver)

    Pin().onto(graphql)


def unpatch():
    pass


def _traced_graphql(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resource = _get_source_str(args, kwargs)

    with pin.tracer.trace(
        name="graphql.query",
        resource=resource,
        service=trace_utils.int_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        return func(*args, **kwargs)


def _traced_operation(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    span_name = "graphql.%s" % (func.__name__,)
    with pin.tracer.trace(
        name=span_name,
        service=trace_utils.int_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        return func(*args, **kwargs)


def _traced_resolver(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    fields_arg = 2
    fields_kw = "field_nodes"
    if graphql_version < (3, 0):
        fields_arg = 3
        fields_kw = "field_asts"
    fields_def = get_argument_value(args, kwargs, fields_arg, fields_kw)
    # field definitions should never be null/empty. A field must exist before
    # a graphql execution context attempts to resolve a query.
    # Only the first field is resolved:
    # https://github.com/graphql-python/graphql-core/blob/v3.0.0/src/graphql/execution/execute.py#L586-L593
    field_name = fields_def[0].name.value

    with pin.tracer.trace(
        name="graphql.resolve",
        resource=field_name,
        service=trace_utils.int_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        return func(*args, **kwargs)


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _get_source_str(f_args, f_kwargs):
    # type (Any, Any) -> str
    source = get_argument_value(f_args, f_kwargs, 1, "source")  # type: Union[str, Source]
    if isinstance(source, Source):
        source_str = source.body
    else:
        source_str = source
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()
