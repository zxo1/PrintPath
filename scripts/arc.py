# SCRIPT_SETTINGS: {    "num_snapshots": {"type": "spinbox", "label": "Number of Snapshots", "range": [1, 500], "default": 10, "tooltip": "The total number of snapshots to take evenly spaced throughout the print."},    "vertical_only_percentage": {"type": "doublespinbox", "label": "Initial Vertical %", "range": [0.0, 1.0], "default": 0.2, "step": 0.05, "decimals": 2, "tooltip": "Percentage of the total Z height (and snapshots) where camera only moves vertically at the start corner, before arcing."},    "horizontal_only_percentage": {"type": "doublespinbox", "label": "Final Horizontal %", "range": [0.0, 1.0], "default": 0.2, "step": 0.05, "decimals": 2, "tooltip": "Percentage of the total Z height (and snapshots) where camera only moves horizontally at the end corner, after arcing."},    "start_corner": {"type": "combobox", "label": "Start Corner (XY)", "items": ["Front-Left", "Front-Right", "Back-Left", "Back-Right"], "default": "Front-Left", "tooltip": "The starting corner for the arc path on the XY plane at the bottom of the print."},    "end_corner": {"type": "combobox", "label": "End Corner (XY)", "items": ["Front-Left", "Front-Right", "Back-Left", "Back-Right"], "default": "Back-Right", "tooltip": "The ending corner for the arc path on the XY plane at the top of the print."},    "arc_control_offset_h": {"type": "doublespinbox", "label": "Horizontal Arc Offset (mm)", "range": [-200.0, 200.0], "default": 0.0, "step": 5.0, "decimals": 1, "tooltip": "Offset for the horizontal component (X or Y) of the arc's control point. Positive values push the arc outwards along that axis."},    "arc_control_offset_v": {"type": "doublespinbox", "label": "Vertical Arc Offset (mm)", "range": [-200.0, 200.0], "default": 0.0, "step": 5.0, "decimals": 1, "tooltip": "Offset for the vertical (Z) component of the arc's control point. Positive values push the arc higher in Z, creating an 'outward' curve in the side profile, like in the example image."},    "z_offset_for_snapshots": {"type": "doublespinbox", "label": "Snapshot Z Offset (mm)", "range": [-10.0, 10.0], "default": 0.0, "step": 0.1, "decimals": 1, "tooltip": "Additional Z offset applied to the snapshot height (can be negative or positive)."},    "first_snapshot_layer": {"type": "spinbox", "label": "First Snapshot Layer (approx.)", "range": [0, 9999], "default": 1, "tooltip": "The approximate layer number (0-indexed) to begin taking snapshots. This setting prevents snapshots from being taken during initial printer routines like purging or bed leveling. Snapshots will only start being inserted once the detected logical layer count reaches this value."},    "camera_distance_z_factor": {"type": "doublespinbox", "label": "Camera Z Follow Factor", "range": [0.5, 2.0], "default": 1.0, "step": 0.05, "decimals": 2, "tooltip": "Adjusts how much the camera's Z position scales with the print's current layer Z. (1.0 = moves with print, <1.0 = less, >1.0 = more). Value of 1.0 means camera follows total print height, not actual current layer Z."}}

import math
import re
import sys

