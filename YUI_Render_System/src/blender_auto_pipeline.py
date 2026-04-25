"""
YUI Box 自动化 Blender 流水线（pipeline）。

功能：
1) 扫描 assets 并复用 auditor 逻辑选择“最新且审计通过”的 .3dm
2) 清空 Blender 当前场景
3) 导入 3dm，并设置 1:1 单位
4) 读取 config/cmf_map.json，按图层名（layer_name）自动创建/挂载 PBR 材质
5) 若存在同名 .stp，打印制造源校验日志
6) 全流程输出清晰 print 日志（非黑箱）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 确保在 Blender 直接执行本脚本时，能导入同目录 auditor.py
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from auditor import (
    audit_single_3dm_file,
    effective_phase_for_file,
    iter_3dm_files,
)

try:
    import bpy  # type: ignore  # pylint: disable=import-error
except Exception:  # noqa: BLE001 pylint: disable=broad-exception-caught
    bpy = None


def log(msg: str) -> None:
    """统一流水线日志出口。"""
    print(f"[blender_auto_pipeline] {msg}")


@dataclass(frozen=True)
class CmfEntry:
    layer_name: str
    material_name: str
    pbr: dict[str, Any]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YUI Box Blender 自动流水线：审计通过后导入并自动挂载 CMF 材质。"
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets",
        help="3dm/stp 资产目录（默认：YUI_Render_System/assets）",
    )
    parser.add_argument(
        "--cmf-map",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "config" / "cmf_map.json",
        help="CMF 映射 JSON（默认：YUI_Render_System/config/cmf_map.json）",
    )
    parser.add_argument(
        "--phase",
        choices=("2", "3", "auto"),
        default="auto",
        help="审计协议版本：2/3/auto（默认 auto）。",
    )
    parser.add_argument(
        "--default-phase",
        choices=(2, 3),
        type=int,
        default=2,
        help="当 --phase=auto 且文件名无法识别时使用的默认协议（默认 2）。",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留场景中的灯光和相机，仅清理并更新几何体。",
    )
    parser.add_argument(
        "--strict-stp",
        action="store_true",
        help="严格 STP 校验：必须存在同名 .stp，否则终止。",
    )
    return parser.parse_args(argv)


def load_cmf_map(cmf_map_path: Path) -> dict[str, CmfEntry]:
    if not cmf_map_path.exists():
        raise FileNotFoundError(f"CMF 配置文件不存在: {cmf_map_path}")

    data = json.loads(cmf_map_path.read_text(encoding="utf-8"))
    layers = data.get("layers", [])
    mapping: dict[str, CmfEntry] = {}
    for row in layers:
        layer_name = str(row.get("layer_name", "")).strip()
        material_name = str(row.get("material_name", "")).strip()
        pbr = row.get("pbr", {}) or {}
        if not layer_name or not material_name:
            log(f"跳过无效 cmf 条目: {row}")
            continue
        mapping[layer_name] = CmfEntry(
            layer_name=layer_name,
            material_name=material_name,
            pbr=dict(pbr),
        )
    log(f"已加载 CMF 映射条目: {len(mapping)}")
    return mapping


def find_latest_audited_3dm(
    assets_dir: Path, phase_mode: str, default_phase_if_auto_unknown: int
) -> tuple[Path | None, int | None]:
    files = sorted(
        list(iter_3dm_files(assets_dir)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    log(f"资产扫描完成，发现 .3dm 文件数量: {len(files)}")
    for f in files:
        phase = effective_phase_for_file(f, phase_mode, default_phase_if_auto_unknown)
        log(f"审计候选文件: {f.name} (Phase{phase})")
        result = audit_single_3dm_file(f, phase)
        if result.ok:
            log(f"审计通过，选中最新文件: {f.name}")
            return f, phase
        log(
            "审计不通过，跳过: "
            f"{f.name}; missing={list(result.missing)} extra={list(result.extra)}"
        )
    return None, None


def verify_same_name_stp(assets_dir: Path, selected_3dm: Path) -> bool:
    stp = assets_dir / f"{selected_3dm.stem}.stp"
    if stp.exists():
        log(f"Manufacturing source verified: {stp.name}")
        return True
    else:
        log(f"未找到同名 STP（stp）: {stp.name}")
        return False


def ensure_blender_runtime() -> None:
    if bpy is None:
        raise RuntimeError(
            "当前环境未检测到 bpy。请在 Blender 中运行："
            "blender -b --python src/blender_auto_pipeline.py -- [args]"
        )


def clear_scene_objects(keep_lights_camera: bool = False) -> None:
    ensure_blender_runtime()
    objs = list(bpy.data.objects)
    count_before = len(objs)

    if keep_lights_camera:
        # 仅删除几何体/辅助对象，保留 LIGHT/CAMERA 以便复用布光和机位
        bpy.ops.object.select_all(action="DESELECT")
        delete_targets = [o for o in objs if getattr(o, "type", "") not in {"LIGHT", "CAMERA"}]
        for obj in delete_targets:
            obj.select_set(True)
        if delete_targets:
            bpy.ops.object.delete(use_global=False)
        log(
            f"场景清理完成（--keep）：总对象={count_before}, "
            f"删除={len(delete_targets)}, 保留灯光/相机"
        )
    else:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        log(f"场景清理完成：总对象={count_before}, 删除全部对象")

    # 清理孤立数据，避免重复运行堆积
    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        if block.users == 0:
            bpy.data.materials.remove(block)


def set_unit_scale_1to1() -> None:
    ensure_blender_runtime()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    log("单位设置完成：METRIC，scale_length=1.0（1:1）")


def _try_import_operator(filepath: str) -> bool:
    """尝试多个常见 3dm 导入 operator。"""
    ensure_blender_runtime()
    candidates = [
        ("import_scene", "rhino"),
        ("import_3dm", "some_data"),
    ]
    for module_name, op_name in candidates:
        module = getattr(bpy.ops, module_name, None)
        operator = getattr(module, op_name, None) if module else None
        if operator is None:
            continue
        log(f"尝试导入 operator: bpy.ops.{module_name}.{op_name}")
        try:
            ret = operator(filepath=filepath)
            ok = "FINISHED" in ret
            if ok:
                log(f"导入成功：operator=bpy.ops.{module_name}.{op_name}")
                return True
            log(f"导入返回非 FINISHED：{ret}")
        except Exception as e:  # noqa: BLE001
            log(f"导入失败：operator=bpy.ops.{module_name}.{op_name}, error={e}")
    return False


def import_3dm(filepath: Path) -> None:
    ensure_blender_runtime()
    ok = _try_import_operator(str(filepath))
    if not ok:
        raise RuntimeError(
            "未找到可用的 3dm 导入器。请确认 Blender 已启用 Rhino 3DM 导入插件，"
            "或根据你环境中的 operator 名称更新 _try_import_operator()。"
        )
    log(f"3DM 导入完成: {filepath.name}")


def find_object_layer_name(obj: Any, known_layer_names: set[str]) -> str | None:
    """
    推断对象所属图层名（layer）：
    1) 自定义属性（常见导入器会写 layer/rhino_layer）
    2) 所属 collection 名
    3) 对象名包含协议图层关键字
    """
    # 1) 尝试自定义属性
    custom_keys = ("layer_name", "rhino_layer", "layer", "Layer", "rhino::layer")
    for k in custom_keys:
        try:
            v = obj.get(k)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            v = None
        if isinstance(v, str) and v.strip() in known_layer_names:
            return v.strip()

    # 2) collection 名匹配
    for col in getattr(obj, "users_collection", []):
        n = (getattr(col, "name", "") or "").strip()
        if n in known_layer_names:
            return n

    # 3) object 名包含图层名
    obj_name = (getattr(obj, "name", "") or "").strip()
    for layer in known_layer_names:
        if layer in obj_name:
            return layer
    return None


def get_or_create_principled_material(entry: CmfEntry):
    ensure_blender_runtime()
    mat = bpy.data.materials.get(entry.material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=entry.material_name)
        log(f"新建材质: {entry.material_name}")
    else:
        log(f"复用材质: {entry.material_name}")

    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        raise RuntimeError(f"材质节点树不可用: {entry.material_name}")

    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nt.nodes.new(type="ShaderNodeBsdfPrincipled")
        out = nt.nodes.get("Material Output")
        if out is not None:
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    pbr = entry.pbr
    base_color = pbr.get("base_color", [0.8, 0.8, 0.8, 1.0])
    roughness = float(pbr.get("roughness", 0.5))
    metallic = float(pbr.get("metallic", 0.0))
    transmission = pbr.get("transmission", None)

    # Blender 4.x / 3.x 通常都支持这些输入名
    if "Base Color" in bsdf.inputs:
        bsdf.inputs["Base Color"].default_value = base_color
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = roughness
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = metallic
    if transmission is not None and "Transmission" in bsdf.inputs:
        bsdf.inputs["Transmission"].default_value = float(transmission)
        log(
            f"材质透射参数已设置: {entry.material_name}, transmission={transmission}"
        )

    log(
        "材质参数已设置: "
        f"{entry.material_name}, base_color={base_color}, "
        f"roughness={roughness}, metallic={metallic}"
    )
    return mat


def assign_cmf_materials(cmf_map: dict[str, CmfEntry]) -> None:
    ensure_blender_runtime()
    known_layers = set(cmf_map.keys())
    objects = [o for o in bpy.context.scene.objects if getattr(o, "type", "") == "MESH"]
    log(f"开始 CMF 自动挂载，MESH 对象数量: {len(objects)}")
    for obj in objects:
        layer_name = find_object_layer_name(obj, known_layers)
        if layer_name is None:
            log(f"跳过对象（未匹配图层）: {obj.name}")
            continue

        entry = cmf_map[layer_name]
        try:
            mat = get_or_create_principled_material(entry)
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
            log(
                f"材质挂载成功: object={obj.name}, layer={layer_name}, "
                f"material={entry.material_name}"
            )
        except Exception as e:  # noqa: BLE001
            log(
                f"材质挂载失败，已跳过: object={obj.name}, layer={layer_name}, error={e}"
            )


def run_pipeline(args: argparse.Namespace) -> int:
    assets_dir = args.assets.resolve()
    cmf_map_path = args.cmf_map.resolve()
    log(f"pipeline 启动，assets={assets_dir}")
    log(f"CMF 配置路径: {cmf_map_path}")

    cmf_map = load_cmf_map(cmf_map_path)

    selected_3dm, used_phase = find_latest_audited_3dm(
        assets_dir=assets_dir,
        phase_mode=args.phase,
        default_phase_if_auto_unknown=args.default_phase,
    )
    if selected_3dm is None or used_phase is None:
        log("未找到“审计通过”的 3dm 文件，流水线终止。")
        return 1

    log(f"最终选择资产: {selected_3dm.name}, protocol=Phase{used_phase}")
    stp_ok = verify_same_name_stp(assets_dir, selected_3dm)
    if args.strict_stp and not stp_ok:
        log("strict-stp 已开启：未检测到同名 .stp，流水线终止。")
        return 1

    clear_scene_objects(keep_lights_camera=args.keep)
    set_unit_scale_1to1()
    import_3dm(selected_3dm)
    assign_cmf_materials(cmf_map)

    log("pipeline 执行完成。")
    return 0


def _extract_script_argv() -> list[str]:
    """
    兼容 Blender 参数传递：
    blender -b --python xxx.py -- --assets ...
    """
    import sys

    if "--" in sys.argv:
        idx = sys.argv.index("--")
        return sys.argv[idx + 1 :]
    # 非 Blender 场景下，允许直接 python script.py --args 调试
    return sys.argv[1:]


if __name__ == "__main__":
    parsed = parse_args(_extract_script_argv())
    raise SystemExit(run_pipeline(parsed))
