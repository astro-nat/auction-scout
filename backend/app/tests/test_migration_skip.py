"""Startup migrations skip what already exists. Pure."""

from app import main


def test_keys():
    assert main._migration_key(
        "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS str_pct FLOAT") == ("column", "enrichment", "str_pct")
    assert main._migration_key(
        "CREATE TABLE IF NOT EXISTS settings (key VARCHAR PRIMARY KEY)") == ("table", "settings")
    assert main._migration_key(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_x ON t (a)") == ("index", "ux_x")
    # A backfill always runs.
    assert main._migration_key("UPDATE auctions SET x = 1 WHERE x IS NULL") is None


def test_existing_columns_take_no_lock(monkeypatch):
    ran = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt):
            ran.append(str(stmt))

    monkeypatch.setattr(main, "_MIGRATIONS", [
        "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS have_it FLOAT",
        "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS new_one FLOAT",
    ])
    monkeypatch.setattr(main, "_already_applied", lambda: {("column", "enrichment", "have_it")})
    monkeypatch.setattr(main.engine, "begin", lambda: Conn())
    assert main._run_migrations() == []
    assert not any("have_it" in s for s in ran)
    assert any("new_one" in s for s in ran)