def run(settings, gcode_lines):
    """
    Applies a corner-to-corner arc camera movement to a G-code file for timelapse.
    The camera's path now features:
    1. An initial vertical climb at the start corner.
    2. A middle phase with an arc in the Z-profile (XZ or YZ plane).
    3. A final vertical climb at the end corner.

    Args:
        settings (dict): A dictionary containing various settings:
            - "firmware" (str): "klipper" or "marlin".
            - "travel_speed" (int): Speed for travel moves (mm/min).
            - "dwell_time" (int): Time to dwell at snapshot position (ms).
            - "retract_length" (float): Filament retract length (mm).
            - "retract_speed" (int): Filament retract speed (mm/s).
            - "z_hop_height" (float): Z-hop height (mm).
            - "num_snapshots" (int): Total number of snapshots to take.
            - "vertical_only_percentage" (float): Percentage of Z-height for initial vertical movement.
            - "horizontal_only_percentage" (float): Percentage of Z-height for final horizontal movement.
            - "start_corner" (str): Name of the starting corner ("Front-Left", etc.).
            - "end_corner" (str): Name of the ending corner.
            - "arc_control_offset_h" (float): Horizontal offset for the Bezier control point.
            - "arc_control_offset_v" (float): Vertical (Z) offset for the Bezier control point.
            - "z_offset_for_snapshots" (float): Additional Z offset for snapshots.
            - "first_snapshot_layer" (int): The first layer (0-indexed) to begin taking snapshots.
            - "camera_distance_z_factor" (float): Factor by which camera Z scales.
            - "min_x", "max_x", "min_y", "max_y": Model bounding box from file (absolute).
            - "min_z_print", "max_z_print": Min/Max Z of the print itself (from toolpath analysis).
        gcode_lines (list): List of G-code lines.

    Returns:
        tuple: (modified_gcode_lines (list), snapshot_points (list))
            modified_gcode_lines: List of G-code lines with added commands.
            snapshot_points: List of (x, y, z) tuples for visualization in the viewer.
    """
    final_gcode = []
    snapshot_points_list = [] # List to store (x, y, z) tuples for visualization

    current_x, current_y, current_z = 0.0, 0.0, 0.0  # Initialize current position
    is_relative = False  # Track G90 (absolute) and G91 (relative)

    # Global position tracking for the original position to return to
    original_pos_x, original_pos_y, original_pos_z = 0.0, 0.0, 0.0

    # New tracking variables for layers and snapshots
    current_logical_layer = 0 # Sequential layer number based on detected changes
    layers_with_inserted_snapshots = set() # Store actual layer numbers where snapshots were inserted
    snapshots_taken_count = 0 # Counts successfully inserted snapshots

    # Extract settings with defaults
    debug_mode = settings.get("debug_mode", False)
    travel_speed = settings.get("travel_speed", 9000)
    dwell_time = settings.get("dwell_time", 500)
    retract_length = settings.get("retract_length", 0.5)
    retract_speed = settings.get("retract_speed", 40)
    z_hop_height = settings.get("z_hop_height", 0.2)
    num_snapshots = settings.get("num_snapshots", 10)
    vertical_only_percentage = settings.get("vertical_only_percentage", 0.2)
    horizontal_only_percentage = settings.get("horizontal_only_percentage", 0.2)
    start_corner_name = settings.get("start_corner", "Front-Left")
    end_corner_name = settings.get("end_corner", "Back-Right")
    arc_control_offset_h = settings.get("arc_control_offset_h", 0.0) # Horizontal component of control point offset
    arc_control_offset_v = settings.get("arc_control_offset_v", 0.0) # Vertical (Z) component of control point offset
    z_offset_for_snapshots = settings.get("z_offset_for_snapshots", 0.0)
    first_snapshot_layer = settings.get("first_snapshot_layer", 1)
    camera_distance_z_factor = settings.get("camera_distance_z_factor", 1.0)

    # Get print dimensions from settings (passed from main.py's parsing)
    min_x_print = settings.get("min_x", 0.0)
    max_x_print = settings.get("max_x", 0.0)
    min_y_print = settings.get("min_y", 0.0)
    max_y_print = settings.get("max_y", 0.0)
    min_z_print = settings.get("min_z_print", 0.0)
    max_z_print = settings.get("max_z", 250.0)

    # Calculate model center for defining the arc control point
    model_center_x = (min_x_print + max_x_print) / 2.0
    model_center_y = (min_y_print + max_y_print) / 2.0
    model_center_z = (min_z_print + max_z_print) / 2.0 # Also useful for Z control point midpoint

    # Map corner names to actual XY coordinates based on print bounding box
    corner_coords = {
        "Front-Left": (min_x_print, min_y_print),
        "Front-Right": (max_x_print, min_y_print),
        "Back-Left": (min_x_print, max_y_print),
        "Back-Right": (max_x_print, max_y_print)
    }

    P0_x, P0_y = corner_coords.get(start_corner_name, (min_x_print, min_y_print))
    P2_x, P2_y = corner_coords.get(end_corner_name, (max_x_print, max_y_print))

    # Divisor for global progress factor (t_global) for Z-scaling and overall snapshot distribution.
    # If num_snapshots is 1, global_progress_divisor is 1, t_global for snapshots_taken_count=0 is 0.0.
    global_progress_divisor = max(1, num_snapshots - 1)
    
    # Calculate global Z thresholds for each phase based on percentages
    z_range_total = max_z_print - min_z_print
    
    # These percentages define the Z-height *fractions* where phases end/begin
    z_end_vertical_phase_percentage = vertical_only_percentage
    z_start_horizontal_phase_percentage = 1.0 - horizontal_only_percentage

    # Ensure phase percentages don't overlap or result in negative arc duration
    if z_end_vertical_phase_percentage + horizontal_only_percentage > 1.0:
        # If total percentage exceeds 1.0, scale them down proportionally
        total_combined_percentage = vertical_only_percentage + horizontal_only_percentage
        vertical_only_percentage_adjusted = vertical_only_percentage / total_combined_percentage
        horizontal_only_percentage_adjusted = horizontal_only_percentage / total_combined_percentage
        
        # Re-assign for use in phase calculations
        z_end_vertical_phase_percentage = vertical_only_percentage_adjusted
        z_start_horizontal_phase_percentage = 1.0 - horizontal_only_percentage_adjusted
        
        if debug_mode: print(f"WARNING: Arc Script: Initial/Final percentages overlap. Adjusted to: Initial Vertical={vertical_only_percentage_adjusted:.2f}, Final Horizontal={horizontal_only_percentage_adjusted:.2f}", file=sys.stdout)

    current_z_for_distinct_layer_check = -9999.0 
    gcode_move_pattern = re.compile(r"^(G0|G1)\s*(?:X([-\d.]+))?\s*(?:Y([-\d.]+))?\s*(?:Z([-\d.]+))?\s*(?:E([-\d.]+))?")

    for line_idx, line in enumerate(gcode_lines):
        original_line = line.strip()

        move_match = gcode_move_pattern.match(original_line)
        if move_match:
            cmd = move_match.group(1)
            x_str, y_str, z_str, e_str = move_match.group(2), move_match.group(3), move_match.group(4), move_match.group(5)

            # Store previous position before updating current_x, current_y, current_z
            original_pos_x, original_pos_y, original_pos_z = current_x, current_y, current_z

            if not is_relative: # Absolute positioning (G90)
                if x_str is not None: current_x = float(x_str)
                if y_str is not None: current_y = float(y_str)
                if z_str is not None: current_z = float(z_str)
            else: # Relative positioning (G91)
                if x_str is not None: current_x += float(x_str)
                if y_str is not None: current_y += float(y_str)
                if z_str is not None: current_z += float(z_str)
            
        # Check for G90/G91 mode changes
        if original_line.upper().startswith("G90"):
            is_relative = False
        elif original_line.upper().startswith("G91"):
            is_relative = True

        # --- Layer Change Detection ---
        is_new_logical_layer = False

        # 1. Prioritize explicit layer comments (e.g., ";LAYER:123")
        layer_num_match = re.search(r"; ?LAYER:(\d+)", original_line, re.IGNORECASE)
        if layer_num_match:
            new_parsed_layer = int(layer_num_match.group(1))
            if new_parsed_layer > current_logical_layer:
                current_logical_layer = new_parsed_layer
                is_new_logical_layer = True
        else:
            # 2. Fallback to significant Z change if no explicit layer number
            if abs(current_z - current_z_for_distinct_layer_check) > 0.05: # Use a small tolerance for Z change
                if current_z > current_z_for_distinct_layer_check: # Only increment if Z goes up
                    current_z_for_distinct_layer_check = current_z
                    current_logical_layer += 1 
                    is_new_logical_layer = True
                # else: Z is same or decreased, not a new logical layer for this purpose
                
        # --- Snapshot Insertion Condition ---
        # Insert a snapshot if:
        # 1. A new logical layer is detected.
        # 2. The current logical layer is at or beyond the first_snapshot_layer setting.
        # 3. A snapshot hasn't already been inserted for this logical layer (prevents duplicates on Z-moves).
        # 4. We haven't yet reached the total number of requested snapshots.
        if is_new_logical_layer and current_logical_layer >= first_snapshot_layer and \
           current_logical_layer not in layers_with_inserted_snapshots and \
           snapshots_taken_count < num_snapshots:

            # Calculate global progress 't_global' for Z-scaling (0.0 to 1.0 across all snapshots)
            # This ensures Z progresses linearly over the total number of snapshots.
            t_global = float(snapshots_taken_count) / global_progress_divisor
            
            # --- Z Height Calculation (Always global for consistent ascent) ---
            # snapshot_base_z interpolates from min_z_print to max_z_print over the entire print
            snapshot_base_z = min_z_print + (z_range_total * t_global)

            # The final safe Z position for the camera, incorporating hop and user's Z offset,
            # plus an optional factor that allows camera to follow current print Z.
            safe_z_for_snapshot = snapshot_base_z + z_hop_height + z_offset_for_snapshots + \
                                  (current_z * (camera_distance_z_factor - 1.0))
            
            # --- XY Position Calculation (Three Phases with Z-profile Arc) ---
            target_x, target_y = P0_x, P0_y # Default to P0 (start corner)

            if t_global < z_end_vertical_phase_percentage:
                # Phase 1: Vertical Only (XY at P0)
                target_x, target_y = P0_x, P0_y
                if debug_mode: print(f"DEBUG: Phase 1 (Vertical Only) for snapshot {snapshots_taken_count+1}. t_global={t_global:.2f}", file=sys.stdout)
            elif t_global > z_start_horizontal_phase_percentage:
                # Phase 3: Horizontal Only (XY at P2)
                target_x, target_y = P2_x, P2_y
                if debug_mode: print(f"DEBUG: Phase 3 (Horizontal Only) for snapshot {snapshots_taken_count+1}. t_global={t_global:.2f}", file=sys.stdout)
            else:
                # Phase 2: Arcing Middle (XY and Z move via Bezier in XZ or YZ plane)
                # Normalize 't' for the arc phase (0.0 to 1.0 within this phase)
                arc_global_start_t = z_end_vertical_phase_percentage
                arc_global_end_t = z_start_horizontal_phase_percentage
                arc_global_duration = arc_global_end_t - arc_global_start_t

                if arc_global_duration > 0:
                    t_arc = (t_global - arc_global_start_t) / arc_global_duration
                else:
                    t_arc = 0.0 # Degenerate case, force to start of arc segment

                # Clamp t_arc between 0 and 1
                t_arc = max(0.0, min(1.0, t_arc))

                # Determine the primary horizontal axis for arcing and the corresponding control points
                delta_x_initial = P2_x - P0_x
                delta_y_initial = P2_y - P0_y

                is_xz_arc_primary = abs(delta_x_initial) >= abs(delta_y_initial)

                if is_xz_arc_primary:
                    # Arc in XZ plane
                    # Define start/end points for the arc segment in X and Z
                    arc_start_x = P0_x
                    arc_end_x = P2_x
                    arc_start_z = min_z_print + (z_range_total * arc_global_start_t)
                    arc_end_z = min_z_print + (z_range_total * arc_global_end_t)

                    # Calculate control point for X and Z for the Bezier curve
                    # Control point is relative to the midpoint of the arc's XZ segment, using offsets
                    mid_arc_x = (arc_start_x + arc_end_x) / 2.0
                    mid_arc_z = (arc_start_z + arc_end_z) / 2.0

                    # P1_x_bezier pulls the X of the control point
                    P1_x_bezier = mid_arc_x + arc_control_offset_h
                    # P1_z_bezier pulls the Z of the control point, creating the Z-profile curve
                    P1_z_bezier = mid_arc_z + arc_control_offset_v

                    # Clamp control point to keep arc mostly within bounds
                    P1_x_bezier = max(min_x_print, min(max_x_print, P1_x_bezier))
                    P1_z_bezier = max(min_z_print, min(max_z_print, P1_z_bezier)) # Clamp Z control point too

                    # Calculate target X and Z using Bezier for this phase
                    target_x = (1 - t_arc)**2 * arc_start_x + 2 * (1 - t_arc) * t_arc * P1_x_bezier + t_arc**2 * arc_end_x
                    snapshot_base_z = (1 - t_arc)**2 * arc_start_z + 2 * (1 - t_arc) * t_arc * P1_z_bezier + t_arc**2 * arc_end_z
                    
                    # Y moves linearly between P0_y and P2_y during the arc phase
                    target_y = P0_y + t_arc * (P2_y - P0_y)

                    if debug_mode: print(f"DEBUG: XZ Arc - t_arc={t_arc:.2f}, P1_bezier=({P1_x_bezier:.2f}, {P1_z_bezier:.2f}), target_Z_base={snapshot_base_z:.2f}", file=sys.stdout)

                else: # YZ arc primary
                    # Arc in YZ plane
                    # Define start/end points for the arc segment in Y and Z
                    arc_start_y = P0_y
                    arc_end_y = P2_y
                    arc_start_z = min_z_print + (z_range_total * arc_global_start_t)
                    arc_end_z = min_z_print + (z_range_total * arc_global_end_t)

                    # Calculate control point for Y and Z for the Bezier curve
                    mid_arc_y = (arc_start_y + arc_end_y) / 2.0
                    mid_arc_z = (arc_start_z + arc_end_z) / 2.0

                    # P1_y_bezier pulls the Y of the control point
                    P1_y_bezier = mid_arc_y + arc_control_offset_h
                    # P1_z_bezier pulls the Z of the control point, creating the Z-profile curve
                    P1_z_bezier = mid_arc_z + arc_control_offset_v

                    # Clamp control point to keep arc mostly within bounds
                    P1_y_bezier = max(min_y_print, min(max_y_print, P1_y_bezier))
                    P1_z_bezier = max(min_z_print, min(max_z_print, P1_z_bezier)) # Clamp Z control point too

                    # Calculate target Y and Z using Bezier for this phase
                    target_y = (1 - t_arc)**2 * arc_start_y + 2 * (1 - t_arc) * t_arc * P1_y_bezier + t_arc**2 * arc_end_y
                    snapshot_base_z = (1 - t_arc)**2 * arc_start_z + 2 * (1 - t_arc) * t_arc * P1_z_bezier + t_arc**2 * arc_end_z
                    
                    # X moves linearly between P0_x and P2_x during the arc phase
                    target_x = P0_x + t_arc * (P2_x - P0_x)

                    if debug_mode: print(f"DEBUG: YZ Arc - t_arc={t_arc:.2f}, P1_bezier=({P1_y_bezier:.2f}, {P1_z_bezier:.2f}), target_Z_base={snapshot_base_z:.2f}", file=sys.stdout)

            # Final safe Z calculation uses snapshot_base_z (which now comes from the Bezier in arc phase)
            safe_z_for_snapshot = snapshot_base_z + z_hop_height + z_offset_for_snapshots + \
                                  (current_z * (camera_distance_z_factor - 1.0))
            
            # Add G-code commands for the snapshot
            final_gcode.append(f"; --- PrintPath Arc Snapshot {snapshots_taken_count + 1}/{num_snapshots} for Layer {current_logical_layer} ---\n")
            final_gcode.append(f"G90 ; Set to Absolute positioning (for move)\n") # Ensure absolute mode before move
            final_gcode.append(f"G0 X{target_x:.3f} Y{target_y:.3f} Z{safe_z_for_snapshot:.3f} F{travel_speed} ; Move to arc snapshot position\n")
            
            if dwell_time > 0:
                final_gcode.append(f"G4 P{dwell_time} ; Dwell for camera\n")
            
            final_gcode.append("TIMELAPSE_TAKE_FRAME\n")
            
            # Record the actual snapshot point for the viewer (X, Y, Z tuple)
            snapshot_points_list.append((target_x, target_y, safe_z_for_snapshot)) 

            # Return to original print position and unretract
            final_gcode.append(f"G0 X{original_pos_x:.3f} Y{original_pos_y:.3f} Z{original_pos_z:.3f} F{travel_speed} ; Return to original print position\n")
            
            if retract_length > 0:
                final_gcode.append(f"G91 ; Set to Relative positioning (for unretraction)\n")
                final_gcode.append(f"G1 E{retract_length:.3f} F{retract_speed * 60:.0f} ; Unretract {retract_length}mm\n")
                final_gcode.append(f"G90 ; Set to Absolute positioning (after unretract)\n")
            
            final_gcode.append(f"; --- END PrintPath Arc Snapshot ---\n")
        
            snapshots_taken_count += 1 # Increment the count *after* a snapshot is successfully inserted
            layers_with_inserted_snapshots.add(current_logical_layer) # Mark this logical layer as having a snapshot
            if debug_mode: 
                print(f"DEBUG: Arc Script: Inserted snapshot {snapshots_taken_count}/{num_snapshots} for logical layer {current_logical_layer} at (X={target_x:.2f}, Y={target_y:.2f}, Z={safe_z_for_snapshot:.2f}).", file=sys.stdout)
        
        final_gcode.append(line) # Always append the original line to the output

    return final_gcode, snapshot_points_list
