import datetime as _real_datetime
import json
import multiprocessing as mp
import pickle
import re

import pytest

from dejaview.patching.setup import datetime_patch
from dejaview.tests.util import launch_dejaview


@pytest.fixture
def setup_datetime_patching():
    with datetime_patch():
        yield


def test_json_serialization(setup_datetime_patching):
    import datetime  # noqa: PLC0415

    # Test datetime
    dt = datetime.datetime(2026, 3, 14, 12, 34, 56)
    iso = dt.isoformat()
    assert iso == "2026-03-14T12:34:56"

    data = json.dumps({"time": dt.isoformat()})
    assert data == '{"time": "2026-03-14T12:34:56"}'

    # Test date
    dt2 = datetime.date(2026, 3, 14)
    iso = dt2.isoformat()
    assert iso == "2026-03-14"

    data = json.dumps({"time": dt2.isoformat()})
    assert data == '{"time": "2026-03-14"}'


def test_pickle(setup_datetime_patching):
    import datetime  # noqa: PLC0415

    # Pickle and unpickle datetime
    dt = datetime.datetime(2026, 3, 14, 12, 34, 56)
    data = pickle.dumps(dt)
    dt2 = pickle.loads(data)

    # Ensure value and type are preserved
    assert dt2 == dt
    assert isinstance(dt2, datetime.datetime)
    assert type(dt2) is datetime.datetime  # ensure subtype info is preserved


def _pickle_worker(q):
    import datetime  # noqa: PLC0415

    dt = datetime.datetime(2026, 3, 14, 12, 34, 56)
    q.put(dt)


def test_pickle_subprocess(setup_datetime_patching):
    import datetime  # noqa: PLC0415

    ctx = mp.get_context("spawn")
    q = ctx.SimpleQueue()

    p = ctx.Process(target=_pickle_worker, args=(q,))
    p.start()

    dt2 = q.get()
    p.join()

    assert isinstance(dt2, datetime.datetime)
    assert type(dt2) is datetime.datetime


def test_memoized_value():
    time0 = _real_datetime.datetime.now()
    d = launch_dejaview(
        """
        import datetime                 # Line 1
        print()                         # Line 2
        print(datetime.datetime.now())  # Line 3
        print(datetime.datetime.now())  # Line 4
        print()                         # Line 5
        """
    )

    def get_datetime(output: str) -> _real_datetime.datetime:
        match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}", output)
        assert match is not None

        timestamp_str = match.group(0)
        return _real_datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")

    # run to line 3
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    time1 = get_datetime(out)
    assert time1 >= time0

    # rerun line 3
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    assert time1 == get_datetime(out)

    # run line 4
    d.sendline("n")
    out = d.assert_line_number(5)
    time2 = get_datetime(out)
    assert time2 > time1

    # rerun lines 3 and 4
    d.sendline("back")
    d.assert_line_number(4)
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    assert time1 == get_datetime(out)
    d.sendline("n")
    out = d.assert_line_number(5)
    assert time2 == get_datetime(out)
