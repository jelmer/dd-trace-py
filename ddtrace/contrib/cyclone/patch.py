"""
"""
from ddtrace import config, Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes, errors
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator
from .. import trace_utils


config._add(
    "cyclone",
    dict(
        _default_service="cyclone",
        distributed_tracing_enabled=True,
    ),
)


log = get_logger(__name__)


@trace_utils.with_traced_module
def traced_requesthandler_deferred_handler(cyclone, pin, func, instance, args, kwargs):
    prev_active = pin.tracer.active()

    active = prev_active
    if config.cyclone.distributed_tracing_enabled:
        propagator = HTTPPropagator()
        context = propagator.extract(instance.request.headers)
        if context.trace_id:
            active = context

    span = pin.tracer.start_span(
        "cyclone.request",
        service=trace_utils.int_service(pin, config.cyclone),
        span_type=SpanTypes.WEB,
        child_of=active,
        activate=False,
    )
    setattr(instance.request, "__datadog_span", span)

    try:
        cls = instance.__class__
        req = instance.request
        handler = "%s.%s" % (cls.__module__, cls.__name__)
        span.resource = "%s %s" % (req.method, handler)
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag("cyclone.handler", handler)
        analytics_sr = config.cyclone.get_analytics_sample_rate(use_global_config=True)
        if analytics_sr is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)
        trace_utils.store_request_headers(req.headers, span, config.cyclone)
    except Exception:
        log.error("error occurred in instrumentation", exc_info=True)

    # The deferred created in _deferred_handler will have a reference to
    # the span and so it will be propagated.
    try:
        pin.tracer.activate(span)
        return func(*args, **kwargs)
    finally:
        pin.tracer.activate(prev_active)


@trace_utils.with_traced_module
def traced_requesthandler_finish(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span", None)
    if not span:
        log.error("no span found on request, possible memory leak")
        return func(*args, **kwargs)

    try:
        status_code = int(instance.get_status())
        url = req.full_url().rsplit("?", 1)[0]
        trace_utils.store_response_headers(instance._headers, span, config.cyclone)
        trace_utils.set_http_meta(
            span,
            config.cyclone,
            method=req.method,
            status_code=status_code,
            url=url,
            query=req.query,
            response_headers=instance._headers,
        )
    except Exception:
        log.error("error occurred in instrumentation", exc_info=True)
    finally:
        try:
            return func(*args, **kwargs)
        finally:
            span.finish()


@trace_utils.with_traced_module
def traced_requesthandler_handle_request_exception(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span", None)
    if not span:
        log.debug("no span found on request")
        return func(*args, **kwargs)

    try:
        exc = args[0]
        # If the exception is a twisted.python.Failure
        # then we can re-raise the exception to get the
        # exception info to report.
        if hasattr(exc, "getTraceback"):
            tb = exc.getTraceback()
            span.set_tag(errors.ERROR_STACK, tb)
        if hasattr(exc, "getErrorMessage"):
            span.set_tag(errors.ERROR_MSG, exc.getErrorMessage())
        if hasattr(exc, "type"):
            span.set_tag(errors.ERROR_TYPE, exc.type)
    finally:
        try:
            return func(*args, **kwargs)
        finally:
            span.finish()


@trace_utils.with_traced_module
def traced_requesthandler_render_string(cyclone, pin, func, instance, args, kwargs):
    template_name = args[0] if len(args) else kwargs.get("template_name")
    with pin.tracer.trace("cyclone.request.render_string", span_type=SpanTypes.TEMPLATE) as span:
        span.set_tag("template_name", template_name)
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_uimodule_render(cyclone, pin, func, instance, args, kwargs):
    with pin.tracer.trace("cyclone.uimodule.render", span_type=SpanTypes.TEMPLATE):
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_template_generate(cyclone, pin, func, instance, args, kwargs):
    with pin.tracer.trace("cyclone.template.generate", span_type=SpanTypes.TEMPLATE):
        return func(*args, **kwargs)


def patch():
    import cyclone

    if getattr(cyclone, "__datadog_patch", False):
        return

    Pin().onto(cyclone)
    trace_utils.wrap("cyclone.web", "RequestHandler._deferred_handler", traced_requesthandler_deferred_handler(cyclone))
    trace_utils.wrap("cyclone.web", "RequestHandler.finish", traced_requesthandler_finish(cyclone))
    trace_utils.wrap(
        "cyclone.web",
        "RequestHandler._handle_request_exception",
        traced_requesthandler_handle_request_exception(cyclone),
    )
    trace_utils.wrap("cyclone.web", "RequestHandler.render_string", traced_requesthandler_render_string(cyclone))
    trace_utils.wrap("cyclone.web", "UIModule.render", traced_uimodule_render(cyclone))
    trace_utils.wrap("cyclone.template", "Template.generate", traced_template_generate(cyclone))

    setattr(cyclone, "__datadog_patch", True)


def unpatch():
    import cyclone

    if not getattr(cyclone, "__datadog_patch", False):
        return

    trace_utils.unwrap(cyclone.web.RequestHandler, "_deferred_handler")
    trace_utils.unwrap(cyclone.web.RequestHandler, "finish")
    trace_utils.unwrap(cyclone.web.RequestHandler, "_handle_request_exception")
    trace_utils.unwrap(cyclone.web.RequestHandler, "render_string")
    trace_utils.unwrap(cyclone.web.UIModule, "render")
    trace_utils.unwrap(cyclone.template.Template, "generate")

    setattr(cyclone, "__datadog_patch", False)
