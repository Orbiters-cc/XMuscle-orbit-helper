def settings_changed(self, _context):
    if getattr(self, "sync_settings_lock", False):
        return
    save_selected_muscle_settings(self)


def selected_driver_settings_changed(self, _context):
    if getattr(self, "sync_settings_lock", False):
        return
    muscle_obj = get_selected_scene_muscle(self)
    if muscle_obj is None:
        return

    slide_bone_name = get_muscle_slide_bone_name(muscle_obj)
    if self.selected_has_slide_driver and slide_bone_name:
        links = infer_links_for_muscle(bpy.context.scene, muscle_obj)
        rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
        if rig_obj and slide_bone_name in rig_obj.pose.bones:
            pose_bone = rig_obj.pose.bones[slide_bone_name]
            pose_bone["xmuscle_slide_axis"] = self.selected_slide_driver_slide_axis
            pose_bone["xmuscle_rotation_axes"] = json.dumps(normalize_axis_flags(self.selected_slide_driver_rotation_axes))
            pose_bone["xmuscle_rotation_combine_mode"] = self.selected_slide_driver_combine_mode
            pose_bone["xmuscle_slide_factor"] = self.selected_slide_driver_factor
            pose_bone["xmuscle_rotation_space"] = self.selected_slide_driver_rotation_space
            pose_bone["xmuscle_slide_driver_mode"] = self.selected_slide_driver_mode
            pose_bone["xmuscle_slide_zero"] = self.selected_slide_driver_zero
            rebuild_slide_driver(rig_obj, slide_bone_name)

    muscle_sys = get_muscle_system(muscle_obj)
    if self.selected_has_length_driver and muscle_sys is not None:
        muscle_sys["xmuscle_length_driver_axes"] = json.dumps(normalize_axis_flags(self.selected_length_driver_rotation_axes))
        muscle_sys["xmuscle_length_driver_combine_mode"] = self.selected_length_driver_combine_mode
        muscle_sys["xmuscle_length_driver_factor"] = self.selected_length_driver_factor
        muscle_sys["xmuscle_length_driver_space"] = self.selected_length_driver_rotation_space
        muscle_sys["xmuscle_length_driver_mode"] = self.selected_length_driver_mode
        muscle_sys["xmuscle_length_driver_zero"] = self.selected_length_driver_zero
        rebuild_base_length_driver(muscle_obj)
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
        if obj.type == "MESH":
            obj.hide_viewport = hidden
        else:
            obj.hide_viewport = False
        if hasattr(obj, "show_in_front"):
            obj.show_in_front = show_through and not hidden


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


