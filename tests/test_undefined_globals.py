"""No module may reference a global it never binds.

Two names shipped to production unbound and both failed the same way: a 500 on
the sign-in route. `membership_announce` was called without being imported, and
the resolve-failure branch of the same handler called `logger`, a name that only
existed inside a different module. Unit tests that imported the module directly
never touched either line, and no CI job so much as compiled the package.

This is the cheap whole-package version of that check: read every module with
`symtable` (nothing is executed) and require that a name used as a global has a
module-level binding or is a builtin. A missing import is precisely a global
with no binding, so it fails here instead of on a customer's sign-in page.
"""

from __future__ import annotations

import builtins
import pathlib
import symtable

import hoja_server

PACKAGE = pathlib.Path(hoja_server.__file__).parent

# Module dunders Python injects at import time; they are never written in source.
INJECTED = {
    "__name__",
    "__file__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__builtins__",
}
AVAILABLE = set(dir(builtins)) | INJECTED


def _bound_at_module_level(table: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }


def _global_references(table: symtable.SymbolTable) -> set[str]:
    """Globals referenced here or in any nested scope (closures are not globals)."""
    used = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    }
    for child in table.get_children():
        used |= _global_references(child)
    return used


def _unbound(path: pathlib.Path) -> list[str]:
    table = symtable.symtable(path.read_text(), path.name, "exec")
    return sorted(_global_references(table) - _bound_at_module_level(table) - AVAILABLE)


def test_every_module_binds_the_globals_it_uses():
    offenders = {
        str(path.relative_to(PACKAGE)): missing
        for path in sorted(PACKAGE.rglob("*.py"))
        if (missing := _unbound(path))
    }

    assert offenders == {}
