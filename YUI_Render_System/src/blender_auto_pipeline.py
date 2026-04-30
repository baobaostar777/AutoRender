"""
YUI Box 自动化 Blender 流水线（pipeline）。

功能：
1) 扫描 assets 选择 .3dm：默认审计( audit )通过才用；可加 --skip-audit 仅用「最新修改时间」
2) 清空 Blender 当前场景
3) 导入 3dm，并设置 1:1 单位
4) CMF JSON：每项可为精确 layer_name（兼容旧逻辑），可加 match_contains_any / match_regex / match_prefixes
   （列表顺序越早越优先）；实色材加 0.2mm bevel( Bevel )，玻璃除外
5) 若存在同名 .stp，打印制造源校验日志
6) 不生成装饰几何体；光影使用 assets/env/studio_standard.exr 环境贴图，
   渲染为透明底( film_transparent )。
7) 全流程输出清晰 print 日志（非黑箱）
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
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


# 工程倒角( engineering bevel )：0.2mm；场景为米制( METRIC, scale 1.0 )故半径=0.0002 m
BEVEL_RADIUS_METERS = 0.0002
ENV_REL_PATH = ("env", "studio_standard.exr")

# 高金属( aluminum 等): Principled 各向异性( anisotropic ) 模拟拉丝/喷砂( brushed / blasted ) 微反射
METAL_ANISO_THRESHOLD = 0.5
DEFAULT_ANISOTROPIC = 0.32
DEFAULT_ANISOTROPIC_ROTATION = 0.15


def _pbr_treats_as_glass(pbr: dict[str, Any]) -> bool:
    if pbr.get("is_glass") is True:
        return True
    tr = pbr.get("transmission", None)
    if tr is not None and float(tr) > 0.01:
        return True
    return False


def _normalize_str_list(val: Any) -> tuple[str, ...]:
    """JSON 中单字符串或多字符串 -> 规整元组。"""
    if val is None:
        return ()
    if isinstance(val, str):
        t = val.strip()
        return (t,) if t else ()
    if isinstance(val, list):
        out: list[str] = []
        for x in val:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return tuple(out)
    return ()


@dataclass(frozen=True)
class CmfRule:
    """单条 CMF 规则（列表顺序决定匹配优先级，先匹配的先生效）。"""

    layer_name: str
    material_name: str
    pbr: dict[str, Any]
    match_contains_any: tuple[str, ...] = ()
    match_regex: str | None = None
    match_prefixes: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    is_fallback: bool = False

    @property
    def has_flexible_match(self) -> bool:
        return bool(
            self.match_contains_any
            or (self.match_regex and self.match_regex.strip())
            or self.match_prefixes
        )


# 兼容旧名
CmfEntry = CmfRule


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
        "--skip-audit",
        action="store_true",
        help="仅渲染：不进行 Phase 审计，使用 assets 下修改时间最新( mtime )的 .3dm。",
    )
    parser.add_argument(
        "--pick-3dm",
        type=Path,
        default=None,
        help=(
            "指定单个 .3dm 路径；与 --skip-audit 合用时直接使用该文件。"
            "未加 --skip-audit 时只对这一份执行 Phase 审计并渲染。"
        ),
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "output",
        help="渲染图输出目录（默认：YUI_Render_System/output）",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="仅导入并挂载材质，不执行最终静帧渲染（用于实时检查）。",
    )
    return parser.parse_args(argv)


def load_cmf_rules(cmf_map_path: Path) -> list[CmfRule]:
    if not cmf_map_path.exists():
        raise FileNotFoundError(f"CMF 配置文件不存在: {cmf_map_path}")

    data = json.loads(cmf_map_path.read_text(encoding="utf-8"))
    layers = data.get("layers", [])
    rules: list[CmfRule] = []
    for row in layers:
        layer_name = str(row.get("layer_name", "")).strip()
        material_name = str(row.get("material_name", "")).strip()
        pbr = row.get("pbr", {}) or {}
        mr = row.get("match_regex")
        mr_s = mr.strip() if isinstance(mr, str) else None
        con = _normalize_str_list(row.get("match_contains_any"))
        pfx = _normalize_str_list(row.get("match_prefixes"))
        ex = _normalize_str_list(row.get("exclude_keywords"))
        is_fallback = bool(row.get("is_fallback", False))
        if not material_name:
            log(f"跳过无效 cmf 条目: {row}")
            continue
        if not layer_name:
            layer_name = material_name.replace(" ", "_")
        rules.append(
            CmfRule(
                layer_name=layer_name,
                material_name=material_name,
                pbr=dict(pbr),
                match_contains_any=con,
                match_regex=mr_s,
                match_prefixes=pfx,
                exclude_keywords=ex,
                is_fallback=is_fallback,
            )
        )
    flex = sum(1 for r in rules if r.has_flexible_match)
    log(f"已加载 CMF 规则条目: {len(rules)}（其中通用匹配 flexible: {flex}）")
    return rules


def load_cmf_map(cmf_map_path: Path) -> list[CmfRule]:
    """历史函数名兼容，返回有序规则列表。"""
    return load_cmf_rules(cmf_map_path)


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


def find_latest_3dm_by_mtime(assets_dir: Path) -> Path | None:
    """不审计：取 assets 目录下最新修改的一条 .3dm。"""
    files = sorted(
        list(iter_3dm_files(assets_dir)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    log(f"[skip-audit] 扫描 .3dm 数量: {len(files)}")
    if not files:
        log("[skip-audit] 未找到 .3dm 文件")
        return None
    pick = files[0]
    log(f"[skip-audit] 选中最新修改: {pick.name}")
    return pick


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


def _idprop_gather_strings(bl_id: Any, _add: Any) -> None:
    """从 Blender ID 自定义属性( custom properties )里收集字符串（不同 3DM 插件键名不一致）。"""
    if bl_id is None:
        return
    try:
        keys_iter = getattr(bl_id, "keys", lambda: [])()
    except Exception:  # noqa: BLE001
        return
    for k in keys_iter:
        if not isinstance(k, str):
            continue
        lk = k.lower()
        try:
            v = bl_id.get(k)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, str):
            _add(v)


def gather_object_layer_hints(obj: Any) -> list[str]:
    """
    收集用于匹配的字符串：Rhino/导入器自定义属性、collections、父级链对象名、自身对象名。
    若插件把图层写在 mesh  datablock 上，也会一并扫到。
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        t = raw.strip()
        if not t:
            return
        if len(t) > 512:
            t = t[:512]
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    custom_keys = (
        "layer_name",
        "rhino_layer",
        "layer",
        "Layer",
        "rhino::layer",
        "RhinoLayer",
        "layerName",
        "LayerName",
        "rhino_layer_name",
    )
    for k in custom_keys:
        try:
            v = obj.get(k)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            v = None
        _add(v)

    _idprop_gather_strings(obj, _add)
    data = getattr(obj, "data", None)
    if data is not None:
        _idprop_gather_strings(data, _add)

    for col in getattr(obj, "users_collection", []) or []:
        _add(getattr(col, "name", ""))

    parent = getattr(obj, "parent", None)
    depth = 0
    while parent is not None and depth < 8:
        _add(getattr(parent, "name", ""))
        parent = getattr(parent, "parent", None)
        depth += 1

    _add(getattr(obj, "name", ""))
    return out


