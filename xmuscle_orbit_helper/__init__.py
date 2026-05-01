import bpy
from bpy.props import PointerProperty

from .core import CORE_CLASSES, XMRB_Settings
from .drawn_muscle import DRAWN_CLASSES
from .mesh_muscle import MESH_CLASSES
from .ui import UI_CLASSES

bl_info = {
    "name": "xmuscles orbit helper",
    "author": "blackorbit",
    "version": (0, 6, 2),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > X-Muscles Orbit",
    "description": "Muscle-centric helper for baking and rebaking X-Muscle deformation into shape keys",
    "category": "Object",
}

ALL_CLASSES = CORE_CLASSES + DRAWN_CLASSES + MESH_CLASSES + UI_CLASSES


def register():
    for cls in ALL_CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.xmuscle_range_baker = PointerProperty(type=XMRB_Settings)


def unregister():
    del bpy.types.Scene.xmuscle_range_baker
    for cls in reversed(ALL_CLASSES):
        bpy.utils.unregister_class(cls)
