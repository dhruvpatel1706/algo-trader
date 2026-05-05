"""Static guard: every moonshot module declares ``LIVE_BROKER_BRIDGE = False``.

The flag is the only static signal that a moonshot module is forbidden from
reaching a real broker. Missing it means a future contributor can wire the
moonshot logic into the trade path and pass review. This test enforces the
invariant at the package level, including subpackages like ``moonshot/rl``.
"""

from __future__ import annotations

import importlib
import pkgutil

import src.moonshot as moonshot_root


def _walk_modules(package) -> list[str]:
    out: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}."):
        # Skip private / dunder modules.
        if any(part.startswith("_") for part in info.name.split(".")):
            continue
        out.append(info.name)
    return out


def test_every_moonshot_module_declares_no_live_bridge() -> None:
    modules = _walk_modules(moonshot_root)
    # Skip pure package-init modules that contain only docstrings.
    non_pkg = [m for m in modules if not m.endswith(".__init__")]
    assert non_pkg, "expected at least one non-init moonshot module"

    missing: list[str] = []
    truthy: list[tuple[str, object]] = []
    for name in non_pkg:
        mod = importlib.import_module(name)
        flag = getattr(mod, "LIVE_BROKER_BRIDGE", "<MISSING>")
        if flag == "<MISSING>":
            missing.append(name)
        elif bool(flag):
            truthy.append((name, flag))
    assert not missing, (
        "every moonshot module must declare `LIVE_BROKER_BRIDGE = False` at "
        f"module scope; missing: {missing}"
    )
    assert not truthy, (
        f"moonshot modules MUST keep LIVE_BROKER_BRIDGE False; saw: {truthy}"
    )
