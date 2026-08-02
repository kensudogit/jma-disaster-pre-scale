#!/usr/bin/env python3
"""pytest が使えない環境向けの最小テストランナー。

    python3 tests/run_tests.py

pytest がある環境では `pytest` をそのまま使ってよい(同じテスト関数を実行する)。
対応しているのは「引数なし or フィクスチャ引数を持つ test_* 関数」と
`pytest.raises` / `pytest.mark.parametrize` の基本形のみ。
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))


def _install_pytest_shim() -> None:
    if "pytest" in sys.modules:
        return
    import types

    module = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, expected, match=None):
            self.expected = expected
            self.match = match
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise AssertionError(f"{self.expected.__name__} が送出されませんでした")
            if not issubclass(exc_type, self.expected):
                return False
            if self.match:
                import re

                if not re.search(self.match, str(exc)):
                    raise AssertionError(f"メッセージが一致しません: {exc!r} !~ {self.match!r}")
            self.value = exc
            return True

    def raises(expected, match=None):
        return _Raises(expected, match)

    def fixture(func=None, **_kwargs):
        def wrap(f):
            f.__is_fixture__ = True
            return f

        return wrap(func) if func else wrap

    class _Mark:
        def parametrize(self, argnames, argvalues):
            def deco(func):
                func.__parametrize__ = (argnames, argvalues)
                return func

            return deco

        def __getattr__(self, _name):
            def deco(func=None, **_kw):
                return func if func else (lambda f: f)

            return deco

    class _Skipped(Exception):
        pass

    def skip(reason=""):
        raise _Skipped(reason)

    module.raises = raises
    module.skip = skip
    module.Skipped = _Skipped
    module.fixture = fixture
    module.mark = _Mark()
    module.approx = lambda v, rel=1e-6, abs=1e-9: v
    sys.modules["pytest"] = module


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    _install_pytest_shim()
    conftest = _load(HERE / "conftest.py")
    fixtures = {
        name: obj
        for name, obj in vars(conftest).items()
        if callable(obj) and getattr(obj, "__is_fixture__", False)
    }

    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []

    for path in sorted(HERE.glob("test_*.py")):
        module = _load(path)
        for name, func in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(func):
                continue
            params = getattr(func, "__parametrize__", None)
            cases = [None] if params is None else list(params[1])
            argnames = (
                [] if params is None
                else ([a.strip() for a in params[0].split(",")]
                      if isinstance(params[0], str) else list(params[0]))
            )
            for case in cases:
                kwargs = {}
                sig = inspect.signature(func)
                if params is not None:
                    values = case if isinstance(case, (tuple, list)) else (case,)
                    kwargs.update(dict(zip(argnames, values)))
                for pname in sig.parameters:
                    if pname in kwargs:
                        continue
                    if pname in fixtures:
                        kwargs[pname] = fixtures[pname]()
                    else:
                        raise RuntimeError(f"未知の引数: {pname} in {name}")
                label = f"{path.name}::{name}" + (f"[{case}]" if params else "")
                try:
                    func(**kwargs)
                    passed += 1
                except sys.modules['pytest'].Skipped as exc:
                    skipped += 1
                    print(f'SKIP {label}: {exc}')
                except Exception:
                    failed += 1
                    failures.append((label, traceback.format_exc()))

    for label, tb in failures:
        print(f"\n=== FAIL {label} ===\n{tb}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
