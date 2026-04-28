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


def get_selection_settings_store(scene):
    raw = scene.get(SELECTION_SETTINGS_PROP, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def save_selection_settings_store(scene, data):
    scene[SELECTION_SETTINGS_PROP] = json.dumps(data)


def remove_deleted_muscle_from_selection_store(scene, muscle_name):
    store = get_selection_settings_store(scene)
    cleaned = {}
    for key, value in store.items():
        names = [item for item in key.split("||") if item]
        filtered = [name for name in names if name != muscle_name]
        if not filtered:
            continue
        cleaned[selection_key_for_names(filtered)] = value
    save_selection_settings_store(scene, cleaned)


def serialize_settings(settings):
    return {
        "body_object_name": settings.body_object.name if settings.body_object else "",
        "rig_object_name": settings.rig_object.name if settings.rig_object else "",
        "bone_name": settings.bone_name,
        "start_rotation": list(settings.start_rotation),
        "end_rotation": list(settings.end_rotation),
        "samples": settings.samples,
        "corrective_iterations": settings.corrective_iterations,
        "key_prefix": settings.key_prefix,
        "replace_existing": settings.replace_existing,
        "replace_target_on_rebake": settings.replace_target_on_rebake,
        "disable_subsurf": settings.disable_subsurf,
        "auto_apply_muscle": settings.auto_apply_muscle,
        "auto_disable_unsupported_modifiers": settings.auto_disable_unsupported_modifiers,
        "use_captured_pose": settings.use_captured_pose,
        "has_start_pose": settings.has_start_pose,
        "has_end_pose": settings.has_end_pose,
        "start_quaternion": list(settings.start_quaternion),
        "end_quaternion": list(settings.end_quaternion),
        "preview_enabled": settings.preview_enabled,
        "preview_factor": settings.preview_factor,
        "preview_restore_quaternion": list(settings.preview_restore_quaternion),
        "auto_generate_animation": settings.auto_generate_animation,
        "mute_live_xmuscle": settings.mute_live_xmuscle,
        "animation_start_frame": settings.animation_start_frame,
        "animation_length": settings.animation_length,
    }


def save_selected_muscle_settings(settings):
    muscle_names = get_selected_muscle_names(settings)
    if not muscle_names:
        return
    payload = json.dumps(serialize_settings(settings))
    if len(muscle_names) == 1:
        muscle_obj = bpy.data.objects.get(muscle_names[0])
        if muscle_obj is not None:
            muscle_obj[MUSCLE_SETTINGS_PROP] = payload
    scene = bpy.context.scene
    store = get_selection_settings_store(scene)
    store[selection_key_for_names(muscle_names)] = json.loads(payload)
    save_selection_settings_store(scene, store)


def apply_saved_settings(settings, payload):
    settings.sync_settings_lock = True
    try:
        body_name = payload.get("body_object_name", "")
        rig_name = payload.get("rig_object_name", "")
        settings.body_object = bpy.data.objects.get(body_name) if body_name else None
        settings.rig_object = bpy.data.objects.get(rig_name) if rig_name else None
        settings.bone_name = payload.get("bone_name", "")
        start_rotation = payload.get("start_rotation")
        end_rotation = payload.get("end_rotation")
        if start_rotation is None or end_rotation is None:
            axis = payload.get("rotation_axis", "X")
            axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 0)
            migrated_start = [0.0, 0.0, 0.0]
            migrated_end = [0.0, 0.0, 0.0]
            migrated_start[axis_index] = payload.get("start_angle", 0.0)
            migrated_end[axis_index] = payload.get("end_angle", math.radians(90.0))
            start_rotation = migrated_start
            end_rotation = migrated_end
        settings.start_rotation = start_rotation
        settings.end_rotation = end_rotation
        settings.samples = payload.get("samples", settings.samples)
        settings.corrective_iterations = payload.get("corrective_iterations", settings.corrective_iterations)
        settings.key_prefix = payload.get("key_prefix", settings.key_prefix)
        settings.replace_existing = payload.get("replace_existing", settings.replace_existing)
        settings.replace_target_on_rebake = payload.get("replace_target_on_rebake", settings.replace_target_on_rebake)
        settings.disable_subsurf = payload.get("disable_subsurf", settings.disable_subsurf)
        settings.auto_apply_muscle = payload.get("auto_apply_muscle", settings.auto_apply_muscle)
        settings.auto_disable_unsupported_modifiers = payload.get("auto_disable_unsupported_modifiers", settings.auto_disable_unsupported_modifiers)
        settings.use_captured_pose = payload.get("use_captured_pose", settings.use_captured_pose)
        settings.has_start_pose = payload.get("has_start_pose", settings.has_start_pose)
        settings.has_end_pose = payload.get("has_end_pose", settings.has_end_pose)
        settings.start_quaternion = payload.get("start_quaternion", list(settings.start_quaternion))
        settings.end_quaternion = payload.get("end_quaternion", list(settings.end_quaternion))
        settings.preview_enabled = payload.get("preview_enabled", False)
        settings.preview_factor = payload.get("preview_factor", settings.preview_factor)
        settings.preview_restore_quaternion = payload.get("preview_restore_quaternion", list(settings.preview_restore_quaternion))
        settings.auto_generate_animation = payload.get("auto_generate_animation", settings.auto_generate_animation)
        settings.mute_live_xmuscle = payload.get("mute_live_xmuscle", False)
        settings.animation_start_frame = payload.get("animation_start_frame", settings.animation_start_frame)
        settings.animation_length = payload.get("animation_length", settings.animation_length)
        if settings.body_object is None:
            ensure_default_body_object(settings, bpy.context.scene)
    finally:
        settings.sync_settings_lock = False


def infer_links_for_group(scene, muscle_names):
    muscles = [bpy.data.objects.get(name) for name in muscle_names]
    muscles = [obj for obj in muscles if obj is not None]
    if not muscles:
        return {}

    inferred = [infer_links_for_muscle(scene, muscle) for muscle in muscles]
    body_names = {item.get("body_object_name", "") for item in inferred if item.get("body_object_name", "")}
    rig_names = {item.get("rig_object_name", "") for item in inferred if item.get("rig_object_name", "")}
    bone_names = {item.get("bone_name", "") for item in inferred if item.get("bone_name", "")}
    return {
        "body_object_name": next(iter(body_names)) if len(body_names) == 1 else "",
        "rig_object_name": next(iter(rig_names)) if len(rig_names) == 1 else "",
        "bone_name": next(iter(bone_names)) if len(bone_names) == 1 else "",
    }


def load_settings_for_selection(settings, muscle_names):
    scene = bpy.context.scene
    payload = {}
    group_key = selection_key_for_names(muscle_names)
    store = get_selection_settings_store(scene)
    if group_key in store and isinstance(store[group_key], dict):
        payload = store[group_key]
    elif len(muscle_names) == 1:
        muscle_obj = bpy.data.objects.get(muscle_names[0])
        if muscle_obj is not None and muscle_obj.get(MUSCLE_SETTINGS_PROP):
            try:
                payload = json.loads(muscle_obj[MUSCLE_SETTINGS_PROP])
            except json.JSONDecodeError:
                payload = {}

    inferred = infer_links_for_group(scene, muscle_names)
    if not payload:
        payload = inferred
    else:
        payload.setdefault("body_object_name", inferred.get("body_object_name", ""))
        payload.setdefault("rig_object_name", inferred.get("rig_object_name", ""))
        payload.setdefault("bone_name", inferred.get("bone_name", ""))

    apply_saved_settings(settings, payload)
    primary = bpy.data.objects.get(settings.muscle_name) or (bpy.data.objects.get(muscle_names[0]) if muscle_names else None)
    settings.rename_buffer = primary.name if primary is not None else ""
    save_selected_muscle_settings(settings)


def load_settings_for_muscle(settings, muscle_obj):
    payload = {}
    if muscle_obj is not None and muscle_obj.get(MUSCLE_SETTINGS_PROP):
        try:
            payload = json.loads(muscle_obj[MUSCLE_SETTINGS_PROP])
        except json.JSONDecodeError:
            payload = {}
    if not payload:
        payload = infer_links_for_muscle(bpy.context.scene, muscle_obj)
    else:
        inferred = infer_links_for_muscle(bpy.context.scene, muscle_obj)
        payload.setdefault("body_object_name", inferred.get("body_object_name", ""))
        payload.setdefault("rig_object_name", inferred.get("rig_object_name", ""))
        payload.setdefault("bone_name", inferred.get("bone_name", ""))
    apply_saved_settings(settings, payload)
    settings.rename_buffer = muscle_obj.name if muscle_obj is not None else ""
    save_selected_muscle_settings(settings)


