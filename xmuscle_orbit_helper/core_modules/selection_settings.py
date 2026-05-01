def get_selection_settings_store(scene):
    raw = scene.get(SELECTION_SETTINGS_PROP, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def save_selection_settings_store(scene, data):
    scene[SELECTION_SETTINGS_PROP] = json.dumps(data)


def remove_deleted_muscle_from_selection_store(scene, muscle_name):
    store = get_selection_settings_store(scene)
    cleaned = {}
    for key, value in store.items():
        names = [item for item in key.split("||") if item]
        filtered = [name for name in names if name != muscle_name]
        if not filtered:
            continue
        cleaned[selection_key_for_names(filtered)] = value
    save_selection_settings_store(scene, cleaned)


def serialize_settings(settings):
    return {
        "body_object_name": settings.body_object.name if settings.body_object else "",
        "rig_object_name": settings.rig_object.name if settings.rig_object else "",
        "bone_name": settings.bone_name,
        "start_rotation": list(settings.start_rotation),
        "end_rotation": list(settings.end_rotation),
        "samples": settings.samples,
        "corrective_iterations": settings.corrective_iterations,
        "key_prefix": settings.key_prefix,
        "replace_existing": settings.replace_existing,
        "replace_target_on_rebake": settings.replace_target_on_rebake,
        "disable_subsurf": settings.disable_subsurf,
        "auto_apply_muscle": settings.auto_apply_muscle,
        "create_slide_driver": settings.create_slide_driver,
        "slide_driver_slide_axis": settings.slide_driver_slide_axis,
        "slide_driver_rotation_axes": sorted(normalize_axis_flags(settings.slide_driver_rotation_axes)),
        "slide_driver_combine_mode": settings.slide_driver_combine_mode,
        "slide_driver_factor": settings.slide_driver_factor,
        "create_length_driver": settings.create_length_driver,
        "length_driver_rotation_axes": sorted(normalize_axis_flags(settings.length_driver_rotation_axes)),
        "length_driver_combine_mode": settings.length_driver_combine_mode,
        "length_driver_factor": settings.length_driver_factor,
        "auto_disable_unsupported_modifiers": settings.auto_disable_unsupported_modifiers,
        "use_captured_pose": settings.use_captured_pose,
        "has_start_pose": settings.has_start_pose,
        "has_end_pose": settings.has_end_pose,
        "start_quaternion": list(settings.start_quaternion),
        "end_quaternion": list(settings.end_quaternion),
        "preview_enabled": settings.preview_enabled,
        "preview_factor": settings.preview_factor,
        "preview_restore_quaternion": list(settings.preview_restore_quaternion),
        "auto_generate_animation": settings.auto_generate_animation,
        "mute_live_xmuscle": settings.mute_live_xmuscle,
        "animation_start_frame": settings.animation_start_frame,
        "animation_length": settings.animation_length,
    }


def save_selected_muscle_settings(settings):
    muscle_names = get_selected_muscle_names(settings)
    if not muscle_names:
        return
    payload = json.dumps(serialize_settings(settings))
    if len(muscle_names) == 1:
        muscle_obj = bpy.data.objects.get(muscle_names[0])
        if muscle_obj is not None:
            muscle_obj[MUSCLE_SETTINGS_PROP] = payload
    scene = bpy.context.scene
    store = get_selection_settings_store(scene)
    store[selection_key_for_names(muscle_names)] = json.loads(payload)
    save_selection_settings_store(scene, store)


def apply_saved_settings(settings, payload):
    settings.sync_settings_lock = True
    try:
        body_name = payload.get("body_object_name", "")
        rig_name = payload.get("rig_object_name", "")
        settings.body_object = bpy.data.objects.get(body_name) if body_name else None
        settings.rig_object = bpy.data.objects.get(rig_name) if rig_name else None
        settings.bone_name = payload.get("bone_name", "")
        start_rotation = payload.get("start_rotation")
        end_rotation = payload.get("end_rotation")
        if start_rotation is None or end_rotation is None:
            axis = payload.get("rotation_axis", "X")
            axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 0)
            migrated_start = [0.0, 0.0, 0.0]
            migrated_end = [0.0, 0.0, 0.0]
            migrated_start[axis_index] = payload.get("start_angle", 0.0)
            migrated_end[axis_index] = payload.get("end_angle", math.radians(90.0))
            start_rotation = migrated_start
            end_rotation = migrated_end
        settings.start_rotation = start_rotation
        settings.end_rotation = end_rotation
        settings.samples = payload.get("samples", settings.samples)
        settings.corrective_iterations = payload.get("corrective_iterations", settings.corrective_iterations)
        settings.key_prefix = payload.get("key_prefix", settings.key_prefix)
        settings.replace_existing = payload.get("replace_existing", settings.replace_existing)
        settings.replace_target_on_rebake = payload.get("replace_target_on_rebake", settings.replace_target_on_rebake)
        settings.disable_subsurf = payload.get("disable_subsurf", settings.disable_subsurf)
        settings.auto_apply_muscle = payload.get("auto_apply_muscle", settings.auto_apply_muscle)
        settings.create_slide_driver = payload.get("create_slide_driver", settings.create_slide_driver)
        settings.slide_driver_slide_axis = payload.get("slide_driver_slide_axis", settings.slide_driver_slide_axis)
        legacy_slide_axis = payload.get("slide_driver_rotation_axis", "X")
        slide_axes = payload.get("slide_driver_rotation_axes", [legacy_slide_axis])
        settings.slide_driver_rotation_axes = encode_axis_flags(slide_axes)
        settings.slide_driver_combine_mode = payload.get("slide_driver_combine_mode", settings.slide_driver_combine_mode)
        settings.slide_driver_factor = payload.get("slide_driver_factor", settings.slide_driver_factor)
        settings.create_length_driver = payload.get("create_length_driver", settings.create_length_driver)
        legacy_length_axis = payload.get("length_driver_rotation_axis", "X")
        length_axes = payload.get("length_driver_rotation_axes", [legacy_length_axis])
        settings.length_driver_rotation_axes = encode_axis_flags(length_axes)
        settings.length_driver_combine_mode = payload.get("length_driver_combine_mode", settings.length_driver_combine_mode)
        settings.length_driver_factor = payload.get("length_driver_factor", settings.length_driver_factor)
        settings.auto_disable_unsupported_modifiers = payload.get("auto_disable_unsupported_modifiers", settings.auto_disable_unsupported_modifiers)
        settings.use_captured_pose = payload.get("use_captured_pose", settings.use_captured_pose)
        settings.has_start_pose = payload.get("has_start_pose", settings.has_start_pose)
        settings.has_end_pose = payload.get("has_end_pose", settings.has_end_pose)
        settings.start_quaternion = payload.get("start_quaternion", list(settings.start_quaternion))
        settings.end_quaternion = payload.get("end_quaternion", list(settings.end_quaternion))
        settings.preview_enabled = payload.get("preview_enabled", False)
        settings.preview_factor = payload.get("preview_factor", settings.preview_factor)
        settings.preview_restore_quaternion = payload.get("preview_restore_quaternion", list(settings.preview_restore_quaternion))
        settings.auto_generate_animation = payload.get("auto_generate_animation", settings.auto_generate_animation)
        settings.mute_live_xmuscle = payload.get("mute_live_xmuscle", False)
        settings.animation_start_frame = payload.get("animation_start_frame", settings.animation_start_frame)
        settings.animation_length = payload.get("animation_length", settings.animation_length)
        if settings.body_object is None:
            ensure_default_body_object(settings, bpy.context.scene)
    finally:
        settings.sync_settings_lock = False


