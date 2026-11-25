"""Blender worker for rendering a GLB's vertex colors from standard views."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def _look_at(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :]
    input_glb = Path(argv[0]).resolve()
    output_dir = Path(argv[1]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(input_glb))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"No mesh imported from {input_glb}")
    for obj in meshes:
        if not obj.data.color_attributes:
            raise RuntimeError(f"{obj.name} has no vertex color attribute")
        material = bpy.data.materials.new(f"{obj.name} Preview")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        colors = nodes.new("ShaderNodeVertexColor")
        colors.layer_name = obj.data.color_attributes[0].name
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 0.85
        output = nodes.new("ShaderNodeOutputMaterial")
        material.node_tree.links.new(colors.outputs["Color"], emission.inputs["Color"])
        material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        obj.data.materials.clear()
        obj.data.materials.append(material)

    corners = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    low = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    high = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    center = (low + high) / 2
    extent = max(high.x - low.x, high.y - low.y, high.z - low.z)

    world = bpy.data.worlds.new("Preview World")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.08, 0.08, 0.08, 1.0)
    background.inputs["Strength"].default_value = 0.75

    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = extent * 1.18
    camera_data.clip_start = max(0.01, extent * 0.001)
    camera_data.clip_end = extent * 10.0

    light_data = bpy.data.lights.new("Key", "AREA")
    light_data.energy = extent * extent * 4.0
    light_data.shape = "DISK"
    light_data.size = extent * 1.5
    light = bpy.data.objects.new("Key", light_data)
    bpy.context.collection.objects.link(light)
    light.location = center + Vector((-extent, -extent, extent * 1.8))

    fill_data = bpy.data.lights.new("Fill", "AREA")
    fill_data.energy = extent * extent * 2.0
    fill_data.size = extent * 1.2
    fill = bpy.data.objects.new("Fill", fill_data)
    bpy.context.collection.objects.link(fill)
    fill.location = center + Vector((extent, extent, extent))

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 720
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.look = "AgX - Medium High Contrast"

    distance = extent * 2.5
    views = {
        "front": Vector((0, -distance, 0)),
        "rear": Vector((0, distance, 0)),
        "side": Vector((distance, 0, 0)),
    }
    for name, offset in views.items():
        camera.location = center + offset
        _look_at(camera, center)
        scene.render.filepath = str(output_dir / f"{input_glb.stem}_{name}.png")
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
