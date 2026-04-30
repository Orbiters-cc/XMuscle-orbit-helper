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
        scene = context.scene
        muscles = core.iter_scene_muscles(context.scene)
        selected_names = set(core.get_selected_muscle_names(settings))
        effective_body = core.get_effective_body_object(settings, scene)

        col = layout.column(align=True)
        col.label(text="Add Muscle")
        if hasattr(scene, "Muscle_Name"):
            col.prop(scene, "Muscle_Name", text="Name")
        col.prop(settings, "body_object", text="Apply To")
        if settings.body_object is None and effective_body is not None:
            col.label(text=f"Default: {effective_body.name}", icon="INFO")
        col.prop(settings, "create_slide_driver")
        if settings.create_slide_driver:
            slide_col = col.column(align=True)
            slide_col.prop(settings, "slide_driver_slide_axis", text="Slide Axis")
            slide_col.prop(settings, "slide_driver_rotation_axes", text="Rotation Axes", expand=True)
            slide_col.prop(settings, "slide_driver_combine_mode", text="Combine")
            slide_col.prop(settings, "slide_driver_factor", text="Strength")
        col.prop(settings, "create_length_driver")
        if settings.create_length_driver:
            length_col = col.column(align=True)
            length_col.prop(settings, "length_driver_rotation_axes", text="Rotation Axes", expand=True)
            length_col.prop(settings, "length_driver_combine_mode", text="Combine")
            length_col.prop(settings, "length_driver_factor", text="Strength")
        add_row = col.row(align=True)
        add_op = add_row.operator("xmuscle_baker.add_muscle", text="Normal", icon="MESH_UVSPHERE")
        add_op.muscle_type = "BASIC"
        add_op = add_row.operator("xmuscle_baker.add_muscle", text="Curved", icon="MOD_CURVE")
        add_op.muscle_type = "STYLIZED"
        add_op = add_row.operator("xmuscle_baker.add_muscle", text="Flat", icon="MESH_PLANE")
        add_op.muscle_type = "STRIP"
        col.separator()
        col.label(text="Scene Muscles")
        if not muscles:
            col.label(text="No X-Muscles found")
            return

        for muscle_obj in muscles:
            box = col.box()
            header = box.row(align=True)
            is_selected = settings.muscle_name == muscle_obj.name
            in_group = muscle_obj.name in selected_names
            toggle = header.operator(
                "xmuscle_baker.toggle_muscle_selection",
                text="",
                icon="CHECKBOX_HLT" if in_group else "CHECKBOX_DEHLT",
            )
            toggle.muscle_name = muscle_obj.name
            if is_selected:
                header.prop(settings, "rename_buffer", text="", icon="FORCE_LENNARDJONES")
                rename_op = header.operator("xmuscle_baker.rename_muscle", text="", icon="CHECKMARK")
                rename_op.muscle_name = muscle_obj.name
            else:
                header.label(text=muscle_obj.name, icon="FORCE_LENNARDJONES")

            actions = box.row(align=True)
            key_prefix = core.get_saved_prefix_for_muscle(muscle_obj, settings.key_prefix)
            bake_op = actions.operator(
                "xmuscle_baker.bake_specific_muscle",
                text="Rebake" if core.muscle_has_baked_keys(context.scene, muscle_obj, key_prefix) else "Bake",
            )
            bake_op.muscle_name = muscle_obj.name
            preview_op = actions.operator("xmuscle_baker.activate_preview_animation", text="", icon="ACTION")
            preview_op.muscle_name = muscle_obj.name
            all_op = actions.operator("xmuscle_baker.select_muscle_elements", text="", icon="RESTRICT_SELECT_OFF")
            all_op.muscle_name = muscle_obj.name
            select_op = actions.operator("xmuscle_baker.select_muscle", text="Only")
            select_op.muscle_name = muscle_obj.name
            delete_op = actions.operator("xmuscle_baker.delete_muscle", text="", icon="TRASH")
            delete_op.muscle_name = muscle_obj.name
            if core.infer_body_for_muscle(context.scene, muscle_obj) is None:
                apply_row = box.row(align=True)
                apply_op = apply_row.operator("xmuscle_baker.apply_muscle", text="Apply", icon="MOD_SHRINKWRAP")
                apply_op.muscle_name = muscle_obj.name

        col.separator()
        col.label(text="Selected Muscle Group Settings")
        if selected_names:
            selected_label = ", ".join(core.get_selected_muscle_names(settings))
            col.label(text=selected_label)
        else:
            col.label(text="Select one or more muscles above to edit their shared saved settings")

        col.prop(settings, "body_object")

        row = col.row(align=True)
        row.prop(settings, "rig_object")
        row.operator("xmuscle_baker.guess_rig", text="", icon="EYEDROPPER")

        if settings.rig_object is not None:
            col.prop_search(settings, "bone_name", settings.rig_object.pose, "bones", text="Bone")
        else:
            col.prop(settings, "bone_name")

        if len(selected_names) == 1:
            col.separator()
            col.label(text="Selected Muscle Drivers")
            if settings.selected_has_slide_driver:
                slide_settings = col.column(align=True)
                slide_settings.label(text="Slide Driver")
                slide_settings.prop(settings, "selected_slide_driver_slide_axis", text="Slide Axis")
                slide_settings.prop(settings, "selected_slide_driver_rotation_axes", text="Rotation Axes", expand=True)
                slide_settings.prop(settings, "selected_slide_driver_combine_mode", text="Combine")
                slide_settings.prop(settings, "selected_slide_driver_rotation_space", text="Space")
                slide_settings.prop(settings, "selected_slide_driver_mode", text="Mode")
                slide_settings.prop(settings, "selected_slide_driver_factor", text="Strength")
                slide_zero_row = slide_settings.row(align=True)
                slide_zero_row.prop(settings, "selected_slide_driver_zero", text="Zero")
                slide_zero_row.operator("xmuscle_baker.capture_driver_zero", text="", icon="EYEDROPPER").target = "SLIDE"
            if settings.selected_has_length_driver:
                length_settings = col.column(align=True)
                length_settings.label(text="Base Length Driver")
                length_settings.prop(settings, "selected_length_driver_rotation_axes", text="Rotation Axes", expand=True)
                length_settings.prop(settings, "selected_length_driver_combine_mode", text="Combine")
                length_settings.prop(settings, "selected_length_driver_rotation_space", text="Space")
                length_settings.prop(settings, "selected_length_driver_mode", text="Mode")
                length_settings.prop(settings, "selected_length_driver_factor", text="Strength")
                length_zero_row = length_settings.row(align=True)
                length_zero_row.prop(settings, "selected_length_driver_zero", text="Zero")
                length_zero_row.operator("xmuscle_baker.capture_driver_zero", text="", icon="EYEDROPPER").target = "LENGTH"

        col.separator()
        col.label(text="Motion Capture")
        col.prop(settings, "use_captured_pose")

        row = col.row(align=True)
        row.operator("xmuscle_baker.store_preview_base", text="Store Current As Restore Pose", icon="ARMATURE_DATA")
        col.separator()
        start_row = col.row(align=True)
        start_row.operator("xmuscle_baker.capture_pose", text="Start", icon="IMPORT").target = "START"
        start_row.prop(settings, "start_rotation", text="")
        end_row = col.row(align=True)
        end_row.operator("xmuscle_baker.capture_pose", text="End", icon="EXPORT").target = "END"
        end_row.prop(settings, "end_rotation", text="")

        pose_info = col.column(align=True)
        pose_info.enabled = False
        pose_info.prop(settings, "has_start_pose", text="Start Pose Captured")
        pose_info.prop(settings, "has_end_pose", text="End Pose Captured")

        col.separator()
        col.label(text="Preview")
        col.prop(settings, "preview_enabled")
        col.prop(settings, "preview_factor", slider=True)
        col.prop(settings, "mute_live_xmuscle")
        visibility = col.row(align=True)
        visibility.label(text="Visibility")
        vis_op = visibility.operator("xmuscle_baker.set_muscle_visibility", text="Hide")
        vis_op.mode = "HIDE"
        vis_op = visibility.operator("xmuscle_baker.set_muscle_visibility", text="Show")
        vis_op.mode = "SHOW"
        vis_op = visibility.operator("xmuscle_baker.set_muscle_visibility", text="Show Through")
        vis_op.mode = "SHOW_THROUGH"

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
        col.operator("xmuscle_baker.bake_range", icon="SHAPEKEY_DATA", text="Bake Selected Muscle Group")


UI_CLASSES = (
    XMRB_PT_panel,
)
