"""按清晰别名运行脚本，例如 `python -m wechat_bot.scripts auto_reply`。"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

from .registry import ScriptSpec, format_script_help_lines, resolve_script_spec


def _load_script_module(script_name: str) -> tuple[ScriptSpec | None, ModuleType | None]:
    spec = resolve_script_spec(script_name)
    if spec is None:
        return None, None
    for module_name in spec.module_names:
        try:
            return spec, importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    return spec, None


def _print_help() -> None:
    print("用法: python -m wechat_bot.scripts <script> [args...]")
    print("")
    print("可用脚本:")
    for line in format_script_help_lines():
        print(line)


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        _print_help()
        return 0

    spec, module = _load_script_module(args[0])
    if spec is None:
        print(f"未知脚本: {args[0]}")
        _print_help()
        return 2
    if module is None or not hasattr(module, "main"):
        print(f"无法导入脚本入口: {spec.display_name}")
        return 2

    old_argv = sys.argv[:]
    try:
        sys.argv = [spec.script_name] + args[1:]
        return int(module.main())
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
