def ensure_object_mode(context):
    active = context.view_layer.objects.active
    if active and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def apply_muscle_to_body(context, muscle_obj, body_obj):
    if muscle_obj is None or not getattr(muscle_obj, "Muscle_XID", False):
        return False, "Muscle not found"
    if body_obj is None or body_obj.type != "MESH":
        return False, "Choose a valid target mesh first"
    if infer_body_for_muscle(context.scene, muscle_obj) == body_obj:
        return True, f"{muscle_obj.name} is already applied to {body_obj.name}"
    if not hasattr(bpy.ops.muscle, "apply_musculature"):
        return False, "X-MUSCLE apply operator is not available"

    ensure_object_mode(context)
    previous_active, previous_selection = snapshot_selection(context)
    try:
        set_single_object_selection(context, muscle_obj)
        body_obj.select_set(True)
        context.view_layer.objects.active = body_obj
        bpy.ops.muscle.apply_musculature()
    except RuntimeError as exc:
        return False, f"Failed to apply {muscle_obj.name} to {body_obj.name}: {exc}"
    finally:
        restore_selection(context, previous_active, previous_selection)

    if infer_body_for_muscle(context.scene, muscle_obj) != body_obj:
        return False, f"{muscle_obj.name} was created but is still not linked to {body_obj.name}"
    return True, f"Applied {muscle_obj.name} to {body_obj.name}"


def create_slide_driver_bone(
    context,
    rig_obj,
    parent_bone_name,
    source_bone_name,
    helper_bone_name,
    slide_axis,
    rotation_axes,
    combine_mode,
    factor,
):
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return False, "No valid armature was found for slide driver creation"
    if parent_bone_name not in rig_obj.data.bones:
        return False, f"Parent bone {parent_bone_name} was not found"
    if source_bone_name not in rig_obj.data.bones:
        return False, f"Source bone {source_bone_name} was not found"

    previous_active, previous_selection = snapshot_selection(context)
    previous_mode = context.mode
    created_name = ""

    try:
        ensure_object_mode(context)
        set_single_object_selection(context, rig_obj)
        bpy.ops.object.mode_set(mode="EDIT")

        edit_bones = rig_obj.data.edit_bones
        parent_edit = edit_bones[parent_bone_name]
        created_name = unique_bone_name(rig_obj, helper_bone_name)
        helper_edit = edit_bones.new(created_name)
        helper_edit.parent = parent_edit
        helper_edit.use_connect = False
        helper_edit.use_deform = False
        helper_edit.head = parent_edit.tail.copy()

        parent_vector = parent_edit.tail - parent_edit.head
        if parent_vector.length < 1e-5:
            parent_vector = Vector((0.0, 0.05, 0.0))
        helper_edit.tail = helper_edit.head + parent_vector.normalized() * max(parent_vector.length * 0.25, 0.05)
        helper_edit.roll = parent_edit.roll

        bpy.ops.object.mode_set(mode="POSE")
        pose_bone = rig_obj.pose.bones[created_name]
        pose_bone.rotation_mode = "XYZ"
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone["xmuscle_slide_factor"] = factor
        pose_bone["xmuscle_slide_parent_bone"] = parent_bone_name
        pose_bone["xmuscle_slide_source_bone"] = source_bone_name
        pose_bone["xmuscle_slide_axis"] = slide_axis
        pose_bone["xmuscle_rotation_axes"] = json.dumps(normalize_axis_flags(rotation_axes))
        pose_bone["xmuscle_rotation_combine_mode"] = combine_mode
        pose_bone["xmuscle_rotation_space"] = "LOCAL_SPACE"
        pose_bone["xmuscle_slide_driver_mode"] = "RAW_DELTA"
        pose_bone["xmuscle_slide_zero"] = 0.0

        try:
            ui_data = pose_bone.id_properties_ui("xmuscle_slide_factor")
            ui_data.update(
                min=-100.0,
                max=100.0,
                soft_min=-20.0,
                soft_max=20.0,
                description="Strength of the X-Muscle Orbit slide driver",
            )
        except Exception:
            pass

        location_index = axis_index(slide_axis)
        fcurve = pose_bone.driver_add("location", location_index)
        driver = fcurve.driver
        driver.type = "SCRIPTED"
        while driver.variables:
            driver.variables.remove(driver.variables[0])

        rot_var = driver.variables.new()
        rot_var.name = "rot"
        rot_var.type = "TRANSFORMS"
        rot_target = rot_var.targets[0]
        rot_target.id = rig_obj
        rot_target.bone_target = source_bone_name
        rot_target.transform_type = rotation_transform_type(rotation_axis)
        rot_target.transform_space = "LOCAL_SPACE"

        factor_var = driver.variables.new()
        factor_var.name = "factor"
        factor_var.type = "SINGLE_PROP"
        factor_target = factor_var.targets[0]
        factor_target.id = rig_obj
        factor_target.data_path = f'pose.bones["{created_name}"]["xmuscle_slide_factor"]'

        driver.expression = "rot * factor"
    except RuntimeError as exc:
        return False, f"Failed to create slide driver bone: {exc}"
    finally:
        try:
            if previous_mode == "POSE" and context.object == rig_obj:
                bpy.ops.object.mode_set(mode="POSE")
            else:
                bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
        restore_selection(context, previous_active, previous_selection)

    return True, created_name


