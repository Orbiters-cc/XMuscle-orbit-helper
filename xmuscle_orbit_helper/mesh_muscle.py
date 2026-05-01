import bpy
from bpy.props import StringProperty

from . import core
from .mesh_conversion import convert_mesh_source_to_muscle


def _ordered_autoaim_bones(context, rig_obj):
    if rig_obj is None:
        return [], ""
    selected_names, active_name = core.get_selected_bone_names_for_autoaim(context, rig_obj)
    if len(selected_names) != 2 or not active_name:
        return [], ""
    ordered = [name for name in selected_names if name != active_name]
    ordered.append(active_name)
    return ordered, active_name

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

            body_obj = core.ensure_default_body_object(settings, scene)
            muscle_obj, error = convert_mesh_source_to_muscle(
                context,
                settings,
                source_obj,
                rig_obj=rig_obj if use_autoaim else None,
                attach_bones=attach_bones if use_autoaim else None,
                length_driver_source_bone_name=length_driver_source_bone_name if use_autoaim else "",
                created_slide_bone_name=created_slide_bone_name,
                body_obj=body_obj,
            )
            if muscle_obj is None:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            created_muscle = muscle_obj
            if error:
                self.report({"WARNING"}, error)
            self.report({"INFO"}, f"Created mesh muscle {muscle_obj.name}")
            return {"FINISHED"}
        finally:
            if created_muscle is None and created_slide_bone_name:
                core.delete_bone_by_name(context, rig_obj, created_slide_bone_name)


MESH_CLASSES = (
    XMRB_OT_toggle_mesh_muscle_creator,
    XMRB_OT_create_mesh_muscle,
)
