from __future__ import annotations

from pathlib import Path
import sys

import bpy  # type: ignore

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import blender_auto_pipeline as pipeline


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    script_argv = sys.argv[1:]
    if "--" in script_argv:
        script_argv = script_argv[script_argv.index("--") + 1 :]
    parsed_cli = pipeline.parse_args(script_argv)
    args = pipeline.parse_args(
        [
            "--assets",
            str(parsed_cli.assets.resolve() if hasattr(parsed_cli, "assets") else project_root / "assets"),
            "--cmf-map",
            str(parsed_cli.cmf_map.resolve() if hasattr(parsed_cli, "cmf_map") else project_root / "config" / "cmf_map.json"),
            "--phase",
            getattr(parsed_cli, "phase", "auto"),
            "--default-phase",
            str(getattr(parsed_cli, "default_phase", 2)),
            *(["--keep"] if getattr(parsed_cli, "keep", False) else []),
            *(["--strict-stp"] if getattr(parsed_cli, "strict_stp", False) else []),
            *(["--skip-audit"] if getattr(parsed_cli, "skip_audit", False) else []),
            *(
                ["--pick-3dm", str(parsed_cli.pick_3dm.resolve())]
                if getattr(parsed_cli, "pick_3dm", None)
                else []
            ),
            "--output-dir",
            str(parsed_cli.output_dir.resolve() if hasattr(parsed_cli, "output_dir") else project_root / "output"),
            "--no-render",
        ]
    )
    code = pipeline.run_pipeline(args)
    if code != 0:
        return code
    out_dir = (
        parsed_cli.output_dir.resolve()
        if hasattr(parsed_cli, "output_dir")
        else project_root / "output"
    )
    out = out_dir / "preview_scene.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_mainfile(filepath=str(out))
    print(f"[build_preview_blend] saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