def create_base_length_driver(muscle_obj, rig_obj, source_bone_name, rotation_axes, combine_mode, factor):
    if muscle_obj is None or not getattr(muscle_obj, "Muscle_XID", False):
        return False, "Muscle not found"
    muscle_sys = muscle_obj.parent if muscle_obj.parent and muscle_obj.parent.type == "ARMATURE" else None
    if muscle_sys is None:
        return False, "Muscle system armature was not found"
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return False, "No valid source rig was found"
    if source_bone_name not in rig_obj.data.bones:
        return False, f"Source bone {source_bone_name} was not found"

    muscle_sys["xmuscle_length_driver_factor"] = factor
    muscle_sys["xmuscle_length_driver_base"] = float(getattr(muscle_sys, "Base_Length", 1.0))
    muscle_sys["xmuscle_length_driver_source_bone"] = source_bone_name
    muscle_sys["xmuscle_length_driver_axes"] = json.dumps(normalize_axis_flags(rotation_axes))
    muscle_sys["xmuscle_length_driver_combine_mode"] = combine_mode
    muscle_sys["xmuscle_length_driver_space"] = "LOCAL_SPACE"
    muscle_sys["xmuscle_length_driver_mode"] = "RAW_DELTA"
    muscle_sys["xmuscle_length_driver_zero"] = 0.0

    try:
        factor_ui = muscle_sys.id_properties_ui("xmuscle_length_driver_factor")
        factor_ui.update(
            min=-100.0,
            max=100.0,
            soft_min=-10.0,
            soft_max=10.0,
            description="Strength of the X-Muscle Orbit Base Length driver",
        )
        base_ui = muscle_sys.id_properties_ui("xmuscle_length_driver_base")
        base_ui.update(
            min=0.0,
            max=100.0,
            soft_min=0.25,
            soft_max=4.0,
            description="Base offset used by the X-Muscle Orbit Base Length driver",
        )
    except Exception:
        pass

    fcurve = muscle_sys.driver_add("Base_Length")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    while driver.variables:
        driver.variables.remove(driver.variables[0])

    rot_var = driver.variables.new()
    rot_var.name = "rot"
    rot_var.type = "TRANSFORMS"
    rot_target = rot_var.targets[0]
    rot_target.id = rig_obj
    rot_target.bone_target = source_bone_name
    rot_target.transform_type = rotation_transform_type(rotation_axis)
    rot_target.transform_space = "LOCAL_SPACE"

    factor_var = driver.variables.new()
    factor_var.name = "factor"
    factor_var.type = "SINGLE_PROP"
    factor_target = factor_var.targets[0]
    factor_target.id = muscle_sys
    factor_target.data_path = '["xmuscle_length_driver_factor"]'

    base_var = driver.variables.new()
    base_var.name = "base"
    base_var.type = "SINGLE_PROP"
    base_target = base_var.targets[0]
    base_target.id = muscle_sys
    base_target.data_path = '["xmuscle_length_driver_base"]'

    driver.expression = "base + rot * factor"
    return True, muscle_sys.name


def rebuild_slide_driver(rig_obj, slide_bone_name):
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return False, "No valid armature was found"
    if slide_bone_name not in rig_obj.pose.bones:
        return False, "Slide bone not found"

    pose_bone = rig_obj.pose.bones[slide_bone_name]
    source_bone_name = pose_bone.get("xmuscle_slide_source_bone", "")
    slide_axis = pose_bone.get("xmuscle_slide_axis", "Y")
    raw_axes = pose_bone.get("xmuscle_rotation_axes", "")
    try:
        rotation_axes = json.loads(raw_axes) if raw_axes else [pose_bone.get("xmuscle_rotation_axis", "X")]
    except json.JSONDecodeError:
        rotation_axes = [pose_bone.get("xmuscle_rotation_axis", "X")]
    combine_mode = pose_bone.get("xmuscle_rotation_combine_mode", "SUM")
    rotation_space = pose_bone.get("xmuscle_rotation_space", "LOCAL_SPACE")
    driver_mode = pose_bone.get("xmuscle_slide_driver_mode", "RAW_DELTA")

    if source_bone_name not in rig_obj.data.bones:
        return False, f"Source bone {source_bone_name} was not found"

    animation_data = rig_obj.animation_data
    if animation_data:
        for fcurve in list(animation_data.drivers):
            if fcurve.data_path == f'pose.bones["{slide_bone_name}"].location':
                rig_obj.driver_remove(fcurve.data_path, fcurve.array_index)

    location_index = axis_index(slide_axis)
    fcurve = pose_bone.driver_add("location", location_index)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    while driver.variables:
        driver.variables.remove(driver.variables[0])

    axes = normalize_axis_flags(rotation_axes)
    var_names = []
    for axis_name in axes:
        rot_var = driver.variables.new()
        var_name = f"r{axis_name.lower()}"
        rot_var.name = var_name
        rot_var.type = "TRANSFORMS"
        rot_target = rot_var.targets[0]
        rot_target.id = rig_obj
        rot_target.bone_target = source_bone_name
        rot_target.transform_type = rotation_transform_type(axis_name)
        rot_target.transform_space = rotation_space
        var_names.append(var_name)

    factor_var = driver.variables.new()
    factor_var.name = "factor"
    factor_var.type = "SINGLE_PROP"
    factor_target = factor_var.targets[0]
    factor_target.id = rig_obj
    factor_target.data_path = f'pose.bones["{slide_bone_name}"]["xmuscle_slide_factor"]'

    zero_var = driver.variables.new()
    zero_var.name = "zero"
    zero_var.type = "SINGLE_PROP"
    zero_target = zero_var.targets[0]
    zero_target.id = rig_obj
    zero_target.data_path = f'pose.bones["{slide_bone_name}"]["xmuscle_slide_zero"]'

    driver.expression = driver_expression_for_mode(driver_mode, combined_rotation_expression(var_names, combine_mode))
    return True, slide_bone_name