def rule_excluded(rule: CmfRule, hay: str) -> bool:
    if not rule.exclude_keywords:
        return False
    hl = hay.lower()
    return any(tok.lower() in hl for tok in rule.exclude_keywords)


def flexible_rule_matches(rule: CmfRule, hint_parts: list[str], hay: str) -> bool:
    if rule_excluded(rule, hay):
        return False
    if rule.match_regex and rule.match_regex.strip():
        try:
            return re.search(rule.match_regex, hay, re.IGNORECASE | re.MULTILINE) is not None
        except re.error as e:
            log(f"match_regex 无效，已跳过: {rule.layer_name}, error={e}")
            return False

    tl = hay.lower()

    if rule.match_prefixes:
        for hp in hint_parts:
            hsl = hp.lower()
            if any(hsl.startswith(p.lower()) for p in rule.match_prefixes):
                return True

    if rule.match_contains_any:
        return any(tok.lower() in tl for tok in rule.match_contains_any)

    return False


def legacy_rule_matches(rule: CmfRule, hint_parts: list[str], hay: str) -> bool:
    """无 flexible 字段时：与任一 hint 完全相同，或在对象名中包含 layer_name。"""
    if rule_excluded(rule, hay):
        return False
    tgt = rule.layer_name.strip()
    if not tgt:
        return False
    if any(h.strip() == tgt for h in hint_parts):
        return True
    if hint_parts:
        obj_name = hint_parts[-1]
        if tgt in obj_name:
            return True
    return False


