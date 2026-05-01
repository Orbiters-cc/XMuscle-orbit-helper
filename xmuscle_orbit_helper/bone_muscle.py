import bpy
from bpy.props import StringProperty
from mathutils import Vector

from . import core
from .drawn_helpers import _bounds_max_extent
from .mesh_conversion import convert_mesh_source_to_muscle


WHITE_BONE_MATERIAL = "XMOH_Bone_White"
BONE_HELPER_OFFSET_MIN = 0.03
BONE_HELPER_OFFSET_BONE_FACTOR = 0.25
BONE_HELPER_OFFSET_MESH_FACTOR = 0.5


def _selected_single_pose_bone(context):
    rig_obj = core.find_armature_for_autoaim(context)
    if rig_obj is None:
        return None, ""
    selected_names, active_name = core.get_selected_bone_names_for_autoaim(context, rig_obj)
    if active_name and active_name in rig_obj.pose.bones:
        return rig_obj, active_name
    if len(selected_names) == 1 and selected_names[0] in rig_obj.pose.bones:
        return rig_obj, selected_names[0]
    return rig_obj, ""


def _pose_bone_world_length(rig_obj, bone_name):
    pose_bone = rig_obj.pose.bones.get(bone_name)
    if pose_bone is None:
        return 0.0
    return (rig_obj.matrix_world @ pose_bone.tail - rig_obj.matrix_world @ pose_bone.head).length


def _world_length_to_armature_length(rig_obj, world_length):
    scale = rig_obj.matrix_world.to_scale()
    max_scale = max(abs(scale.x), abs(scale.y), abs(scale.z), 1e-6)
    return world_length / max_scale


def _create_bone_endpoint_helper(context, rig_obj, parent_bone_name, helper_base_name, world_offset):
    if rig_obj is None or rig_obj.type != "ARMATURE":
        return False, "No valid armature was found"
    if parent_bone_name not in rig_obj.data.bones:
        return False, f"Parent bone {parent_bone_name} was not found"

    previous_active, previous_selection = core.snapshot_selection(context)
    previous_mode = context.mode
    created_name = ""

    try:
        core.ensure_object_mode(context)
        core.set_single_object_selection(context, rig_obj)
        bpy.ops.object.mode_set(mode="EDIT")

        edit_bones = rig_obj.data.edit_bones
        parent_edit = edit_bones[parent_bone_name]
        created_name = core.unique_bone_name(rig_obj, helper_base_name)
        helper_edit = edit_bones.new(created_name)
        helper_edit.parent = parent_edit
        helper_edit.use_connect = False
        helper_edit.use_deform = False

        parent_vector = parent_edit.tail - parent_edit.head
        if parent_vector.length < 1e-6:
            parent_vector = Vector((0.0, 0.05, 0.0))
        local_offset = max(_world_length_to_armature_length(rig_obj, world_offset), BONE_HELPER_OFFSET_MIN)
        local_axis = parent_vector.normalized()
        helper_edit.head = parent_edit.tail.copy()
        helper_edit.tail = helper_edit.head + local_axis * local_offset
        helper_edit.roll = parent_edit.roll

        bpy.ops.object.mode_set(mode="POSE")
        pose_bone = rig_obj.pose.bones[created_name]
        pose_bone.rotation_mode = "XYZ"
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone["xmuscle_orbit_bone_endpoint"] = True
        pose_bone["xmuscle_orbit_bone_source"] = parent_bone_name
    except RuntimeError as exc:
        return False, f"Failed to create bone endpoint helper: {exc}"
    finally:
        try:
            if previous_mode == "POSE" and context.object == rig_obj:
                bpy.ops.object.mode_set(mode="POSE")
            else:
                bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
        core.restore_selection(context, previous_active, previous_selection)

    return True, created_name


def _set_muscle_bone_order(muscle_obj, bone_names):
    bone_list = getattr(muscle_obj, "Bone_List", None)
    if bone_list is None:
        return
    try:
        bone_list.clear()
        for bone_name in bone_names:
            item = bone_list.add()
            item.name = bone_name
    except Exception:
        pass


def _force_body_modifier_vertex_group(body_obj, muscle_obj, source_bone_name):
    if body_obj is None or muscle_obj is None or not source_bone_name:
        return
    if source_bone_name not in body_obj.vertex_groups:
        return
    for modifier in body_obj.modifiers:
        if modifier.type == "SHRINKWRAP" and getattr(modifier, "target", None) == muscle_obj and not modifier.vertex_group:
            modifier.vertex_group = source_bone_name


