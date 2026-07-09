"""Make pytest honour the self-scoring test convention.

conftest.py

The test modules return True or False rather than raising assertions, so that each
one runs standalone and prints a score. Pytest, left alone, calls such a function,
discards the boolean, sees no exception, and reports a pass -- which means a broken
test is green under pytest and red only when run by hand.

This hook closes that gap without putting a single assertion in a test module: the
returned value is inspected, and a falsy result fails the item.
"""

import pytest


def pytest_pyfunc_call(pyfuncitem):
    names = pyfuncitem._fixtureinfo.argnames
    arguments = {name: pyfuncitem.funcargs[name] for name in names}
    result = pyfuncitem.obj(**arguments)
    if result is None:
        return True
    if not result:
        pytest.fail("test returned {0!r}".format(result), pytrace=False)
    return True


# End of file #
