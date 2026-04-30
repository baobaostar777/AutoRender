"""
YUI Render System 统一入口（entry point）。

命令：
- audit  : 执行图层审计
- render : 先审计，审计通过后再启动 Blender 自动流水线
- preview: 生成并打开可实时查看的 preview_scene.blend
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import signal
from pathlib import Path
from typing import Sequence

import auditor


class Color:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RESET = "\033[0m"


LOG_FILE_HANDLE = None


def _supports_color() -> bool:
    return sys.stdout.isatty() or os.environ.get("WT_SESSION") is not None


def _paint(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{Color.RESET}"


def print_pass(msg: str) -> None:
    _emit_line(f"{_paint('[PASS]', Color.GREEN)} {msg}")


def print_fail(msg: str) -> None:
    _emit_line(f"{_paint('[FAIL]', Color.RED)} {msg}")


def print_info(msg: str) -> None:
    _emit_line(f"{_paint('[INFO]', Color.YELLOW)} {msg}")


def _emit_line(message: str) -> None:
    print(message)
    if LOG_FILE_HANDLE is not None:
        LOG_FILE_HANDLE.write(message + "\n")
        LOG_FILE_HANDLE.flush()


class TeeStream:
    """同时写入终端和日志文件的流对象（用于 redirect_stdout/stderr）。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> int:
        for s in self.streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


def default_assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def default_cmf_map_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "cmf_map.json"


def pipeline_script_path() -> Path:
    return Path(__file__).resolve().parent / "blender_auto_pipeline.py"


def preview_builder_script_path() -> Path:
    return Path(__file__).resolve().parent / "build_preview_blend.py"


def preview_view_script_path() -> Path:
    return Path(__file__).resolve().parent / "set_material_preview.py"


def preview_blend_output_path(output_dir: Path | None = None) -> Path:
    out_dir = (
        output_dir.resolve()
        if output_dir is not None
        else (Path(__file__).resolve().parent.parent / "output").resolve()
    )
    return out_dir / "preview_scene.blend"


def preview_pid_file(output_dir: Path | None = None) -> Path:
    out_dir = (
        output_dir.resolve()
        if output_dir is not None
        else (Path(__file__).resolve().parent.parent / "output").resolve()
    )
    return out_dir / ".preview_blender.pid"


def _find_blender_on_path() -> Path | None:
    exe = shutil.which("blender") or shutil.which("blender.exe")
    return Path(exe) if exe else None


