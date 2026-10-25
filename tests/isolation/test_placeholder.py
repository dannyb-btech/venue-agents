"""Placeholder until P0-03 adds the stamp database.

P0-03 replaces this with tests proving tenant A can't read or write tenant B for every table,
and that queries fail closed when no tenant is set.
"""


def test_isolation_suite_is_collected() -> None:
    assert True
