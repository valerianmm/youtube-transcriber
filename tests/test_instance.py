from ytt.instance import InstanceLock, running_port


def test_second_lock_fails_and_reports_port():
    a, b = InstanceLock(), InstanceLock()
    assert running_port() is None
    assert a.acquire(8765) is True
    assert b.acquire(9999) is False
    assert running_port() == 8765
    a.release()
    assert running_port() is None
    assert b.acquire(9999) is True
    b.release()