def find_preview_actions(settings, muscle_obj=None):
    muscle_obj = muscle_obj or get_selected_scene_muscle(settings)
    if muscle_obj is None:
        return None, None, None, None
    scene = bpy.context.scene
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    links = infer_links_for_muscle(scene, muscle_obj)
    rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
    shape_action_name, rig_action_name = preview_action_names(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    return bpy.data.actions.get(shape_action_name), bpy.data.actions.get(rig_action_name), body_obj, rig_obj


def ensure_object_mode(context):
    active = context.view_layer.objects.active
    if active and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def apply_muscle_to_body(context, muscle_obj, body_obj):
    if muscle_obj is None or not getattr(muscle_obj, "Muscle_XID", False):
        return False, "Muscle not found"
    if body_obj is None or body_obj.type != "MESH":
        return False, "Choose a valid target mesh first"
    if infer_body_for_muscle(context.scene, muscle_obj) == body_obj:
        return True, f"{muscle_obj.name} is already applied to {body_obj.name}"
    if not hasattr(bpy.ops.muscle, "apply_musculature"):
        return False, "X-MUSCLE apply operator is not available"

    ensure_object_mode(context)
    previous_active, previous_selection = snapshot_selection(context)
    try:
        set_single_object_selection(context, muscle_obj)
        body_obj.select_set(True)
        context.view_layer.objects.active = body_obj
        bpy.ops.muscle.apply_musculature()
    except RuntimeError as exc:
        return False, f"Failed to apply {muscle_obj.name} to {body_obj.name}: {exc}"
    finally:
        restore_selection(context, previous_active, previous_selection)

    if infer_body_for_muscle(context.scene, muscle_obj) != body_obj:
        return False, f"{muscle_obj.name} was created but is still not linked to {body_obj.name}"
    return True, f"Applied {muscle_obj.name} to {body_obj.name}"


def settings_changed(self, _context):
    if getattr(self, "sync_settings_lock", False):
        return
    save_selected_muscle_settings(self)


def set_selected_muscles(settings, muscle_names, active_name=None):
    unique_names = []
    for name in muscle_names:
        if name and name not in unique_names and bpy.data.objects.get(name):
            unique_names.append(name)

    settings.sync_settings_lock = True
    try:
        settings.selected_muscles_json = json.dumps(unique_names)
        if active_name in unique_names:
            settings.muscle_name = active_name
        elif unique_names:
            settings.muscle_name = unique_names[0]
        else:
            settings.muscle_name = ""
    finally:
        settings.sync_settings_lock = False
    load_settings_for_selection(settings, unique_names)


def set_single_object_selection(context, obj):
    view_layer = context.view_layer
    if view_layer is None or obj is None:
        return
    for selected in list(context.selected_objects):
        selected.select_set(False)
    if view_layer.objects.get(obj.name) is None:
        return
    obj.select_set(True)
    view_layer.objects.active = obj


def find_armature_for_autoaim(context):
    obj = context.object
    if obj and obj.type == "ARMATURE":
        return obj
    active = context.view_layer.objects.active
    if active and active.type == "ARMATURE":
        return active
    selected_armatures = [obj for obj in context.selected_objects if obj.type == "ARMATURE"]
    if len(selected_armatures) == 1:
        return selected_armatures[0]
    return None


def get_selected_bone_names_for_autoaim(context, rig_obj):
    active_name = ""
    selected_names = []

    if context.mode == "POSE" and context.pose_object == rig_obj:
        if context.active_pose_bone:
            active_name = context.active_pose_bone.name
        selected_names = [bone.name for bone in context.selected_pose_bones or []]
    elif context.mode == "EDIT_ARMATURE" and context.object == rig_obj:
        active_bone = context.active_bone
        if active_bone:
            active_name = active_bone.name
        selected_names = [bone.name for bone in context.selected_bones or []]
    else:
        active_bone = rig_obj.data.bones.active
        if active_bone:
            active_name = active_bone.name
        selected_names = [bone.name for bone in rig_obj.data.bones if bone.select]

    selected_names = [name for name in selected_names if name in rig_obj.pose.bones]
    if active_name and active_name not in selected_names and active_name in rig_obj.pose.bones:
        selected_names.append(active_name)
    return selected_names, active_name


def prepare_autoaim_pose_selection(context, rig_obj, selected_names, active_name):
    if len(selected_names) < 2 or not active_name:
        return False, "Select exactly two connected bones and make one of them active"
    if active_name not in selected_names:
        return False, "The active bone must be part of the selection"

    ordered = [name for name in selected_names if name != active_name]
    ordered.append(active_name)
    if len(ordered) != 2:
        return False, "Select exactly two bones for Add To Selected Bones"

    ensure_object_mode(context)
    set_single_object_selection(context, rig_obj)
    bpy.ops.object.mode_set(mode="POSE")

    for pose_bone in rig_obj.pose.bones:
        pose_bone.select = False
        if "selection_order" in pose_bone:
            del pose_bone["selection_order"]

    for index, bone_name in enumerate(ordered):
        pose_bone = rig_obj.pose.bones[bone_name]
        pose_bone.select = True
        pose_bone["selection_order"] = index

    rig_obj.data.bones.active = rig_obj.data.bones[active_name]
    return True, ordered


def get_muscle_collection(muscle_obj):
    if muscle_obj is None:
        return None
    for collection in muscle_obj.users_collection:
        if collection.name == muscle_obj.name:
            return collection
    return muscle_obj.users_collection[0] if muscle_obj.users_collection else None


def iter_muscle_elements(muscle_obj):
    collection = get_muscle_collection(muscle_obj)
    if collection is None:
        return [muscle_obj]
    return list(collection.objects)


def set_muscle_visibility_mode(muscle_obj, mode):
    elements = iter_muscle_elements(muscle_obj)
    show_through = mode == "SHOW_THROUGH"
    hidden = mode == "HIDE"
    for obj in elements:
        obj.hide_viewport = hidden
        if hasattr(obj, "show_in_front"):
            obj.show_in_front = show_through and not hidden
    if hasattr(muscle_obj, "Muscle_View3D"):
        muscle_obj.Muscle_View3D = not hidden
    if hasattr(muscle_obj, "Micro_Controller_View3D"):
        muscle_obj.Micro_Controller_View3D = (not hidden) and show_through and getattr(muscle_obj, "Micro_Controller", False)


def set_all_muscles_visibility_mode(scene, mode):
    for muscle_obj in iter_scene_muscles(scene):
        set_muscle_visibility_mode(muscle_obj, mode)


def make_object_active(context, obj):
    set_single_object_selection(context, obj)


def find_muscle_by_name(body_obj, muscle_name):
    for muscle in iter_linked_muscles(body_obj):
        if muscle.name == muscle_name:
            return muscle
    return None


def iter_body_xmuscle_modifiers(body_obj):
    if body_obj is None or body_obj.type != "MESH":
        return []

    result = []
    for modifier in body_obj.modifiers:
        if modifier.type == "SHRINKWRAP" and getattr(getattr(modifier, "target", None), "Muscle_XID", False):
            result.append(modifier)
        elif modifier.name == "XMSL_SkinCorrector" and modifier.type == "CORRECTIVE_SMOOTH":
            result.append(modifier)
    return result


def snapshot_xmuscle_live_state(body_obj):
    state = {
        "body": {},
        "muscles": {},
    }
    if body_obj is not None:
        for attr in ("Skin_Corrector_View3D", "Skin_Corrector_Render"):
            if hasattr(body_obj, attr):
                state["body"][attr] = getattr(body_obj, attr)

    for muscle in iter_linked_muscles(body_obj):
        state["muscles"][muscle.name] = {
            "Muscle_View3D": getattr(muscle, "Muscle_View3D", True),
            "Muscle_Render": getattr(muscle, "Muscle_Render", True),
            "Micro_Controller_View3D": getattr(muscle, "Micro_Controller_View3D", False),
            "Micro_Controller_Render": getattr(muscle, "Micro_Controller_Render", False),
        }
    return state


def restore_xmuscle_live_state(body_obj, state):
    if body_obj is not None:
        for attr, value in state.get("body", {}).items():
            if hasattr(body_obj, attr):
                setattr(body_obj, attr, value)

    for muscle in iter_linked_muscles(body_obj):
        muscle_state = state.get("muscles", {}).get(muscle.name)
        if not muscle_state:
            continue
        for attr, value in muscle_state.items():
            if hasattr(muscle, attr):
                setattr(muscle, attr, value)


def set_xmuscle_live_state(body_obj, enabled, solo_muscle=None):
    if body_obj is not None:
        for attr in ("Skin_Corrector_View3D", "Skin_Corrector_Render"):
            if hasattr(body_obj, attr):
                setattr(body_obj, attr, enabled)

    for muscle in iter_linked_muscles(body_obj):
        is_selected = solo_muscle is not None and muscle.name == solo_muscle.name
        active = enabled and (solo_muscle is None or is_selected)
        if hasattr(muscle, "Muscle_View3D"):
            muscle.Muscle_View3D = active
        if hasattr(muscle, "Muscle_Render"):
            muscle.Muscle_Render = active
        if hasattr(muscle, "Micro_Controller_View3D"):
            muscle.Micro_Controller_View3D = active and getattr(muscle, "Micro_Controller", False)
        if hasattr(muscle, "Micro_Controller_Render"):
            muscle.Micro_Controller_Render = active and getattr(muscle, "Micro_Controller", False)


def set_linked_muscles_enabled(body_obj, enabled, solo_muscle=None):
    for muscle in iter_linked_muscles(body_obj):
        is_selected = solo_muscle is not None and muscle.name == solo_muscle.name
        active = enabled and (solo_muscle is None or is_selected)
        if hasattr(muscle, "Muscle_View3D"):
            muscle.Muscle_View3D = active
        if hasattr(muscle, "Muscle_Render"):
            muscle.Muscle_Render = active
        if hasattr(muscle, "Micro_Controller_View3D"):
            muscle.Micro_Controller_View3D = active and getattr(muscle, "Micro_Controller", False)
        if hasattr(muscle, "Micro_Controller_Render"):
            muscle.Micro_Controller_Render = active and getattr(muscle, "Micro_Controller", False)


def get_driver_rig_from_muscle(muscle_obj):
    if muscle_obj is None or muscle_obj.parent is None or muscle_obj.parent.type != "ARMATURE":
        return None

    controller = None
    for pose_bone in muscle_obj.parent.pose.bones:
        for constraint in pose_bone.constraints:
            target = getattr(constraint, "target", None)
            if target and target.type == "EMPTY":
                controller = target
                break
        if controller:
            break

    if controller and controller.parent and controller.parent.type == "ARMATURE":
        return controller.parent
    return None


def sanitize_key_token(text):
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text).strip("_") or "Muscle"


