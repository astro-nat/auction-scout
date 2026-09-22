"""No module may reference a name it never defines or imports.

Twice now a scripted edit has inserted a call while its import silently
failed to apply - the anchor line had changed - and both times the module
still imported cleanly, because Python only resolves a global when the line
runs. The second one, a missing `timed`, broke EVERY enrichment for a day:
97 lots in a row failed with "name 'timed' is not defined", and the app
simply offered to price them again.

A syntax check cannot see this and neither can an import. This walks the
symbol table instead: every name a function reads from module scope must
exist in that module or in builtins.
"""

import builtins
import importlib
import pkgutil
import symtable
from pathlib import Path

import pytest

PACKAGES = ["app.workers", "app.services", "app.routers"]


def _modules():
    for pkg_name in PACKAGES:
        pkg = importlib.import_module(pkg_name)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_"):
                continue
            yield f"{pkg_name}.{info.name}"


def _undefined(module) -> list:
    """Names read from module scope that resolve nowhere.

    Only function scopes are walked. A name assigned anywhere in its own
    scope is skipped, which covers imports made inside a function - those
    are a local binding, not a module global.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")
    table = symtable.symtable(source, module.__file__, "exec")
    seen = set()

    def walk(scope, top=False):
        if not top:
            for sym in scope.get_symbols():
                if sym.is_global() and not sym.is_assigned():
                    seen.add(sym.get_name())
        for child in scope.get_children():
            walk(child)

    walk(table, top=True)
    return sorted(n for n in seen
                  if not hasattr(module, n) and not hasattr(builtins, n))


@pytest.mark.parametrize("name", sorted(_modules()))
def test_every_name_used_actually_resolves(name):
    module = importlib.import_module(name)
    missing = _undefined(module)
    assert not missing, (
        f"{name} uses {missing} but never defines or imports them. "
        "This is the shape of a failed import edit: the module still loads, "
        "and the error only appears when that line runs in production.")


def test_the_check_would_have_caught_the_real_bug(tmp_path):
    """Pin the detector itself against the exact defect it exists for:
    a helper used inside a function, with no import to back it."""
    src = tmp_path / "broken.py"
    src.write_text(
        "import os\n"
        "\n"
        "def run():\n"
        "    with timed('enrich', 'lot'):\n"
        "        return os.getpid()\n",
        encoding="utf-8")
    table = symtable.symtable(src.read_text(encoding="utf-8"), str(src), "exec")
    referenced = set()
    for child in table.get_children():
        for sym in child.get_symbols():
            if sym.is_global() and not sym.is_assigned():
                referenced.add(sym.get_name())
    assert "timed" in referenced
    assert "os" in referenced          # imported at module scope, so it resolves
