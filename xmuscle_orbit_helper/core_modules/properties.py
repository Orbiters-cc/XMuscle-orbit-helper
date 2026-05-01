class XMRB_Settings(bpy.types.PropertyGroup):
    sync_settings_lock: BoolProperty(default=False)
    selected_muscles_json: StringProperty(default="[]")
    body_object: PointerProperty(
        name="Body",
        type=bpy.types.Object,
        description="Target body mesh that already receives X-Muscle shrinkwrap deformation",
        update=settings_changed,
        poll=lambda _self, obj: obj and obj.type == "MESH" and not getattr(obj, "Muscle_XID", False),
    )
    show_mesh_muscle_creator: BoolProperty(
        name="Show Custom Mesh Creator",
        description="Show the custom mesh X-Muscle conversion controls",
        default=False,
    )
    show_bone_muscle_creator: BoolProperty(
        name="Show Mesh Bone Creator",
        description="Show the mesh-as-bone X-Muscle conversion controls",
        default=False,
    )
    mesh_source_object: PointerProperty(
        name="Custom Mesh",
        type=bpy.types.Object,
        description="Mesh object to convert into an X-Muscle while preserving helper driver support",
        poll=lambda _self, obj: obj and obj.type == "MESH" and not getattr(obj, "Muscle_XID", False),
    )
    bone_source_object: PointerProperty(
        name="Bone Mesh",
        type=bpy.types.Object,
        description="Mesh object to convert into a pinned X-Muscle bone attached to the selected armature bone",
        poll=lambda _self, obj: obj and obj.type == "MESH" and not getattr(obj, "Muscle_XID", False),
    )
    rig_object: PointerProperty(
        name="Rig",
        type=bpy.types.Object,
        description="Armature that drives the pose for the muscle motion",
        update=settings_changed,
        poll=lambda _self, obj: obj and obj.type == "ARMATURE",
    )
    muscle_name: StringProperty(
        name="Muscle",
        description="Currently selected X-Muscle to bake",
        update=settings_changed,
    )
    bone_name: StringProperty(
        name="Bone",
        description="Pose bone to animate and sample while baking",
        update=settings_changed,
    )
    start_rotation: FloatVectorProperty(
        name="Start Rotation",
        description="Fallback start rotation in XYZ Euler angles, used only when no captured start/end poses are stored",
        size=3,
        subtype="EULER",
        default=(0.0, 0.0, 0.0),
        update=preview_update,
    )
    end_rotation: FloatVectorProperty(
        name="End Rotation",
        description="Fallback end rotation in XYZ Euler angles, used only when no captured start/end poses are stored",
        size=3,
        subtype="EULER",
        default=(math.radians(90.0), 0.0, 0.0),
        update=preview_update,
    )
    samples: IntProperty(
        name="Samples",
        description="How many shape keys to create between start and end, inclusive",
        default=5,
        min=2,
        max=128,
        update=settings_changed,
    )
    corrective_iterations: IntProperty(
        name="Solver Iterations",
        description="Corrective shape solver iterations per sample. Higher values improve fidelity but can increase bake time dramatically",
        default=12,
        min=1,
        max=20,
        update=settings_changed,
    )
    key_prefix: StringProperty(
        name="Prefix",
        description="Prefix used for all generated shape keys and preview actions",
        default="XMSL_BAKE_",
        update=settings_changed,
    )
    replace_existing: BoolProperty(
        name="Replace Existing",
        description="Remove previously generated shape keys that share the same prefix before baking new ones",
        default=False,
        update=settings_changed,
    )
    replace_target_on_rebake: BoolProperty(
        name="Replace For Target Muscle On Rebake",
        description="Before baking, remove only the previously generated shape keys and preview actions for the selected muscle",
        default=True,
        update=settings_changed,
    )
    disable_subsurf: BoolProperty(
        name="Disable Subsurf",
        description="Temporarily disable viewport subdivision modifiers while baking, then restore them automatically",
        default=True,
        update=settings_changed,
    )
    auto_apply_muscle: BoolProperty(
        name="Auto-Apply Muscle",
        description="If the chosen muscle is not yet linked to the body, call X-Muscle's Apply Muscles to Body automatically",
        default=True,
        update=settings_changed,
    )
    create_slide_driver: BoolProperty(
        name="Add Slide Driver Bone",
        description="When adding a muscle from a 2-bone Auto Aim selection, create a helper bone on the first bone that slides from the second bone's rotation",
        default=False,
        update=settings_changed,
    )
    slide_driver_slide_axis: EnumProperty(
        name="Slide Axis",
        description="Which local translation axis the new helper bone should slide on",
        items=AXIS_ITEMS,
        default="Y",
        update=settings_changed,
    )
    slide_driver_rotation_axes: EnumProperty(
        name="Source Rotation Axes",
        description="Which local rotation axes of the second selected bone should drive the slide motion",
        items=AXIS_FLAG_ITEMS,
        options={"ENUM_FLAG"},
        default={"X"},
        update=settings_changed,
    )
    slide_driver_combine_mode: EnumProperty(
        name="Slide Combine",
        description="How multiple selected source rotation axes should be combined for the slide driver",
        items=COMBINE_MODE_ITEMS,
        default="SUM",
        update=settings_changed,
    )
    slide_driver_factor: FloatProperty(
        name="Slide Strength",
        description="Initial multiplier applied between the source rotation and the helper bone slide; can be edited later on the created helper bone custom property",
        default=1.0,
        min=-20.0,
        max=20.0,
        soft_min=-10.0,
        soft_max=10.0,
        update=settings_changed,
    )
    create_length_driver: BoolProperty(
        name="Add Length Driver",
        description="When adding a muscle from a 2-bone Auto Aim selection, drive the X-Muscle Base Length from the original second selected bone rotation",
        default=False,
        update=settings_changed,
    )
    length_driver_rotation_axes: EnumProperty(
        name="Length Rotation Axes",
        description="Which local rotation axes of the original second selected bone should drive the X-Muscle Base Length",
        items=AXIS_FLAG_ITEMS,
        options={"ENUM_FLAG"},
        default={"X"},
        update=settings_changed,
    )
    length_driver_combine_mode: EnumProperty(
        name="Length Combine",
        description="How multiple selected source rotation axes should be combined for the Base Length driver",
        items=COMBINE_MODE_ITEMS,
        default="SUM",
        update=settings_changed,
    )
    length_driver_factor: FloatProperty(
        name="Length Strength",
        description="Initial multiplier applied between the source rotation and the X-Muscle Base Length; can be edited later on the created X-Muscle system custom property",
        default=0.15,
        min=-20.0,
        max=20.0,
        soft_min=-5.0,
        soft_max=5.0,
        update=settings_changed,
    )
    selected_has_slide_driver: BoolProperty(
        name="Has Slide Driver",
        default=False,
    )
    selected_slide_driver_slide_axis: EnumProperty(
        name="Slide Driver Slide Axis",
        description="Edit the local slide axis used by the selected muscle's helper slide bone",
        items=AXIS_ITEMS,
        default="Y",
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_rotation_axes: EnumProperty(
        name="Slide Driver Rotation Axes",
        description="Edit which local rotation axes drive the selected muscle's helper slide bone",
        items=AXIS_FLAG_ITEMS,
        options={"ENUM_FLAG"},
        default={"X"},
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_combine_mode: EnumProperty(
        name="Slide Driver Combine",
        description="Choose how multiple slide-driver source axes combine on the selected muscle",
        items=COMBINE_MODE_ITEMS,
        default="SUM",
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_factor: FloatProperty(
        name="Slide Driver Strength",
        description="Edit the multiplier used by the selected muscle's helper slide bone driver",
        default=1.0,
        min=-100.0,
        max=100.0,
        soft_min=-20.0,
        soft_max=20.0,
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_rotation_space: EnumProperty(
        name="Slide Driver Space",
        description="Choose whether the selected muscle's slide driver reads the source bone in local or world rotation space",
        items=DRIVER_SPACE_ITEMS,
        default="LOCAL_SPACE",
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_mode: EnumProperty(
        name="Slide Driver Mode",
        description="Choose how the selected muscle's slide driver evaluates the source rotation",
        items=DRIVER_MODE_ITEMS,
        default="RAW_DELTA",
        update=selected_driver_settings_changed,
    )
    selected_slide_driver_zero: FloatProperty(
        name="Slide Driver Zero",
        description="Shift the selected muscle slide driver's zero point on its source rotation channel",
        default=0.0,
        subtype="ANGLE",
        soft_min=-6.283185307179586,
        soft_max=6.283185307179586,
        update=selected_driver_settings_changed,
    )
    selected_has_length_driver: BoolProperty(
        name="Has Length Driver",
        default=False,
    )
    selected_length_driver_rotation_axes: EnumProperty(
        name="Length Driver Rotation Axes",
        description="Edit which local rotation axes drive the selected muscle's X-Muscle Base Length",
        items=AXIS_FLAG_ITEMS,
        options={"ENUM_FLAG"},
        default={"X"},
        update=selected_driver_settings_changed,
    )
    selected_length_driver_combine_mode: EnumProperty(
        name="Length Driver Combine",
        description="Choose how multiple Base Length source axes combine on the selected muscle",
        items=COMBINE_MODE_ITEMS,
        default="SUM",
        update=selected_driver_settings_changed,
    )
    selected_length_driver_factor: FloatProperty(
        name="Length Driver Strength",
        description="Edit the multiplier used by the selected muscle's X-Muscle Base Length driver",
        default=0.15,
        min=-100.0,
        max=100.0,
        soft_min=-20.0,
        soft_max=20.0,
        update=selected_driver_settings_changed,
    )
    selected_length_driver_rotation_space: EnumProperty(
        name="Length Driver Space",
        description="Choose whether the selected muscle's Base Length driver reads the source bone in local or world rotation space",
        items=DRIVER_SPACE_ITEMS,
        default="LOCAL_SPACE",
        update=selected_driver_settings_changed,
    )
    selected_length_driver_mode: EnumProperty(
        name="Length Driver Mode",
        description="Choose how the selected muscle's Base Length driver evaluates the source rotation",
        items=DRIVER_MODE_ITEMS,
        default="RAW_DELTA",
        update=selected_driver_settings_changed,
    )
    selected_length_driver_zero: FloatProperty(
        name="Length Driver Zero",
        description="Shift the selected muscle Base Length driver's zero point on its source rotation channel",
        default=0.0,
        subtype="ANGLE",
        soft_min=-6.283185307179586,
        soft_max=6.283185307179586,
        update=selected_driver_settings_changed,
    )
    auto_disable_unsupported_modifiers: BoolProperty(
        name="Auto-Disable Unsupported Modifiers",
        description="Temporarily disable body modifiers that can change topology or break shape key transfer, then restore them after the bake",
        default=True,
        update=settings_changed,
    )
    use_captured_pose: BoolProperty(
        name="Use Captured Poses",
        description="Use the exact current bone rotations captured as start and end poses instead of manual angle input",
        default=True,
        update=preview_update,
    )
    has_start_pose: BoolProperty(default=False, update=settings_changed)
    has_end_pose: BoolProperty(default=False, update=settings_changed)
    start_quaternion: FloatVectorProperty(
        name="Start Quaternion",
        description="Stored start pose rotation in quaternion form",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    end_quaternion: FloatVectorProperty(
        name="End Quaternion",
        description="Stored end pose rotation in quaternion form",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    preview_enabled: BoolProperty(
        name="Live Preview",
        description="Drive the selected bone in the viewport using the preview slider between captured start and end poses",
        default=False,
        update=preview_update,
    )
    preview_factor: FloatProperty(
        name="Preview",
        description="Viewport preview position between the start pose and the end pose",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=preview_update,
    )
    preview_restore_quaternion: FloatVectorProperty(
        name="Restore Quaternion",
        size=4,
        default=(1.0, 0.0, 0.0, 0.0),
        update=settings_changed,
    )
    preview_update_lock: BoolProperty(default=False)
    auto_generate_animation: BoolProperty(
        name="Auto-Generate Preview Animation",
        description="Create a simple preview action for the bone and generated shape keys after the bake finishes",
        default=True,
        update=settings_changed,
    )
    mute_live_xmuscle: BoolProperty(
        name="Mute Live X-Muscle On Body",
        description="Temporarily disable the body's X-Muscle shrinkwrap and skin-corrector modifiers so you can inspect only the baked shape keys",
        default=False,
        update=mute_xmuscle_update,
    )
    saved_xmuscle_modifier_state: StringProperty(default="")
    mute_update_lock: BoolProperty(default=False)
    animation_start_frame: IntProperty(
        name="Anim Start",
        description="First frame of the generated preview animation",
        default=1,
        min=1,
        update=settings_changed,
    )
    animation_length: IntProperty(
        name="Anim Length",
        description="Duration in frames of the generated preview animation from start pose to end pose",
        default=24,
        min=1,
        update=settings_changed,
    )
    show_advanced_options: BoolProperty(
        name="Enable Advanced Options",
        description="Show destructive or lower-level bake options",
        default=False,
    )
    rename_buffer: StringProperty(
        name="Rename",
        description="Temporary field used to rename the selected muscle and its related baked outputs",
        default="",
    )


