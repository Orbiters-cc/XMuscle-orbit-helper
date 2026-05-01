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
        settings = context.scene.xmuscle_range_baker
        rig_obj = find_armature_for_autoaim(context)
        selected_names = []
        active_name = ""
        use_autoaim = False
        ordered_bones = []
        created_slide_bone_name = ""
        length_driver_source_bone_name = ""
        operator_map = {
            "BASIC": bpy.ops.muscle.add_basic_muscle,
            "STYLIZED": bpy.ops.muscle.add_muscle,
            "STRIP": bpy.ops.muscle.add_strip_muscle,
        }

        if rig_obj is not None:
            selected_names, active_name = get_selected_bone_names_for_autoaim(context, rig_obj)
            use_autoaim = len(selected_names) == 2 and bool(active_name)
            if use_autoaim:
                ordered_bones = [name for name in selected_names if name != active_name]
                ordered_bones.append(active_name)
                if len(ordered_bones) == 2:
                    length_driver_source_bone_name = ordered_bones[1]
                if settings.create_slide_driver:
                    helper_base_name = f"{getattr(context.scene, 'Muscle_Name', 'Muscle')}_slide"
                    ok, slide_result = create_slide_driver_bone(
                        context,
                        rig_obj,
                        ordered_bones[0],
                        ordered_bones[1],
                        helper_base_name,
                        settings.slide_driver_slide_axis,
                        settings.slide_driver_rotation_axes,
                        settings.slide_driver_combine_mode,
                        settings.slide_driver_factor,
                    )
                    if not ok:
                        self.report({"ERROR"}, slide_result)
                        return {"CANCELLED"}
                    created_slide_bone_name = slide_result
                    selected_names = [ordered_bones[0], created_slide_bone_name]
                    active_name = created_slide_bone_name

                ok, result = prepare_autoaim_pose_selection(context, rig_obj, selected_names, active_name)
                if not ok:
                    if created_slide_bone_name:
                        delete_bone_by_name(context, rig_obj, created_slide_bone_name)
                    self.report({"ERROR"}, result)
                    return {"CANCELLED"}
                ordered_bones = result
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
            if created_slide_bone_name:
                delete_bone_by_name(context, rig_obj, created_slide_bone_name)
            self.report({"ERROR"}, f"X-Muscle creation failed: {exc}")
            return {"CANCELLED"}
        finally:
            scene.Create_Type = previous_create_type

        after_muscles = iter_scene_muscles(scene)
        created = [obj for obj in after_muscles if obj.name not in before_names]
        if created:
            created_muscle = created[-1]
            if created_slide_bone_name:
                created_muscle["xmuscle_orbit_slide_bone"] = created_slide_bone_name
            set_muscle_visibility_mode(created_muscle, "SHOW_THROUGH")
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
                report_message = f"Created {created_muscle.name} ({creation_mode}) and applied it to {target_body.name}"
            else:
                report_message = f"Created {created_muscle.name} ({creation_mode}), but no target Body mesh is set"

            if created_slide_bone_name:
                report_message += f"; muscle attached to slide bone {created_slide_bone_name}"
            elif settings.create_slide_driver:
                report_message += "; slide bone skipped (requires a valid 2-bone Auto Aim selection)"

            if settings.create_length_driver:
                if use_autoaim and rig_obj is not None and length_driver_source_bone_name:
                    ok, length_result = create_base_length_driver(
                        created_muscle,
                        rig_obj,
                        length_driver_source_bone_name,
                        settings.length_driver_rotation_axes,
                        settings.length_driver_combine_mode,
                        settings.length_driver_factor,
                    )
                    if ok:
                        report_message += f"; Base Length driver added on {length_result}"
                    else:
                        report_message += f"; Base Length driver skipped ({length_result})"
                else:
                    report_message += "; Base Length driver skipped (requires a valid 2-bone Auto Aim selection)"

            self.report({"INFO"} if target_body is not None else {"WARNING"}, report_message)
        else:
            if created_slide_bone_name:
                delete_bone_by_name(context, rig_obj, created_slide_bone_name)
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


