"""Private Python names stay inside the folder that owns them, and a file that imports too much is pointed out."""

from gate.checks import boundaries

QUIET = "[boundaries]\ncycles = false\n"


def private(repo, selected=None):
    return {(f.path, f.symbol) for f in boundaries.run(repo.context(selected)) if f.rule == "private"}


def package(repo, *names):
    for name in names:
        repo.write(f"{name}/__init__.py", "")


def test_a_private_name_may_be_shared_inside_its_package_but_not_taken_from_outside(repo):
    repo.write("gate.toml", QUIET)
    package(repo, "app", "app/billing", "app/orders")
    repo.write("app/billing/rates.py", "def _round(x):\n    return x\n\n\ndef rate():\n    return _round(1)\n")
    repo.write("app/billing/charge.py", "from app.billing.rates import _round, rate\nfrom .rates import _round as r\n")
    repo.write("app/orders/total.py", "import os\nfrom app.billing.rates import rate, _round\n")
    repo.write("app/orders/renamed.py", "from app.billing.rates import _round as round_it\n")
    assert private(repo) == {("app/orders/total.py", "_round"), ("app/orders/renamed.py", "_round")}
    (repo.root / "app/orders/renamed.py").unlink()
    (finding,) = [f for f in boundaries.run(repo.context()) if f.rule == "private"]
    assert (finding.line, finding.key) == (2, "boundaries/private:app/orders/total.py->app/billing/rates.py::_round")
    assert "outside app/billing/ which owns it" in finding.message and finding.severity == "fail"


def test_a_private_module_belongs_to_the_folder_it_sits_in(repo):
    repo.write("gate.toml", QUIET)
    package(repo, "app", "app/billing", "app/billing/_engine", "app/orders")
    repo.write("app/billing/_impl.py", "x = 1\n")
    repo.write("app/billing/_engine/core.py", "y = 1\n")
    repo.write("app/billing/api.py", "from app.billing._impl import x\nfrom ._engine.core import y\nfrom app.billing import _impl\n")
    repo.write("app/orders/a.py", "from app.billing._impl import x\n")
    repo.write("app/orders/b.py", "import app.billing._engine.core\n")
    repo.write("app/orders/c.py", "from app.billing import _impl\n")
    assert private(repo) == {("app/orders/a.py", "_impl"), ("app/orders/b.py", "_engine"), ("app/orders/c.py", "_impl")}
    assert len([f for f in boundaries.run(repo.context()) if f.rule == "private"]) == 3, "each import reported once"


def test_what_is_not_a_private_import_is_left_alone(repo):
    repo.write("gate.toml", QUIET)
    package(repo, "app", "app/billing", "app/orders")
    repo.write("app/billing/rates.py", "def rate():\n    return 1\n\n\n__version__ = \"1\"\n")
    repo.write("app/orders/total.py", "from app.billing.rates import rate as _rate, __version__\nfrom app.billing import rates as _r\n")
    repo.write("tests/test_rates.py", "from app.billing.rates import _anything\n")
    repo.write("_scripts.py", "z = 1\n")
    repo.write("app/orders/uses_root.py", "import _scripts\n")   # a private module at the root has no outside
    assert private(repo) == set()


def test_private_imports_can_be_switched_off_and_only_selected_files_are_judged(repo):
    package(repo, "app", "app/billing", "app/orders")
    repo.write("app/billing/rates.py", "def _round(x):\n    return x\n")
    repo.write("app/orders/old.py", "from app.billing.rates import _round\n")
    repo.write("app/orders/new.py", "from app.billing.rates import _round\n")
    assert private(repo, ["app/orders/new.py"]) == {("app/orders/new.py", "_round")}
    repo.write("gate.toml", "[boundaries]\nprivate = false\n")
    assert private(repo) == set()


def test_a_file_importing_more_modules_than_the_limit_gets_a_warning(repo):
    repo.write("gate.toml", QUIET + "max_imports = 3\n")
    for i in range(5):
        repo.write(f"src/m{i}.ts", f"export const v{i} = {i}\n")
    many = "".join(f'import {{ v{i} }} from "./m{i}"\n' for i in range(4))
    repo.write("src/many.ts", many + 'import { v0 as again } from "./m0"\nimport x from "zod"\n')
    repo.write("src/at_limit.ts", "".join(f'import {{ v{i} }} from "./m{i}"\n' for i in range(3)))
    repo.write("src/many.test.ts", many)
    (finding,) = [f for f in boundaries.run(repo.context()) if f.rule == "fan-out"]
    assert (finding.path, finding.value, finding.limit, finding.severity) == ("src/many.ts", 4, 3, "warn")
    repo.write("gate.toml", QUIET + "max_imports = 0\n")
    assert [f for f in boundaries.run(repo.context()) if f.rule == "fan-out"] == []
