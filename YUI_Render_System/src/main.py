"""
YUI Render System 统一入口（entry point）。

命令：
- audit  : 执行图层审计
- render : 先审计，审计通过后再启动 Blender 自动流水线
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import auditor


class Color:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty() or os.environ.get("WT_SESSION") is not None


def _paint(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{Color.RESET}"


def print_pass(msg: str) -> None:
    print(f"{_paint('[PASS]', Color.GREEN)} {msg}")


def print_fail(msg: str) -> None:
    print(f"{_paint('[FAIL]', Color.RED)} {msg}")


def print_info(msg: str) -> None:
    print(f"{_paint('[INFO]', Color.YELLOW)} {msg}")


def default_assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def default_cmf_map_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "cmf_map.json"


def pipeline_script_path() -> Path:
    return Path(__file__).resolve().parent / "blender_auto_pipeline.py"


def _find_blender_on_path() -> Path | None:
    exe = shutil.which("blender") or shutil.which("blender.exe")
    return Path(exe) if exe else None


def _find_blender_from_registry() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg  # pylint: disable=import-outside-toplevel
    except Exception:  # noqa: BLE001
        return None

    candidate_values: list[str] = []
    keys = [
        r"SOFTWARE\BlenderFoundation",
        r"SOFTWARE\WOW6432Node\BlenderFoundation",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe",
    ]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                            if isinstance(value, str) and value:
                                candidate_values.append(value)
                            i += 1
                        except OSError:
                            break
                    # 默认值也尝试读取
                    try:
                        default_value, _ = winreg.QueryValueEx(key, "")
                        if isinstance(default_value, str) and default_value:
                            candidate_values.append(default_value)
                    except OSError:
                        pass
            except OSError:
                continue

    for raw in candidate_values:
        p = Path(raw.strip().strip('"'))
        if p.exists() and p.name.lower() == "blender.exe":
            return p
        # 有些键只给目录
        maybe = p / "blender.exe"
        if maybe.exists():
            return maybe
    return None


def detect_blender_path() -> Path | None:
    """自动识别 Blender 可执行路径（优先 PATH，再查常见安装目录）。"""
    on_path = _find_blender_on_path()
    if on_path is not None:
        return on_path

    from_reg = _find_blender_from_registry()
    if from_reg is not None:
        return from_reg

    candidates = [
        Path(r"D:\Blender\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YUI Render System 统一入口：audit / render"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="执行图层审计")
    audit_parser.add_argument("--assets", type=Path, default=default_assets_dir())
    audit_parser.add_argument("--phase", choices=("2", "3", "auto"), default="auto")
    audit_parser.add_argument("--default-phase", type=int, choices=(2, 3), default=2)

    render_parser = subparsers.add_parser(
        "render", help="先审计，通过后启动 blender_auto_pipeline"
    )
    render_parser.add_argument("--assets", type=Path, default=default_assets_dir())
    render_parser.add_argument("--cmf-map", type=Path, default=default_cmf_map_path())
    render_parser.add_argument("--phase", choices=("2", "3", "auto"), default="auto")
    render_parser.add_argument("--default-phase", type=int, choices=(2, 3), default=2)
    render_parser.add_argument(
        "--keep",
        action="store_true",
        help="保留场景中的灯光和相机，仅更新几何体。",
    )
    render_parser.add_argument(
        "--strict-stp",
        action="store_true",
        help="开启后必须存在同名 .stp 才执行渲染。",
    )
    render_parser.add_argument(
        "--blender",
        type=Path,
        default=None,
        help="可选：手动指定 Blender 可执行文件路径。默认自动检测。",
    )

    return parser.parse_args(list(argv) if argv is not None else None)


def run_audit(args: argparse.Namespace) -> int:
    audit_args = [
        "--assets",
        str(args.assets.resolve()),
        "--phase",
        args.phase,
        "--default-phase",
        str(args.default_phase),
    ]
    print_info("开始执行审计...")
    code = auditor.main(audit_args)
    if code == 0:
        print_pass("审计通过")
    else:
        print_fail("审计失败")
    return code


def run_render(args: argparse.Namespace) -> int:
    # 1) 先审计
    print_info("render 前置审计开始")
    audit_code = auditor.main(
        [
            "--assets",
            str(args.assets.resolve()),
            "--phase",
            args.phase,
            "--default-phase",
            str(args.default_phase),
        ]
    )
    if audit_code != 0:
        print_fail("审计未通过，render 已终止")
        return audit_code
    print_pass("审计通过，准备启动 Blender 流水线")

    # 2) 自动识别 Blender 路径
    blender_path = args.blender.resolve() if args.blender else detect_blender_path()
    if blender_path is None or not blender_path.exists():
        print_fail("未能自动识别 Blender 路径，请用 --blender 指定")
        return 2
    print_info(f"Blender 路径: {blender_path}")

    # 3) 启动流水线
    cmd = [
        str(blender_path),
        "-b",
        "--python-exit-code",
        "1",
        "--python",
        str(pipeline_script_path()),
        "--",
        "--assets",
        str(args.assets.resolve()),
        "--cmf-map",
        str(args.cmf_map.resolve()),
        "--phase",
        args.phase,
        "--default-phase",
        str(args.default_phase),
    ]
    if args.keep:
        cmd.append("--keep")
    if args.strict_stp:
        cmd.append("--strict-stp")
    print_info("启动 blender_auto_pipeline ...")
    print_info("命令: " + " ".join(cmd))

    proc = subprocess.run(cmd, check=False)
    if proc.returncode == 0:
        print_pass("render 流水线执行完成")
    else:
        print_fail(f"render 流水线失败，退出码: {proc.returncode}")
    return int(proc.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "audit":
        return run_audit(args)
    if args.command == "render":
        return run_render(args)
    raise ValueError(f"未知命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