def pick_cmf_rule_for_object(rules: list[CmfRule], obj: Any) -> CmfRule | None:
    hints = gather_object_layer_hints(obj)
    hay = "|".join(hints)
    fallback_rule: CmfRule | None = None
    for rule in rules:
        if rule.is_fallback:
            if fallback_rule is None:
                fallback_rule = rule
            continue
        if rule.has_flexible_match:
            if flexible_rule_matches(rule, hints, hay):
                return rule
        elif legacy_rule_matches(rule, hints, hay):
            return rule
    return fallback_rule


def get_or_create_principled_material(entry: CmfRule):
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
    # 关键修复：
    # Blender 本地化环境下节点名可能不是 "Principled BSDF"，
    # 旧逻辑会不断新建节点却不一定连到输出，导致视觉仍是默认白材质。
    # 这里改为每次重建最小节点树，确保唯一 BSDF 连到输出。
    nt.nodes.clear()
    out = nt.nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300.0, 0.0)
    bsdf = nt.nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0.0, 0.0)
    pbr = entry.pbr
    is_glass = _pbr_treats_as_glass(pbr)
    if not is_glass:
        geom = nt.nodes.new(type="ShaderNodeNewGeometry")
        geom.location = (-500.0, 0.0)
        bevel = nt.nodes.new(type="ShaderNodeBevel")
        bevel.location = (-260.0, 0.0)
        bevel.name = f"{entry.material_name}_Bevel"
        if "Radius" in bevel.inputs:
            bevel.inputs["Radius"].default_value = BEVEL_RADIUS_METERS
        if "Normal" in bevel.inputs and "Normal" in geom.outputs:
            nt.links.new(geom.outputs["Normal"], bevel.inputs["Normal"])
        if "Normal" in bsdf.inputs and "Normal" in bevel.outputs:
            nt.links.new(bevel.outputs["Normal"], bsdf.inputs["Normal"])
        log(
            f"工程倒角已连接: {entry.material_name}, radius={BEVEL_RADIUS_METERS}m (0.2mm)"
        )
    else:
        log(f"跳过硬边倒角(玻璃/透射类): {entry.material_name}")

    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

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

    # 铝合金等: Metallic 高时施加轻微各向异性( anisotropic )，拉丝/喷砂高光方向感
    if (
        not is_glass
        and metallic > METAL_ANISO_THRESHOLD
        and "Anisotropic" in bsdf.inputs
    ):
        a_val = float(pbr.get("anisotropic", DEFAULT_ANISOTROPIC))
        r_val = float(
            pbr.get("anisotropic_rotation", DEFAULT_ANISOTROPIC_ROTATION)
        )
        bsdf.inputs["Anisotropic"].default_value = max(0.0, min(1.0, a_val))
        if "Anisotropic Rotation" in bsdf.inputs:
            bsdf.inputs["Anisotropic Rotation"].default_value = r_val
        # 可选: 显式切向( tangent ) 强化各向异性方向（无 UV/版本不兼容时仅依赖数值 anisotropy 亦可）
        if "Tangent" in bsdf.inputs:
            try:
                tan_nd = nt.nodes.new(type="ShaderNodeTangent")
                tan_nd.location = (bsdf.location.x - 220.0, bsdf.location.y - 240.0)
                out_sock = tan_nd.outputs.get("Tangent", tan_nd.outputs[0])
                nt.links.new(out_sock, bsdf.inputs["Tangent"])
            except Exception as e:  # noqa: BLE001
                log(f"Tangent 节点未连接(已忽略): {e}")
        log(
            f"金属各向异性已应用: {entry.material_name}, "
            f"anisotropic={bsdf.inputs['Anisotropic'].default_value}, "
            f"rotation={r_val}"
        )

    # 视口辅助色也同步，避免某些视图模式下仍接近默认白
    try:
        mat.diffuse_color = tuple(base_color)
    except Exception:  # noqa: BLE001
        pass

    log(
        "材质参数已设置: "
        f"{entry.material_name}, base_color={base_color}, "
        f"roughness={roughness}, metallic={metallic}"
    )
    return mat


