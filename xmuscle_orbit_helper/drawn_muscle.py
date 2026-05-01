import bpy
import gpu
from bpy.props import EnumProperty
from gpu_extras.batch import batch_for_shader

from . import core
from .drawn_helpers import (
    CLOSE_DISTANCE_PX,
    MAX_SMOOTHING_LEVELS,
    PICK_DISTANCE_PX,
    SLIDER_HEIGHT,
    SLIDER_TOP_OFFSET,
    SLIDER_WIDTH,
    _convert_mesh_to_xmuscle,
    _create_drawn_mesh,
    _distance_2d,
    _draw_centered_text_line,
    _draw_rect_2d,
    _find_muscle_controller,
    _mouse_coord,
    _normalize_xmuscle_control_display,
    _object_world_vertices,
    _parent_object_to_bone,
    _raycast_body,
    _remove_temp_mesh_object,
    _restore_object_world_vertices,
    _screen_point,
)

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
