# Installing LayerLoom From GitHub

LayerLoom is currently distributed as source code. The recommended path is:

1. Install Python 3.10, 3.11, or 3.12.
2. Clone or download the GitHub repository.
3. Create a virtual environment.
4. Install LayerLoom into that environment.
5. Run `layerloom-doctor`.
6. Launch `layerloom-gui`.

Using a virtual environment matters. It prevents LayerLoom from accidentally
using an unrelated Python from another app, Conda environment, slicer, or system
tool.

## macOS And Linux

```bash
git clone https://github.com/your-org/layerloom.git
cd layerloom
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
layerloom-doctor
layerloom-gui
```

If `python3` is not found, install Python from
[python.org](https://www.python.org/downloads/) or your package manager.

## Windows PowerShell

```powershell
git clone https://github.com/your-org/layerloom.git
cd layerloom
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
layerloom-doctor
layerloom-gui
```

If PowerShell blocks activation, run this once for the current user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then reopen PowerShell and activate the environment again.

## Verify The Install

Run:

```bash
layerloom-doctor
```

The doctor command prints your Python executable, Python version, operating
system, LayerLoom install location, required package imports, and whether the
tutorial examples are present.

If it reports missing packages, make sure the virtual environment is activated
and reinstall:

```bash
python -m pip install -e ".[gui]"
```

## Linux GUI / OpenGL Notes

The GUI uses Qt, PyVista, and VTK. On Linux, these libraries need a working
desktop session and OpenGL support. If the GUI imports successfully but the
window does not render, check that:

- you are running from a desktop session, not a headless SSH session
- GPU/OpenGL drivers are installed
- system Qt/OpenGL libraries are available through your distribution

CI only tests imports and command-line smoke behavior. It does not open the
interactive GUI window.

## Slicer Notes

LayerLoom outputs standard 3MF files. Open the woven output in your slicer and
assign generated objects such as `all_cyan` or `all_yellow` to the matching
loaded filaments.

Keep the slicer layer height equal to the LayerLoom weave height. The first
layer height should be an integer multiple of the weave height.

In Ultimaker Cura 5.12.0, disable automatic object drop-to-plate behavior before
opening woven LayerLoom outputs. Automatic Z placement can break the woven layer
schedule.
