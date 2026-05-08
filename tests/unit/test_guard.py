import pytest
from patcha.utils.guard import CollectorGuard


@pytest.mark.unit
def test_ok_initially():
    g = CollectorGuard("x")
    assert g.ok is True


@pytest.mark.unit
def test_fail_increments_but_stays_enabled():
    g = CollectorGuard("x", max_failures=3)
    g.fail(Exception("e1"))
    assert g.ok is True
    g.fail(Exception("e2"))
    assert g.ok is True


@pytest.mark.unit
def test_disabled_after_max_failures():
    g = CollectorGuard("x", max_failures=3)
    for i in range(3):
        g.fail(Exception(f"e{i}"))
    assert g.ok is False


@pytest.mark.unit
def test_success_resets_counter():
    g = CollectorGuard("x", max_failures=3)
    g.fail(Exception("e1"))
    g.fail(Exception("e2"))
    g.success()
    g.fail(Exception("e3"))
    assert g.ok is True


@pytest.mark.unit
def test_fail_when_disabled_is_noop():
    g = CollectorGuard("x", max_failures=1)
    g.fail(Exception("first"))
    assert g.ok is False
    g.fail(Exception("second"))
    assert g.ok is False
    assert g._failures == 1


@pytest.mark.unit
def test_custom_max_failures():
    g = CollectorGuard("x", max_failures=1)
    g.fail(Exception("e"))
    assert g.ok is False


@pytest.mark.unit
def test_custom_msg_overrides_exception_str():
    g = CollectorGuard("x", max_failures=3)
    g.fail(Exception("raw"), msg="custom message")
    assert g._failures == 1
