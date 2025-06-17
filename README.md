# PrintPath

**PrintPath** is a cross-platform tool for generating time-lapse G-code from 3D printer files, optimized for Klipper and Marlin firmware. It supports custom scripting modes like Octolapse's "Orbit", layer-aware snapshot planning, and both 2D and 3D path previews.

## Features

- Drag & drop G-code interface
- Orbit mode (built-in)
- Scriptable snapshot modes via `/scripts`
- Live 2D snapshot preview (top/front/3/4 view)
- Layer-by-layer navigation
- Collapsible log console
- Script metadata and safety-checked script generation

## How to Use

1. Drag a G-code file onto the app
2. Choose your timelapse mode (Orbit or script)
3. Preview the snapshot path
4. Export modified G-code

## Script Format

Create scripts in the `scripts/` folder. Each should have:

```
# name: MyCoolMode
# description: This mode does a spiral around the print.
def run(settings, gcode_data):
    return modified_gcode_lines
```

## License

MIT
