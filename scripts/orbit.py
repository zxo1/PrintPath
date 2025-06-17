# name: Orbit
# description: Default 360 orbit mode for snapshot path

def run(gcode_lines, settings):
    output = []
    z_height = None
    angle = 0
    dwell = settings.get("dwell_time", 500)
    orbit_speed = settings.get("orbit_speed", 10)
    z_offset = settings.get("z_offset", 0.2)

    for line in gcode_lines:
        output.append(line)
        if line.startswith(";LAYER:"):
            z_height = float(z_height + z_offset if z_height else z_offset)
            output.append(f"G1 X0 Y0 Z{z_height:.2f} F3000 ; Orbit Start\n")
            output.append(f"G4 P{dwell} ; Dwell\n")
            angle = (angle + 1) % 360

    return output