def infer_links_for_group(scene, muscle_names):
    muscles = [bpy.data.objects.get(name) for name in muscle_names]
    muscles = [obj for obj in muscles if obj is not None]
    if not muscles:
        return {}

    inferred = [infer_links_for_muscle(scene, muscle) for muscle in muscles]
    body_names = {item.get("body_object_name", "") for item in inferred if item.get("body_object_name", "")}
    rig_names = {item.get("rig_object_name", "") for item in inferred if item.get("rig_object_name", "")}
    bone_names = {item.get("bone_name", "") for item in inferred if item.get("bone_name", "")}
    return {
        "body_object_name": next(iter(body_names)) if len(body_names) == 1 else "",
        "rig_object_name": next(iter(rig_names)) if len(rig_names) == 1 else "",
        "bone_name": next(iter(bone_names)) if len(bone_names) == 1 else "",
    }


def load_settings_for_selection(settings, muscle_names):
    scene = bpy.context.scene
    payload = {}
    group_key = selection_key_for_names(muscle_names)
    store = get_selection_settings_store(scene)
    if group_key in store and isinstance(store[group_key], dict):
        payload = store[group_key]
    elif len(muscle_names) == 1:
        muscle_obj = bpy.data.objects.get(muscle_names[0])
        if muscle_obj is not None and muscle_obj.get(MUSCLE_SETTINGS_PROP):
            try:
                payload = json.loads(muscle_obj[MUSCLE_SETTINGS_PROP])
            except json.JSONDecodeError:
                payload = {}

    inferred = infer_links_for_group(scene, muscle_names)
    if not payload:
        payload = inferred
    else:
        payload.setdefault("body_object_name", inferred.get("body_object_name", ""))
        payload.setdefault("rig_object_name", inferred.get("rig_object_name", ""))
        payload.setdefault("bone_name", inferred.get("bone_name", ""))

    apply_saved_settings(settings, payload)
    primary = bpy.data.objects.get(settings.muscle_name) or (bpy.data.objects.get(muscle_names[0]) if muscle_names else None)
    settings.rename_buffer = primary.name if primary is not None else ""
    sync_selected_driver_settings(settings, primary if len(muscle_names) == 1 else None)
    save_selected_muscle_settings(settings)


