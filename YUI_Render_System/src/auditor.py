"""
3DM 图层（layer）审计：支持 Phase2 / Phase3 拆件协议，不依赖 Blender（bpy）。

- Phase2：YUI_Box_Phase2_Disassembly Instructions.md §1 表格「图层分配」列
- Phase3：与仓库内 YUI_Box_Phase3_Assembly_V1.3dm 一致的 6+Rhino 默认图层
  （待后续独立 Phase3 说明文档时可将常量同步过去）
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

# -----------------------------------------------------------------------------

# 协议：必须的图层短名（与 Rhinoceros 图层面板 Name 一致）

# -----------------------------------------------------------------------------



REQUIRED_LAYERS_PHASE2: tuple[str, ...] = (

    "01_Housing_Front",

    "01_Housing_Base",

    "02_Lid_Assembly",

    "03_Screen_Glass",

    "04_Interaction",

    "05_Internal_Parts",

)



# 来源：YUI_Box_Phase3_Assembly_V1.3dm 中除「默认值」外的功能图层

REQUIRED_LAYERS_PHASE3: tuple[str, ...] = (

    "01_Main_Body",

    "02_Top_Lid",

    "03_Internal",

    "04_Screen",

    "05_Internal_Shield",

    "06_Back_Hardware",

)



# Rhino 自带默认图层，不应视为「协议多出来的图层」

RHINO_STOCK_LAYER_SHORT_NAMES: frozenset[str] = frozenset(

    {

        "默认值",

        "Default",

    }

)



# 与 .cursorrules / 历史命名 不一致时的提示（不代替协议，仅 Phase2 使用）

POSSIBLE_LEGACY_LAYER_NAMES_PHASE2: dict[str, str] = {

    "Lid_Assembly": "02_Lid_Assembly",

    "04_Side_Knob": "04_Interaction",

    "06_Light_Pipe": "05_Internal_Parts",

}



# Phase3：从 Phase2 迁过来时常见错名提示（可逐步扩充）

POSSIBLE_LEGACY_LAYER_NAMES_PHASE3: dict[str, str] = {

    "01_Housing_Front": "01_Main_Body",

    "02_Lid_Assembly": "02_Top_Lid",

    "03_Screen_Glass": "04_Screen",

}



LEGACY_HINTS_BY_PHASE: dict[int, dict[str, str]] = {

    2: POSSIBLE_LEGACY_LAYER_NAMES_PHASE2,

    3: POSSIBLE_LEGACY_LAYER_NAMES_PHASE3,

}



LOGGER = logging.getLogger(__name__)





def required_layers_for_phase(phase: int) -> tuple[str, ...]:

    if phase == 2:

        return REQUIRED_LAYERS_PHASE2

    if phase == 3:

        return REQUIRED_LAYERS_PHASE3

    raise ValueError(f"无效 phase: {phase} (仅支持 2 或 3)")





# --- 解耦：路径与 I/O -----------------------------------------------------------------





def default_assets_dir() -> Path:

    """默认 assets 目录：相对本文件 ../../assets。"""

    return Path(__file__).resolve().parent.parent / "assets"





def iter_3dm_files(assets_dir: Path) -> Iterator[Path]:

    """在目录下按文件名顺序列出所有 .3dm（不递归子目录，避免误扫）。"""

    if not assets_dir.is_dir():

        LOGGER.warning("assets 目录不存在或不是目录: %s", assets_dir)

        return

    yield from sorted(assets_dir.glob("*.3dm"), key=lambda p: p.name.lower())





def read_3dm_model(path: Path):

    """读取 3dm 为 rhino3dm File3dm；依赖未安装时抛 ImportError，文件损坏时由库抛错。"""

    import rhino3dm as r3d  # 延迟导入，便于 --help 不依赖



    return r3d.File3dm.Read(str(path))





# --- 解耦：Phase 与文件名 ----------------------------------------------------------------





def detect_phase_from_filename(file_name: str) -> int | None:

    """

    从文件名识别 Phase2 / Phase3，例如 *Phase2*、*phase_3*。

    未识别则返回 None。

    """

    n = file_name.lower()

    if re.search(r"phase[_\s-]?3", n):

        return 3

    if re.search(r"phase[_\s-]?2", n):

        return 2

    return None





def effective_phase_for_file(path: Path, mode: str, default_if_unknown: int) -> int:

    """

    mode: '2' | '3' | 'auto'

    无法 auto 且未识别文件名时，使用 default_if_unknown 并打日志。

    """

    if mode in ("2", "3"):

        return int(mode)

    detected = detect_phase_from_filename(path.name)

    if detected is not None:

        LOGGER.info("从文件名推断为 Phase%d: %s", detected, path.name)

        return detected

    LOGGER.warning(

        "无法从文件名识别 Phase2/Phase3，使用默认 Phase%d: %s",

        default_if_unknown,

        path.name,

    )

    return default_if_unknown





# --- 解耦：从模型提取「用于比对」的图层名集合 -------------------------------





def layer_table_short_names(file3dm) -> set[str]:

    """取模型中所有图层的短名 (Layer.Name)。"""
    names: set[str] = set()

    table = file3dm.Layers

    n = len(table)

    for i in range(n):

        layer = table[i]

        if layer is None:

            continue

        name = (layer.Name or "").strip()

        if name:

            names.add(name)

    return names





def layer_table_full_path_tokens(file3dm) -> set[str]:

    """同时收集 FullPath 及最后一段，便于嵌套子图层时仍能匹配协议名。"""

    out: set[str] = set()

    table = file3dm.Layers

    for i in range(len(table)):

        layer = table[i]

        if layer is None:

            continue

        fp = (getattr(layer, "FullPath", None) or "").strip()

        if fp:

            out.add(fp)

            for sep in ("::", " / "):

                if sep in fp:

                    out.add(fp.split(sep)[-1].strip())

    return {x for x in out if x}





# --- 解耦：单文件审计逻辑 ----------------------------------------------------





@dataclass(frozen=True)

class FileAuditResult:

    path: Path

    protocol_phase: int

    ok: bool

    missing: tuple[str, ...]

    extra: tuple[str, ...]

    legacy_hints: tuple[str, ...]





def _filter_rhino_stock_from_extra(extra: Sequence[str]) -> tuple[str, ...]:

    return tuple(

        n

        for n in sorted(extra)

        if n not in RHINO_STOCK_LAYER_SHORT_NAMES

    )





def audit_layer_names_against_required(

    short_names: set[str],

    path_tokens: set[str],

    required: Sequence[str],

    legacy_map: dict[str, str] | None,

) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:

    """返回 (缺失, 多余短名, 旧名提示)。"""
    req = list(required)

    combined = set(short_names) | set(path_tokens)

    missing = tuple(sorted(r for r in req if r not in combined))

    required_set = set(req)

    raw_extra = [n for n in short_names if n not in required_set]

    extra = _filter_rhino_stock_from_extra(raw_extra)

    hints: list[str] = []

    if legacy_map:

        for legacy, new in legacy_map.items():

            if new in missing and legacy in combined:

                hints.append(

                    f"协议需「{new}」，但发现旧名「{legacy}」— 可能未重命名图层"

                )

    return missing, extra, tuple(hints)





def audit_single_3dm_file(path: Path, protocol_phase: int) -> FileAuditResult:

    """对单个 .3dm 按指定 Phase 执行审计。"""

    required = required_layers_for_phase(protocol_phase)

    legacy = LEGACY_HINTS_BY_PHASE.get(protocol_phase, {})

    short: set[str] = set()

    tokens: set[str] = set()

    try:

        model = read_3dm_model(path)

        short = layer_table_short_names(model)

        tokens = layer_table_full_path_tokens(model)

    except ImportError as e:

        LOGGER.error("未安装 rhino3dm: %s. 请执行: pip install -r requirements.txt", e)

        return FileAuditResult(

            path, protocol_phase, False, tuple(required), (), (str(e),)

        )

    except Exception as e:  # noqa: BLE001

        LOGGER.error("无法读取 3dm: %s", path, exc_info=True)

        return FileAuditResult(

            path, protocol_phase, False, tuple(required), (), (str(e),)

        )



    missing, extra, hints = audit_layer_names_against_required(

        short, tokens, required, legacy

    )

    ok = len(missing) == 0

    return FileAuditResult(

        path=path,

        protocol_phase=protocol_phase,

        ok=ok,

        missing=missing,

        extra=extra,

        legacy_hints=tuple(hints),

    )





# --- 解耦：报告与入口 --------------------------------------------------------





@dataclass

class ProjectAuditReport:

    results: list[FileAuditResult] = field(default_factory=list)



    @property

    def all_ok(self) -> bool:

        return all(r.ok for r in self.results) if self.results else True





def format_result_human_readable(r: FileAuditResult) -> str:

    """清晰指出缺失/多余/旧名提示。"""

    lines = [f"文件: {r.path.name}", f"  路径: {r.path}"]

    lines.append(f"  使用协议: Phase{r.protocol_phase}")

    if r.ok:

        lines.append(

            f"  状态: 通过 — 必须图层均已出现（按 Phase{r.protocol_phase} 协议）。"

        )

    else:

        lines.append("  状态: 不通过。")

    if r.missing:

        lines.append("  缺失的协议图层名: " + ", ".join(f"`{m}`" for m in r.missing))

    if r.extra:

        lines.append("  协议未列出的图层名（短名）: " + ", ".join(f"`{e}`" for e in r.extra))

    for h in r.legacy_hints:

        lines.append(f"  提示: {h}")

    return "\n".join(lines)





def run_auditor(assets_dir: Path, phase_mode: str, default_phase_if_auto_unknown: int) -> ProjectAuditReport:

    """扫描 assets 下所有 .3dm 并逐文件审计。"""

    report = ProjectAuditReport()

    files = list(iter_3dm_files(assets_dir))

    if not files:

        LOGGER.info("未找到任何 .3dm 文件，目录: %s", assets_dir)

    for p in files:

        ph = effective_phase_for_file(p, phase_mode, default_phase_if_auto_unknown)

        LOGGER.info("开始审计: %s (Phase%d)", p, ph)

        res = audit_single_3dm_file(p, ph)

        report.results.append(res)

    return report





def _configure_logging_verbose(verbose: bool) -> None:

    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(

        level=level,

        format="%(levelname)s %(name)s: %(message)s",

        stream=sys.stderr,

    )





def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:

    p = argparse.ArgumentParser(

        description="审计 assets 下 3dm 的图层名是否符合 YUI Box Phase2 / Phase3 拆件协议。"

    )

    p.add_argument(

        "--assets",

        type=Path,

        default=None,

        help="含 .3dm 的目录（默认: YUI_Render_System/assets）",

    )

    p.add_argument(

        "--phase",

        choices=("2", "3", "auto"),

        default="auto",

        help="2=仅 Phase2 表，3=仅 Phase3 表，auto=按文件名含 Phase2/Phase3 自动选择（否则见 --default-phase）",

    )

    p.add_argument(

        "--default-phase",

        type=int,

        choices=(2, 3),

        default=2,

        help="--phase auto 且无法从文件名推断时使用（默认 2）",

    )

    p.add_argument("-v", "--verbose", action="store_true", help="调试日志")

    return p.parse_args(list(argv) if argv is not None else None)





def main(argv: Sequence[str] | None = None) -> int:

    args = parse_args(argv)

    _configure_logging_verbose(args.verbose)

    assets = (args.assets if args.assets is not None else default_assets_dir()).resolve()

    LOGGER.info("扫描目录(assets): %s", assets)

    LOGGER.info(

        "Phase2 必须图层: %s",

        ", ".join(REQUIRED_LAYERS_PHASE2),

    )

    LOGGER.info(

        "Phase3 必须图层: %s",

        ", ".join(REQUIRED_LAYERS_PHASE3),

    )

    rep = run_auditor(assets, args.phase, args.default_phase)

    for r in rep.results:

        print(format_result_human_readable(r))

        print()

    if not rep.results:

        print("未处理任何文件。")

    return 0 if rep.all_ok or not rep.results else 1





if __name__ == "__main__":

    raise SystemExit(main())

