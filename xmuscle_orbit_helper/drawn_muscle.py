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


class XMRB_OT_draw_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.draw_muscle"
    bl_label = "Draw Muscle"
    bl_description = "Draw a custom surface on the body, convert it to an X-Muscle, and attach it to the selected bones"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(
        items=(
            ("START", "Start", ""),
            ("END", "End", ""),
        )
    )

    def _draw_callback(self, context):
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        gpu.state.line_width_set(2.5)

        points = [item["co"] for item in self.points]
        if self.state == "DRAW" and self.hover_hit is not None:
            points_for_line = points + [self.hover_hit["co"]]
        else:
            points_for_line = points

        if len(points_for_line) >= 2:
            batch = batch_for_shader(shader, "LINE_STRIP", {"pos": points_for_line})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.05, 0.02, 0.95))
            batch.draw(shader)

        if self.state != "DRAW" and len(points) >= 3:
            closed = points + [points[0]]
            batch = batch_for_shader(shader, "LINE_STRIP", {"pos": closed})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.05, 0.02, 0.85))
            batch.draw(shader)

        if points:
            gpu.state.point_size_set(8.0)
            batch = batch_for_shader(shader, "POINTS", {"pos": points})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.35, 0.25, 1.0))
            batch.draw(shader)

        if self.start_index is not None:
            gpu.state.point_size_set(13.0)
            batch = batch_for_shader(shader, "POINTS", {"pos": [self.points[self.start_index]["co"]]})
            shader.bind()
            shader.uniform_float("color", (0.1, 1.0, 0.35, 1.0))
            batch.draw(shader)

        if self.end_index is not None:
            gpu.state.point_size_set(13.0)
            batch = batch_for_shader(shader, "POINTS", {"pos": [self.points[self.end_index]["co"]]})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.28, 0.15, 1.0))
            batch.draw(shader)

        if self.hover_index is not None and self.points:
            gpu.state.point_size_set(15.0)
            batch = batch_for_shader(shader, "POINTS", {"pos": [self.points[self.hover_index]["co"]]})
            shader.bind()
            shader.uniform_float("color", (1.0, 0.85, 0.1, 1.0))
            batch.draw(shader)

        gpu.state.point_size_set(1.0)
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")

    def _instruction_lines(self):
        if self.state == "DRAW":
            return (
                "Draw the muscle outline on the body",
                "Left click: add point   Enter/click first point: close   Backspace: undo   Esc: cancel",
            )
        if self.state == "PICK_START":
            return (
                "Pick the START attachment point",
                "Click one drawn vertex. This end attaches to the first selected bone.",
            )
        if self.state == "SMOOTH":
            return (
                f"Adjust muscle smoothing: {self.smoothing_level}",
                "Drag the slider, use mouse wheel, or arrow keys. Enter/left click outside confirms.",
            )
        return (
            "Pick the END attachment point",
            "Click another drawn vertex. This end attaches to the second selected bone or slide helper.",
        )

    def _draw_text_callback(self, context):
        region = context.region
        if region is None:
            return

        font_id = 0
        primary, secondary = self._instruction_lines()
        y = region.height - 54

        _draw_centered_text_line(font_id, primary, region.width, y, 24, (1.0, 0.18, 0.12, 1.0))
        _draw_centered_text_line(font_id, secondary, region.width, y - 28, 15, (0.95, 0.95, 0.85, 1.0))

        if self.state == "SMOOTH":
            x, slider_y, width, height = self._slider_rect(context)
            gpu.state.blend_set("ALPHA")
            _draw_rect_2d(x - 4, slider_y - 4, width + 8, height + 8, (0.0, 0.0, 0.0, 0.55))
            _draw_rect_2d(x, slider_y, width, height, (0.18, 0.18, 0.18, 0.95))
            filled = width * (self.smoothing_level / MAX_SMOOTHING_LEVELS)
            _draw_rect_2d(x, slider_y, filled, height, (1.0, 0.08, 0.04, 0.95))
            handle_x = x + filled - 5
            _draw_rect_2d(handle_x, slider_y - 6, 10, height + 12, (1.0, 0.9, 0.82, 1.0))
            gpu.state.blend_set("NONE")

    def _tag_redraw(self, context):
        if context.area:
            context.area.tag_redraw()

    def _cleanup_draw_handler(self):
        if getattr(self, "_draw_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handle, "WINDOW")
            self._draw_handle = None
        if getattr(self, "_text_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._text_handle, "WINDOW")
            self._text_handle = None

    def _cleanup_preview(self):
        _remove_temp_mesh_object(getattr(self, "preview_source_obj", None))
        self.preview_source_obj = None
        self.preview_world_coords = []

    def _slider_rect(self, context):
        region = context.region
        x = (region.width - SLIDER_WIDTH) * 0.5
        y = region.height - SLIDER_TOP_OFFSET
        return x, y, SLIDER_WIDTH, SLIDER_HEIGHT

    def _slider_contains(self, context, mouse_xy):
        x, y, width, height = self._slider_rect(context)
        return x - 12 <= mouse_xy[0] <= x + width + 12 and y - 12 <= mouse_xy[1] <= y + height + 12

    def _slider_level_from_mouse(self, context, mouse_xy):
        x, _y, width, _height = self._slider_rect(context)
        factor = max(0.0, min(1.0, (mouse_xy[0] - x) / width))
        return int(round(factor * MAX_SMOOTHING_LEVELS))

    def _set_smoothing_level(self, context, level):
        level = max(0, min(MAX_SMOOTHING_LEVELS, int(level)))
        if level == self.smoothing_level and self.preview_source_obj is not None:
            return
        self.smoothing_level = level
        self._rebuild_preview_mesh(context)

    def _rebuild_preview_mesh(self, context):
        self._cleanup_preview()
        obj = _create_drawn_mesh(
            context,
            self.mesh_name,
            self.points,
            smoothing=self.smoothing_level,
            body_obj=self.body_obj,
        )
        if obj is None:
            return
        obj.name = f"{self.mesh_name}_preview"
        obj.show_in_front = True
        obj.display_type = "TEXTURED"
        self.preview_source_obj = obj
        self.preview_world_coords = _object_world_vertices(obj)
        self._tag_redraw(context)

    def _enter_smoothing_preview(self, context):
        self.state = "SMOOTH"
        self.smoothing_level = 1
        self.slider_dragging = False
        self._rebuild_preview_mesh(context)
        self.report({"INFO"}, "Adjust smoothing, then press Enter to create the X-Muscle")

    def _nearest_point_index(self, context, mouse_xy):
        best_index = None
        best_distance = float("inf")
        for index, item in enumerate(self.points):
            screen = _screen_point(context, item["co"])
            distance = _distance_2d(screen, mouse_xy)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        if best_distance <= PICK_DISTANCE_PX:
            return best_index
        return None

    def _update_hover(self, context, event):
        mouse_xy = _mouse_coord(event)
        self.hover_index = self._nearest_point_index(context, mouse_xy) if self.state != "DRAW" else None
        self.hover_hit = _raycast_body(context, self.body_obj, mouse_xy) if self.state == "DRAW" else None
        if self.hover_hit is not None:
            self.hover_hit = {"co": self.hover_hit[0], "normal": self.hover_hit[1]}

    def _close_loop_ready(self, context, mouse_xy):
        if len(self.points) < 3:
            return False
        first_screen = _screen_point(context, self.points[0]["co"])
        return _distance_2d(first_screen, mouse_xy) <= CLOSE_DISTANCE_PX

    def _finish(self, context):
        self._cleanup_draw_handler()
        if self.start_index is None or self.end_index is None or self.start_index == self.end_index:
            self.report({"ERROR"}, "Pick distinct start and end vertices for the drawn muscle")
            return {"CANCELLED"}

        source_obj = self.preview_source_obj
        if source_obj is None:
            source_obj = _create_drawn_mesh(
                context,
                self.mesh_name,
                self.points,
                smoothing=getattr(self, "smoothing_level", 1),
                body_obj=self.body_obj,
            )
            if source_obj is None:
                self.report({"ERROR"}, "Failed to create drawn muscle mesh")
                return {"CANCELLED"}
        source_world_coords = self.preview_world_coords or _object_world_vertices(source_obj)
        self.preview_source_obj = None

        created_slide_bone_name = ""
        attach_bones = list(self.ordered_bones)
        if self.use_autoaim and self.settings.create_slide_driver:
            helper_base_name = f"{getattr(context.scene, 'Muscle_Name', 'DrawnMuscle')}_slide"
            ok, result = core.create_slide_driver_bone(
                context,
                self.rig_obj,
                attach_bones[0],
                attach_bones[1],
                helper_base_name,
                self.settings.slide_driver_slide_axis,
                self.settings.slide_driver_rotation_axes,
                self.settings.slide_driver_combine_mode,
                self.settings.slide_driver_factor,
            )
            if ok:
                created_slide_bone_name = result
                attach_bones[1] = result
            else:
                self.report({"WARNING"}, f"Slide driver skipped: {result}")

        muscle_obj, error = _convert_mesh_to_xmuscle(context, source_obj)
        if muscle_obj is None:
            if created_slide_bone_name:
                core.delete_bone_by_name(context, self.rig_obj, created_slide_bone_name)
            _remove_temp_mesh_object(source_obj)
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        _remove_temp_mesh_object(source_obj)

        if created_slide_bone_name:
            muscle_obj["xmuscle_orbit_slide_bone"] = created_slide_bone_name

        if self.use_autoaim:
            start_point = self.points[self.start_index]["co"]
            end_point = self.points[self.end_index]["co"]
            muscle_sys = core.get_muscle_system(muscle_obj)
            controller = _find_muscle_controller(muscle_obj)
            if muscle_sys is not None:
                _parent_object_to_bone(context, muscle_sys, self.rig_obj, attach_bones[0], start_point)
            if controller is not None:
                _parent_object_to_bone(context, controller, self.rig_obj, attach_bones[1], end_point)

            if self.settings.create_length_driver:
                ok, result = core.create_base_length_driver(
                    muscle_obj,
                    self.rig_obj,
                    self.ordered_bones[1],
                    self.settings.length_driver_rotation_axes,
                    self.settings.length_driver_combine_mode,
                    self.settings.length_driver_factor,
                )
                if not ok:
                    self.report({"WARNING"}, f"Base Length driver skipped: {result}")

        if not _restore_object_world_vertices(muscle_obj, source_world_coords):
            self.report({"WARNING"}, "Drawn mesh placement could not be restored after X-Muscle conversion")

        _normalize_xmuscle_control_display(muscle_obj, source_world_coords)
        core.set_muscle_visibility_mode(muscle_obj, "SHOW_THROUGH")

        body_obj = core.ensure_default_body_object(self.settings, context.scene)
        if body_obj is not None:
            ok, result = core.apply_muscle_to_body(context, muscle_obj, body_obj)
            if not ok:
                self.report({"WARNING"}, result)

        core.set_selected_muscles(self.settings, [muscle_obj.name], active_name=muscle_obj.name)
        core.set_single_object_selection(context, muscle_obj)
        self.report({"INFO"}, f"Created drawn muscle {muscle_obj.name}")
        return {"FINISHED"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup_draw_handler()
            self._cleanup_preview()
            self._tag_redraw(context)
            return {"CANCELLED"}

        if self.state == "SMOOTH":
            if event.type == "MOUSEMOVE" and self.slider_dragging:
                self._set_smoothing_level(context, self._slider_level_from_mouse(context, _mouse_coord(event)))
                return {"RUNNING_MODAL"}
            if event.type == "MOUSEMOVE":
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                mouse_xy = _mouse_coord(event)
                if self._slider_contains(context, mouse_xy):
                    self.slider_dragging = True
                    self._set_smoothing_level(context, self._slider_level_from_mouse(context, mouse_xy))
                    return {"RUNNING_MODAL"}
                return self._finish(context)
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self.slider_dragging = False
                return {"RUNNING_MODAL"}
            if event.type in {"WHEELUPMOUSE", "NUMPAD_PLUS", "PLUS", "RIGHT_ARROW", "UP_ARROW"}:
                self._set_smoothing_level(context, self.smoothing_level + 1)
                return {"RUNNING_MODAL"}
            if event.type in {"WHEELDOWNMOUSE", "NUMPAD_MINUS", "MINUS", "LEFT_ARROW", "DOWN_ARROW"}:
                self._set_smoothing_level(context, self.smoothing_level - 1)
                return {"RUNNING_MODAL"}
            if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
                return self._finish(context)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            self._update_hover(context, event)
            self._tag_redraw(context)
            return {"RUNNING_MODAL"}

        if self.state == "DRAW" and event.type in {"BACK_SPACE", "DEL"} and event.value == "PRESS":
            if self.points:
                self.points.pop()
            self._tag_redraw(context)
            return {"RUNNING_MODAL"}

        if self.state == "DRAW" and event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if len(self.points) >= 3:
                self.state = "PICK_START"
                self.report({"INFO"}, "Pick the start attachment vertex")
            self._tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            mouse_xy = _mouse_coord(event)
            if self.state == "DRAW":
                if self._close_loop_ready(context, mouse_xy):
                    self.state = "PICK_START"
                    self.report({"INFO"}, "Pick the start attachment vertex")
                elif self.hover_hit is not None:
                    self.points.append(self.hover_hit)
                self._tag_redraw(context)
                return {"RUNNING_MODAL"}

            picked_index = self._nearest_point_index(context, mouse_xy)
            if picked_index is None:
                return {"RUNNING_MODAL"}
            if self.state == "PICK_START":
                self.start_index = picked_index
                self.state = "PICK_END"
                self.report({"INFO"}, "Pick the end attachment vertex")
                self._tag_redraw(context)
                return {"RUNNING_MODAL"}
            if self.state == "PICK_END":
                self.end_index = picked_index
                self._enter_smoothing_preview(context)
                return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def invoke(self, context, _event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"ERROR"}, "Draw Muscle must be started from a 3D View")
            return {"CANCELLED"}

        self.settings = context.scene.xmuscle_range_baker
        self.body_obj = core.get_effective_body_object(self.settings, context.scene)
        if self.body_obj is None:
            self.report({"ERROR"}, "Choose an Apply To body mesh before drawing")
            return {"CANCELLED"}

        self.rig_obj = core.find_armature_for_autoaim(context)
        self.ordered_bones, _active_name = _ordered_autoaim_bones(context, self.rig_obj)
        self.use_autoaim = self.rig_obj is not None and len(self.ordered_bones) == 2
        self.mesh_name = f"{getattr(context.scene, 'Muscle_Name', 'DrawnMuscle') or 'DrawnMuscle'}_drawn_source"
        self.points = []
        self.hover_hit = None
        self.hover_index = None
        self.start_index = None
        self.end_index = None
        self.smoothing_level = 1
        self.slider_dragging = False
        self.preview_source_obj = None
        self.preview_world_coords = []
        self.state = "DRAW"
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback,
            (context,),
            "WINDOW",
            "POST_VIEW",
        )
        self._text_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_text_callback,
            (context,),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, "Click body points; click the first point or press Enter to close; Esc cancels")
        return {"RUNNING_MODAL"}


DRAWN_CLASSES = (
    XMRB_OT_draw_muscle,
)
