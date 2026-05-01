# Compatibility facade for the X-Muscle Orbit Helper core API.
#
# Blender stores registered classes by module path. Loading the feature modules
# into this namespace keeps the stable `xmuscle_orbit_helper.core` path while
# allowing the implementation to live in focused files under `core_modules/`.
from pathlib import Path

_CORE_MODULES = (
    "foundation.py",
    "selection_settings.py",
    "xmuscle_creation.py",
    "xmuscle_scene_state.py",
    "bake_state.py",
    "corrective_baking.py",
    "properties.py",
    "muscle_operators.py",
    "bake_operators.py",
)
_CORE_MODULES_DIR = Path(__file__).with_name("core_modules")
for _module_name in _CORE_MODULES:
    _module_path = _CORE_MODULES_DIR / _module_name
    exec(compile(_module_path.read_text(), str(_module_path), "exec"), globals())

del Path, _CORE_MODULES, _CORE_MODULES_DIR, _module_name, _module_path