def rebuild_base_length_driver(muscle_obj):
    muscle_sys = get_muscle_system(muscle_obj)
    if muscle_sys is None:
        return False, "Muscle system armature was not found"

    source_bone_name = muscle_sys.get("xmuscle_length_driver_source_bone", "")
    raw_axes = muscle_sys.get("xmuscle_length_driver_axes", "")
    try:
        rotation_axes = json.loads(raw_axes) if raw_axes else [muscle_sys.get("xmuscle_length_driver_axis", "X")]
    except json.JSONDecodeError:
        rotation_axes = [muscle_sys.get("xmuscle_length_driver_axis", "X")]
    combine_mode = muscle_sys.get("xmuscle_length_driver_combine_mode", "SUM")
    rotation_space = muscle_sys.get("xmuscle_length_driver_space", "LOCAL_SPACE")
    driver_mode = muscle_sys.get("xmuscle_length_driver_mode", "RAW_DELTA")
    links = infer_links_for_muscle(bpy.context.scene, muscle_obj)
    rig_obj = bpy.data.objects.get(links["rig_object_name"]) if links.get("rig_object_name") else None
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return False, "Source rig was not found"
    if source_bone_name not in rig_obj.data.bones:
        return False, f"Source bone {source_bone_name} was not found"

    animation_data = muscle_sys.animation_data
    if animation_data:
        for fcurve in list(animation_data.drivers):
            if fcurve.data_path == "Base_Length":
                muscle_sys.driver_remove("Base_Length")
                break

    fcurve = muscle_sys.driver_add("Base_Length")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    while driver.variables:
        driver.variables.remove(driver.variables[0])

    axes = normalize_axis_flags(rotation_axes)
    var_names = []
    for axis_name in axes:
        rot_var = driver.variables.new()
        var_name = f"r{axis_name.lower()}"
        rot_var.name = var_name
        rot_var.type = "TRANSFORMS"
        rot_target = rot_var.targets[0]
        rot_target.id = rig_obj
        rot_target.bone_target = source_bone_name
        rot_target.transform_type = rotation_transform_type(axis_name)
        rot_target.transform_space = rotation_space
        var_names.append(var_name)

    factor_var = driver.variables.new()
    factor_var.name = "factor"
    factor_var.type = "SINGLE_PROP"
    factor_target = factor_var.targets[0]
    factor_target.id = muscle_sys
    factor_target.data_path = '["xmuscle_length_driver_factor"]'

    base_var = driver.variables.new()
    base_var.name = "base"
    base_var.type = "SINGLE_PROP"
    base_target = base_var.targets[0]
    base_target.id = muscle_sys
    base_target.data_path = '["xmuscle_length_driver_base"]'

    zero_var = driver.variables.new()
    zero_var.name = "zero"
    zero_var.type = "SINGLE_PROP"
    zero_target = zero_var.targets[0]
    zero_target.id = muscle_sys
    zero_target.data_path = '["xmuscle_length_driver_zero"]'

    driver.expression = f"base + ({driver_expression_for_mode(driver_mode, combined_rotation_expression(var_names, combine_mode))})"
    return True, muscle_sys.name


def delete_bone_by_name(context, rig_obj, bone_name):
    if rig_obj is None or rig_obj.type != "ARMATURE" or not bone_name:
        return
    if bone_name not in rig_obj.data.bones:
        return

    previous_active, previous_selection = snapshot_selection(context)
    try:
        ensure_object_mode(context)
        set_single_object_selection(context, rig_obj)
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bone = rig_obj.data.edit_bones.get(bone_name)
        if edit_bone is not None:
            rig_obj.data.edit_bones.remove(edit_bone)
    except RuntimeError:
        pass
    finally:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
        restore_selection(context, previous_active, previous_selection)