def load_settings_for_muscle(settings, muscle_obj):
    payload = {}
    if muscle_obj is not None and muscle_obj.get(MUSCLE_SETTINGS_PROP):
        try:
            payload = json.loads(muscle_obj[MUSCLE_SETTINGS_PROP])
        except json.JSONDecodeError:
            payload = {}
    if not payload:
        payload = infer_links_for_muscle(bpy.context.scene, muscle_obj)
    else:
        inferred = infer_links_for_muscle(bpy.context.scene, muscle_obj)
        payload.setdefault("body_object_name", inferred.get("body_object_name", ""))
        payload.setdefault("rig_object_name", inferred.get("rig_object_name", ""))
        payload.setdefault("bone_name", inferred.get("bone_name", ""))
    apply_saved_settings(settings, payload)
    settings.rename_buffer = muscle_obj.name if muscle_obj is not None else ""
    sync_selected_driver_settings(settings, muscle_obj)
    save_selected_muscle_settings(settings)


def sync_selected_driver_settings(settings, muscle_obj=None):
    muscle_obj = muscle_obj or get_selected_scene_muscle(settings)
    settings.sync_settings_lock = True
    try:
        slide_bone_name = get_muscle_slide_bone_name(muscle_obj)
        slide_bone = None
        rig_obj = None
        if slide_bone_name:
            links = infer_links_for_muscle(bpy.context.scene, muscle_obj)
            rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
            if rig_obj and slide_bone_name in rig_obj.pose.bones:
                slide_bone = rig_obj.pose.bones[slide_bone_name]

        settings.selected_has_slide_driver = slide_bone is not None
        settings.selected_slide_driver_slide_axis = slide_bone.get("xmuscle_slide_axis", "Y") if slide_bone else "Y"
        slide_axes_raw = slide_bone.get("xmuscle_rotation_axes", "") if slide_bone else ""
        try:
            slide_axes = json.loads(slide_axes_raw) if slide_axes_raw else [slide_bone.get("xmuscle_rotation_axis", "X")]
        except Exception:
            slide_axes = [slide_bone.get("xmuscle_rotation_axis", "X")] if slide_bone else ["X"]
        settings.selected_slide_driver_rotation_axes = encode_axis_flags(slide_axes)
        settings.selected_slide_driver_combine_mode = slide_bone.get("xmuscle_rotation_combine_mode", "SUM") if slide_bone else "SUM"
        settings.selected_slide_driver_factor = float(slide_bone.get("xmuscle_slide_factor", 1.0)) if slide_bone else 1.0
        settings.selected_slide_driver_rotation_space = slide_bone.get("xmuscle_rotation_space", "LOCAL_SPACE") if slide_bone else "LOCAL_SPACE"
        settings.selected_slide_driver_mode = slide_bone.get("xmuscle_slide_driver_mode", "RAW_DELTA") if slide_bone else "RAW_DELTA"
        settings.selected_slide_driver_zero = float(slide_bone.get("xmuscle_slide_zero", 0.0)) if slide_bone else 0.0

        muscle_sys = get_muscle_system(muscle_obj)
        has_length = muscle_sys is not None and ("xmuscle_length_driver_axis" in muscle_sys.keys() or "xmuscle_length_driver_axes" in muscle_sys.keys())
        settings.selected_has_length_driver = has_length
        length_axes_raw = muscle_sys.get("xmuscle_length_driver_axes", "") if has_length else ""
        try:
            length_axes = json.loads(length_axes_raw) if length_axes_raw else [muscle_sys.get("xmuscle_length_driver_axis", "X")]
        except Exception:
            length_axes = [muscle_sys.get("xmuscle_length_driver_axis", "X")] if has_length else ["X"]
        settings.selected_length_driver_rotation_axes = encode_axis_flags(length_axes)
        settings.selected_length_driver_combine_mode = muscle_sys.get("xmuscle_length_driver_combine_mode", "SUM") if has_length else "SUM"
        settings.selected_length_driver_factor = float(muscle_sys.get("xmuscle_length_driver_factor", 0.15)) if has_length else 0.15
        settings.selected_length_driver_rotation_space = muscle_sys.get("xmuscle_length_driver_space", "LOCAL_SPACE") if has_length else "LOCAL_SPACE"
        settings.selected_length_driver_mode = muscle_sys.get("xmuscle_length_driver_mode", "RAW_DELTA") if has_length else "RAW_DELTA"
        settings.selected_length_driver_zero = float(muscle_sys.get("xmuscle_length_driver_zero", 0.0)) if has_length else 0.0
    finally:
        settings.sync_settings_lock = False


def find_preview_actions(settings, muscle_obj=None):
    muscle_obj = muscle_obj or get_selected_scene_muscle(settings)
    if muscle_obj is None:
        return None, None, None, None
    scene = bpy.context.scene
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    links = infer_links_for_muscle(scene, muscle_obj)
    rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
    shape_action_name, rig_action_name = preview_action_names(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    return bpy.data.actions.get(shape_action_name), bpy.data.actions.get(rig_action_name), body_obj, rig_obj