def assign_cmf_materials(rules: list[CmfRule]) -> None:
    ensure_blender_runtime()
    objects = [o for o in bpy.context.scene.objects if getattr(o, "type", "") == "MESH"]
    log(f"开始 CMF 自动挂载，MESH 对象数量: {len(objects)}（规则条目: {len(rules)}）")
    for obj in objects:
        entry = pick_cmf_rule_for_object(rules, obj)
        if entry is None:
            log(f"跳过对象（无任何规则命中）: {obj.name}")
            continue

        try:
            mat = get_or_create_principled_material(entry)
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
            hints = gather_object_layer_hints(obj)
            log(
                "材质挂载成功: "
                f"object={obj.name}, rule_key={entry.layer_name}, "
                f"material={entry.material_name}, hints={hints}"
            )
        except Exception as e:  # noqa: BLE001
            log(f"材质挂载失败，已跳过: object={obj.name}, rule={entry.layer_name}, error={e}")


def _mesh_objects() -> list[Any]:
    ensure_blender_runtime()
    return [o for o in bpy.context.scene.objects if getattr(o, "type", "") == "MESH"]


def _compute_scene_bbox_world(mesh_objs: list[Any]) -> tuple[Any, Any]:
    """
    计算所有网格对象在世界坐标下的包围盒 (min_xyz, max_xyz)。
    """
    ensure_blender_runtime()
    from mathutils import Vector  # type: ignore

    if not mesh_objs:
        raise RuntimeError("场景中没有可用于构图的 MESH 对象。")
    min_v = Vector((1e18, 1e18, 1e18))
    max_v = Vector((-1e18, -1e18, -1e18))
    for obj in mesh_objs:
        for corner in obj.bound_box:
            wc = obj.matrix_world @ Vector(corner)
            min_v.x = min(min_v.x, wc.x)
            min_v.y = min(min_v.y, wc.y)
            min_v.z = min(min_v.z, wc.z)
            max_v.x = max(max_v.x, wc.x)
            max_v.y = max(max_v.y, wc.y)
            max_v.z = max(max_v.z, wc.z)
    return min_v, max_v


def _ensure_camera_for_bbox(keep_mode: bool, mesh_objs: list[Any]) -> Any:
    """
    相机自动构图：基于包围盒中心与尺寸计算机位，避免硬编码坐标。
    keep 模式下若已有 scene.camera 则直接复用。
    """
    ensure_blender_runtime()
    from mathutils import Vector  # type: ignore

    scene = bpy.context.scene
    if keep_mode and scene.camera is not None:
        log(f"keep 模式复用现有相机: {scene.camera.name}")
        return scene.camera

    min_v, max_v = _compute_scene_bbox_world(mesh_objs)
    center = (min_v + max_v) * 0.5
    size = max_v - min_v
    max_dim = max(size.x, size.y, size.z)
    distance = max(0.5, max_dim * 2.2)

    cam_obj = scene.camera
    if cam_obj is None:
        cam_data = bpy.data.cameras.new("AutoCamera")
        cam_obj = bpy.data.objects.new("AutoCamera", cam_data)
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
        log("新建自动相机: AutoCamera")
    else:
        log(f"复用场景相机并重定位: {cam_obj.name}")

    cam_obj.location = center + Vector((distance, -distance, distance * 0.7))
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return cam_obj


