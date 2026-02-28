"""
Vitis/HLS .cfg file parser.

Parses Vitis configuration files to extract:
- HLS top function and source file associations
- Kernel instance mappings (nk=)
- Stream connections between kernels (stream_connect=)
- Memory bank assignments (sp=)
"""

import logging
from typing import List, Tuple
from pathlib import Path
import os

from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


def _parse_cfg_sections(content: str) -> dict:
    """Parse .cfg file into dict of {section: [(key, value), ...]} supporting duplicate keys."""
    sections = {}
    current_section = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip().lower()
            if current_section not in sections:
                sections[current_section] = []
        elif "=" in line and current_section is not None:
            key, _, value = line.partition("=")
            sections[current_section].append((key.strip().lower(), value.strip()))

    return sections


def analyze_vitis_cfg(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    nodes = []
    relationships = []
    file_path = str(file_path)
    rel_path = os.path.relpath(file_path, repo_path) if repo_path else file_path
    module_path = rel_path.replace("/", ".").replace("\\", ".")

    try:
        sections = _parse_cfg_sections(content)
    except Exception as e:
        logger.warning(f"Failed to parse .cfg file {file_path}: {e}")
        return nodes, relationships

    # Parse [hls] section
    if "hls" in sections:
        top_func = None
        source_files = []

        for key, value in sections["hls"]:
            if key == "syn.top" and value:
                top_func = value.strip()
            elif key.startswith("syn.file") and "cflags" not in key and value:
                source_files.append(value.strip().lstrip("./"))

        if top_func:
            component_id = f"{module_path}.{top_func}"
            nodes.append(Node(
                id=component_id,
                name=top_func,
                component_type="hls_top",
                file_path=file_path,
                relative_path=rel_path,
                node_type="hls_top",
                display_name=f"HLS top: {top_func}",
                component_id=component_id,
                is_hls_kernel=True,
            ))

            for src in source_files:
                relationships.append(CallRelationship(
                    caller=component_id,
                    callee=src,
                    relationship_type="hls_source",
                    is_resolved=False,
                ))

    # Parse [connectivity] section
    if "connectivity" in sections:
        for key, value in sections["connectivity"]:
            if not value:
                continue

            if key == "nk":
                # nk=kernel:count:instance_name
                parts = value.split(":")
                if len(parts) >= 3:
                    kernel_name, count, instance_name = parts[0], parts[1], parts[2]
                    comp_id = f"{module_path}.{instance_name}"
                    nodes.append(Node(
                        id=comp_id,
                        name=instance_name,
                        component_type="kernel_instance",
                        file_path=file_path,
                        relative_path=rel_path,
                        node_type="kernel_instance",
                        display_name=f"kernel {kernel_name} as {instance_name}",
                        component_id=comp_id,
                    ))

            elif key == "stream_connect":
                # stream_connect=src_inst.port:dst_inst.port
                parts = value.split(":")
                if len(parts) == 2:
                    src, dst = parts
                    src_inst = src.split(".")[0] if "." in src else src
                    dst_inst = dst.split(".")[0] if "." in dst else dst
                    relationships.append(CallRelationship(
                        caller=f"{module_path}.{src_inst}",
                        callee=f"{module_path}.{dst_inst}",
                        relationship_type="stream_connect",
                        is_resolved=True,
                    ))

            elif key == "sp":
                # sp=instance.port:DDR[0]
                parts = value.split(":")
                if len(parts) >= 2:
                    port_spec = parts[0]
                    memory = ":".join(parts[1:])
                    inst = port_spec.split(".")[0] if "." in port_spec else port_spec
                    relationships.append(CallRelationship(
                        caller=f"{module_path}.{inst}",
                        callee=memory,
                        relationship_type="memory_map",
                        is_resolved=False,
                    ))

    return nodes, relationships
