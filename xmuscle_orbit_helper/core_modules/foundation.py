import math
import json
import time
from contextlib import contextmanager

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Euler, Matrix, Quaternion, Vector


SUPPORTED_DEFORMATION_MODIFIERS = {
    "ARMATURE",
    "CAST",
    "CORRECTIVE_SMOOTH",
    "CURVE",
    "DISPLACE",
    "HOOK",
    "LAPLACIANDEFORM",
    "LATTICE",
    "MESH_DEFORM",
    "SHRINKWRAP",
    "SIMPLE_DEFORM",
    "SMOOTH",
    "SURFACE_DEFORM",
    "WARP",
    "WAVE",
}


CAPTURE_ITEMS = (
    ("START", "Start", "Capture the current bone pose as the start pose"),
    ("END", "End", "Capture the current bone pose as the end pose"),
)


AXIS_ITEMS = (
    ("X", "X", ""),
    ("Y", "Y", ""),
    ("Z", "Z", ""),
)

AXIS_FLAG_ITEMS = (
    ("X", "X", "", 1),
    ("Y", "Y", "", 2),
    ("Z", "Z", "", 4),
)

COMBINE_MODE_ITEMS = (
    ("SUM", "Sum", "Sum the selected source rotation channels"),
    ("AVERAGE", "Average", "Average the selected source rotation channels"),
)


DRIVER_MODE_ITEMS = (
    ("RAW_DELTA", "Raw Delta", "Use the chosen Euler rotation channel minus the stored zero offset"),
    ("WRAPPED_DELTA", "Wrapped Angle", "Use the shortest signed angle around the stored zero offset to avoid jumps near the working range"),
    ("SINE", "Sine", "Use a smooth sine response of the chosen channel around the stored zero offset"),
    ("COSINE", "Cosine", "Use a smooth cosine response of the chosen channel around the stored zero offset"),
)


DRIVER_SPACE_ITEMS = (
    ("LOCAL_SPACE", "Local", "Read the driver source rotation in local bone space"),
    ("WORLD_SPACE", "World", "Read the driver source rotation in world space"),
)


MUSCLE_SETTINGS_PROP = "_xmoh_settings"
SELECTION_SETTINGS_PROP = "_xmoh_selection_settings"


def iter_linked_muscles(body_obj):
    muscles = []
    if body_obj is None or body_obj.type != "MESH":
        return muscles

    seen = set()
    for modifier in body_obj.modifiers:
        target = getattr(modifier, "target", None)
        if modifier.type != "SHRINKWRAP" or target is None:
            continue
        if not getattr(target, "Muscle_XID", False):
            continue
        if target.name in seen:
            continue
        muscles.append(target)
        seen.add(target.name)
    return muscles


def iter_scene_muscles(scene):
    return [obj for obj in scene.objects if obj.type == "MESH" and getattr(obj, "Muscle_XID", False)]


def get_muscle_controller(muscle_obj):
    if muscle_obj is None or muscle_obj.parent is None or muscle_obj.parent.type != "ARMATURE":
        return None

    expected_name = muscle_obj.parent.name.replace("System", "_ctrl")
    scene = bpy.context.scene

    direct_candidates = []
    for obj in scene.objects:
        if obj.type != "EMPTY":
            continue
        if obj.parent is None or obj.parent.type != "ARMATURE":
            continue
        if not getattr(obj, "parent_bone", ""):
            continue
        if obj.name == expected_name:
            return obj
        direct_candidates.append(obj)

    targeted = []
    for pose_bone in muscle_obj.parent.pose.bones:
        for constraint in pose_bone.constraints:
            target = getattr(constraint, "target", None)
            if target and target.type == "EMPTY":
                if target.name == expected_name:
                    return target
                targeted.append(target)

    muscle_bone_name = getattr(muscle_obj.parent, "parent_bone", "")
    for target in targeted + direct_candidates:
        if target.parent_bone and target.parent_bone == muscle_bone_name:
            return target

    return targeted[0] if targeted else (direct_candidates[0] if direct_candidates else None)


def infer_body_for_muscle(scene, muscle_obj):
    if muscle_obj is None:
        return None
    for obj in scene.objects:
        if obj.type != "MESH" or getattr(obj, "Muscle_XID", False):
            continue
        for modifier in obj.modifiers:
            if modifier.type == "SHRINKWRAP" and getattr(modifier, "target", None) == muscle_obj:
                return obj
    return None


def get_default_body_object(scene):
    if scene is None:
        return None
    body_obj = scene.objects.get("Body")
    if body_obj and body_obj.type == "MESH" and not getattr(body_obj, "Muscle_XID", False):
        return body_obj
    return None


def get_effective_body_object(settings, scene=None):
    if settings is None:
        return None
    body_obj = getattr(settings, "body_object", None)
    if body_obj and body_obj.type == "MESH" and not getattr(body_obj, "Muscle_XID", False):
        return body_obj
    scene = scene or bpy.context.scene
    return get_default_body_object(scene)


def ensure_default_body_object(settings, scene=None):
    if settings is None:
        return None
    body_obj = getattr(settings, "body_object", None)
    if body_obj and body_obj.type == "MESH" and not getattr(body_obj, "Muscle_XID", False):
        return body_obj
    scene = scene or bpy.context.scene
    default_body = get_default_body_object(scene)
    if default_body is not None:
        settings.body_object = default_body
    return default_body


