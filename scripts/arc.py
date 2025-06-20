import re
import math
import sys

# SCRIPT_SETTINGS: {"num_snapshots": {"type": "spinbox", "label": "Number of Snapshots", "range": [0, 500], "default": 10, "tooltip": "The total number of snapshots to take evenly spaced throughout the print."}, "arc_radius": {"type": "doublespinbox", "label": "Arc Radius (mm)", "range": [50.0, 500.0], "default": 100.0, "step": 5.0, "decimals": 1, "tooltip": "The radius of the arc path around the print center."}, "arc_start_angle_deg": {"type": "doublespinbox", "label": "Arc Start Angle (°)", "range": [0.0, 360.0], "default": 0.0, "step": 5.0, "decimals": 1, "tooltip": "The starting angle of the arc (0° is positive X, 90° is positive Y)."}, "arc_end_angle_deg": {"type": "doublespinbox", "label": "Arc End Angle (°)", "range": [0.0, 360.0], "default": 90.0, "step": 5.0, "decimals": 1, "tooltip": "The ending angle of the arc."}, "arc_height_offset": {"type": "doublespinbox", "label": "Arc Base Height Offset (mm)", "range": [-50.0, 50.0], "default": 0.0, "step": 1.0, "decimals": 1, "tooltip": "A constant vertical offset for the camera's path relative to the current layer Z."}, "camera_distance_z_factor": {"type": "doublespinbox", "label": "Camera Z Follow Factor", "range": [0.5, 2.0], "default": 1.0, "step": 0.05, "decimals": 2, "tooltip": "Adjusts how much the camera's Z position scales with the print's current layer Z. (1.0 = moves with print, <1.0 = less, >1.0 = more)"}}

