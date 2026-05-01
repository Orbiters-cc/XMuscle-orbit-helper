# XMuscle Orbit Helper

XMuscle Orbit Helper is a Blender 5 addon that makes the X-Muscle System easier to use for game-character workflows.
It adds muscle-centric creation, preview, baking, and cleanup tools around the original `xmusclesystem` addon without modifying that addon.

## What It Does

- Creates X-Muscles from five workflows: Normal, Curved, Flat, Drawn-on-body, and Custom Mesh.
- Auto-applies newly created muscles to the selected body mesh, defaulting to a mesh named `Body` when available.
- Adds optional helper drivers when creating a muscle from two selected bones:
  - Slide driver bone: creates a helper bone attached to the first selected bone and driven by the second selected bone rotation.
  - Base Length driver: drives the X-Muscle Base Length from selected source bone rotation.
- Bakes X-Muscle deformation into shape key ranges for export workflows.
- Generates preview animation actions for baked shape keys.
- Temporarily mutes live X-Muscle deformation so baked shape keys can be inspected alone.
- Provides scene-level visibility controls for all muscles: Hide, Show, and Show Through.
- Cleans up X-Muscle systems, baked keys, preview actions, and helper bones with undo support.

## Setup

1. Install and enable the original X-Muscle System addon.
2. Build this addon zip with `package_addon.bat`.
3. Install `dist/xmuscle_orbit_helper_clean.zip` in Blender.
4. Enable `xmuscles orbit helper`.
5. Open `View3D > Sidebar > X-Muscles Orbit`.

For local iteration, use `dev_sync_addon.bat` and then run `F3 > Reload Scripts` in Blender.
This avoids repeatedly reinstalling the zip.

## Basic Workflow

1. Select or set the `Apply To` body mesh.
2. Optionally select two pose bones before creating a muscle.
3. Choose a muscle creation mode:
   - `Normal`, `Curved`, `Flat`: delegate to X-Muscle Auto Aim/basic creation.
   - `Drawn`: draw a closed shape on the body, pick start/end attachment points, adjust smoothing, then convert.
   - `Mesh`: choose an existing mesh object and convert it into an X-Muscle.
4. Edit per-muscle bake settings from the Scene Muscles list.
5. Bake selected muscles into shape keys.
6. Use the generated preview action button to inspect the result.

## Architecture

The addon is intentionally split by role. Keep files focused and small.

- `__init__.py`: Blender addon metadata and class registration.
- `ui.py`: Sidebar panel drawing only. It should not contain business logic.
- `core.py`: Compatibility facade. It loads the feature modules into the stable `xmuscle_orbit_helper.core` namespace so old imports and Blender class registration remain stable.
- `core_modules/foundation.py`: Constants, scene queries, muscle lookup, naming, driver expression helpers.
- `core_modules/selection_settings.py`: Per-muscle and multi-muscle settings persistence.
- `core_modules/xmuscle_creation.py`: X-Muscle creation helpers, slide driver creation, Base Length driver creation.
- `core_modules/xmuscle_scene_state.py`: Selection helpers, Auto Aim setup, visibility, live X-Muscle state, display state.
- `core_modules/bake_state.py`: Shape-key cleanup, body modifier snapshots, pose sampling, mute/restore helpers.
- `core_modules/corrective_baking.py`: Corrective shape-key solver and preview animation generation.
- `core_modules/properties.py`: `XMRB_Settings` Blender property group.
- `core_modules/muscle_operators.py`: Muscle list, creation, apply, delete, visibility, rename, and preview action operators.
- `core_modules/bake_operators.py`: Pose capture, driver zero capture, restore-pose capture, and range bake operator.
- `drawn_helpers.py`: Drawn/mesh conversion helpers, generated mesh geometry, viewport drawing primitives, X-Muscle conversion utilities.
- `drawn_muscle.py`: Modal Drawn muscle workflow only.
- `mesh_muscle.py`: Custom Mesh muscle workflow only.

## Development Checks

Run these before handing off changes:

```powershell
$files = @(
  '.\xmuscle_orbit_helper\core.py',
  '.\xmuscle_orbit_helper\mesh_muscle.py',
  '.\xmuscle_orbit_helper\drawn_helpers.py',
  '.\xmuscle_orbit_helper\drawn_muscle.py',
  '.\xmuscle_orbit_helper\ui.py',
  '.\xmuscle_orbit_helper\__init__.py'
) + (Get-ChildItem .\xmuscle_orbit_helper\core_modules\*.py | ForEach-Object FullName)
python -m py_compile @files
```

```powershell
& 'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' --background --python-expr "import bpy; bpy.ops.preferences.addon_disable(module='xmuscle_orbit_helper'); bpy.ops.preferences.addon_enable(module='xmuscle_orbit_helper'); import xmuscle_orbit_helper; print(xmuscle_orbit_helper.bl_info['version'])"
```

Then run:

```powershell
.\package_addon.bat
.\dev_sync_addon.bat
```

## Notes

- The original `xmusclesystem` folder is intentionally ignored by git.
- `.blend` and `.fbx` files are ignored because they are large local test assets.
- Do not scale X-Muscle system/controller objects to make handles smaller; use display-only settings such as `empty_display_size` and pose-bone custom-shape display scale.
