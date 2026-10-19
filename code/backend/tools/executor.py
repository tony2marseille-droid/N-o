# -*- coding: utf-8 -*-
"""Interface publique des outils serveur (chantier 3, 2026-10-17).

Code splitté dans backend/tools/ : registry (dispatch + permissions),
t_util, t_files + sandbox, t_web, t_image, t_cad, t_mesh, t_blender.
Ce module reste < 100 lignes et réexporte l'API stable consommée par
le pont Rust (execute_tool, ollama_tool_specs, PermissionManager) et
par les scripts locaux (ex. tool_run_command, tool_write_file).
"""
from backend.tools.registry import (
    ALLOWED_LEVELS,
    MAX_TOOL_ROUNDS,
    TOOL_DEFINITIONS,
    PermissionDenied,
    PermissionManager,
    execute_tool,
    level_rank,
    ollama_tool_specs,
)
from backend.tools.t_util import tool_calculator, tool_get_datetime
from backend.tools.t_files import (
    tool_check_code,
    tool_list_dir,
    tool_read_file,
    tool_run_command,
    tool_write_file,
)
from backend.tools.t_web import tool_web_fetch, tool_web_search
from backend.tools.t_web import tool_deep_research
from backend.tools.t_image import (
    tool_analyze_image,
    tool_fetch_image,
    tool_find_reference_image,
    tool_generate_image,
    tool_image_search,
)
from backend.tools.t_cad import (
    tool_render_preview,
    tool_render_scad,
    tool_slice_model,
    tool_validate_design,
)
from backend.tools.t_mesh import (
    tool_fuse_meshes,
    tool_generate_mesh_from_image,
    tool_mesh_boolean,
    tool_mesh_info,
    tool_repair_mesh,
    tool_render_stl_views,
    tool_validate_mesh,
)
from backend.tools.t_blender import tool_blender_render, tool_blender_run

__all__ = [
    "TOOL_DEFINITIONS", "MAX_TOOL_ROUNDS", "ALLOWED_LEVELS", "level_rank",
    "PermissionDenied", "PermissionManager", "execute_tool",
    "ollama_tool_specs",
    "tool_calculator", "tool_get_datetime",
    "tool_list_dir", "tool_read_file", "tool_write_file",
    "tool_run_command", "tool_check_code",
    "tool_web_search", "tool_web_fetch", "tool_deep_research",
    "tool_generate_image", "tool_image_search", "tool_fetch_image",
    "tool_analyze_image", "tool_find_reference_image",
    "tool_render_scad", "tool_slice_model", "tool_render_preview",
    "tool_validate_design",
    "tool_generate_mesh_from_image", "tool_mesh_info", "tool_repair_mesh",
    "tool_mesh_boolean", "tool_fuse_meshes", "tool_render_stl_views",
    "tool_validate_mesh", "tool_blender_run", "tool_blender_render",
]
