# Auto-split facade for the X-Muscle Orbit Helper core module.
# The part files are executed in this module namespace so Blender class
# registration keeps the stable xmuscle_orbit_helper.core module path.
from pathlib import Path

_CORE_PARTS_DIR = Path(__file__).with_name("core_parts")
for _part_path in sorted(_CORE_PARTS_DIR.glob("part_*.py")):
    exec(compile(_part_path.read_text(), str(_part_path), "exec"), globals())

del Path, _CORE_PARTS_DIR, _part_path
