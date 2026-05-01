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


class XMRB_OT_capture_driver_zero(bpy.types.Operator):
    bl_idname = "xmuscle_baker.capture_driver_zero"
    bl_label = "Capture Driver Zero"
    bl_description = "Use the current source bone rotation as the zero point for the selected muscle driver"

    target: EnumProperty(
        items=(
            ("SLIDE", "Slide", ""),
            ("LENGTH", "Length", ""),
        )
    )

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        muscle_obj = get_selected_scene_muscle(settings)
        if muscle_obj is None:
            self.report({"ERROR"}, "Select a single muscle first")
            return {"CANCELLED"}

        links = infer_links_for_muscle(context.scene, muscle_obj)
        rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
        if rig_obj is None or rig_obj.type != "ARMATURE":
            self.report({"ERROR"}, "Source rig not found for this muscle")
            return {"CANCELLED"}

        settings.sync_settings_lock = True
        try:
            if self.target == "SLIDE":
                slide_bone_name = get_muscle_slide_bone_name(muscle_obj)
                if not slide_bone_name or slide_bone_name not in rig_obj.pose.bones:
                    self.report({"ERROR"}, "Selected muscle has no slide driver bone")
                    return {"CANCELLED"}
                slide_bone = rig_obj.pose.bones[slide_bone_name]
                source_bone_name = slide_bone.get("xmuscle_slide_source_bone", "")
                raw_axes = slide_bone.get("xmuscle_rotation_axes", "")
                try:
                    axes = json.loads(raw_axes) if raw_axes else [slide_bone.get("xmuscle_rotation_axis", "X")]
                except Exception:
                    axes = [slide_bone.get("xmuscle_rotation_axis", "X")]
                combine_mode = slide_bone.get("xmuscle_rotation_combine_mode", "SUM")
                rotation_space = slide_bone.get("xmuscle_rotation_space", "LOCAL_SPACE")
                zero_value = sample_combined_bone_rotation(rig_obj, source_bone_name, axes, rotation_space, combine_mode)
                slide_bone["xmuscle_slide_zero"] = zero_value
                settings.selected_slide_driver_zero = zero_value
                rebuild_slide_driver(rig_obj, slide_bone_name)
            else:
                muscle_sys = get_muscle_system(muscle_obj)
                if muscle_sys is None or "xmuscle_length_driver_source_bone" not in muscle_sys.keys():
                    self.report({"ERROR"}, "Selected muscle has no Base Length driver")
                    return {"CANCELLED"}
                source_bone_name = muscle_sys.get("xmuscle_length_driver_source_bone", "")
                raw_axes = muscle_sys.get("xmuscle_length_driver_axes", "")
                try:
                    axes = json.loads(raw_axes) if raw_axes else [muscle_sys.get("xmuscle_length_driver_axis", "X")]
                except Exception:
                    axes = [muscle_sys.get("xmuscle_length_driver_axis", "X")]
                combine_mode = muscle_sys.get("xmuscle_length_driver_combine_mode", "SUM")
                rotation_space = muscle_sys.get("xmuscle_length_driver_space", "LOCAL_SPACE")
                zero_value = sample_combined_bone_rotation(rig_obj, source_bone_name, axes, rotation_space, combine_mode)
                muscle_sys["xmuscle_length_driver_zero"] = zero_value
                settings.selected_length_driver_zero = zero_value
                rebuild_base_length_driver(muscle_obj)
        finally:
            settings.sync_settings_lock = False

        save_selected_muscle_settings(settings)
        self.report({"INFO"}, "Driver zero captured from current pose")
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
    XMRB_OT_capture_driver_zero,
    XMRB_OT_store_preview_base,
    XMRB_OT_bake_range,
)
