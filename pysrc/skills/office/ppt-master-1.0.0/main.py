from __future__ import annotations

import importlib.util
from pathlib import Path


_IMPL_PATH = Path(__file__).resolve().parent / "scripts" / "main.py"
_SPEC = importlib.util.spec_from_file_location("ppt_master_impl", _IMPL_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)

main = _MODULE.main
