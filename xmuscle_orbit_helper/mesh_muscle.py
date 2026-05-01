import bpy
from bpy.props import StringProperty

from . import core
from .drawn_muscle import (
    _convert_mesh_to_xmuscle,
    _find_muscle_controller,
    _object_world_vertices,
    _parent_object_to_bone,
    _remove_temp_mesh_object,
    _restore_object_world_vertices,
    _normalize_xmuscle_control_display,
)


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


def _ordered_autoaim_bones(context, rig_obj):
    if rig_obj is None:
        return [], ""
    selected_names, active_name = core.get_selected_bone_names_for_autoaim(context, rig_obj)
    if len(selected_names) != 2 or not active_name:
        return [], ""
    ordered = [name for name in selected_names if name != active_name]
    ordered.append(active_name)
    return ordered, active_name


def _pose_bone_world_head(rig_obj, bone_name):
    pose_bone = rig_obj.pose.bones.get(bone_name)
    if pose_bone is None:
        return rig_obj.matrix_world.translation
    return rig_obj.matrix_world @ pose_bone.head


def _pose_bone_world_tail(rig_obj, bone_name):
    pose_bone = rig_obj.pose.bones.get(bone_name)
    if pose_bone is None:
        return rig_obj.matrix_world.translation
    return rig_obj.matrix_world @ pose_bone.tail


class XMRB_OT_toggle_mesh_muscle_creator(bpy.types.Operator):
    bl_idname = "xmuscle_baker.toggle_mesh_muscle_creator"
    bl_label = "Mesh Muscle"
    bl_description = "Show or hide custom mesh conversion controls"

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        settings.show_mesh_muscle_creator = not settings.show_mesh_muscle_creator
        active = context.view_layer.objects.active
        if settings.show_mesh_muscle_creator and active and active.type == "MESH" and not getattr(active, "Muscle_XID", False):
            settings.mesh_source_object = active
        return {"FINISHED"}


class XMRB_OT_create_mesh_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.create_mesh_muscle"
    bl_label = "Create Mesh Muscle"
    bl_description = "Convert the chosen custom mesh into an X-Muscle and apply the same optional helper drivers"
    bl_options = {"REGISTER", "UNDO"}

    source_name: StringProperty(default="")

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        source_obj = bpy.data.objects.get(self.source_name) if self.source_name else settings.mesh_source_object
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Choose a mesh object to convert")
            return {"CANCELLED"}
        if getattr(source_obj, "Muscle_XID", False):
            self.report({"ERROR"}, "Choose a regular mesh, not an existing X-Muscle")
            return {"CANCELLED"}

        scene = context.scene
        rig_obj = core.find_armature_for_autoaim(context)
        ordered_bones, _active_name = _ordered_autoaim_bones(context, rig_obj)
        use_autoaim = rig_obj is not None and len(ordered_bones) == 2
        attach_bones = list(ordered_bones)
        length_driver_source_bone_name = attach_bones[1] if use_autoaim else ""
        created_slide_bone_name = ""
        temp_obj = None
        created_muscle = None

        try:
            if use_autoaim and settings.create_slide_driver:
                helper_base_name = f"{getattr(scene, 'Muscle_Name', source_obj.name) or source_obj.name}_slide"
                ok, result = core.create_slide_driver_bone(
                    context,
                    rig_obj,
                    attach_bones[0],
                    attach_bones[1],
                    helper_base_name,
                    settings.slide_driver_slide_axis,
                    settings.slide_driver_rotation_axes,
                    settings.slide_driver_combine_mode,
                    settings.slide_driver_factor,
                )
                if not ok:
                    self.report({"ERROR"}, result)
                    return {"CANCELLED"}
                created_slide_bone_name = result
                attach_bones[1] = result

            temp_obj = _duplicate_mesh_as_world_source(context, source_obj, f"{source_obj.name}_xmoh_convert_source")
            source_world_coords = _object_world_vertices(temp_obj)

            previous_name = getattr(scene, "Muscle_Name", "")
            if hasattr(scene, "Muscle_Name") and not scene.Muscle_Name.strip():
                scene.Muscle_Name = source_obj.name
            muscle_obj, error = _convert_mesh_to_xmuscle(context, temp_obj)
            if hasattr(scene, "Muscle_Name"):
                scene.Muscle_Name = previous_name
            if muscle_obj is None:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            created_muscle = muscle_obj

            if created_slide_bone_name:
                muscle_obj["xmuscle_orbit_slide_bone"] = created_slide_bone_name

            if use_autoaim:
                muscle_sys = core.get_muscle_system(muscle_obj)
                controller = _find_muscle_controller(muscle_obj)
                if muscle_sys is not None:
                    _parent_object_to_bone(context, muscle_sys, rig_obj, attach_bones[0], _pose_bone_world_head(rig_obj, attach_bones[0]))
                if controller is not None:
                    _parent_object_to_bone(context, controller, rig_obj, attach_bones[1], _pose_bone_world_tail(rig_obj, attach_bones[1]))
                if settings.create_length_driver and length_driver_source_bone_name:
                    ok, result = core.create_base_length_driver(
                        muscle_obj,
                        rig_obj,
                        length_driver_source_bone_name,
                        settings.length_driver_rotation_axes,
                        settings.length_driver_combine_mode,
                        settings.length_driver_factor,
                    )
                    if not ok:
                        self.report({"WARNING"}, f"Base Length driver skipped: {result}")

            if not _restore_object_world_vertices(muscle_obj, source_world_coords):
                self.report({"WARNING"}, "Converted mesh placement could not be restored after X-Muscle conversion")

            _normalize_xmuscle_control_display(muscle_obj, source_world_coords)
            core.set_muscle_visibility_mode(muscle_obj, "SHOW_THROUGH")
            body_obj = core.ensure_default_body_object(settings, scene)
            if body_obj is not None:
                ok, result = core.apply_muscle_to_body(context, muscle_obj, body_obj)
                if not ok:
                    self.report({"WARNING"}, result)

            core.set_selected_muscles(settings, [muscle_obj.name], active_name=muscle_obj.name)
            core.set_single_object_selection(context, muscle_obj)
            self.report({"INFO"}, f"Created mesh muscle {muscle_obj.name}")
            return {"FINISHED"}
        finally:
            _remove_temp_mesh_object(temp_obj)
            if created_muscle is None and created_slide_bone_name:
                core.delete_bone_by_name(context, rig_obj, created_slide_bone_name)


MESH_CLASSES = (
    XMRB_OT_toggle_mesh_muscle_creator,
    XMRB_OT_create_mesh_muscle,
)