def _find_blender_from_registry() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg  # pylint: disable=import-outside-toplevel
    except ImportError:
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
                            _name, value, _ = winreg.EnumValue(key, i)
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
        description="YUI Render System 统一入口：audit / render / preview"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="执行图层审计")
    audit_parser.add_argument("--assets", type=Path, default=default_assets_dir())
    audit_parser.add_argument("--phase", choices=("2", "3", "auto"), default="auto")
    audit_parser.add_argument("--default-phase", type=int, choices=(2, 3), default=2)
    audit_parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="可选：将控制台输出同时写入日志文件。",
    )

    render_parser = subparsers.add_parser(
        "render", help="先审计，通过后启动 blender_auto_pipeline"
    )
    render_parser.add_argument("--assets", type=Path, default=default_assets_dir())
    render_parser.add_argument("--cmf-map", type=Path, default=default_cmf_map_path())
    render_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "output",
        help="渲染图输出目录。",
    )
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
    render_parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="可选：将控制台输出同时写入日志文件。",
    )
    render_parser.add_argument(
        "--no-render",
        action="store_true",
        help="仅导入与挂材质，不执行 PNG 出图（便于实时查看）。",
    )
    render_parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="仅渲染：跳过 Phase 审计，取 assets 内最新修改的 .3dm；适合不同产品结构 + 通用 CMF 映射。",
    )
    render_parser.add_argument(
        "--pick-3dm",
        type=Path,
        default=None,
        help="指定单个 .3dm（建议与 --skip-audit 同用，避免多份资产时选错 mtime）。",
    )

    preview_parser = subparsers.add_parser(
        "preview",
        help="生成并打开 preview_scene.blend（导入+挂材质，不出 PNG）",
    )
    preview_parser.add_argument("--assets", type=Path, default=default_assets_dir())
    preview_parser.add_argument("--cmf-map", type=Path, default=default_cmf_map_path())
    preview_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "output",
        help="预览场景输出目录。",
    )
    preview_parser.add_argument("--phase", choices=("2", "3", "auto"), default="auto")
    preview_parser.add_argument("--default-phase", type=int, choices=(2, 3), default=2)
    preview_parser.add_argument(
        "--keep",
        action="store_true",
        help="保留灯光和相机，仅更新几何体。",
    )
    preview_parser.add_argument(
        "--strict-stp",
        action="store_true",
        help="开启后必须存在同名 .stp 才执行预览构建。",
    )
    preview_parser.add_argument(
        "--blender",
        type=Path,
        default=None,
        help="可选：手动指定 Blender 可执行文件路径。默认自动检测。",
    )
    preview_parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="可选：将控制台输出同时写入日志文件。",
    )
    preview_parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="与 render 一致：预览构建跳过审计，仅用最新修改的 .3dm。",
    )
    preview_parser.add_argument(
        "--pick-3dm",
        type=Path,
        default=None,
        help="与 render 一致：指定要构建预览的 .3dm。",
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
    code = _run_auditor_with_optional_tee(audit_args)
    if code == 0:
        print_pass("审计通过")
    else:
        print_fail("审计失败")
    return code


def run_render(args: argparse.Namespace) -> int:
    # 1) 审计（可走 --skip-audit 跳过）
    if getattr(args, "skip_audit", False):
        print_info("render：已 --skip-audit，跳过图层审计（使用最新修改的 .3dm）")
    else:
        print_info("render 前置审计开始")
        audit_code = _run_auditor_with_optional_tee(
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
        "--output-dir",
        str(args.output_dir.resolve()),
        "--phase",
        args.phase,
        "--default-phase",
        str(args.default_phase),
    ]
    if getattr(args, "skip_audit", False):
        cmd.append("--skip-audit")
    if getattr(args, "pick_3dm", None):
        cmd.extend(["--pick-3dm", str(args.pick_3dm.resolve())])
    if args.keep:
        cmd.append("--keep")
    if args.strict_stp:
        cmd.append("--strict-stp")
    if args.no_render:
        cmd.append("--no-render")
    print_info("启动 blender_auto_pipeline ...")
    print_info("命令: " + " ".join(cmd))

    rc = _run_subprocess_with_live_tee(cmd)
    if rc == 0:
        print_pass("render 流水线执行完成")
    else:
        print_fail(f"render 流水线失败，退出码: {rc}")
    return int(rc)


def run_preview(args: argparse.Namespace) -> int:
    if getattr(args, "skip_audit", False):
        print_info("preview：已 --skip-audit，跳过图层审计（使用最新修改的 .3dm）")
    else:
        print_info("preview 前置审计开始")
        audit_code = _run_auditor_with_optional_tee(
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
            print_fail("审计未通过，preview 已终止")
            return audit_code
        print_pass("审计通过，准备构建 preview_scene.blend")

    # 2) 自动识别 Blender 路径
    blender_path = args.blender.resolve() if args.blender else detect_blender_path()
    if blender_path is None or not blender_path.exists():
        print_fail("未能自动识别 Blender 路径，请用 --blender 指定")
        return 2
    print_info(f"Blender 路径: {blender_path}")

    # 3) 后台构建 preview .blend
    build_cmd = [
        str(blender_path),
        "-b",
        "--python-exit-code",
        "1",
        "--python",
        str(preview_builder_script_path()),
        "--",
        "--assets",
        str(args.assets.resolve()),
        "--cmf-map",
        str(args.cmf_map.resolve()),
        "--output-dir",
        str(args.output_dir.resolve()),
        "--phase",
        args.phase,
        "--default-phase",
        str(args.default_phase),
    ]
    if getattr(args, "skip_audit", False):
        build_cmd.append("--skip-audit")
    if getattr(args, "pick_3dm", None):
        build_cmd.extend(["--pick-3dm", str(args.pick_3dm.resolve())])
    if args.keep:
        build_cmd.append("--keep")
    if args.strict_stp:
        build_cmd.append("--strict-stp")
    print_info("启动 preview 构建 ...")
    print_info("命令: " + " ".join(build_cmd))

    build_rc = _run_subprocess_with_live_tee(build_cmd)
    if build_rc != 0:
        print_fail(f"preview 构建失败，退出码: {build_rc}")
        return int(build_rc)

    preview_blend = preview_blend_output_path(args.output_dir)
    if not preview_blend.exists():
        print_fail(f"preview 文件不存在: {preview_blend}")
        return 3

    # 4) 若已有上次 preview 打开的窗口，先关闭，避免多窗口混淆
    pid_path = preview_pid_file(args.output_dir)
    _close_previous_preview_window(pid_path)

    # 5) 打开 GUI 查看，并记录 pid
    print_info(f"打开预览场景: {preview_blend}")
    proc = _open_preview_in_material_mode(blender_path, preview_blend)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    print_pass("preview 已在 Blender GUI 打开")
    return 0


def _close_previous_preview_window(pid_path: Path) -> None:
    # A) 先尝试关闭 PID 文件里记录的旧窗口
    if pid_path.exists():
        raw = pid_path.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            pid = int(raw)
            print_info(f"检测到上次 preview 窗口 PID={pid}，先尝试关闭")
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    # B) 再扫描并关闭所有打开 preview_scene.blend 的 Blender 进程（防止 PID 丢失导致双窗口）
    if os.name == "nt":
        _close_windows_preview_processes()
    else:
        _close_posix_preview_processes()


def _close_windows_preview_processes() -> None:
    ps_cmd = (
        "Get-CimInstance Win32_Process "
        "| Where-Object { $_.Name -ieq 'blender.exe' -and $_.CommandLine -match 'preview_scene\\.blend' } "
        "| ForEach-Object { $_.ProcessId }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    pids = [line.strip() for line in out.stdout.splitlines() if line.strip().isdigit()]
    for pid in pids:
        print_info(f"关闭旧 preview 窗口进程 PID={pid}")
        subprocess.run(
            ["taskkill", "/PID", pid, "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _close_posix_preview_processes() -> None:
    out = subprocess.run(
        ["pgrep", "-f", "blender.*preview_scene.blend"],
        check=False,
        capture_output=True,
        text=True,
    )
    pids = [line.strip() for line in out.stdout.splitlines() if line.strip().isdigit()]
    for pid in pids:
        try:
            print_info(f"关闭旧 preview 窗口进程 PID={pid}")
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass


def _open_preview_in_material_mode(blender_path: Path, preview_blend: Path) -> subprocess.Popen:
    """
    以 GUI 打开 preview，并将 3D 视图强制切到 Material Preview，避免看起来“全灰”。
    """
    return subprocess.Popen(
        [str(blender_path), str(preview_blend), "--python", str(preview_view_script_path())]
    )


def _run_subprocess_with_live_tee(cmd: list[str]) -> int:
    """实时输出子进程日志；如启用日志文件则同步落盘。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        _emit_line(line)
    proc.wait()
    return int(proc.returncode)


def _run_auditor_with_optional_tee(audit_args: list[str]) -> int:
    if LOG_FILE_HANDLE is None:
        return auditor.main(audit_args)
    tee_out = TeeStream(sys.stdout, LOG_FILE_HANDLE)
    with contextlib.redirect_stdout(tee_out), contextlib.redirect_stderr(tee_out):
        return auditor.main(audit_args)


def _open_log_file_if_needed(args: argparse.Namespace):
    log_file = getattr(args, "log_file", None)
    if log_file is None:
        return None
    path = Path(log_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    global LOG_FILE_HANDLE  # pylint: disable=global-statement
    args = parse_args(argv)
    LOG_FILE_HANDLE = _open_log_file_if_needed(args)
    try:
        if LOG_FILE_HANDLE is not None:
            print_info(f"日志写入文件: {Path(LOG_FILE_HANDLE.name)}")
        if args.command == "audit":
            return run_audit(args)
        if args.command == "render":
            return run_render(args)
        if args.command == "preview":
            return run_preview(args)
        raise ValueError(f"未知命令: {args.command}")
    finally:
        if LOG_FILE_HANDLE is not None:
            LOG_FILE_HANDLE.close()
            LOG_FILE_HANDLE = None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
