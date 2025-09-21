
> **Weave your 3D prints into color.**  
> Slice a 3MF into repeating layer bands (EVEN/ODD or more), ready for multi-color and multi-material printing.

---

## Why Layer Loom?

Just like a loom weaves threads into fabric, **Layer Loom** weaves alternating color/material layers into your 3D prints.  
It takes a standard `.3mf` file and groups its Z-bands into **K repeating sets** (default **2 = EVEN/ODD**). The result:  
- 🧱 **Even/Odd banding** for striking striped effects  
- 🎨 **Multi-material orchestration** for MMU / AMS workflows  
- 🪡 **Custom patterns** with K > 2 groups  

---

## Features

- 🔁 Group layers into **K** repeating sets (default 2 = EVEN/ODD; pass `--groups N` for more).  
- 🧭 Smart Z0 handling: `auto-global`, `auto-local`, or `fixed`.  
- 🧱 Scene objects (optional): whole-model EVEN/ODD composites for quick inspection.  
- 🏷️ Slicer-friendly names: `part_001_ODD`, `part_001_EVEN`, `SCENE_K2_ODD`, …  
- ⚡ Integrated “bake” step for world placement if your 3MF has transforms/components.  

---

## Install

```bash
# from source
python3 -m pip install -e .
```

Requires Python 3.9+, `trimesh`, `numpy`, and `lxml`.

---

## Quick start

```bash
layerloom \
  -i benchy_cutup.3mf \
  -o benchy_grouped.3mf \
  -H 0.20 \
  --z0-mode auto-global \
  --groups 2 \
  --include-scene \
  -v
```

This generates a grouped `.3mf` with alternating EVEN/ODD bands. Open in Bambu Studio, PrusaSlicer, or OrcaSlicer to preview.

---

## Example outputs

* **Input**: sliced Benchy `.3mf`
* **Output (K=2)**:

  * `part_001_EVEN`
  * `part_001_ODD`
  * `SCENE_K2_EVEN` (optional scene mesh)
  * `SCENE_K2_ODD`

![Example screenshot of Layer Loom output in Bambu Studio](docs/images/benchy_loom.png)
*(placeholder — add your screenshot here)*

---

## CLI

```
usage: layerloom [-h] -i INPUT -o OUTPUT -H LAYER_HEIGHT_MM
                 [--z0-mode {auto-global,auto-local,fixed}]
                 [--z0-mm Z0_MM] [--groups GROUPS]
                 [--include-scene] [-v]
```

| Flag                    | Type  | Default     | Description                     |
| ----------------------- | ----- | ----------- | ------------------------------- |
| `-i, --input`           | path  | —           | Input 3MF                       |
| `-o, --output`          | path  | —           | Output 3MF                      |
| `-H, --layer-height-mm` | float | —           | Band height (mm)                |
| `--z0-mode`             | enum  | auto-global | Origin: global, local, fixed    |
| `--z0-mm`               | float | —           | Z0 (if `fixed`)                 |
| `--groups`              | int   | 2           | Number of repeating groups      |
| `--include-scene`       | flag  | off         | Append scene-level group meshes |
| `-v, --verbose`         | flag  | off         | Extra logging                   |

---

## Ideas for branding

* **Logo sketch**:
  A simple woven pattern (like two threads interlaced) with the text *Layer Loom*.
  Or a stylized “L” formed from stacked colored bands.

* **Screenshots**:

  * Input Benchy `.3mf` in slicer
  * Output with EVEN/ODD coloring visible
  * Optional: AMS preview showing alternating colors

* **Interactive demo**:
  Export a small `SCENE_K2_EVEN.stl` to `demo/`. GitHub’s STL viewer lets visitors rotate the model right in the browser.

---

## Roadmap

* [ ] Patterned grouping (e.g., 2-1-2 band repeats)
* [ ] GUI helper (`layerloom-gui`)
* [ ] Publish to PyPI

---

## License

MIT © Finn Beruldsen
