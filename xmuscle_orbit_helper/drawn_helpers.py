import math

import blf
import bpy
import gpu
from bpy.props import EnumProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import core


CLOSE_DISTANCE_PX = 16.0
PICK_DISTANCE_PX = 18.0
MUSCLE_THICKNESS = 0.05
MAX_SMOOTHING_LEVELS = 4
SLIDER_WIDTH = 360
SLIDER_HEIGHT = 18
SLIDER_TOP_OFFSET = 116
XMSL_DEFAULT_MUSCLE_LENGTH = 5.16
XMSL_DEFAULT_CUSTOM_SHAPE_SCALE = 5.0
CONTROL_EMPTY_WORLD_SIZE_FACTOR = 0.025
SYSTEM_CUSTOM_SHAPE_DISPLAY_BOOST = 1.45


def _mouse_coord(event):
    return event.mouse_region_x, event.mouse_region_y


def _screen_point(context, world_position):
    if context.region is None or context.region_data is None:
        return None
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world_position)


def _distance_2d(a, b):
    if a is None or b is None:
        return float("inf")
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _raycast_body(context, body_obj, mouse_xy):
    if body_obj is None or body_obj.type != "MESH":
        return None
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return None

    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_xy)
    ray_direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_xy).normalized()

    depsgraph = context.evaluated_depsgraph_get()
    eval_body = body_obj.evaluated_get(depsgraph)
    inv = eval_body.matrix_world.inverted()
    local_origin = inv @ ray_origin
    local_direction = (inv.to_3x3() @ ray_direction).normalized()

    hit, location, normal, _face_index = eval_body.ray_cast(local_origin, local_direction)
    if not hit:
        return None

    world_location = eval_body.matrix_world @ location
    normal_matrix = eval_body.matrix_world.to_3x3().inverted().transposed()
    world_normal = (normal_matrix @ normal).normalized()
    return world_location, world_normal


def _average_vector(values, fallback):
    if not values:
        return fallback.copy()
    result = Vector((0.0, 0.0, 0.0))
    for value in values:
        result += value
    if result.length < 1e-6:
        return fallback.copy()
    return result.normalized()


def _smooth_loop_points(points, levels):
    result = [{"co": item["co"].copy(), "normal": item["normal"].copy()} for item in points]
    for _index in range(max(0, min(MAX_SMOOTHING_LEVELS, int(levels)))):
        smoothed = []
        count = len(result)
        for index in range(count):
            current = result[index]
            nxt = result[(index + 1) % count]
            q_normal = current["normal"].lerp(nxt["normal"], 0.25)
            r_normal = current["normal"].lerp(nxt["normal"], 0.75)
            if q_normal.length < 1e-6:
                q_normal = current["normal"].copy()
            if r_normal.length < 1e-6:
                r_normal = nxt["normal"].copy()
            smoothed.append({
                "co": current["co"].lerp(nxt["co"], 0.25),
                "normal": q_normal.normalized(),
            })
            smoothed.append({
                "co": current["co"].lerp(nxt["co"], 0.75),
                "normal": r_normal.normalized(),
            })
        result = smoothed
    return result


def _raycast_loop_screen_center(context, body_obj, positions):
    screens = [_screen_point(context, co) for co in positions]
    screens = [screen for screen in screens if screen is not None]
    if not screens:
        return None
    center = (
        sum(screen.x for screen in screens) / len(screens),
        sum(screen.y for screen in screens) / len(screens),
    )
    return _raycast_body(context, body_obj, center)