def run(settings, gcode_lines):
    """
    Applies an arc camera movement to a G-code file for timelapse.
    The printer moves to a specified point on an arc, takes a snapshot, and returns.

    Args:
        settings (dict): A dictionary containing various settings:
            - "firmware" (str): "klipper" or "marlin".
            - "travel_speed" (int): Speed for travel moves (mm/min).
            - "dwell_time" (int): Time to dwell at snapshot position (ms).
            - "retract_length" (float): Filament retract length (mm).
            - "retract_speed" (int): Filament retract speed (mm/s).
            - "z_hop_height" (float): Z-hop height (mm).
            - "num_snapshots" (int): Total number of snapshots to take.
            - "arc_radius" (float): Radius of the arc path.
            - "arc_start_angle_deg" (float): Starting angle of the arc in degrees.
            - "arc_end_angle_deg" (float): Ending angle of the arc in degrees.
            - "arc_height_offset" (float): Constant vertical offset for the camera's path.
            - "camera_distance_z_factor" (float): Factor by which camera Z scales with print Z.
            - "min_x", "max_x", "min_y", "max_y", "max_z": Object bounding box and max Z from file.
            - "bed_dimensions": {"x": float, "y": float} bed dimensions.
        gcode_lines (list): List of G-code lines.

    Returns:
        list: Modified G-code lines with arc movements.
    """
    modified_lines = []
    current_x, current_y, current_z = 0.0, 0.0, 0.0  # Initialize current position
    is_relative = False  # Track G90 (absolute) and G91 (relative)

    # Initialize _last_snapshot_z as a local variable for this run
    _last_snapshot_z = -9999.0 # Initialize to an impossible Z value

    # Extract settings with defaults
    firmware = settings.get("firmware", "klipper").lower()
    travel_speed = settings.get("travel_speed", 9000)
    dwell_time = settings.get("dwell_time", 500)
    retract_length = settings.get("retract_length", 0.5)
    retract_speed = settings.get("retract_speed", 40)
    z_hop_height = settings.get("z_hop_height", 0.2)
    num_snapshots = settings.get("num_snapshots", 10)
    arc_radius = settings.get("arc_radius", 100.0)
    arc_start_angle_deg = settings.get("arc_start_angle_deg", 0.0)
    arc_end_angle_deg = settings.get("arc_end_angle_deg", 90.0)
    arc_height_offset = settings.get("arc_height_offset", 0.0)
    camera_distance_z_factor = settings.get("camera_distance_z_factor", 1.0)


    # Get print dimensions from settings (from main.py's parsing)
    min_x = settings.get("min_x", 0.0)
    max_x = settings.get("max_x", 0.0)
    min_y = settings.get("min_y", 0.0)
    max_y = settings.get("max_y", 0.0)
    max_z_print = settings.get("max_z", 250.0) # Max Z of the print itself

    # Calculate center of the print bed/object
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    
    print(f"DEBUG: Arc script settings: num_snapshots={num_snapshots}, radius={arc_radius}, start_angle={arc_start_angle_deg}, end_angle={arc_end_angle_deg}, height_offset={arc_height_offset}, z_factor={camera_distance_z_factor}", file=sys.__stdout__)
    print(f"DEBUG: Print Center: ({center_x:.2f}, {center_y:.2f})", file=sys.__stdout__)

    # Regex to find layer changes (typical slicer comments or G-code commands)
    layer_change_pattern = re.compile(r"; ?(LAYER|Z):?([\d.]+)|(?:;[ \t]*BEFORE_LAYER_CHANGE)|(?:;[ \t]*AFTER_LAYER_CHANGE)|G1[^\n]*Z[-\d.]+")

    # Keep track of Z heights for each detected layer
    layers_z_heights = []
    current_layer_z_for_detection = -1.0 # Initialize to a value that won't match first layer

    # First pass: Identify layer changes and actual Z heights
    for line in gcode_lines:
        z_match = re.search(r"Z([-\d.]+)", line)
        if z_match:
            current_z = float(z_match.group(1))

        layer_match = layer_change_pattern.search(line)
        if layer_match:
            # Add layer and its Z height only if it's a new distinct Z height
            if not layers_z_heights or abs(current_z - current_layer_z_for_detection) > 0.001: # Check for distinct Z
                layers_z_heights.append(current_z)
                current_layer_z_for_detection = current_z
        # We don't modify lines in this pass, just collect layer Zs.
        # Modified lines are built in the second pass.

    # Sort and deduplicate layers_z_heights in case they were not perfectly ordered or had small variations
    layers_z_heights = sorted(list(set(layers_z_heights)))
    # Simple deduplication for very close Zs after sorting
    deduplicated_layers = []
    if layers_z_heights:
        deduplicated_layers.append(layers_z_heights[0])
        for i in range(1, len(layers_z_heights)):
            if abs(layers_z_heights[i] - layers_z_heights[i-1]) > 0.001:
                deduplicated_layers.append(layers_z_heights[i])
    layers_z_heights = deduplicated_layers

    print(f"DEBUG: Detected {len(layers_z_heights)} distinct Z layers for snapshot placement.", file=sys.__stdout__)

    # If num_snapshots is 0, no snapshots are taken.
    if num_snapshots == 0:
        print("INFO: num_snapshots is 0. No arc movements will be added.", file=sys.__stdout__)
        return gcode_lines # Return original lines if no snapshots requested

    # Determine which layers will have snapshots
    snapshot_target_z = set()
    if len(layers_z_heights) > 0:
        if num_snapshots >= len(layers_z_heights):
            # Take a snapshot at every detected layer
            snapshot_target_z = set(layers_z_heights)
        else:
            # Calculate indices for evenly spaced snapshots
            for i in range(num_snapshots):
                layer_index = int(i / (num_snapshots - 1) * (len(layers_z_heights) - 1)) if num_snapshots > 1 else 0
                snapshot_target_z.add(layers_z_heights[layer_index])
    
    print(f"DEBUG: Snapshot Target Z Heights: {sorted(list(snapshot_target_z))}", file=sys.__stdout__)

    # Second pass: Insert G-code commands for arc movement
    final_gcode = []
    current_x, current_y, current_z = 0.0, 0.0, 0.0 # Reset for processing
    last_extruded_e = 0.0

    # Pattern to find G0/G1 moves and capture X, Y, Z, E values
    gcode_move_pattern = re.compile(r"^(G0|G1)\s*(?:X([-\d.]+))?\s*(?:Y([-\d.]+))?\s*(?:Z([-\d.]+))?\s*(?:E([-\d.]+))?")

    # Keep track of the Z height of the layer currently being processed (for angle calculation)
    # This will be updated when a layer comment is processed.
    current_processing_layer_z = 0.0
    
    # Store already taken snapshots to avoid duplicates for the same Z height
    processed_snapshot_z = set()

    for line in gcode_lines:
        original_line = line.strip()

        # Update current position based on G0/G1 commands
        move_match = gcode_move_pattern.match(original_line)
        if move_match:
            cmd = move_match.group(1)
            x_str, y_str, z_str, e_str = move_match.group(2), move_match.group(3), move_match.group(4), move_match.group(5)

            # Store previous position before updating
            prev_x, prev_y, prev_z = current_x, current_y, current_z

            if not is_relative: # Absolute positioning (G90)
                if x_str is not None: current_x = float(x_str)
                if y_str is not None: current_y = float(y_str)
                if z_str is not None: current_z = float(z_str)
            else: # Relative positioning (G91)
                if x_str is not None: current_x += float(x_str)
                if y_str is not None: current_y += float(y_str)
                if z_str is not None: current_z += float(z_str)
            
            if e_str is not None:
                if cmd == "G1": # Only G1 moves are typically for extrusion
                    last_extruded_e = float(e_str) # This assumes absolute E. For relative E, more complex.
                else: # G0 moves often don't have E, or E is zero for travel
                    pass 

        # Check for G90/G91 mode changes
        if original_line.upper().startswith("G90"):
            is_relative = False
        elif original_line.upper().startswith("G91"):
            is_relative = True

        final_gcode.append(line)

        # Trigger snapshot if a layer change comment is encountered and it's a target layer
        layer_comment_match = re.search(r"; ?(LAYER|Z):?([\d.]+)", original_line)
        if layer_comment_match:
            detected_z = float(layer_comment_match.group(2))
            current_processing_layer_z = detected_z # Update the current Z for arc angle calculation

            # Check if this Z height is one of our target snapshot layers
            # and if we haven't processed a snapshot for this Z height yet
            if any(abs(detected_z - sl_z) < 0.01 for sl_z in snapshot_target_z) and detected_z not in processed_snapshot_z:
                
                # Calculate the interpolation factor for the current layer's Z
                # Factor will be 0.0 for the lowest layer in layers_z_heights, 1.0 for highest
                # Avoid division by zero if only one layer
                if len(layers_z_heights) > 1:
                    z_factor_for_angle = (detected_z - layers_z_heights[0]) / (layers_z_heights[-1] - layers_z_heights[0])
                else:
                    z_factor_for_angle = 0.0 # If only one layer, no interpolation

                # Interpolate the angle along the arc
                current_arc_angle_deg = arc_start_angle_deg + (arc_end_angle_deg - arc_start_angle_deg) * z_factor_for_angle

                # Calculate the snapshot X, Y coordinates on the arc
                # Convert degrees to radians for math.cos/sin
                target_x = center_x + arc_radius * math.cos(math.radians(current_arc_angle_deg))
                target_y = center_y + arc_radius * math.sin(math.radians(current_arc_angle_deg))
                
                # Calculate the target Z height for the camera
                # current_z is the nozzle's actual Z height at this point
                # arc_height_offset is a fixed offset
                # camera_distance_z_factor allows camera to move up/down relative to print progress
                target_z_camera = current_z + arc_height_offset + (current_z * (camera_distance_z_factor - 1.0))
                
                final_gcode.append(f"; PrintPath: Arc snapshot for Z={detected_z:.2f} (Angle: {current_arc_angle_deg:.1f}deg)\n")
                
                # Store current state and switch to absolute mode for moves
                if firmware == "klipper":
                    final_gcode.append("M400 ; Wait for moves to finish\n")
                    final_gcode.append("M114 ; Report position\n")
                    final_gcode.append(f"SAVE_GCODE_STATE NAME=PRINTPATH_ARC_STATE\n")
                    final_gcode.append("G90 ; Absolute positioning\n")
                    final_gcode.append(f"G0 Z{target_z_camera + z_hop_height:.3f} F{travel_speed}\n") # Z-hop to camera Z + hop
                    final_gcode.append(f"G0 X{target_x:.3f} Y{target_y:.3f} F{travel_speed}\n") # Move to snapshot position
                    final_gcode.append(f"M204 P{retract_speed} T{retract_speed}\n") # Set accel for retract
                    final_gcode.append(f"G1 E-{retract_length:.3f} F{retract_speed * 60}\n") # Retract
                    final_gcode.append("M400\n") # Wait for retract to finish
                    final_gcode.append(f"G4 P{dwell_time} ; Dwell\n") # Dwell
                    final_gcode.append("TIMELAPSE_TAKE_FRAME\n")
                    final_gcode.append(f"G1 E{retract_length:.3f} F{retract_speed * 60}\n") # Unretract
                    final_gcode.append(f"RESTORE_GCODE_STATE NAME=PRINTPATH_ARC_STATE MOVE=1\n")
                elif firmware == "marlin":
                    final_gcode.append("M400 ; Wait for moves to finish\n")
                    final_gcode.append("M114 ; Report position\n")
                    final_gcode.append("G90 ; Absolute positioning\n")
                    final_gcode.append(f"G1 Z{target_z_camera + z_hop_height:.3f} F{travel_speed}\n") # Z-hop to camera Z + hop
                    final_gcode.append(f"G1 X{target_x:.3f} Y{target_y:.3f} F{travel_speed}\n") # Move to snapshot position
                    final_gcode.append(f"M204 P{retract_speed} T{retract_speed}\n") # Set accel for retract (Marlin uses P for print, T for travel)
                    final_gcode.append(f"G1 E-{retract_length:.3f} F{retract_speed * 60}\n") # Retract
                    final_gcode.append("M400\n") # Wait for retract to finish
                    final_gcode.append(f"G4 P{dwell_time} ; Dwell\n") # Dwell
                    final_gcode.append("TIMELAPSE_TAKE_FRAME\n")
                    final_gcode.append(f"G1 E{retract_length:.3f} F{retract_speed * 60}\n") # Unretract
                    # Restore original position (Marlin lacks SAVE/RESTORE_GCODE_STATE)
                    # This requires knowing the exact X,Y,Z before the move, which `current_x/y/z` track.
                    if is_relative: # If original was relative, go back relative
                        final_gcode.append(f"G91 ; Relative positioning\n")
                        final_gcode.append(f"G0 X{current_x - target_x:.3f} Y{current_y - target_y:.3f} Z{current_z - (target_z_camera + z_hop_height) + z_hop_height:.3f} F{travel_speed}\n")
                        final_gcode.append(f"G90 ; Absolute positioning (reset to original mode assumed by Marlin slicers)\n") # Revert to G90 as most slicers are G90 by default
                    else: # If original was absolute, go back absolute
                        final_gcode.append(f"G90 ; Absolute positioning\n")
                        final_gcode.append(f"G0 X{current_x:.3f} Y{current_y:.3f} Z{current_z:.3f} F{travel_speed}\n")
                    final_gcode.append("M400\n") # Wait for moves to finish

                processed_snapshot_z.add(detected_z) # Mark this layer as snapshotted

        # If a line contains G1 E... (extrusion), update last_extruded_e
        if re.search(r"G1.*E([-\d.]+)", original_line) and not re.search(r"E-", original_line):
             # Ensure it's not a retraction (E-)
             last_extruded_e_match = re.search(r"E([-\d.]+)", original_line)
             if last_extruded_e_match:
                 last_extruded_e = float(last_extruded_e_match.group(1))

    return final_gcode
