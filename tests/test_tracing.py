from cbm_memory.tracing import Tracer


def test_start_and_finish_span():
    tracer = Tracer()
    span = tracer.start("op")
    span.finish(result="ok")
    assert span.duration_s is not None
    assert span.duration_s >= 0
    exported = tracer.export()
    assert len(exported) == 1
    assert exported[0]["name"] == "op"
    assert exported[0]["attributes"]["result"] == "ok"


def test_parent_child_linkage():
    tracer = Tracer()
    parent = tracer.start("parent")
    child = tracer.start("child", parent=parent)
    assert child.trace_id == parent.trace_id
    assert child.parent_id == parent.span_id


def test_bounded_export():
    tracer = Tracer(max_spans=2)
    tracer.start("a")
    tracer.start("b")
    tracer.start("c")
    assert len(tracer.spans()) == 2
    assert [s.name for s in tracer.spans()] == ["b", "c"]


def test_clear():
    tracer = Tracer()
    tracer.start("a")
    tracer.clear()
    assert tracer.spans() == []


def test_invalid_inputs():
    try:
        Tracer(max_spans=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero max_spans accepted")
    tracer = Tracer()
    try:
        tracer.start("  ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank name accepted")