def _set_object_white_material(obj, _source_world_coords):
    if obj is None or obj.type != "MESH":
        return
    material = bpy.data.materials.get(WHITE_BONE_MATERIAL)
    if material is None:
        material = bpy.data.materials.new(WHITE_BONE_MATERIAL)
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    obj.color = (1.0, 1.0, 1.0, 1.0)


class XMRB_OT_toggle_bone_muscle_creator(bpy.types.Operator):
    bl_idname = "xmuscle_baker.toggle_bone_muscle_creator"
    bl_label = "Bone Muscle"
    bl_description = "Show or hide mesh-as-bone conversion controls"

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        settings.show_bone_muscle_creator = not settings.show_bone_muscle_creator
        active = context.view_layer.objects.active
        if settings.show_bone_muscle_creator and active and active.type == "MESH" and not getattr(active, "Muscle_XID", False):
            settings.bone_source_object = active
        return {"FINISHED"}


class XMRB_OT_create_bone_muscle(bpy.types.Operator):
    bl_idname = "xmuscle_baker.create_bone_muscle"
    bl_label = "Create Bone"
    bl_description = "Convert the chosen mesh into a white X-Muscle bone attached to the selected armature bone"
    bl_options = {"REGISTER", "UNDO"}

    source_name: StringProperty(default="")

    def execute(self, context):
        settings = context.scene.xmuscle_range_baker
        source_obj = bpy.data.objects.get(self.source_name) if self.source_name else settings.bone_source_object
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Choose a mesh object to convert into a bone")
            return {"CANCELLED"}
        if getattr(source_obj, "Muscle_XID", False):
            self.report({"ERROR"}, "Choose a regular mesh, not an existing X-Muscle")
            return {"CANCELLED"}

        rig_obj, bone_name = _selected_single_pose_bone(context)
        if rig_obj is None or not bone_name:
            self.report({"ERROR"}, "Select one armature bone before creating a mesh bone")
            return {"CANCELLED"}

        bbox_corners = [source_obj.matrix_world @ Vector(corner) for corner in source_obj.bound_box]
        mesh_extent = max(0.001, _bounds_max_extent(bbox_corners))
        helper_offset = max(
            mesh_extent * BONE_HELPER_OFFSET_MESH_FACTOR,
            _pose_bone_world_length(rig_obj, bone_name) * BONE_HELPER_OFFSET_BONE_FACTOR,
            BONE_HELPER_OFFSET_MIN,
        )

        ok, helper_result = _create_bone_endpoint_helper(
            context,
            rig_obj,
            bone_name,
            f"{getattr(context.scene, 'Muscle_Name', source_obj.name) or source_obj.name}_bone_endpoint",
            helper_offset,
        )
        if not ok:
            self.report({"ERROR"}, helper_result)
            return {"CANCELLED"}
        created_helper_bone_name = helper_result

        created_muscle = None
        try:
            body_obj = core.ensure_default_body_object(settings, context.scene)
            muscle_obj, error = convert_mesh_source_to_muscle(
                context,
                settings,
                source_obj,
                rig_obj=rig_obj,
                attach_bones=[bone_name, created_helper_bone_name],
                body_obj=body_obj,
                post_convert=_set_object_white_material,
            )
            if muscle_obj is None:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            created_muscle = muscle_obj
            muscle_obj["xmuscle_orbit_bone_source"] = bone_name
            muscle_obj["xmuscle_orbit_bone_helper"] = created_helper_bone_name
            _set_muscle_bone_order(muscle_obj, [bone_name, created_helper_bone_name])
            if body_obj is not None:
                _force_body_modifier_vertex_group(body_obj, muscle_obj, bone_name)
            if error:
                self.report({"WARNING"}, error)
            self.report({"INFO"}, f"Created mesh bone {muscle_obj.name} attached to {bone_name}")
            return {"FINISHED"}
        finally:
            if created_muscle is None:
                core.delete_bone_by_name(context, rig_obj, created_helper_bone_name)


BONE_CLASSES = (
    XMRB_OT_toggle_bone_muscle_creator,
    XMRB_OT_create_bone_muscle,
)
