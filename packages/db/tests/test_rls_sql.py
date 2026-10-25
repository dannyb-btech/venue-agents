import pytest

from va_db.rls import tenant_table_sql


def test_policy_statements() -> None:
    statements = tenant_table_sql("venue", ["SELECT", "INSERT"])
    assert statements[0] == "ALTER TABLE venue ENABLE ROW LEVEL SECURITY"
    assert statements[1] == "ALTER TABLE venue FORCE ROW LEVEL SECURITY"
    assert "USING (tenant_id = va_current_tenant())" in statements[2]
    assert "WITH CHECK (tenant_id = va_current_tenant())" in statements[2]
    assert statements[3] == "GRANT INSERT, SELECT ON venue TO va_app"


@pytest.mark.parametrize("table", ["venue; DROP TABLE tenant", "Venue", "", "a-b"])
def test_rejects_unsafe_identifiers(table: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        tenant_table_sql(table, ["SELECT"])


@pytest.mark.parametrize("privileges", [[], ["TRUNCATE"], ["SELECT", "ALL"]])
def test_rejects_unknown_privileges(privileges: list[str]) -> None:
    with pytest.raises(ValueError, match="privileges"):
        tenant_table_sql("venue", privileges)