def infer_links_for_muscle(scene, muscle_obj):
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    controller = get_muscle_controller(muscle_obj)
    rig_obj = controller.parent if controller and controller.parent and controller.parent.type == "ARMATURE" else None
    bone_name = controller.parent_bone if controller else ""

    if not rig_obj or not bone_name:
        fallback_rig = muscle_obj.parent if muscle_obj and muscle_obj.parent and muscle_obj.parent.type == "ARMATURE" else None
        fallback_bone = getattr(muscle_obj.parent, "parent_bone", "") if muscle_obj and muscle_obj.parent else ""
        rig_obj = rig_obj or fallback_rig
        bone_name = bone_name or fallback_bone
    return {
        "body_object_name": body_obj.name if body_obj else "",
        "rig_object_name": rig_obj.name if rig_obj else "",
        "bone_name": bone_name,
    }


def get_saved_prefix_for_muscle(muscle_obj, default_prefix):
    if muscle_obj is None:
        return default_prefix
    payload = muscle_obj.get(MUSCLE_SETTINGS_PROP)
    if not payload:
        return default_prefix
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return default_prefix
    return data.get("key_prefix", default_prefix) or default_prefix


def muscle_has_baked_keys(scene, muscle_obj, prefix):
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    if body_obj is None or body_obj.data.shape_keys is None:
        return False
    token = sanitize_key_token(muscle_obj.name)
    expected_prefix = f"{prefix}{token}_"
    return any(key.name.startswith(expected_prefix) for key in body_obj.data.shape_keys.key_blocks if key.name != "Basis")


def muscle_key_prefix(prefix, muscle_name):
    return f"{prefix}{sanitize_key_token(muscle_name)}_"


def preview_action_names(prefix, muscle_obj, body_obj, rig_obj):
    muscle_token = sanitize_key_token(muscle_obj.name)
    body_token = sanitize_key_token(body_obj.name) if body_obj else "Body"
    rig_token = sanitize_key_token(rig_obj.name) if rig_obj else "Rig"
    return (
        f"{prefix}{muscle_token}_{body_token}_ShapePreview",
        f"{prefix}{muscle_token}_{rig_token}_BonePreview",
    )


def get_settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "xmuscle_range_baker"):
        return None
    return scene.xmuscle_range_baker


def get_selected_scene_muscle(settings):
    if not settings or not settings.muscle_name:
        return None
    return bpy.data.objects.get(settings.muscle_name)


def get_muscle_system(muscle_obj):
    if muscle_obj and muscle_obj.parent and muscle_obj.parent.type == "ARMATURE":
        return muscle_obj.parent
    return None


def get_muscle_slide_bone_name(muscle_obj):
    if muscle_obj is None:
        return ""
    return muscle_obj.get("xmuscle_orbit_slide_bone", "")


def sample_bone_rotation_channel(rig_obj, bone_name, axis_name, rotation_space):
    if rig_obj is None or rig_obj.type != "ARMATURE" or bone_name not in rig_obj.pose.bones:
        return 0.0
    pose_bone = rig_obj.pose.bones[bone_name]
    if rotation_space == "WORLD_SPACE":
        matrix = rig_obj.matrix_world @ pose_bone.matrix
        return matrix.to_euler("XYZ")[axis_index(axis_name)]
    return pose_bone.matrix_basis.to_euler("XYZ")[axis_index(axis_name)]


def sample_combined_bone_rotation(rig_obj, bone_name, axes, rotation_space, combine_mode):
    values = [sample_bone_rotation_channel(rig_obj, bone_name, axis, rotation_space) for axis in normalize_axis_flags(axes)]
    return combine_rotation_values(values, combine_mode)


def get_selected_muscle_names(settings):
    if not settings:
        return []
    raw = getattr(settings, "selected_muscles_json", "")
    if raw:
        try:
            names = json.loads(raw)
            if isinstance(names, list):
                return [name for name in names if isinstance(name, str) and bpy.data.objects.get(name)]
        except json.JSONDecodeError:
            pass
    if settings.muscle_name and bpy.data.objects.get(settings.muscle_name):
        return [settings.muscle_name]
    return []


def selection_key_for_names(muscle_names):
    return "||".join(sorted(set(muscle_names)))


def rotation_transform_type(axis_name):
    return {
        "X": "ROT_X",
        "Y": "ROT_Y",
        "Z": "ROT_Z",
    }.get(axis_name, "ROT_X")


def axis_index(axis_name):
    return {
        "X": 0,
        "Y": 1,
        "Z": 2,
    }.get(axis_name, 0)


def normalize_axis_flags(value):
    if isinstance(value, str):
        value = {value}
    elif not value:
        value = set()
    axes = [axis for axis in ("X", "Y", "Z") if axis in value]
    return axes or ["X"]


def encode_axis_flags(axes):
    return set(normalize_axis_flags(axes))


def combined_rotation_expression(var_names, combine_mode):
    active_vars = [name for name in var_names if name]
    if not active_vars:
        return "0.0"
    joined = " + ".join(active_vars)
    if len(active_vars) == 1 or combine_mode != "AVERAGE":
        return joined
    return f"(({joined}) / {len(active_vars)})"


def combine_rotation_values(values, combine_mode):
    if not values:
        return 0.0
    total = sum(values)
    if combine_mode == "AVERAGE":
        return total / len(values)
    return total


def driver_expression_for_mode(mode, rot_expr="rot"):
    if mode == "WRAPPED_DELTA":
        return f"atan2(sin(({rot_expr}) - zero), cos(({rot_expr}) - zero)) * factor"
    if mode == "SINE":
        return f"sin(({rot_expr}) - zero) * factor"
    if mode == "COSINE":
        return f"cos(({rot_expr}) - zero) * factor"
    return f"(({rot_expr}) - zero) * factor"


def unique_bone_name(rig_obj, base_name):
    existing = set(rig_obj.data.bones.keys())
    if base_name not in existing:
        return base_name
    index = 1
    while True:
        candidate = f"{base_name}.{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


