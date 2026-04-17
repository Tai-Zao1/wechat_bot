#!/usr/bin/env python3
"""Build PyInstaller bundle and NSIS installer in one command."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except Exception:
    tomllib = None


def find_makensis_from_registry() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:
        return None

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\NSIS"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\NSIS"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\NSIS"),
    ]
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                install_dir, _ = winreg.QueryValueEx(key, "")
            candidate = Path(str(install_dir)) / "makensis.exe"
            if candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return None


def resolve_makensis(custom: str | None) -> str:
    if custom:
        return custom

    from_path = shutil.which("makensis")
    if from_path:
        return from_path

    candidates = [
        Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
        Path(r"C:\Program Files\NSIS\makensis.exe"),
    ]
    nsis_home = os.getenv("NSIS_HOME")
    if nsis_home:
        candidates.insert(0, Path(nsis_home) / "makensis.exe")
    reg_path = find_makensis_from_registry()
    if reg_path:
        candidates.insert(0, Path(reg_path))

    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "makensis.exe not found.\n"
        "Please install NSIS from https://nsis.sourceforge.io/Download\n"
        "or run with --makensis \"C:\\Program Files (x86)\\NSIS\\makensis.exe\""
    )


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"[BUILD] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def load_toml_file(path: Path) -> dict:
    if tomllib is None or not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_build_config(root: Path, config_path: str | None) -> dict[str, str]:
    config_file = (root / (config_path or "installer/build_config.toml")).resolve()
    data = load_toml_file(config_file)
    section = data.get("installer") if isinstance(data.get("installer"), dict) else data
    if not isinstance(section, dict):
        return {}
    config: dict[str, str] = {}
    for key in (
        "spec",
        "nsi",
        "makensis",
        "app_version",
        "out_name",
        "app_name",
        "display_name",
        "app_dir_name",
    ):
        value = section.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            config[key] = text
    return config


def resolve_default_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if tomllib is not None and pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            version = str((data.get("project") or {}).get("version") or "").strip()
            if version:
                return version
        except Exception:
            pass

    setup_py = root / "setup.py"
    if setup_py.exists():
        try:
            text = setup_py.read_text(encoding="utf-8")
        except Exception:
            text = setup_py.read_text(errors="ignore")
        match = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", text)
        if match:
            version = str(match.group(1)).strip()
            if version:
                return version

    return "dev"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PyInstaller output and NSIS installer.")
    parser.add_argument(
        "--config",
        default="installer/build_config.toml",
        help="Build config TOML path (default: installer/build_config.toml)",
    )
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Skip PyInstaller build step.")
    parser.add_argument(
        "--spec",
        default="pywechat_bot_gui.spec",
        help="PyInstaller spec path (default: pywechat_bot_gui.spec)",
    )
    parser.add_argument(
        "--nsi",
        default="installer/pywechat_bot_gui.nsi",
        help="NSIS script path (default: installer/pywechat_bot_gui.nsi)",
    )
    parser.add_argument("--makensis", default=None, help="Full path to makensis.exe")
    parser.add_argument(
        "--app-version",
        default=None,
        help="Installer version passed to NSIS as APP_VERSION. Default: auto-detect from project metadata.",
    )
    parser.add_argument(
        "--out-name",
        default=None,
        help="Installer output filename passed to NSIS as OUT_NAME.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Installer display name passed to NSIS as APP_NAME.",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Full installer display name passed to NSIS as DISPLAY_NAME.",
    )
    parser.add_argument(
        "--app-dir-name",
        default=None,
        help="Install directory name under Program Files, passed to NSIS as APP_DIR_NAME.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = load_build_config(root, args.config)

    spec_path = (args.spec or "").strip() or config.get("spec") or "pywechat_bot_gui.spec"
    nsi_path = (args.nsi or "").strip() or config.get("nsi") or "installer/pywechat_bot_gui.nsi"
    makensis_path = (args.makensis or "").strip() or config.get("makensis")
    app_version = (args.app_version or "").strip() or config.get("app_version") or resolve_default_version(root)
    out_name = (args.out_name or "").strip() or config.get("out_name")
    app_name = (args.app_name or "").strip() or config.get("app_name")
    display_name = (args.display_name or "").strip() or config.get("display_name")
    app_dir_name = (args.app_dir_name or "").strip() or config.get("app_dir_name")

    spec = (root / spec_path).resolve()
    nsi = (root / nsi_path).resolve()

    if sys.platform != "win32":
        print("[WARN] NSIS packaging is intended to run on Windows.")

    if not args.skip_pyinstaller:
        if not spec.exists():
            raise FileNotFoundError(f"Spec file not found: {spec}")
        run_cmd([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)], cwd=root)

    dist_dir = root / "dist" / "pywechat_bot_gui"
    if not dist_dir.exists():
        raise FileNotFoundError(f"PyInstaller output not found: {dist_dir}")

    if not nsi.exists():
        raise FileNotFoundError(f"NSIS script not found: {nsi}")
    makensis = resolve_makensis(makensis_path)
    nsis_cmd = [makensis, "/INPUTCHARSET", "UTF8", f"/DAPP_VERSION={app_version}"]
    if out_name:
        nsis_cmd.append(f"/DOUT_NAME={out_name}")
    if app_name:
        nsis_cmd.append(f"/DAPP_NAME={app_name}")
    if display_name:
        nsis_cmd.append(f"/DDISPLAY_NAME={display_name}")
    if app_dir_name:
        nsis_cmd.append(f"/DAPP_DIR_NAME={app_dir_name}")
    nsis_cmd.append(str(nsi))
    run_cmd(nsis_cmd, cwd=nsi.parent)

    output_name = out_name or f"PyWechatBotInstaller_v{app_version}.exe"
    print(f"[BUILD] Done: dist/{output_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