def apply_studio_world_and_transparency(assets_dir: Path) -> None:
    """
    标准化光影: 仅使用 assets/env/studio_standard.exr 作为环境照明( IBL, HDRI )，
    不创建额外灯光对象。渲染可合成透明底。
    """
    ensure_blender_runtime()
    scene = bpy.context.scene
    exr_path = Path(assets_dir).joinpath(*ENV_REL_PATH)

    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("YUI_StudioWorld")
        scene.world = world
    world.use_nodes = True
    wnt = world.node_tree
    if wnt is None:
        return
    wnt.nodes.clear()
    w_out = wnt.nodes.new(type="ShaderNodeOutputWorld")
    w_out.location = (300.0, 0.0)
    bg = wnt.nodes.new(type="ShaderNodeBackground")
    bg.location = (0.0, 0.0)
    if exr_path.is_file():
        try:
            env_tex = wnt.nodes.new(type="ShaderNodeTexEnvironment")
            env_tex.location = (-400.0, 0.0)
            env_tex.name = "StudioStandard_EXR"
            abs_path = str(exr_path.resolve())
            env_tex.image = bpy.data.images.load(abs_path, check_existing=True)
            wnt.links.new(env_tex.outputs["Color"], bg.inputs["Color"])
            log(f"已加载世界环境( HDRI ): {exr_path}")
        except Exception as e:  # noqa: BLE001
            bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
            log(
                f"环境图加载失败，已使用中性灰占位: {exr_path}, error={e}"
            )
    else:
        bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
        log(
            f"未找到 {exr_path}，已使用中性灰世界环境(占位)。"
            f"请将 studio_standard.exr 放入: {exr_path.parent}/"
        )
    if "Strength" in bg.inputs:
        bg.inputs["Strength"].default_value = 1.0
    wnt.links.new(bg.outputs["Background"], w_out.inputs["Surface"])
    scene.view_settings.exposure = 0.0
    scene.render.film_transparent = True
    log("标准化光影已应用(仅 IBL, 无自动三点光); film_transparent=True")


def _world_background_node() -> Any | None:
    ensure_blender_runtime()
    world = bpy.context.scene.world
    if world is None or world.node_tree is None:
        return None
    for n in world.node_tree.nodes:
        if n.bl_idname == "ShaderNodeBackground":
            return n
    return None