def _create_drawn_mesh(context, name, points, smoothing=1, body_obj=None):
    smoothed_points = _smooth_loop_points(points, smoothing)
    positions = [item["co"].copy() for item in smoothed_points]
    normals = [item["normal"].copy() for item in smoothed_points]
    if len(positions) < 3:
        return None

    normal = _average_vector(normals, Vector((0.0, 0.0, 1.0)))
    half_thickness = MUSCLE_THICKNESS * 0.5
    projected_center = _raycast_loop_screen_center(context, body_obj, positions) if body_obj is not None else None
    if projected_center is not None:
        center_surface = projected_center[0]
        center_normal = projected_center[1]
    else:
        center_surface = sum(positions, Vector((0.0, 0.0, 0.0))) / len(positions)
        center_normal = normal

    top = [co + item["normal"] * half_thickness for co, item in zip(positions, smoothed_points)]
    bottom = [co - item["normal"] * half_thickness for co, item in zip(positions, smoothed_points)]
    center_top = center_surface + center_normal * half_thickness
    center_bottom = center_surface - center_normal * half_thickness

    verts = top + bottom + [center_top, center_bottom]
    top_center_index = len(verts) - 2
    bottom_center_index = len(verts) - 1
    count = len(top)
    faces = []

    for index in range(count):
        next_index = (index + 1) % count
        faces.append((top_center_index, index, next_index))
        faces.append((bottom_center_index, count + next_index, count + index))
        faces.append((index, count + index, count + next_index, next_index))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()
    mesh.validate()

    obj = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(obj)
    core.set_single_object_selection(context, obj)
    bpy.ops.object.shade_smooth()

    return obj


def _object_world_vertices(obj):
    return [obj.matrix_world @ vertex.co.copy() for vertex in obj.data.vertices]


def _restore_object_world_vertices(obj, world_coords):
    if obj is None or obj.type != "MESH" or len(obj.data.vertices) != len(world_coords):
        return False

    local_coords = [obj.matrix_world.inverted() @ co for co in world_coords]

    if obj.data.shape_keys is not None:
        for key_block in obj.data.shape_keys.key_blocks:
            if len(key_block.data) != len(local_coords):
                continue
            for index, co in enumerate(local_coords):
                key_block.data[index].co = co

    for index, co in enumerate(local_coords):
        obj.data.vertices[index].co = co

    obj.data.update()
    return True


def _bounds_max_extent(world_coords):
    if not world_coords:
        return XMSL_DEFAULT_MUSCLE_LENGTH
    min_x = min(co.x for co in world_coords)
    min_y = min(co.y for co in world_coords)
    min_z = min(co.z for co in world_coords)
    max_x = max(co.x for co in world_coords)
    max_y = max(co.y for co in world_coords)
    max_z = max(co.z for co in world_coords)
    return max(max_x - min_x, max_y - min_y, max_z - min_z)


def _object_world_scale_max(obj):
    if obj is None:
        return 1.0
    scale = obj.matrix_world.to_scale()
    return max(abs(scale.x), abs(scale.y), abs(scale.z), 1e-6)


def _set_pose_bone_custom_shape_display_size(pose_bone, display_scale):
    if pose_bone.custom_shape is None:
        return
    if hasattr(pose_bone, "custom_shape_scale_xyz"):
        pose_bone.custom_shape_scale_xyz = (display_scale, display_scale, display_scale)
    elif hasattr(pose_bone, "custom_shape_scale"):
        pose_bone.custom_shape_scale = display_scale


def _normalize_xmuscle_control_display(muscle_obj, world_coords):
    mesh_extent = max(0.001, _bounds_max_extent(world_coords))
    display_factor = max(0.02, mesh_extent / XMSL_DEFAULT_MUSCLE_LENGTH)
    empty_world_size = max(0.01, mesh_extent * CONTROL_EMPTY_WORLD_SIZE_FACTOR)
    custom_shape_size = XMSL_DEFAULT_CUSTOM_SHAPE_SCALE * display_factor * SYSTEM_CUSTOM_SHAPE_DISPLAY_BOOST

    controller = _find_muscle_controller(muscle_obj)
    if controller is not None and hasattr(controller, "empty_display_size"):
        controller.empty_display_size = empty_world_size / _object_world_scale_max(controller)

    muscle_sys = core.get_muscle_system(muscle_obj)
    if muscle_sys is not None:
        if hasattr(muscle_sys, "empty_display_size"):
            muscle_sys.empty_display_size = empty_world_size / _object_world_scale_max(muscle_sys)
        if getattr(muscle_sys, "type", None) == "ARMATURE":
            for pose_bone in muscle_sys.pose.bones:
                _set_pose_bone_custom_shape_display_size(pose_bone, custom_shape_size)


