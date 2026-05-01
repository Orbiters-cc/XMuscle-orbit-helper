# AGENTS.md

Guidance for future coding agents working on XMuscle Orbit Helper.

## Primary Rule

Keep the addon maintainable. Do not re-create giant files or anonymous numbered splits. Every module should have a clear role, a clear owner feature, and a practical reason to exist.

## Architecture Rules

- Keep `ui.py` limited to Blender UI drawing and operator wiring.
- Keep modal viewport workflows in their own modules, such as `drawn_muscle.py`.
- Keep reusable logic outside modal/operator files, such as `drawn_helpers.py`.
- Keep Blender property definitions in `core_modules/properties.py`.
- Keep operator classes grouped by user-facing feature:
  - Muscle management operators in `core_modules/muscle_operators.py`.
  - Bake/capture operators in `core_modules/bake_operators.py`.
  - Mesh conversion operators in `mesh_muscle.py`.
  - Mesh-as-bone conversion operators in `bone_muscle.py`.
- Keep pure or mostly-pure helpers grouped by domain:
  - Queries and naming in `core_modules/foundation.py`.
  - Settings persistence in `core_modules/selection_settings.py`.
  - X-Muscle creation and drivers in `core_modules/xmuscle_creation.py`.
  - Scene state and visibility in `core_modules/xmuscle_scene_state.py`.
  - Bake setup/restore state in `core_modules/bake_state.py`.
  - Corrective shape solving in `core_modules/corrective_baking.py`.

## About `core.py`

`core.py` is a compatibility facade. Blender registration and other modules already depend on the stable `xmuscle_orbit_helper.core` module path, so `core.py` loads focused modules into its namespace.

Do not put implementation back into `core.py`.
If a new feature needs core-level helpers, add them to the appropriate `core_modules/*.py` file and update the loader order only when necessary.

## File Size Standard

Aim to keep every Python source file below 600 lines.
If a file grows past that, split it by role before adding more features.
Avoid splitting by arbitrary line count; split by responsibility.

## X-Muscle Integration Constraints

- Do not edit the original `xmusclesystem` addon unless explicitly requested.
- Prefer wrapping or compensating for X-Muscle behavior from this addon.
- Do not scale X-Muscle system/controller objects to change handle size. Object scale affects muscle behavior. Use display-only fields such as `empty_display_size` or pose-bone custom-shape display scale.
- Mesh Bone creation must not add pins. Parent the X-Muscle System and Ctrl to the selected armature bone with a tiny non-zero separation and do not scale the converted muscle object.
- Preserve user-created scene data. Temporary objects must be removed on success, cancellation, and failure.
- Any deletion workflow must remain undoable.

## Blender API Rules

- Avoid context-sensitive operators unless there is no stable data API alternative.
- When using Blender operators, explicitly set mode, active object, and selection.
- Restore selection and scene state where practical.
- Modal operators must support `Esc` and right-click cancellation.
- Modal preview objects must be cleaned up on cancellation and failed conversion.

## Testing Checklist

Run these before finishing changes:

```powershell
$files = @(
  '.\xmuscle_orbit_helper\core.py',
  '.\xmuscle_orbit_helper\bone_muscle.py',
  '.\xmuscle_orbit_helper\mesh_muscle.py',
  '.\xmuscle_orbit_helper\drawn_helpers.py',
  '.\xmuscle_orbit_helper\drawn_muscle.py',
  '.\xmuscle_orbit_helper\ui.py',
  '.\xmuscle_orbit_helper\__init__.py'
) + (Get-ChildItem .\xmuscle_orbit_helper\core_modules\*.py | ForEach-Object FullName)
python -m py_compile @files
```

```powershell
& 'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' --background --python-expr "import bpy; bpy.ops.preferences.addon_disable(module='xmuscle_orbit_helper'); bpy.ops.preferences.addon_enable(module='xmuscle_orbit_helper'); import xmuscle_orbit_helper; print('OK', xmuscle_orbit_helper.bl_info['version'])"
```

Then rebuild and sync:

```powershell
.\package_addon.bat
.\dev_sync_addon.bat
```

## Git Hygiene

- Do not commit `.blend`, `.fbx`, `dist/`, `__pycache__`, or the ignored `xmusclesystem` addon.
- Do not revert user changes unless explicitly requested.
- Keep refactors behavior-preserving unless the task explicitly requests behavior changes.
- Update `README.md` and this file when architecture or workflow changes.