def _render_still(
    output_dir: Path, selected_3dm: Path, keep_mode: bool, assets_dir: Path
) -> Path:
    """
    写出 output 静帧( still ) PNG。与视口( viewport )不同，无影棚 EXR 时 headless( 无头 ) 渲染
    常偏暗/发灰，因此仅在**本次 bpy.ops.render 调用**内临时提高曝光( exposure )/世界强度，
    执行完会恢复，不持久改变你在 Blender 里看到的光照。
    """
    ensure_blender_runtime()
    output_dir.mkdir(parents=True, exist_ok=True)
    mesh_objs = _mesh_objects()
    _ensure_camera_for_bbox(keep_mode, mesh_objs)

    scene = bpy.context.scene
    engine_candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES")
    selected_engine = None
    for engine in engine_candidates:
        try:
            scene.render.engine = engine
            selected_engine = engine
            break
        except TypeError:
            continue
    if selected_engine is None:
        raise RuntimeError("未找到可用渲染引擎（EEVEE/CYCLES）。")
    log(f"渲染引擎: {selected_engine}")
    # PNG 透明与 film_transparent 搭配；RGBA 由 Blender 在透明底时自动写出 alpha
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_dir / f"{selected_3dm.stem}_{stamp}.png"
    scene.render.filepath = str(out_file)
    exr = Path(assets_dir).joinpath(*ENV_REL_PATH)
    no_hdri = not exr.is_file()
    old_exp = float(scene.view_settings.exposure)
    bg = _world_background_node()
    old_bg_strength: float | None = None
    if no_hdri:
        # 与占位灰世界( apply_studio 保持弱光不变 )配合：仅 PNG 出图( file output ) 拉曝光/环境强度
        scene.view_settings.exposure = old_exp + 0.65
        if bg is not None and "Strength" in bg.inputs:
            old_bg_strength = float(bg.inputs["Strength"].default_value)
            bg.inputs["Strength"].default_value = max(
                2.0, min(5.0, old_bg_strength * 2.2)
            )
        log(
            "静帧文件输出( file output )补偿(仅本次 render): 无 EXR 时"
            f" exposure+0.65"
            + (
                f", World Strength 临时 {bg.inputs['Strength'].default_value:.2f}"
                if old_bg_strength is not None and bg is not None
                else ""
            )
        )
    log(f"开始渲染静帧: {out_file}")
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        scene.view_settings.exposure = old_exp
        if old_bg_strength is not None and bg is not None and "Strength" in bg.inputs:
            bg.inputs["Strength"].default_value = old_bg_strength
    log(f"渲染完成(文件输出( file output )参数已恢复): {out_file}")
    return out_file


def run_pipeline(args: argparse.Namespace) -> int:
    assets_dir = args.assets.resolve()
    cmf_map_path = args.cmf_map.resolve()
    log(f"pipeline 启动，assets={assets_dir}")
    log(f"CMF 配置路径: {cmf_map_path}")

    rules = load_cmf_rules(cmf_map_path)

    pick = getattr(args, "pick_3dm", None)
    skip = getattr(args, "skip_audit", False)

    if pick is not None:
        picked = pick.resolve()
        if not picked.is_file():
            log(f"--pick-3dm 无效（非文件）: {picked}")
            return 1
        if picked.suffix.lower() != ".3dm":
            log(f"--pick-3dm 需指向 .3dm：{picked}")
            return 1
        if skip:
            selected_3dm = picked
            used_phase = None
            log(f"[skip-audit][pick-3dm] 使用指定文件: {selected_3dm}")
        else:
            ph_guess = effective_phase_for_file(picked, args.phase, args.default_phase)
            result = audit_single_3dm_file(picked, ph_guess)
            if not result.ok:
                log(
                    f"指定文件审计不通过: {picked.name}; "
                    f"missing={list(result.missing)} extra={list(result.extra)}"
                )
                return 1
            selected_3dm = picked
            used_phase = ph_guess
            log(f"[pick-3dm] 审计通过: {picked.name}, protocol=Phase{used_phase}")
    elif skip:
        selected_3dm = find_latest_3dm_by_mtime(assets_dir)
        if selected_3dm is None:
            log("[skip-audit] 流水线终止（无可用 .3dm）。")
            return 1
        used_phase = None
        log(f"[skip-audit] 最终选择资产: {selected_3dm.name}")
    else:
        selected_pair = find_latest_audited_3dm(
            assets_dir=assets_dir,
            phase_mode=args.phase,
            default_phase_if_auto_unknown=args.default_phase,
        )
        selected_3dm, used_phase = selected_pair
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
    assign_cmf_materials(rules)
    apply_studio_world_and_transparency(assets_dir)
    if args.no_render:
        log("no-render 已开启：跳过静帧渲染，保留场景供实时检查。")
    else:
        _render_still(
            args.output_dir.resolve(),
            selected_3dm,
            keep_mode=args.keep,
            assets_dir=assets_dir,
        )

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
