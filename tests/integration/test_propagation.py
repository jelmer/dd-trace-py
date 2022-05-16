import json

import pytest

from ddtrace import Tracer
from ddtrace import config
from ddtrace.internal.compat import StringIO
from ddtrace.internal.writer import LogWriter
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_global_config


@pytest.fixture(
    params=[
        dict(global_config=dict()),
        dict(
            global_config=dict(_x_datadog_tags_max_length="0", _x_datadog_tags_enabled=False),
        ),
        dict(global_config=dict(), partial_flush_enabled=True, partial_flush_min_spans=2),
    ]
)
def tracer(request):
    global_config = request.param["global_config"]
    partial_flush_enabled = request.param.get("partial_flush_enabled", False)
    partial_flush_min_spans = request.param.get("partial_flush_min_spans", None)
    with override_global_config(request.param):
        tracer = Tracer()
        out = StringIO()
        kwargs = dict(writer=LogWriter(out=out))
        if partial_flush_enabled is True:
            kwargs["partial_flush_enabled"] = True
        if partial_flush_min_spans is not None:
            kwargs["partial_flush_min_spans"] = partial_flush_min_spans
        tracer.configure(**kwargs)
        yield tracer
        tracer.shutdown()


@pytest.mark.parametrize(
    "trace_tags_and_span_check",
    [
        ("_dd.p.dm=value,any=tag", lambda s: s["meta"].get("_dd.p.dm") == "value" and s["meta"].get("any") == "tag"),
        ("_dd.p.upstream_services=foo", lambda s: "meta" not in s or "_dd.p.upstream_services" not in s["meta"]),
    ],
)
def test_trace_tags_multispan(tracer, trace_tags_and_span_check):
    # type: (Tracer, str) -> None
    trace_tags, span_check = trace_tags_and_span_check
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-tags": trace_tags,
    }
    context = HTTPPropagator.extract(headers)

    # DEV: Trace consists of a simple p->c1 case where c1 is finished before p.
    # But the trace also includes p->c2->gc where c2 and p are finished before
    # gc is finished.
    p = tracer.start_span("p", child_of=context)
    c1 = tracer.start_span("c1", child_of=p)
    c1.finish()
    c2 = tracer.start_span("c2", child_of=p)
    gc = tracer.start_span("gc", child_of=c2)
    c2.finish()
    p.finish()
    gc.finish()

    # DEV: Log writer output will contain newline delimited json
    payloads = tracer._writer.out.getvalue().strip().split("\n")

    # DEV: Payloads will be different if partial flushing enabled
    if not tracer._partial_flush_enabled:
        assert len(payloads) == 1
        traces = json.loads(payloads[0])["traces"]
        assert len(traces[0]) == 4
        assert traces[0][0]["name"] == "p"
        if config._x_datadog_tags_enabled is True:
            assert span_check(traces[0][0])
        else:
            assert len(traces[0][0]["meta"]) == 1
        assert traces[0][1]["name"] == "c1"
        assert any("meta" not in s for s in traces[0][1:])
    else:
        assert len(payloads) == 2
        p1traces = json.loads(payloads[0])["traces"]
        p2traces = json.loads(payloads[1])["traces"]
        assert len(p1traces[0]) == 2
        assert len(p2traces[0]) == 2
        assert p1traces[0][0]["name"] == "c1"
        assert p1traces[0][1]["name"] == "c2"
        if config._x_datadog_tags_enabled is True:
            assert span_check(p1traces[0][0])
        else:
            assert "meta" not in p1traces[0][0]
            assert "meta" not in p1traces[0][1]
        assert p2traces[0][0]["name"] == "p"
        assert p2traces[0][1]["name"] == "gc"
        if config._x_datadog_tags_enabled is True:
            assert span_check(p2traces[0][0])
        else:
            assert len(p2traces[0][0]["meta"]) == 2
        assert "meta" not in p2traces[0][1]
