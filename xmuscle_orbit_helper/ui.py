import bpy

from . import core


class XMRB_PT_panel(bpy.types.Panel):
    bl_label = "xmuscles orbit helper"
    bl_idname = "XMRB_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "X-Muscles Orbit"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.xmuscle_range_baker
        muscles = core.iter_scene_muscles(context.scene)

        col = layout.column(align=True)
        col.label(text="Scene Muscles")
        if not muscles:
            col.label(text="No X-Muscles found")
            return

        for muscle_obj in muscles:
            row = col.row(align=True)
            is_selected = settings.muscle_name == muscle_obj.name
            if is_selected:
                row.prop(settings, "rename_buffer", text="", icon="FORCE_LENNARDJONES")
                rename_op = row.operator("xmuscle_baker.rename_muscle", text="", icon="CHECKMARK")
                rename_op.muscle_name = muscle_obj.name
            else:
                row.label(text=muscle_obj.name, icon="FORCE_LENNARDJONES")
            key_prefix = core.get_saved_prefix_for_muscle(muscle_obj, settings.key_prefix)
            bake_op = row.operator(
                "xmuscle_baker.bake_specific_muscle",
                text="Rebake" if core.muscle_has_baked_keys(context.scene, muscle_obj, key_prefix) else "Bake",
            )
            bake_op.muscle_name = muscle_obj.name
            preview_op = row.operator("xmuscle_baker.activate_preview_animation", text="", icon="ACTION")
            preview_op.muscle_name = muscle_obj.name
            select_op = row.operator("xmuscle_baker.select_muscle", text="Select")
            select_op.muscle_name = muscle_obj.name

        col.separator()
        col.label(text="Selected Muscle Settings")
        if settings.muscle_name:
            selected_muscle = bpy.data.objects.get(settings.muscle_name)
            if selected_muscle is not None:
                col.label(text=selected_muscle.name, icon="FORCE_LENNARDJONES")
        else:
            col.label(text="Select a muscle above to edit its saved bake settings")

        col.prop(settings, "body_object")

        row = col.row(align=True)
        row.prop(settings, "rig_object")
        row.operator("xmuscle_baker.guess_rig", text="", icon="EYEDROPPER")

        if settings.rig_object is not None:
            col.prop_search(settings, "bone_name", settings.rig_object.pose, "bones", text="Bone")
        else:
            col.prop(settings, "bone_name")

        col.separator()
        col.label(text="Motion Capture")
        col.prop(settings, "use_captured_pose")

        row = col.row(align=True)
        row.operator("xmuscle_baker.capture_pose", text="Capture Start", icon="IMPORT").target = "START"
        row.operator("xmuscle_baker.capture_pose", text="Capture End", icon="EXPORT").target = "END"
        col.operator("xmuscle_baker.store_preview_base", text="Store Current As Restore Pose", icon="ARMATURE_DATA")

        pose_info = col.column(align=True)
        pose_info.enabled = False
        pose_info.prop(settings, "has_start_pose", text="Start Pose Captured")
        pose_info.prop(settings, "has_end_pose", text="End Pose Captured")

        col.separator()
        col.label(text="Manual Start Rotation")
        col.prop(settings, "start_rotation", text="")
        col.label(text="Manual End Rotation")
        col.prop(settings, "end_rotation", text="")

        col.separator()
        col.label(text="Preview")
        col.prop(settings, "preview_enabled")
        col.prop(settings, "preview_factor", slider=True)
        col.prop(settings, "mute_live_xmuscle")

        col.separator()
        col.label(text="Bake Output")
        col.prop(settings, "samples")
        col.prop(settings, "corrective_iterations")
        col.prop(settings, "key_prefix")
        estimate_primary, estimate_secondary = core.describe_bake_estimate(settings)
        col.label(text=estimate_primary, icon="TIME")
        if estimate_secondary:
            col.label(text=estimate_secondary)

        col.separator()
        col.label(text="Preview Animation")
        col.prop(settings, "auto_generate_animation")
        col.prop(settings, "animation_start_frame")
        col.prop(settings, "animation_length")

        col.separator()
        col.prop(settings, "show_advanced_options")
        if settings.show_advanced_options:
            advanced = col.column(align=True)
            advanced.label(text="Advanced Options")
            advanced.prop(settings, "replace_existing")
            advanced.prop(settings, "replace_target_on_rebake")
            advanced.prop(settings, "disable_subsurf")
            advanced.prop(settings, "auto_disable_unsupported_modifiers")
            advanced.prop(settings, "auto_apply_muscle")

        col.separator()
        col.operator("xmuscle_baker.bake_range", icon="SHAPEKEY_DATA", text="Bake Selected Muscle")


UI_CLASSES = (
    XMRB_PT_panel,
)