def _set_font_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, 72)


def _draw_text_line(font_id, text, x, y, size, color):
    _set_font_size(font_id, size)
    blf.position(font_id, x + 2, y - 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, text)
    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _draw_centered_text_line(font_id, text, region_width, y, size, color):
    _set_font_size(font_id, size)
    width = blf.dimensions(font_id, text)[0]
    _draw_text_line(font_id, text, (region_width - width) * 0.5, y, size, color)


def _draw_rect_2d(x, y, width, height, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(
        shader,
        "TRIS",
        {
            "pos": (
                (x, y, 0.0),
                (x + width, y, 0.0),
                (x + width, y + height, 0.0),
                (x, y, 0.0),
                (x + width, y + height, 0.0),
                (x, y + height, 0.0),
            )
        },
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _remove_temp_mesh_object(obj):
    if obj is None or bpy.data.objects.get(obj.name) is None:
        return
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _duplicate_mesh_as_world_source(context, source_obj, name):
    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = source_obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eval_obj, depsgraph=depsgraph)
    mesh.transform(source_obj.matrix_world)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(obj)
    obj.show_in_front = True
    return obj


def _parent_object_to_bone(context, obj, rig_obj, bone_name, world_location):
    if obj is None or rig_obj is None or bone_name not in rig_obj.pose.bones:
        return False

    core.ensure_object_mode(context)
    obj.hide_viewport = False
    obj.matrix_world.translation = world_location
    core.set_single_object_selection(context, obj)
    rig_obj.select_set(True)
    context.view_layer.objects.active = rig_obj
    bpy.ops.object.mode_set(mode="POSE")

    for pose_bone in rig_obj.pose.bones:
        pose_bone.select = False
    pose_bone = rig_obj.pose.bones[bone_name]
    pose_bone.select = True
    rig_obj.data.bones.active = rig_obj.data.bones[bone_name]

    try:
        bpy.ops.object.parent_set(type="BONE", keep_transform=True)
    except TypeError:
        bpy.ops.object.parent_set(type="BONE")
    except RuntimeError:
        return False
    finally:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass

    return True


def _find_muscle_controller(muscle_obj):
    controller = core.get_muscle_controller(muscle_obj)
    if controller is not None:
        return controller

    muscle_sys = core.get_muscle_system(muscle_obj)
    if muscle_sys is None:
        return None
    expected_name = muscle_sys.name.replace("System", "_ctrl")
    return bpy.data.objects.get(expected_name)


def _convert_mesh_to_xmuscle(context, source_obj):
    if source_obj is None:
        return None, "No drawn mesh was created"
    if not hasattr(bpy.ops.muscle, "convert_to_muscle"):
        return None, "X-Muscle convert operator is not available"

    scene = context.scene
    previous_create_type = getattr(scene, "Create_Type", "MANUAL")
    before_names = {obj.name for obj in core.iter_scene_muscles(scene)}

    core.ensure_object_mode(context)
    core.set_single_object_selection(context, source_obj)
    try:
        scene.Create_Type = "MANUAL"
        if hasattr(scene, "Muscle_Name") and not scene.Muscle_Name.strip():
            scene.Muscle_Name = "DrawnMuscle"
        bpy.ops.muscle.convert_to_muscle()
    except RuntimeError as exc:
        return None, f"X-Muscle conversion failed: {exc}"
    finally:
        scene.Create_Type = previous_create_type

    created = [obj for obj in core.iter_scene_muscles(scene) if obj.name not in before_names]
    if not created:
        return None, "X-Muscle conversion finished but no new muscle was detected"
    return created[-1], ""


def _ordered_autoaim_bones(context, rig_obj):
    if rig_obj is None:
        return [], ""
    selected_names, active_name = core.get_selected_bone_names_for_autoaim(context, rig_obj)
    if len(selected_names) != 2 or not active_name:
        return [], ""
    ordered = [name for name in selected_names if name != active_name]
    ordered.append(active_name)
    if len(ordered) != 2:
        return [], ""
    return ordered, active_name


