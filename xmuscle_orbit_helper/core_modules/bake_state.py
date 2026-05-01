def sanitize_key_token(text):
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text).strip("_") or "Muscle"


def build_key_name(prefix, muscle_name, index, total):
    digits = max(2, len(str(total)))
    token = sanitize_key_token(muscle_name)
    return f"{prefix}{token}_{index:0{digits}d}"


def format_duration_brief(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def estimate_bake_seconds(body_obj, samples, corrective_iterations, muscle_count=1):
    if body_obj is None or body_obj.type != "MESH":
        return 0.0
    vertex_count = len(body_obj.data.vertices)
    polygon_count = len(body_obj.data.polygons)
    base = 2.0
    per_iteration = (vertex_count * 0.00002) + (polygon_count * 0.000025)
    return base + (samples * corrective_iterations * max(1, muscle_count) * per_iteration)


def describe_bake_estimate(settings):
    body_obj = settings.body_object
    if body_obj is None or body_obj.type != "MESH":
        return "Estimate unavailable", ""
    vertex_count = len(body_obj.data.vertices)
    polygon_count = len(body_obj.data.polygons)
    muscle_count = max(1, len(get_selected_muscle_names(settings)))
    estimate_seconds = estimate_bake_seconds(body_obj, settings.samples, settings.corrective_iterations, muscle_count)
    primary = f"Estimated bake: about {format_duration_brief(estimate_seconds)}"
    secondary = f"{muscle_count} muscle(s) x {settings.samples} samples x {settings.corrective_iterations} iterations on {vertex_count:,} verts / {polygon_count:,} polys"
    return primary, secondary


def remove_existing_shape_keys(context, body_obj, prefix):
    if not body_obj.data.shape_keys:
        return

    key_blocks = list(body_obj.data.shape_keys.key_blocks)
    keys_to_remove = [key for key in key_blocks if key.name != "Basis" and key.name.startswith(prefix)]
    if not keys_to_remove:
        return

    ensure_object_mode(context)
    make_object_active(context, body_obj)
    for key in keys_to_remove:
        body_obj.active_shape_key_index = body_obj.data.shape_keys.key_blocks.find(key.name)
        bpy.ops.object.shape_key_remove(all=False)


def remove_shape_keys_for_muscle(context, body_obj, prefix, muscle_name):
    if not body_obj.data.shape_keys:
        return

    token_prefix = muscle_key_prefix(prefix, muscle_name)
    key_blocks = list(body_obj.data.shape_keys.key_blocks)
    keys_to_remove = [key for key in key_blocks if key.name != "Basis" and key.name.startswith(token_prefix)]
    if not keys_to_remove:
        return

    ensure_object_mode(context)
    make_object_active(context, body_obj)
    for key in keys_to_remove:
        body_obj.active_shape_key_index = body_obj.data.shape_keys.key_blocks.find(key.name)
        bpy.ops.object.shape_key_remove(all=False)


def remove_preview_actions(prefix, muscle_obj, body_obj, rig_obj):
    shape_name, rig_name = preview_action_names(prefix, muscle_obj, body_obj, rig_obj)
    if body_obj and body_obj.data.shape_keys and body_obj.data.shape_keys.animation_data:
        if body_obj.data.shape_keys.animation_data.action and body_obj.data.shape_keys.animation_data.action.name == shape_name:
            body_obj.data.shape_keys.animation_data.action = None
    if rig_obj and rig_obj.animation_data:
        if rig_obj.animation_data.action and rig_obj.animation_data.action.name == rig_name:
            rig_obj.animation_data.action = None
    for action_name in (shape_name, rig_name):
        action = bpy.data.actions.get(action_name)
        if action and action.users == 0:
            bpy.data.actions.remove(action)


def remove_body_links_for_muscle(body_obj, muscle_obj):
    if body_obj is None or muscle_obj is None:
        return
    to_remove = []
    for modifier in body_obj.modifiers:
        target = getattr(modifier, "target", None)
        if modifier.type == "SHRINKWRAP" and target == muscle_obj:
            to_remove.append(modifier)
    for modifier in to_remove:
        body_obj.modifiers.remove(modifier)


def delete_muscle_system(context, muscle_obj, key_prefix):
    if muscle_obj is None:
        return False, "Muscle not found"

    scene = context.scene
    muscle_name = muscle_obj.name
    slide_bone_name = muscle_obj.get("xmuscle_orbit_slide_bone", "")
    body_obj = infer_body_for_muscle(scene, muscle_obj)
    links = infer_links_for_muscle(scene, muscle_obj)
    rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
    collection = get_muscle_collection(muscle_obj)
    elements = list(iter_muscle_elements(muscle_obj))

    ensure_object_mode(context)
    if body_obj is not None:
        remove_shape_keys_for_muscle(context, body_obj, key_prefix, muscle_obj.name)
        remove_preview_actions(key_prefix, muscle_obj, body_obj, rig_obj)
        remove_body_links_for_muscle(body_obj, muscle_obj)

    for obj in elements:
        if bpy.data.objects.get(obj.name) is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    if collection is not None and bpy.data.collections.get(collection.name) is not None:
        bpy.data.collections.remove(collection)

    if slide_bone_name:
        delete_bone_by_name(context, rig_obj, slide_bone_name)

    return True, f"Deleted {muscle_name}"


def evaluate_body_vertices(context, body_obj):
    depsgraph = context.evaluated_depsgraph_get()
    context.view_layer.update()
    evaluated = body_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        coords = [vertex.co.copy() for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    return coords


def snapshot_body_modifiers(body_obj):
    return {
        modifier.name: {
            "show_viewport": modifier.show_viewport,
            "show_render": modifier.show_render,
        }
        for modifier in body_obj.modifiers
    }


def restore_body_modifiers(body_obj, state):
    for modifier in body_obj.modifiers:
        if modifier.name in state:
            modifier.show_viewport = state[modifier.name]["show_viewport"]
            modifier.show_render = state[modifier.name]["show_render"]


def snapshot_body_xmuscle_driver_mute_state(body_obj):
    state = {}
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return state

    modifier_names = {modifier.name for modifier in iter_body_xmuscle_modifiers(body_obj)}
    for fcurve in animation_data.drivers:
        if not any(fcurve.data_path.startswith(f'modifiers["{name}"]') for name in modifier_names):
            continue
        if not (fcurve.data_path.endswith("show_viewport") or fcurve.data_path.endswith("show_render")):
            continue
        state[fcurve.data_path] = fcurve.mute
    return state


def set_body_xmuscle_driver_mute_state(body_obj, mute):
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return

    modifier_names = {modifier.name for modifier in iter_body_xmuscle_modifiers(body_obj)}
    for fcurve in animation_data.drivers:
        if not any(fcurve.data_path.startswith(f'modifiers["{name}"]') for name in modifier_names):
            continue
        if not (fcurve.data_path.endswith("show_viewport") or fcurve.data_path.endswith("show_render")):
            continue
        fcurve.mute = mute


def restore_body_xmuscle_driver_mute_state(body_obj, state):
    animation_data = getattr(body_obj, "animation_data", None)
    if animation_data is None:
        return

    for fcurve in animation_data.drivers:
        if fcurve.data_path in state:
            fcurve.mute = state[fcurve.data_path]


def disable_unsupported_modifiers(body_obj, disable_subsurf):
    disabled = []
    for modifier in body_obj.modifiers:
        if not modifier.show_viewport:
            continue
        if modifier.type == "SUBSURF" and disable_subsurf:
            modifier.show_viewport = False
            modifier.show_render = False
            disabled.append(modifier.name)
            continue
        if modifier.type not in SUPPORTED_DEFORMATION_MODIFIERS:
            modifier.show_viewport = False
            modifier.show_render = False
            disabled.append(modifier.name)
    return disabled


def snapshot_xmuscle_body_modifiers(body_obj):
    return {
        modifier.name: {
            "show_viewport": modifier.show_viewport,
            "show_render": modifier.show_render,
        }
        for modifier in iter_body_xmuscle_modifiers(body_obj)
    }


def restore_xmuscle_body_modifiers(body_obj, state):
    for modifier in iter_body_xmuscle_modifiers(body_obj):
        if modifier.name in state:
            modifier.show_viewport = state[modifier.name]["show_viewport"]
            modifier.show_render = state[modifier.name]["show_render"]


def set_body_xmuscle_state(body_obj, enabled, solo_muscle=None):
    for modifier in iter_body_xmuscle_modifiers(body_obj):
        is_selected_muscle = getattr(getattr(modifier, "target", None), "name", None) == getattr(solo_muscle, "name", None)
        if modifier.type == "SHRINKWRAP":
            active = enabled and (solo_muscle is None or is_selected_muscle)
        else:
            active = enabled
        modifier.show_viewport = active
        modifier.show_render = active


def snapshot_selection(context):
    active = context.view_layer.objects.active
    selected = [obj for obj in context.selected_objects]
    return active, selected


def restore_selection(context, active, selected):
    for item in list(context.selected_objects):
        item.select_set(False)
    for obj in selected:
        if obj.name in bpy.data.objects:
            bpy.data.objects[obj.name].select_set(True)
    if active and active.name in bpy.data.objects:
        context.view_layer.objects.active = bpy.data.objects[active.name]


def snapshot_muscle_display_state(muscles):
    state = {}
    for muscle in muscles:
        state[muscle.name] = {
            "muscle_view3d": getattr(muscle, "Muscle_View3D", True),
            "micro_view3d": getattr(muscle, "Micro_Controller_View3D", False),
            "hide_viewport": muscle.hide_viewport,
        }
    return state


def restore_muscle_display_state(muscles, state):
    for muscle in muscles:
        values = state.get(muscle.name)
        if not values:
            continue
        muscle.Muscle_View3D = values["muscle_view3d"]
        muscle.Micro_Controller_View3D = values["micro_view3d"]
        muscle.hide_viewport = values["hide_viewport"]


def isolate_single_muscle(muscles, active_muscle):
    for muscle in muscles:
        is_active = muscle == active_muscle
        muscle.Muscle_View3D = is_active
        muscle.hide_viewport = not is_active
        if hasattr(muscle, "Micro_Controller_View3D"):
            muscle.Micro_Controller_View3D = is_active and getattr(muscle, "Micro_Controller", False)


def pose_bone_quaternion(pose_bone):
    if pose_bone.rotation_mode == "QUATERNION":
        quat = pose_bone.rotation_quaternion.copy()
    elif pose_bone.rotation_mode == "AXIS_ANGLE":
        axis_angle = pose_bone.rotation_axis_angle
        quat = Quaternion(axis_angle[1:4], axis_angle[0])
    else:
        quat = pose_bone.rotation_euler.to_quaternion()
    quat.normalize()
    return quat


def apply_quaternion_to_pose_bone(pose_bone, quat):
    if pose_bone.rotation_mode == "QUATERNION":
        pose_bone.rotation_quaternion = quat
    elif pose_bone.rotation_mode == "AXIS_ANGLE":
        axis, angle = quat.to_axis_angle()
        pose_bone.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pose_bone.rotation_euler = quat.to_euler(pose_bone.rotation_mode)


def sampled_quaternions(start_quat, end_quat, samples):
    if samples <= 1:
        return [end_quat.copy()]
    result = []
    for index in range(samples):
        factor = index / (samples - 1)
        result.append(start_quat.slerp(end_quat, factor))
    return result


def sampled_vectors(start_vector, end_vector, samples):
    if samples <= 1:
        return [Vector(end_vector)]
    start_vec = Vector(start_vector)
    end_vec = Vector(end_vector)
    result = []
    for index in range(samples):
        factor = index / (samples - 1)
        result.append(start_vec.lerp(end_vec, factor))
    return result


def get_preview_pose_bone(settings):
    rig_obj = settings.rig_object
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return None
    if not settings.bone_name or settings.bone_name not in rig_obj.pose.bones:
        return None
    return rig_obj.pose.bones[settings.bone_name]


def apply_preview(settings, context=None):
    if settings.preview_update_lock:
        return
    pose_bone = get_preview_pose_bone(settings)
    if pose_bone is None:
        return

    if settings.preview_enabled:
        if settings.use_captured_pose and settings.has_start_pose and settings.has_end_pose:
            start_quat = Quaternion(settings.start_quaternion)
            end_quat = Quaternion(settings.end_quaternion)
            quat = start_quat.slerp(end_quat, settings.preview_factor)
        else:
            start_vec = Vector(settings.start_rotation)
            end_vec = Vector(settings.end_rotation)
            euler = start_vec.lerp(end_vec, settings.preview_factor)
            quat = Euler(tuple(euler), "XYZ").to_quaternion()
        apply_quaternion_to_pose_bone(pose_bone, quat)
    else:
        quat = Quaternion(settings.preview_restore_quaternion)
        apply_quaternion_to_pose_bone(pose_bone, quat)

    context = context or bpy.context
    if context and context.view_layer:
        context.view_layer.update()


def preview_update(self, context):
    apply_preview(self, context)
    settings_changed(self, context)


def mute_xmuscle_update(self, context):
    body_obj = self.body_object
    if body_obj is None or self.mute_update_lock:
        return

    if self.mute_live_xmuscle:
        payload = {
            "live_state": snapshot_xmuscle_live_state(body_obj),
            "modifier_state": snapshot_xmuscle_body_modifiers(body_obj),
            "driver_mute_state": snapshot_body_xmuscle_driver_mute_state(body_obj),
        }
        self.saved_xmuscle_modifier_state = json.dumps(payload)
        set_body_xmuscle_driver_mute_state(body_obj, mute=True)
        set_xmuscle_live_state(body_obj, enabled=False)
        set_body_xmuscle_state(body_obj, enabled=False)
    else:
        if self.saved_xmuscle_modifier_state:
            try:
                payload = json.loads(self.saved_xmuscle_modifier_state)
            except json.JSONDecodeError:
                payload = {}
            restore_xmuscle_live_state(body_obj, payload.get("live_state", {}))
            restore_xmuscle_body_modifiers(body_obj, payload.get("modifier_state", {}))
            restore_body_xmuscle_driver_mute_state(body_obj, payload.get("driver_mute_state", {}))
    if context and context.view_layer:
        context.view_layer.update()
    settings_changed(self, context)


@contextmanager
def preserved_pose_bone_rotation(pose_bone):
    original_mode = pose_bone.rotation_mode
    original_euler = pose_bone.rotation_euler.copy()
    original_quaternion = pose_bone.rotation_quaternion.copy()
    original_axis_angle = tuple(pose_bone.rotation_axis_angle)
    try:
        yield
    finally:
        pose_bone.rotation_mode = original_mode
        pose_bone.rotation_euler = original_euler
        pose_bone.rotation_quaternion = original_quaternion
        pose_bone.rotation_axis_angle = original_axis_angle


def ensure_body_shape_keys(context, body_obj):
    if body_obj.data.shape_keys is None:
        make_object_active(context, body_obj)
        body_obj.shape_key_add(name="Basis", from_mix=False)


def clear_keyframe_values(key_blocks, frame):
    for key_block in key_blocks:
        key_block.value = 0.0
        key_block.keyframe_insert(data_path="value", frame=frame)


def snapshot_shape_key_values(body_obj):
    if body_obj.data.shape_keys is None:
        return {}
    return {key_block.name: key_block.value for key_block in body_obj.data.shape_keys.key_blocks}


def restore_shape_key_values(body_obj, values):
    if body_obj.data.shape_keys is None:
        return
    for key_block in body_obj.data.shape_keys.key_blocks:
        if key_block.name in values:
            key_block.value = values[key_block.name]


def zero_all_shape_keys(body_obj):
    if body_obj.data.shape_keys is None:
        return
    for key_block in body_obj.data.shape_keys.key_blocks:
        if key_block.name != "Basis":
            key_block.value = 0.0
    body_obj.active_shape_key_index = 0
    body_obj.show_only_shape_key = False


def update_mesh_state(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    ob.update_tag()
    bpy.context.view_layer.update()
    ob.data.update()