def build_key_name(prefix, muscle_name, index, total):
    digits = max(2, len(str(total)))
    token = sanitize_key_token(muscle_name)
    return f"{prefix}{token}_{index:0{digits}d}"


def format_duration_brief(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def estimate_bake_seconds(body_obj, samples, corrective_iterations, muscle_count=1):
    if body_obj is None or body_obj.type != "MESH":
        return 0.0
    vertex_count = len(body_obj.data.vertices)
    polygon_count = len(body_obj.data.polygons)
    base = 2.0
    per_iteration = (vertex_count * 0.00002) + (polygon_count * 0.000025)
    return base + (samples * corrective_iterations * max(1, muscle_count) * per_iteration)


def describe_bake_estimate(settings):
    body_obj = settings.body_object
    if body_obj is None or body_obj.type != "MESH":
        return "Estimate unavailable", ""
    vertex_count = len(body_obj.data.vertices)
    polygon_count = len(body_obj.data.polygons)
    muscle_count = max(1, len(get_selected_muscle_names(settings)))
    estimate_seconds = estimate_bake_seconds(body_obj, settings.samples, settings.corrective_iterations, muscle_count)
    primary = f"Estimated bake: about {format_duration_brief(estimate_seconds)}"
    secondary = f"{muscle_count} muscle(s) x {settings.samples} samples x {settings.corrective_iterations} iterations on {vertex_count:,} verts / {polygon_count:,} polys"
    return primary, secondary


def remove_existing_shape_keys(context, body_obj, prefix):
    if not body_obj.data.shape_keys:
        return

    key_blocks = list(body_obj.data.shape_keys.key_blocks)
    keys_to_remove = [key for key in key_blocks if key.name != "Basis" and key.name.startswith(prefix)]
    if not keys_to_remove:
        return

    ensure_object_mode(context)
    make_object_active(context, body_obj)
    for key in keys_to_remove:
        body_obj.active_shape_key_index = body_obj.data.shape_keys.key_blocks.find(key.name)
        bpy.ops.object.shape_key_remove(all=False)


def remove_shape_keys_for_muscle(context, body_obj, prefix, muscle_name):
    if not body_obj.data.shape_keys:
        return

    token_prefix = muscle_key_prefix(prefix, muscle_name)
    key_blocks = list(body_obj.data.shape_keys.key_blocks)
    keys_to_remove = [key for key in key_blocks if key.name != "Basis" and key.name.startswith(token_prefix)]
    if not keys_to_remove:
        return

    ensure_object_mode(context)
    make_object_active(context, body_obj)
    for key in keys_to_remove:
        body_obj.active_shape_key_index = body_obj.data.shape_keys.key_blocks.find(key.name)
        bpy.ops.object.shape_key_remove(all=False)


def remove_preview_actions(prefix, muscle_obj, body_obj, rig_obj):
    shape_name, rig_name = preview_action_names(prefix, muscle_obj, body_obj, rig_obj)
    if body_obj and body_obj.data.shape_keys and body_obj.data.shape_keys.animation_data:
        if body_obj.data.shape_keys.animation_data.action and body_obj.data.shape_keys.animation_data.action.name == shape_name:
            body_obj.data.shape_keys.animation_data.action = None
    if rig_obj and rig_obj.animation_data:
        if rig_obj.animation_data.action and rig_obj.animation_data.action.name == rig_name:
            rig_obj.animation_data.action = None
    for action_name in (shape_name, rig_name):
        action = bpy.data.actions.get(action_name)
        if action and action.users == 0:
            bpy.data.actions.remove(action)


def remove_body_links_for_muscle(body_obj, muscle_obj):
    if body_obj is None or muscle_obj is None:
        return
    to_remove = []
    for modifier in body_obj.modifiers:
        target = getattr(modifier, "target", None)
        if modifier.type == "SHRINKWRAP" and target == muscle_obj:
            to_remove.append(modifier)
    for modifier in to_remove:
        body_obj.modifiers.remove(modifier)


def delete_muscle_system(context, muscle_obj, key_prefix):
    if muscle_obj is None:
        return False, "Muscle not found"

    scene = context.scene
    muscle_name = muscle_obj.name
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    links = infer_links_for_muscle(scene, muscle_obj)
    rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
    collection = get_muscle_collection(muscle_obj)
    elements = list(iter_muscle_elements(muscle_obj))

    ensure_object_mode(context)
    if body_obj is not None:
        remove_shape_keys_for_muscle(context, body_obj, key_prefix, muscle_obj.name)
        remove_preview_actions(key_prefix, muscle_obj, body_obj, rig_obj)
        remove_body_links_for_muscle(body_obj, muscle_obj)

    for obj in elements:
        if bpy.data.objects.get(obj.name) is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    if collection is not None and bpy.data.collections.get(collection.name) is not None:
        bpy.data.collections.remove(collection)

    return True, f"Deleted {muscle_name}"


def evaluate_body_vertices(context, body_obj):
    depsgraph = context.evaluated_depsgraph_get()
    context.view_layer.update()
    evaluated = body_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        coords = [vertex.co.copy() for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    return coords


def snapshot_body_modifiers(body_obj):
    return {
        modifier.name: {
            "show_viewport": modifier.show_viewport,
            "show_render": modifier.show_render,
        }
        for modifier in body_obj.modifiers
    }


def restore_body_modifiers(body_obj, state):
    for modifier in body_obj.modifiers:
        if modifier.name in state:
            modifier.show_viewport = state[modifier.name]["show_viewport"]
            modifier.show_render = state[modifier.name]["show_render"]


def snapshot_body_xmuscle_driver_mute_state(body_obj):
    state = {}
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return state

    modifier_names = {modifier.name for modifier in iter_body_xmuscle_modifiers(body_obj)}
    for fcurve in animation_data.drivers:
        if not any(fcurve.data_path.startswith(f'modifiers["{name}"]') for name in modifier_names):
            continue
        if not (fcurve.data_path.endswith("show_viewport") or fcurve.data_path.endswith("show_render")):
            continue
        state[fcurve.data_path] = fcurve.mute
    return state


def set_body_xmuscle_driver_mute_state(body_obj, mute):
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return

    modifier_names = {modifier.name for modifier in iter_body_xmuscle_modifiers(body_obj)}
    for fcurve in animation_data.drivers:
        if not any(fcurve.data_path.startswith(f'modifiers["{name}"]') for name in modifier_names):
            continue
        if not (fcurve.data_path.endswith("show_viewport") or fcurve.data_path.endswith("show_render")):
            continue
        fcurve.mute = mute


def restore_body_xmuscle_driver_mute_state(body_obj, state):
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return

    for fcurve in animation_data.drivers:
        if fcurve.data_path in state:
            fcurve.mute = state[fcurve.data_path]


def disable_unsupported_modifiers(body_obj, disable_subsurf):
    disabled = []
    for modifier in body_obj.modifiers:
        if not modifier.show_viewport:
            continue
        if modifier.type == "SUBSURF" and disable_subsurf:
            modifier.show_viewport = False
            modifier.show_render = False
            disabled.append(modifier.name)
            continue
        if modifier.type not in SUPPORTED_DEFORMATION_MODIFIERS:
            modifier.show_viewport = False
            modifier.show_render = False
            disabled.append(modifier.name)
    return disabled


def snapshot_xmuscle_body_modifiers(body_obj):
    return {
        modifier.name: {
            "show_viewport": modifier.show_viewport,
            "show_render": modifier.show_render,
        }
        for modifier in iter_body_xmuscle_modifiers(body_obj)
    }


def restore_xmuscle_body_modifiers(body_obj, state):
    for modifier in iter_body_xmuscle_modifiers(body_obj):
        if modifier.name in state:
            modifier.show_viewport = state[modifier.name]["show_viewport"]
            modifier.show_render = state[modifier.name]["show_render"]


def set_body_xmuscle_state(body_obj, enabled, solo_muscle=None):
    for modifier in iter_body_xmuscle_modifiers(body_obj):
        is_selected_muscle = getattr(getattr(modifier, "target", None), "name", None) == getattr(solo_muscle, "name", None)
        if modifier.type == "SHRINKWRAP":
            active = enabled and (solo_muscle is None or is_selected_muscle)
        else:
            active = enabled
        modifier.show_viewport = active
        modifier.show_render = active


def snapshot_selection(context):
    active = context.view_layer.objects.active
    selected = [obj for obj in context.selected_objects]
    return active, selected


def restore_selection(context, active, selected):
    for item in list(context.selected_objects):
        item.select_set(False)
    for obj in selected:
        if obj.name in bpy.data.objects:
            bpy.data.objects[obj.name].select_set(True)
    if active and active.name in bpy.data.objects:
        context.view_layer.objects.active = bpy.data.objects[active.name]


def snapshot_muscle_display_state(muscles):
    state = {}
    for muscle in muscles:
        state[muscle.name] = {
            "muscle_view3d": getattr(muscle, "Muscle_View3D", True),
            "micro_view3d": getattr(muscle, "Micro_Controller_View3D", False),
            "hide_viewport": muscle.hide_viewport,
        }
    return state


def restore_muscle_display_state(muscles, state):
    for muscle in muscles:
        values = state.get(muscle.name)
        if not values:
            continue
        muscle.Muscle_View3D = values["muscle_view3d"]
        muscle.Micro_Controller_View3D = values["micro_view3d"]
        muscle.hide_viewport = values["hide_viewport"]


def isolate_single_muscle(muscles, active_muscle):
    for muscle in muscles:
        is_active = muscle == active_muscle
        muscle.Muscle_View3D = is_active
        muscle.hide_viewport = not is_active
        if hasattr(muscle, "Micro_Controller_View3D"):
            muscle.Micro_Controller_View3D = is_active and getattr(muscle, "Micro_Controller", False)


def pose_bone_quaternion(pose_bone):
    if pose_bone.rotation_mode == "QUATERNION":
        quat = pose_bone.rotation_quaternion.copy()
    elif pose_bone.rotation_mode == "AXIS_ANGLE":
        axis_angle = pose_bone.rotation_axis_angle
        quat = Quaternion(axis_angle[1:4], axis_angle[0])
    else:
        quat = pose_bone.rotation_euler.to_quaternion()
    quat.normalize()
    return quat


def apply_quaternion_to_pose_bone(pose_bone, quat):
    if pose_bone.rotation_mode == "QUATERNION":
        pose_bone.rotation_quaternion = quat
    elif pose_bone.rotation_mode == "AXIS_ANGLE":
        axis, angle = quat.to_axis_angle()
        pose_bone.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pose_bone.rotation_euler = quat.to_euler(pose_bone.rotation_mode)


def sampled_quaternions(start_quat, end_quat, samples):
    if samples <= 1:
        return [end_quat.copy()]
    result = []
    for index in range(samples):
        factor = index / (samples - 1)
        result.append(start_quat.slerp(end_quat, factor))
    return result


def sampled_vectors(start_vector, end_vector, samples):
    if samples <= 1:
        return [Vector(end_vector)]
    start_vec = Vector(start_vector)
    end_vec = Vector(end_vector)
    result = []
    for index in range(samples):
        factor = index / (samples - 1)
        result.append(start_vec.lerp(end_vec, factor))
    return result


def get_preview_pose_bone(settings):
    rig_obj = settings.rig_object
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return None
    if not settings.bone_name or settings.bone_name not in rig_obj.pose.bones:
        return None
    return rig_obj.pose.bones[settings.bone_name]


def apply_preview(settings, context=None):
    if settings.preview_update_lock:
        return
    pose_bone = get_preview_pose_bone(settings)
    if pose_bone is None:
        return

    if settings.preview_enabled:
        if settings.use_captured_pose and settings.has_start_pose and settings.has_end_pose:
            start_quat = Quaternion(settings.start_quaternion)
            end_quat = Quaternion(settings.end_quaternion)
            quat = start_quat.slerp(end_quat, settings.preview_factor)
        else:
            start_vec = Vector(settings.start_rotation)
            end_vec = Vector(settings.end_rotation)
            euler = start_vec.lerp(end_vec, settings.preview_factor)
            quat = Euler(tuple(euler), "XYZ").to_quaternion()
        apply_quaternion_to_pose_bone(pose_bone, quat)
    else:
        quat = Quaternion(settings.preview_restore_quaternion)
        apply_quaternion_to_pose_bone(pose_bone, quat)

    context = context or bpy.context
    if context and context.view_layer:
        context.view_layer.update()


def preview_update(self, context):
    apply_preview(self, context)
    settings_changed(self, context)


def mute_xmuscle_update(self, context):
    body_obj = self.body_object
    if body_obj is None or self.mute_update_lock:
        return

    if self.mute_live_xmuscle:
        payload = {
            "live_state": snapshot_xmuscle_live_state(body_obj),
            "modifier_state": snapshot_xmuscle_body_modifiers(body_obj),
            "driver_mute_state": snapshot_body_xmuscle_driver_mute_state(body_obj),
        }
        self.saved_xmuscle_modifier_state = json.dumps(payload)
        set_body_xmuscle_driver_mute_state(body_obj, mute=True)
        set_xmuscle_live_state(body_obj, enabled=False)
        set_body_xmuscle_state(body_obj, enabled=False)
    else:
        if self.saved_xmuscle_modifier_state:
            try:
                payload = json.loads(self.saved_xmuscle_modifier_state)
            except json.JSONDecodeError:
                payload = {}
            restore_xmuscle_live_state(body_obj, payload.get("live_state", {}))
            restore_xmuscle_body_modifiers(body_obj, payload.get("modifier_state", {}))
            restore_body_xmuscle_driver_mute_state(body_obj, payload.get("driver_mute_state", {}))
    if context and context.view_layer:
        context.view_layer.update()
    settings_changed(self, context)


@contextmanager
def preserved_pose_bone_rotation(pose_bone):
    original_mode = pose_bone.rotation_mode
    original_euler = pose_bone.rotation_euler.copy()
    original_quaternion = pose_bone.rotation_quaternion.copy()
    original_axis_angle = tuple(pose_bone.rotation_axis_angle)
    try:
        yield
    finally:
        pose_bone.rotation_mode = original_mode
        pose_bone.rotation_euler = original_euler
        pose_bone.rotation_quaternion = original_quaternion
        pose_bone.rotation_axis_angle = original_axis_angle


def ensure_body_shape_keys(context, body_obj):
    if body_obj.data.shape_keys is None:
        make_object_active(context, body_obj)
        body_obj.shape_key_add(name="Basis", from_mix=False)


def clear_keyframe_values(key_blocks, frame):
    for key_block in key_blocks:
        key_block.value = 0.0
        key_block.keyframe_insert(data_path="value", frame=frame)


def snapshot_shape_key_values(body_obj):
    if body_obj.data.shape_keys is None:
        return {}
    return {key_block.name: key_block.value for key_block in body_obj.data.shape_keys.key_blocks}


def restore_shape_key_values(body_obj, values):
    if body_obj.data.shape_keys is None:
        return
    for key_block in body_obj.data.shape_keys.key_blocks:
        if key_block.name in values:
            key_block.value = values[key_block.name]


def zero_all_shape_keys(body_obj):
    if body_obj.data.shape_keys is None:
        return
    for key_block in body_obj.data.shape_keys.key_blocks:
        if key_block.name != "Basis":
            key_block.value = 0.0
    body_obj.active_shape_key_index = 0
    body_obj.show_only_shape_key = False


def update_mesh_state(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    ob.update_tag()
    bpy.context.view_layer.update()
    ob.data.update()


def corrective_reset_transform(ob):
    ob.matrix_local.identity()


def corrective_extract_vert_coords(verts):
    return [vertex.co.copy() for vertex in verts]


def corrective_extract_mapped_coords(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eobj = ob.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eobj)
    try:
        arr = [vertex.co.copy() for vertex in mesh.vertices]
    finally:
        mesh.user_clear()
        bpy.data.meshes.remove(mesh)
    update_mesh_state(ob)
    return arr


def corrective_apply_vert_coords(ob, mesh, coords):
    for index, vertex in enumerate(mesh):
        vertex.co = coords[index]
    update_mesh_state(ob)


def duplicate_flatten_modifiers(context, ob, name):
    depsgraph = context.evaluated_depsgraph_get()
    eobj = ob.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eobj)
    new_object = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(new_object)
    return new_object


def add_corrective_pose_shape(source, target, iterations=12, progress_callback=None):
    threshold = 1e-16

    mesh_target = target.data
    mesh_source = source.data

    original_matrix_local = target.matrix_local.copy()
    corrective_reset_transform(target)

    if not mesh_target.shape_keys:
        basis = target.shape_key_add()
        basis.name = "Basis"
        update_mesh_state(target)
        target.active_shape_key_index = 0

    target.show_only_shape_key = False
    target.active_shape_key_index = 0

    new_shapekey = target.shape_key_add()
    update_mesh_state(target)
    target.active_shape_key_index = target.data.shape_keys.key_blocks.find(new_shapekey.name)
    target.show_only_shape_key = True

    vertex_group = target.active_shape_key.vertex_group
    target.active_shape_key.vertex_group = ""
    key_verts = target.active_shape_key.data

    x = corrective_extract_vert_coords(key_verts)
    target_coords = corrective_extract_vert_coords(mesh_source.vertices)

    for iteration_index in range(iterations):
        dx = [[], [], [], [], [], []]
        mapped = corrective_extract_mapped_coords(target)

        for index in range(len(mesh_target.vertices)):
            epsilon = (target_coords[index] - mapped[index]).length
            if epsilon < threshold:
                epsilon = 0.0

            dx[0].append(x[index] + 0.5 * epsilon * Vector((1, 0, 0)))
            dx[1].append(x[index] + 0.5 * epsilon * Vector((-1, 0, 0)))
            dx[2].append(x[index] + 0.5 * epsilon * Vector((0, 1, 0)))
            dx[3].append(x[index] + 0.5 * epsilon * Vector((0, -1, 0)))
            dx[4].append(x[index] + 0.5 * epsilon * Vector((0, 0, 1)))
            dx[5].append(x[index] + 0.5 * epsilon * Vector((0, 0, -1)))

        for axis in range(6):
            corrective_apply_vert_coords(target, key_verts, dx[axis])
            dx[axis] = corrective_extract_mapped_coords(target)

        for index in range(len(mesh_target.vertices)):
            epsilon = (target_coords[index] - mapped[index]).length
            if epsilon < threshold:
                continue
            gx = list((dx[0][index] - dx[1][index]) / epsilon)
            gy = list((dx[2][index] - dx[3][index]) / epsilon)
            gz = list((dx[4][index] - dx[5][index]) / epsilon)
            gradient = Matrix((gx, gy, gz))
            delta = target_coords[index] - mapped[index]
            x[index] += gradient @ delta

        corrective_apply_vert_coords(target, key_verts, x)
        if progress_callback is not None:
            progress_callback(iteration_index + 1, iterations)

    target.active_shape_key.vertex_group = vertex_group
    target.active_shape_key.value = 1.0
    target.show_only_shape_key = False
    update_mesh_state(target)
    target.matrix_local = original_matrix_local
    return target.active_shape_key


def remove_temporary_object(obj):
    if obj is None:
        return
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def mute_existing_nla_tracks(animation_data):
    if animation_data is None:
        return
    for track in animation_data.nla_tracks:
        track.mute = True


def generate_preview_animation(settings, muscle_obj, body_obj, rig_obj, pose_bone, sampled_rots, key_names):
    scene = bpy.context.scene
    start_frame = settings.animation_start_frame
    end_frame = start_frame + max(1, settings.animation_length)
    total_keys = max(1, len(key_names))

    sample_frames = []
    for index in range(total_keys):
        if total_keys == 1:
            frame = end_frame
        else:
            frame = round(start_frame + (end_frame - start_frame) * (index / (total_keys - 1)))
        sample_frames.append(frame)

    shape_keys = body_obj.data.shape_keys
    if shape_keys.animation_data is None:
        shape_keys.animation_data_create()
    mute_existing_nla_tracks(shape_keys.animation_data)
    remove_preview_actions(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    shape_action_name, rig_action_name = preview_action_names(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    shape_action = bpy.data.actions.new(name=shape_action_name)
    shape_keys.animation_data.action = shape_action

    relevant_keys = [shape_keys.key_blocks[name] for name in key_names if name in shape_keys.key_blocks]
    for frame in sample_frames:
        clear_keyframe_values(relevant_keys, frame)

    for frame, key_block in zip(sample_frames, relevant_keys):
        key_block.value = 1.0
        key_block.keyframe_insert(data_path="value", frame=frame)

    if rig_obj.animation_data is None:
        rig_obj.animation_data_create()
    mute_existing_nla_tracks(rig_obj.animation_data)
    rig_action = bpy.data.actions.new(name=rig_action_name)
    rig_obj.animation_data.action = rig_action

    for frame, quat in zip(sample_frames, sampled_rots):
        apply_quaternion_to_pose_bone(pose_bone, quat)
        if pose_bone.rotation_mode == "QUATERNION":
            pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        elif pose_bone.rotation_mode == "AXIS_ANGLE":
            pose_bone.keyframe_insert(data_path="rotation_axis_angle", frame=frame)
        else:
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)

    scene.frame_start = min(scene.frame_start, start_frame)
    scene.frame_end = max(scene.frame_end, end_frame)
    scene.frame_set(start_frame)


class XMRB_Settings(bpy.types.PropertyGroup):
    sync_settings_lock: BoolProperty(default=False)
    selected_muscles_json: StringProperty(default="[]")
    body_object: PointerProperty(
        name="Body",
        type=bpy.types.Object,
        description="Target body mesh that already receives X-Muscle shrinkwrap deformation",
        update=settings_changed,
        poll=lambda _self, obj: obj and obj.type == "MESH" and not getattr(obj, "Muscle_XID", False),
    )
    rig_object: PointerProperty(
        name="Rig",
        type=bpy.types.Object,
        description="Armature that drives the pose for the muscle motion",
        update=settings_changed,
        poll=lambda _self, obj: obj and obj.type == "ARMATURE",
    )
    muscle_name: StringProperty(
        name="Muscle",
        description="Currently selected X-Muscle to bake",
        update=settings_changed,
    )
    bone_name: StringProperty(
        name="Bone",
        description="Pose bone to animate and sample while baking",
        update=settings_changed,
    )
    start_rotation: FloatVectorProperty(
        name="Start Rotation",
        description="Fallback start rotation in XYZ Euler angles, used only when no captured start/end poses are stored",
        size=3,
        subtype="EULER",
        default=(0.0, 0.0, 0.0),
        update=preview_update,
    )
    end_rotation: FloatVectorProperty(
        name="End Rotation",
        description="Fallback end rotation in XYZ Euler angles, used only when no captured start/end poses are stored",
        size=3,
        subtype="EULER",
        default=(math.radians(90.0), 0.0, 0.0),
        update=preview_update,
    )
    samples: IntProperty(
        name="Samples",
        description="How many shape keys to create between start and end, inclusive",
        default=5,
        min=2,
        max=128,
        update=settings_changed,
    )
    corrective_iterations: IntProperty(
        name="Solver Iterations",
        description="Corrective shape solver iterations per sample. Higher values improve fidelity but can increase bake time dramatically",
        default=12,
        min=1,
        max=20,
        update=settings_changed,
    )
    key_prefix: StringProperty(
        name="Prefix",
        description="Prefix used for all generated shape keys and preview actions",
        default="XMSL_BAKE_",
        update=settings_changed,
    )
    replace_existing: BoolProperty(
        name="Replace Existing",
        description="Remove previously generated shape keys that share the same prefix before baking new ones",
        default=False,
        update=settings_changed,
    )
    replace_target_on_rebake: BoolProperty(
        name="Replace For Target Muscle On Rebake",
        description="Before baking, remove only the previously generated shape keys and preview actions for the selected muscle",
        default=True,
        update=settings_changed,
    )
    disable_subsurf: BoolProperty(
        name="Disable Subsurf",
        description="Temporarily disable viewport subdivision modifiers while baking, then restore them automatically",
        default=True,
        update=settings_changed,
    )
    auto_apply_muscle: BoolProperty(
        name="Auto-Apply Muscle",
        description="If the chosen muscle is not yet linked to the body, call X-Muscle's Apply Muscles to Body automatically",
        default=True,
        update=settings_changed,
    )
    auto_disable_unsupported_modifiers: BoolProperty(
        name="Auto-Disable Unsupported Modifiers",
        description="Temporarily disable body modifiers that can change topology or break shape key transfer, then restore them after the bake",
        default=True,
        update=settings_changed,
    )
    use_captured_pose: BoolProperty(
        name="Use Captured Poses",
        description="Use the exact current bone rotations captured as start and end poses instead of manual angle input",
        default=True,
        update=preview_update,
    )
    has_start_pose: BoolProperty(default=False, update=settings_changed)
    has_end_pose: BoolProperty(default=False, update=settings_changed)
    start_quaternion: FloatVectorProperty(
        name="Start Quaternion",
        description="Stored start pose rotation in quaternion form",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    end_quaternion: FloatVectorProperty(
        name="End Quaternion",
        description="Stored end pose rotation in quaternion form",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    preview_enabled: BoolProperty(
        name="Live Preview",
        description="Drive the selected bone in the viewport using the preview slider between captured start and end poses",
        default=False,
        update=preview_update,
    )
    preview_factor: FloatProperty(
        name="Preview",
        description="Viewport preview position between the start pose and the end pose",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=preview_update,
    )
    preview_restore_quaternion: FloatVectorProperty(
        name="Restore Quaternion",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    preview_update_lock: BoolProperty(default=False)
    auto_generate_animation: BoolProperty(
        name="Auto-Generate Preview Animation",
        description="Create a simple preview action for the bone and generated shape keys after the bake finishes",
        default=True,
        update=settings_changed,
    )
    mute_live_xmuscle: BoolProperty(
        name="Mute Live X-Muscle On Body",
        description="Temporarily disable the body's X-Muscle shrinkwrap and skin-corrector modifiers so you can inspect only the baked shape keys",
        default=False,
        update=mute_xmuscle_update,
    )
    saved_xmuscle_modifier_state: StringProperty(default="")
    mute_update_lock: BoolProperty(default=False)
    animation_start_frame: IntProperty(
        name="Anim Start",
        description="First frame of the generated preview animation",
        default=1,
        min=1,
        update=settings_changed,
    )
    animation_length: IntProperty(
        name="Anim Length",
        description="Duration in frames of the generated preview animation from start pose to end pose",
        default=24,
        min=1,
        update=settings_changed,
    )
    show_advanced_options: BoolProperty(
        name="Enable Advanced Options",
        description="Show destructive or lower-level bake options",
        default=False,
    )
    rename_buffer: StringProperty(
        name="Rename",
        description="Temporary field used to rename the selected muscle and its related baked outputs",
        default="",
    )


class XMRB_OT_guess_rig(bpy.types.Operator):
    bl_idname = "xmuscle_baker.guess_rig"
    bl_label = "Guess Rig"
    bl_description = "Infer the driving rig from the selected X-Muscle"

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle = find_muscle_by_name(settings.body_object, settings.muscle_name)
        rig = get_driver_rig_from_muscle(muscle)
        if rig is None:
            self.report({"WARNING"}, "Could not infer a driving rig from the selected muscle")
            return {"CANCELLED"}
        settings.rig_object = rig
        return {"FINISHED"}


class XMRB_OT_add_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.add_muscle"
    bl_label = "Add Muscle"
    bl_description = "Create an X-Muscle. When two target bones are selected, it automatically uses Auto Aim with the required setup"
    bl_options = {"REGISTER", "UNDO"}

    muscle_type: EnumProperty(
        items=(
            ("BASIC", "Normal", ""),
            ("STYLIZED", "Curved", ""),
            ("STRIP", "Flat", ""),
        )
    )

    def execute(self, context):
        rig_obj = find_armature_for_autoaim(context)
        selected_names = []
        active_name = ""
        use_autoaim = False
        operator_map = {
            "BASIC": bpy.ops.muscle.add_basic_muscle,
            "STYLIZED": bpy.ops.muscle.add_muscle,
            "STRIP": bpy.ops.muscle.add_strip_muscle,
        }

        if rig_obj is not None:
            selected_names, active_name = get_selected_bone_names_for_autoaim(context, rig_obj)
            use_autoaim = len(selected_names) == 2 and bool(active_name)
            if use_autoaim:
                ok, result = prepare_autoaim_pose_selection(context, rig_obj, selected_names, active_name)
                if not ok:
                    self.report({"ERROR"}, result)
                    return {"CANCELLED"}
        else:
            ensure_object_mode(context)

        scene = context.scene
        previous_create_type = getattr(scene, "Create_Type", "MANUAL")
        previous_name = getattr(scene, "Muscle_Name", "Muscle")
        previous_active, previous_selection = snapshot_selection(context)
        before_names = {obj.name for obj in iter_scene_muscles(scene)}

        try:
            scene.Create_Type = "AUTOAIM" if use_autoaim else "MANUAL"
            if not getattr(scene, "Muscle_Name", "").strip():
                scene.Muscle_Name = "Muscle"
            operator_map[self.muscle_type]()
        except RuntimeError as exc:
            self.report({"ERROR"}, f"X-Muscle creation failed: {exc}")
            return {"CANCELLED"}
        finally:
            scene.Create_Type = previous_create_type

        after_muscles = iter_scene_muscles(scene)
        created = [obj for obj in after_muscles if obj.name not in before_names]
        if created:
            created_muscle = created[-1]
            settings = context.scene.xmuscle_range_baker
            ensure_default_body_object(settings, scene)
            set_selected_muscles(settings, [created_muscle.name], active_name=created_muscle.name)
            set_single_object_selection(context, created_muscle)
            creation_mode = "Auto Aim" if use_autoaim else "normal"
            target_body = settings.body_object
            if target_body is not None:
                ok, message = apply_muscle_to_body(context, created_muscle, target_body)
                if not ok:
                    self.report({"WARNING"}, f"Created {created_muscle.name} ({creation_mode}), but {message}")
                    return {"FINISHED"}
                self.report({"INFO"}, f"Created {created_muscle.name} ({creation_mode}) and applied it to {target_body.name}")
            else:
                self.report({"WARNING"}, f"Created {created_muscle.name} ({creation_mode}), but no target Body mesh is set")
        else:
            restore_selection(context, previous_active, previous_selection)
            scene.Muscle_Name = previous_name
            self.report({"WARNING"}, "No new muscle was detected after creation")
        return {"FINISHED"}


class XMRB_OT_select_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.select_muscle"
    bl_label = "Select Muscle"
    bl_description = "Select this muscle and load its saved bake settings"

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        set_selected_muscles(settings, [muscle_obj.name], active_name=muscle_obj.name)
        set_single_object_selection(context, muscle_obj)
        return {"FINISHED"}


class XMRB_OT_toggle_muscle_selection(bpy.types.Operator):
    bl_idname = "xmuscle_baker.toggle_muscle_selection"
    bl_label = "Toggle Muscle Selection"
    bl_description = "Add or remove this muscle from the current bake selection group"

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        selected = get_selected_muscle_names(settings)
        if muscle_obj.name in selected:
            selected = [name for name in selected if name != muscle_obj.name]
            active_name = selected[0] if selected else ""
        else:
            selected.append(muscle_obj.name)
            active_name = muscle_obj.name
        set_selected_muscles(settings, selected, active_name=active_name)
        return {"FINISHED"}


class XMRB_OT_select_muscle_elements(bpy.types.Operator):
    bl_idname = "xmuscle_baker.select_muscle_elements"
    bl_label = "Select Muscle Elements"
    bl_description = "Select all objects that belong to this muscle system so they can be moved together"

    muscle_name: StringProperty()

    def execute(self, context):
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        for selected in list(context.selected_objects):
            selected.select_set(False)
        for obj in iter_muscle_elements(muscle_obj):
            obj.hide_viewport = False
            obj.select_set(True)
        context.view_layer.objects.active = muscle_obj
        self.report({"INFO"}, "Selected all muscle elements")
        return {"FINISHED"}


class XMRB_OT_apply_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.apply_muscle"
    bl_label = "Apply Muscle"
    bl_description = "Apply this muscle to the currently chosen target body mesh"

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        body_obj = ensure_default_body_object(settings, context.scene)
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}
        if body_obj is None:
            self.report({"ERROR"}, "Choose a target body mesh in the Add Muscle section first")
            return {"CANCELLED"}
        ok, message = apply_muscle_to_body(context, muscle_obj, body_obj)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if settings.muscle_name == muscle_obj.name:
            save_selected_muscle_settings(settings)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class XMRB_OT_delete_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.delete_muscle"
    bl_label = "Delete Muscle"
    bl_description = "Delete this muscle system and its baked helper assets"
    bl_options = {"REGISTER", "UNDO"}

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        selected_names = [name for name in get_selected_muscle_names(settings) if name != muscle_obj.name]
        ok, message = delete_muscle_system(context, muscle_obj, settings.key_prefix)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        remove_deleted_muscle_from_selection_store(context.scene, self.muscle_name)
        active_name = selected_names[0] if selected_names else ""
        set_selected_muscles(settings, selected_names, active_name=active_name)
        if not selected_names:
            settings.rename_buffer = ""
        self.report({"INFO"}, message)
        return {"FINISHED"}


class XMRB_OT_set_muscle_visibility(bpy.types.Operator):
    bl_idname = "xmuscle_baker.set_muscle_visibility"
    bl_label = "Set Muscle Visibility"
    bl_description = "Set the visibility mode for all muscles in the scene"

    mode: EnumProperty(
        items=(
            ("HIDE", "Hide", ""),
            ("SHOW", "Show", ""),
            ("SHOW_THROUGH", "Show Through", ""),
        )
    )

    def execute(self, context):
        if not iter_scene_muscles(context.scene):
            self.report({"ERROR"}, "No muscles found in the scene")
            return {"CANCELLED"}
        set_all_muscles_visibility_mode(context.scene, self.mode)
        return {"FINISHED"}


class XMRB_OT_activate_preview_animation(bpy.types.Operator):
    bl_idname = "xmuscle_baker.activate_preview_animation"
    bl_label = "Activate Preview Animation"
    bl_description = "Make the generated preview actions for this muscle active on the rig and shape keys"

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        settings.sync_settings_lock = True
        settings.muscle_name = muscle_obj.name
        settings.sync_settings_lock = False
        load_settings_for_muscle(settings, muscle_obj)

        shape_action, rig_action, body_obj, rig_obj = find_preview_actions(settings, muscle_obj)
        if shape_action is None and rig_action is None:
            self.report({"WARNING"}, "No preview animation found for this muscle yet")
            return {"CANCELLED"}

        if body_obj and body_obj.data.shape_keys:
            shape_keys = body_obj.data.shape_keys
            if shape_keys.animation_data is None:
                shape_keys.animation_data_create()
            if shape_action is not None:
                shape_keys.animation_data.action = shape_action

        if rig_obj is not None:
            if rig_obj.animation_data is None:
                rig_obj.animation_data_create()
            if rig_action is not None:
                rig_obj.animation_data.action = rig_action

        self.report({"INFO"}, "Preview animation activated")
        return {"FINISHED"}


class XMRB_OT_rename_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.rename_muscle"
    bl_label = "Rename Muscle"
    bl_description = "Rename this muscle through X-Muscle and rename previously baked keys and preview actions for it"

    muscle_name: StringProperty()

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = bpy.data.objects.get(self.muscle_name)
        if muscle_obj is None:
            self.report({"ERROR"}, "Muscle not found")
            return {"CANCELLED"}

        new_name = settings.rename_buffer.strip()
        if not new_name:
            self.report({"ERROR"}, "Enter a new muscle name first")
            return {"CANCELLED"}
        old_name = muscle_obj.name
        if new_name == old_name:
            return {"FINISHED"}

        body_obj = infer_body_for_muscle(context.scene, muscle_obj)
        rig_obj = bpy.data.objects.get(infer_links_for_muscle(context.scene, muscle_obj)["rig_object_name"]) if muscle_obj else None
        old_shape_action_name, old_rig_action_name = preview_action_names(settings.key_prefix, muscle_obj, body_obj, rig_obj)
        old_prefix = muscle_key_prefix(settings.key_prefix, old_name)
        old_selection = snapshot_selection(context)
        old_active = context.view_layer.objects.active

        try:
            set_single_object_selection(context, muscle_obj)
            if hasattr(context.scene, "Muscle_Name") and hasattr(bpy.ops.muscle, "rename_muscle"):
                context.scene.Muscle_Name = new_name
                bpy.ops.muscle.rename_muscle()
            else:
                muscle_obj.name = new_name
        finally:
            restore_selection(context, old_active, old_selection[1])

        renamed_muscle = bpy.data.objects.get(new_name)
        if renamed_muscle is None:
            renamed_muscle = muscle_obj if muscle_obj.name == new_name else None
        if renamed_muscle is None:
            self.report({"ERROR"}, "Rename failed")
            return {"CANCELLED"}

        new_body_obj = infer_body_for_muscle(context.scene, renamed_muscle) or body_obj
        new_rig_name = infer_links_for_muscle(context.scene, renamed_muscle)["rig_object_name"]
        new_rig_obj = bpy.data.objects.get(new_rig_name) if new_rig_name else rig_obj
        new_prefix = muscle_key_prefix(settings.key_prefix, renamed_muscle.name)

        if new_body_obj and new_body_obj.data.shape_keys:
            for key_block in new_body_obj.data.shape_keys.key_blocks:
                if key_block.name.startswith(old_prefix):
                    key_block.name = new_prefix + key_block.name[len(old_prefix):]

        new_shape_action_name, new_rig_action_name = preview_action_names(settings.key_prefix, renamed_muscle, new_body_obj, new_rig_obj)
        shape_action = bpy.data.actions.get(old_shape_action_name)
        if shape_action:
            shape_action.name = new_shape_action_name
        rig_action = bpy.data.actions.get(old_rig_action_name)
        if rig_action:
            rig_action.name = new_rig_action_name

        selected_names = get_selected_muscle_names(settings)
        selected_names = [renamed_muscle.name if name == old_name else name for name in selected_names]
        set_selected_muscles(settings, selected_names, active_name=renamed_muscle.name)
        settings.rename_buffer = renamed_muscle.name
        save_selected_muscle_settings(settings)
        self.report({"INFO"}, f"Renamed muscle to {renamed_muscle.name}")
        return {"FINISHED"}


class XMRB_OT_bake_specific_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.bake_specific_muscle"
    bl_label = "Bake Muscle"
    bl_description = "Select a muscle, load its saved settings, and bake or rebake it"

    muscle_name: StringProperty()

    def execute(self, context):
        result = bpy.ops.xmuscle_baker.select_muscle(muscle_name=self.muscle_name)
        if "CANCELLED" in result:
            return {"CANCELLED"}
        return bpy.ops.xmuscle_baker.bake_range()


class XMRB_OT_capture_pose(bpy.types.Operator):
    bl_idname = "xmuscle_baker.capture_pose"
    bl_label = "Capture Pose"
    bl_description = "Capture the current selected bone rotation as the start pose or end pose"

    target: EnumProperty(items=CAPTURE_ITEMS)

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        pose_bone = get_preview_pose_bone(settings)
        if pose_bone is None:
            self.report({"ERROR"}, "Choose a rig and a valid bone before capturing a pose")
            return {"CANCELLED"}

        quat = pose_bone_quaternion(pose_bone)
        settings.preview_update_lock = True
        settings.preview_restore_quaternion = quat[:]
        if self.target == "START":
            settings.start_quaternion = quat[:]
            settings.has_start_pose = True
            settings.preview_factor = 0.0
        else:
            settings.end_quaternion = quat[:]
            settings.has_end_pose = True
            settings.preview_factor = 1.0
        settings.preview_update_lock = False
        apply_preview(settings, context)
        save_selected_muscle_settings(settings)
        return {"FINISHED"}


class XMRB_OT_store_preview_base(bpy.types.Operator):
    bl_idname = "xmuscle_baker.store_preview_base"
    bl_label = "Store Current As Restore Pose"
    bl_description = "Store the current bone pose so disabling Live Preview restores it"

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        pose_bone = get_preview_pose_bone(settings)
        if pose_bone is None:
            self.report({"ERROR"}, "Choose a rig and a valid bone first")
            return {"CANCELLED"}
        settings.preview_restore_quaternion = pose_bone_quaternion(pose_bone)[:]
        save_selected_muscle_settings(settings)
        return {"FINISHED"}


class XMRB_OT_bake_range(bpy.types.Operator):
    bl_idname = "xmuscle_baker.bake_range"
    bl_label = "Bake Muscle Range"
    bl_description = "Bake one linked X-Muscle deformation into multi-step body shape keys"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        body_obj = settings.body_object
        rig_obj = settings.rig_object
        selected_muscle_names = get_selected_muscle_names(settings)

        if body_obj is None:
            self.report({"ERROR"}, "Choose a body mesh")
            return {"CANCELLED"}
        if rig_obj is None:
            self.report({"ERROR"}, "Choose the armature that drives the pose")
            return {"CANCELLED"}
        if not settings.bone_name or settings.bone_name not in rig_obj.pose.bones:
            self.report({"ERROR"}, "Choose a valid pose bone on the selected armature")
            return {"CANCELLED"}
        if not selected_muscle_names:
            self.report({"ERROR"}, "Select at least one muscle from the scene list first")
            return {"CANCELLED"}

        ensure_object_mode(context)
        previous_active, previous_selection = snapshot_selection(context)

        ensure_body_shape_keys(context, body_obj)
        if settings.replace_existing:
            remove_existing_shape_keys(context, body_obj, settings.key_prefix)

        muscles = iter_linked_muscles(body_obj)
        display_state = snapshot_muscle_display_state(muscles)
        modifier_state = snapshot_body_modifiers(body_obj)
        xmuscle_live_state = snapshot_xmuscle_live_state(body_obj)
        xmuscle_body_modifier_state = snapshot_xmuscle_body_modifiers(body_obj)
        xmuscle_driver_mute_state = snapshot_body_xmuscle_driver_mute_state(body_obj)
        shape_key_values = snapshot_shape_key_values(body_obj)
        pose_bone = rig_obj.pose.bones[settings.bone_name]

        if settings.use_captured_pose and settings.has_start_pose and settings.has_end_pose:
            sampled_rots = sampled_quaternions(
                Quaternion(settings.start_quaternion),
                Quaternion(settings.end_quaternion),
                settings.samples,
            )
        else:
            sampled_rots = []
            for euler_vector in sampled_vectors(settings.start_rotation, settings.end_rotation, settings.samples):
                sampled_rots.append(Euler(tuple(euler_vector), "XYZ").to_quaternion())

        disabled_modifiers = []
        created_key_names_by_muscle = {}
        estimated_seconds = estimate_bake_seconds(body_obj, len(sampled_rots), settings.corrective_iterations, len(selected_muscle_names))
        bake_started_at = time.perf_counter()
        window_manager = context.window_manager
        total_progress_steps = max(1, len(sampled_rots) * max(1, settings.corrective_iterations) * max(1, len(selected_muscle_names)))
        if estimated_seconds > 0:
            self.report({"INFO"}, f"Estimated bake time: about {format_duration_brief(estimated_seconds)}")

        try:
            if settings.auto_disable_unsupported_modifiers:
                disabled_modifiers = disable_unsupported_modifiers(body_obj, settings.disable_subsurf)
            set_body_xmuscle_driver_mute_state(body_obj, mute=True)
            window_manager.progress_begin(0, total_progress_steps)

            with preserved_pose_bone_rotation(pose_bone):
                for muscle_position, muscle_name in enumerate(selected_muscle_names, start=1):
                    muscle = find_muscle_by_name(body_obj, muscle_name)
                    if muscle is None:
                        if not settings.auto_apply_muscle:
                            self.report({"ERROR"}, f"{muscle_name} is not linked to the body mesh")
                            return {"CANCELLED"}
                        if not hasattr(bpy.ops.muscle, "apply_musculature"):
                            self.report({"ERROR"}, "X-Muscle System is not available; cannot auto-apply muscles to the body")
                            return {"CANCELLED"}

                        scene_muscle = bpy.data.objects.get(muscle_name)
                        if scene_muscle is None or not getattr(scene_muscle, "Muscle_XID", False):
                            self.report({"ERROR"}, f"{muscle_name} could not be found in the scene")
                            return {"CANCELLED"}
                        ok, message = apply_muscle_to_body(context, scene_muscle, body_obj)
                        if not ok:
                            self.report({"ERROR"}, message)
                            return {"CANCELLED"}
                        muscles = iter_linked_muscles(body_obj)
                        muscle = find_muscle_by_name(body_obj, muscle_name)

                    if muscle is None:
                        self.report({"ERROR"}, f"The body mesh still has no shrinkwrap link to {muscle_name}")
                        return {"CANCELLED"}

                    if settings.replace_target_on_rebake and not settings.replace_existing:
                        remove_shape_keys_for_muscle(context, body_obj, settings.key_prefix, muscle.name)
                        remove_preview_actions(settings.key_prefix, muscle, body_obj, rig_obj)

                    isolate_single_muscle(muscles, muscle)
                    zero_all_shape_keys(body_obj)
                    context.view_layer.update()
                    created_key_names = []

                    for index, quat in enumerate(sampled_rots, start=1):
                        zero_all_shape_keys(body_obj)
                        apply_quaternion_to_pose_bone(pose_bone, quat)
                        context.view_layer.update()

                        set_body_xmuscle_state(body_obj, enabled=True, solo_muscle=muscle)
                        context.view_layer.update()
                        source_obj = duplicate_flatten_modifiers(
                            context,
                            body_obj,
                            f"{body_obj.name}_{sanitize_key_token(muscle.name)}_xmuscle_bake_{index:03d}",
                        )

                        try:
                            zero_all_shape_keys(body_obj)
                            set_body_xmuscle_state(body_obj, enabled=False)
                            context.view_layer.update()
                            body_obj.active_shape_key_index = 0
                            generated_shape = add_corrective_pose_shape(
                                source_obj,
                                body_obj,
                                iterations=settings.corrective_iterations,
                                progress_callback=lambda iteration_done, iteration_total, sample_index=index, muscle_offset=muscle_position - 1: window_manager.progress_update(
                                    (muscle_offset * len(sampled_rots) * iteration_total) + ((sample_index - 1) * iteration_total) + iteration_done
                                ),
                            )
                            key_name = build_key_name(settings.key_prefix, muscle.name, index, len(sampled_rots))
                            generated_shape.name = key_name
                            generated_shape.slider_min = 0.0
                            generated_shape.slider_max = 1.0
                            generated_shape.value = 0.0
                            created_key_names.append(key_name)
                        finally:
                            remove_temporary_object(source_obj)
                            zero_all_shape_keys(body_obj)

                    created_key_names_by_muscle[muscle.name] = created_key_names
                    if settings.auto_generate_animation and created_key_names:
                        generate_preview_animation(settings, muscle, body_obj, rig_obj, pose_bone, sampled_rots, created_key_names)

        finally:
            window_manager.progress_end()
            restore_shape_key_values(body_obj, shape_key_values)
            restore_body_xmuscle_driver_mute_state(body_obj, xmuscle_driver_mute_state)
            restore_xmuscle_body_modifiers(body_obj, xmuscle_body_modifier_state)
            restore_xmuscle_live_state(body_obj, xmuscle_live_state)
            restore_body_modifiers(body_obj, modifier_state)
            restore_muscle_display_state(muscles, display_state)
            restore_selection(context, previous_active, previous_selection)
            context.view_layer.update()

        elapsed_seconds = time.perf_counter() - bake_started_at
        total_created = sum(len(names) for names in created_key_names_by_muscle.values())
        message = f"Created {total_created} shape keys across {len(created_key_names_by_muscle)} muscle(s) on {body_obj.name} in {format_duration_brief(elapsed_seconds)}"
        if estimated_seconds > 0:
            message += f" (estimate was {format_duration_brief(estimated_seconds)})"
        if disabled_modifiers:
            message += f"; temporarily disabled and restored: {', '.join(disabled_modifiers)}"
        save_selected_muscle_settings(settings)
        self.report({"INFO"}, message)
        return {"FINISHED"}


CORE_CLASSES = (
    XMRB_Settings,
    XMRB_OT_guess_rig,
    XMRB_OT_add_muscle,
    XMRB_OT_select_muscle,
    XMRB_OT_toggle_muscle_selection,
    XMRB_OT_select_muscle_elements,
    XMRB_OT_apply_muscle,
    XMRB_OT_delete_muscle,
    XMRB_OT_set_muscle_visibility,
    XMRB_OT_activate_preview_animation,
    XMRB_OT_rename_muscle,
    XMRB_OT_bake_specific_muscle,
    XMRB_OT_capture_pose,
    XMRB_OT_store_preview_base,
    XMRB_OT_bake_range,
)
