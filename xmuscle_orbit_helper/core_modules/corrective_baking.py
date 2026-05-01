def corrective_reset_transform(ob):
    ob.matrix_local.identity()


def corrective_extract_vert_coords(verts):
    return [vertex.co.copy() for vertex in verts]


def corrective_extract_mapped_coords(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eobj = ob.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eobj)
    try:
        arr = [vertex.co.copy() for vertex in mesh.vertices]
    finally:
        mesh.user_clear()
        bpy.data.meshes.remove(mesh)
    update_mesh_state(ob)
    return arr


def corrective_apply_vert_coords(ob, mesh, coords):
    for index, vertex in enumerate(mesh):
        vertex.co = coords[index]
    update_mesh_state(ob)


def duplicate_flatten_modifiers(context, ob, name):
    depsgraph = context.evaluated_depsgraph_get()
    eobj = ob.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(eobj)
    new_object = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(new_object)
    return new_object


def add_corrective_pose_shape(source, target, iterations=12, progress_callback=None):
    threshold = 1e-16

    mesh_target = target.data
    mesh_source = source.data

    original_matrix_local = target.matrix_local.copy()
    corrective_reset_transform(target)

    if not mesh_target.shape_keys:
        basis = target.shape_key_add()
        basis.name = "Basis"
        update_mesh_state(target)
        target.active_shape_key_index = 0

    target.show_only_shape_key = False
    target.active_shape_key_index = 0

    new_shapekey = target.shape_key_add()
    update_mesh_state(target)
    target.active_shape_key_index = target.data.shape_keys.key_blocks.find(new_shapekey.name)
    target.show_only_shape_key = True

    vertex_group = target.active_shape_key.vertex_group
    target.active_shape_key.vertex_group = ""
    key_verts = target.active_shape_key.data

    x = corrective_extract_vert_coords(key_verts)
    target_coords = corrective_extract_vert_coords(mesh_source.vertices)

    for iteration_index in range(iterations):
        dx = [[], [], [], [], [], []]
        mapped = corrective_extract_mapped_coords(target)

        for index in range(len(mesh_target.vertices)):
            epsilon = (target_coords[index] - mapped[index]).length
            if epsilon < threshold:
                epsilon = 0.0

            dx[0].append(x[index] + 0.5 * epsilon * Vector((1, 0, 0)))
            dx[1].append(x[index] + 0.5 * epsilon * Vector((-1, 0, 0)))
            dx[2].append(x[index] + 0.5 * epsilon * Vector((0, 1, 0)))
            dx[3].append(x[index] + 0.5 * epsilon * Vector((0, -1, 0)))
            dx[4].append(x[index] + 0.5 * epsilon * Vector((0, 0, 1)))
            dx[5].append(x[index] + 0.5 * epsilon * Vector((0, 0, -1)))

        for axis in range(6):
            corrective_apply_vert_coords(target, key_verts, dx[axis])
            dx[axis] = corrective_extract_mapped_coords(target)

        for index in range(len(mesh_target.vertices)):
            epsilon = (target_coords[index] - mapped[index]).length
            if epsilon < threshold:
                continue
            gx = list((dx[0][index] - dx[1][index]) / epsilon)
            gy = list((dx[2][index] - dx[3][index]) / epsilon)
            gz = list((dx[4][index] - dx[5][index]) / epsilon)
            gradient = Matrix((gx, gy, gz))
            delta = target_coords[index] - mapped[index]
            x[index] += gradient @ delta

        corrective_apply_vert_coords(target, key_verts, x)
        if progress_callback is not None:
            progress_callback(iteration_index + 1, iterations)

    target.active_shape_key.vertex_group = vertex_group
    target.active_shape_key.value = 1.0
    target.show_only_shape_key = False
    update_mesh_state(target)
    target.matrix_local = original_matrix_local
    return target.active_shape_key


def remove_temporary_object(obj):
    if obj is None:
        return
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def mute_existing_nla_tracks(animation_data):
    if animation_data is None:
        return
    for track in animation_data.nla_tracks:
        track.mute = True


def generate_preview_animation(settings, muscle_obj, body_obj, rig_obj, pose_bone, sampled_rots, key_names):
    scene = bpy.context.scene
    start_frame = settings.animation_start_frame
    end_frame = start_frame + max(1, settings.animation_length)
    total_keys = max(1, len(key_names))

    sample_frames = []
    for index in range(total_keys):
        if total_keys == 1:
            frame = end_frame
        else:
            frame = round(start_frame + (end_frame - start_frame) * (index / (total_keys - 1)))
        sample_frames.append(frame)

    shape_keys = body_obj.data.shape_keys
    if shape_keys.animation_data is None:
        shape_keys.animation_data_create()
    mute_existing_nla_tracks(shape_keys.animation_data)
    remove_preview_actions(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    shape_action_name, rig_action_name = preview_action_names(settings.key_prefix, muscle_obj, body_obj, rig_obj)
    shape_action = bpy.data.actions.new(name=shape_action_name)
    shape_keys.animation_data.action = shape_action

    relevant_keys = [shape_keys.key_blocks[name] for name in key_names if name in shape_keys.key_blocks]
    for frame in sample_frames:
        clear_keyframe_values(relevant_keys, frame)

    for frame, key_block in zip(sample_frames, relevant_keys):
        key_block.value = 1.0
        key_block.keyframe_insert(data_path="value", frame=frame)

    if rig_obj.animation_data is None:
        rig_obj.animation_data_create()
    mute_existing_nla_tracks(rig_obj.animation_data)
    rig_action = bpy.data.actions.new(name=rig_action_name)
    rig_obj.animation_data.action = rig_action

    for frame, quat in zip(sample_frames, sampled_rots):
        apply_quaternion_to_pose_bone(pose_bone, quat)
        if pose_bone.rotation_mode == "QUATERNION":
            pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        elif pose_bone.rotation_mode == "AXIS_ANGLE":
            pose_bone.keyframe_insert(data_path="rotation_axis_angle", frame=frame)
        else:
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)

    scene.frame_start = min(scene.frame_start, start_frame)
    scene.frame_end = max(scene.frame_end, end_frame)
    scene.frame_set(start_frame)


