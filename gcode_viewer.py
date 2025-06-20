import sys
import re # Import regex for parsing
from PyQt5.QtWidgets import QWidget, QApplication, QSizePolicy
from PyQt5.QtGui import QPainter, QColor, QPen, QTransform, QFont, QPainterPath
from PyQt5.QtCore import Qt, QPointF, QRectF

class GCodeViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gcode_data = [] # List of QPointF (X, Y) points for toolpath
        self.gcode_z_data = [] # List of Z values corresponding to gcode_data points
        self.layer_start_points = [] # List of (QPointF(x,y), z_value) for layer starts (pre-processing detection)
        self.processed_snapshot_points = [] # New: List of (QPointF(x,y), z_value) for actual snapshot points (post-processing result)
        self.bed_x = 220.0 # Default bed dimensions
        self.bed_y = 220.0
        self.bed_z_max = 250.0 # Max Z for the bed, used for front view
        self.min_x = 0.0
        self.max_x = 0.0
        self.min_y = 0.0
        self.max_y = 0.0
        self.min_z = 0.0
        self.max_z = 0.0 # True max Z from G-code data
        self.calculated_content_bounds = QRectF(0, 0, self.bed_x, self.bed_y) # Initial bounds
        self.view_mode = 'top' # 'top' or 'front'

        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #2e2e2e; border: 1px solid #555;")
        print("GCodeViewer: Initialized.", file=sys.__stdout__)

    def set_bed_dimensions(self, x, y, z_max=250.0):
        """
        Sets the physical dimensions of the printer bed.
        z_max is typically the max printable height.
        """
        self.bed_x = max(1.0, float(x))
        self.bed_y = max(1.0, float(y))
        self.bed_z_max = max(1.0, float(z_max)) # Ensure z_max is sensible
        print(f"GCodeViewer: Bed dimensions set to {self.bed_x:.1f}x{self.bed_y:.1f}x{self.bed_z_max:.1f}.", file=sys.__stdout__)
        self.update_content_bounds()
        self.update() # Request a repaint

    def set_gcode_data(self, data):
        """
        Sets the G-code toolpath data.
        Data is expected as a list of (QPointF(x, y), z_value) tuples.
        """
        if data is None:
            self.gcode_data = []
            self.gcode_z_data = []
            # Reset min/max values to defaults or 'empty' state
            self.min_x, self.max_x = 0.0, 0.0
            self.min_y, self.max_y = 0.0, 0.0
            self.min_z, self.max_z = 0.0, 0.0
            self.update_content_bounds()
            self.update()
            print("GCodeViewer: Received None data. Clearing toolpath.", file=sys.__stdout__)
            return

        self.gcode_data = []
        self.gcode_z_data = []
        
        # Initialize min/max with values that will be easily overwritten
        self.min_x, self.max_x = float('inf'), float('-inf')
        self.min_y, self.max_y = float('inf'), float('-inf')
        self.min_z, self.max_z = float('inf'), float('-inf')

        for point_xy, z_val in data: # Unpack the (QPointF, z_value) tuple
            if isinstance(point_xy, QPointF): 
                x_val, y_val = point_xy.x(), point_xy.y()
                
                # Update min/max for toolpath points
                self.min_x = min(self.min_x, x_val)
                self.max_x = max(self.max_x, x_val)
                self.min_y = min(self.min_y, y_val)
                self.max_y = max(self.max_y, y_val)
                self.min_z = min(self.min_z, z_val)
                self.max_z = max(self.max_z, z_val)

                self.gcode_data.append(point_xy) # Store QPointF(x,y)
                self.gcode_z_data.append(z_val) # Store Z value

        if self.gcode_data:
            print(f"GCodeViewer: Received G-code data with {len(self.gcode_data)} points.", file=sys.__stdout__)
            print(f"GCodeViewer: First point: ({self.gcode_data[0].x():.1f}, {self.gcode_data[0].y():.1f}, Z={self.gcode_z_data[0]:.1f})", file=sys.__stdout__)
            print(f"GCodeViewer: Last point: ({self.gcode_data[-1].x():.1f}, {self.gcode_data[-1].y():.1f}, Z={self.gcode_z_data[-1]:.1f})", file=sys.__stdout__)
        else:
            print("GCodeViewer: No valid toolpath data received.", file=sys.__stdout__)

        # Ensure min/max values are at least 0 if no moves or only Z0 moves
        self.min_x = min(self.min_x, 0.0) # Ensure 0 is always included for bounds
        self.min_y = min(self.min_y, 0.0)
        self.min_z = min(self.min_z, 0.0)
        self.max_x = max(self.max_x, self.bed_x) # Ensure bed maxes are included
        self.max_y = max(self.max_y, self.bed_y)
        self.max_z = max(self.max_z, 0.0) # Ensure max_z is at least 0

        self.update_content_bounds()
        self.update() # Request a repaint

    def set_layer_start_points(self, points):
        """
        Sets the list of layer start points (potential snapshot positions from initial parse).
        Data is expected as a list of (QPointF(x, y), z_value) tuples.
        """
        self.layer_start_points = points if points is not None else []
        print(f"GCodeViewer: Received {len(self.layer_start_points)} layer start points.", file=sys.__stdout__)
        # No immediate update() call here, as this is for pre-processing.
        # update() will be called by set_gcode_data or set_processed_snapshot_points later.

    def set_processed_snapshot_points(self, points):
        """
        Sets the list of actual snapshot points generated after G-code processing.
        These will take precedence for display over layer_start_points.
        Data is expected as a list of (QPointF(x, y), z_value) tuples.
        """
        self.processed_snapshot_points = points if points is not None else []
        print(f"GCodeViewer: Received {len(self.processed_snapshot_points)} processed snapshot points.", file=sys.__stdout__)
        self.update() # Request a repaint immediately as these are the "final" dots

    def parse_and_set_processed_snapshot_points(self, gcode_lines, debug_mode=False):
        """
        Parses a list of G-code lines (typically the processed output) to find
        TIMELAPSE_TAKE_FRAME commands and their preceding XYZ coordinates.
        Updates self.processed_snapshot_points.
        """
        processed_snapshot_locations = []
        current_x, current_y, current_z = 0.0, 0.0, 0.0
        is_relative_positioning = False

        gcode_move_pattern = re.compile(r"^(G0|G1)\s*(?:X([-\d.]+))?\s*(?:Y([-\d.]+))?\s*(?:Z([-\d.]+))?.*$")

        if debug_mode: print("DEBUG: GCodeViewer: Starting parse for processed snapshot points...", file=sys.__stdout__)

        for line_num, line in enumerate(gcode_lines):
            stripped_line = line.strip()
            line_upper = stripped_line.upper()

            # Update positioning mode
            if line_upper.startswith("G90"):
                is_relative_positioning = False
            elif line_upper.startswith("G91"):
                is_relative_positioning = True
            
            # Update current XYZ position
            move_match = gcode_move_pattern.match(stripped_line)
            if move_match:
                x_str, y_str, z_str = move_match.group(2), move_match.group(3), move_match.group(4)
                
                prev_x, prev_y, prev_z = current_x, current_y, current_z

                if x_str is not None:
                    x_val = float(x_str)
                    current_x = prev_x + x_val if is_relative_positioning else x_val
                
                if y_str is not None:
                    y_val = float(y_str)
                    current_y = prev_y + y_val if is_relative_positioning else y_val
                
                if z_str is not None:
                    z_val = float(z_str)
                    current_z = prev_z + z_val if is_relative_positioning else z_val
            
            # Check for TIMELAPSE_TAKE_FRAME and record current position
            if "TIMELAPSE_TAKE_FRAME" in line_upper:
                processed_snapshot_locations.append((QPointF(current_x, current_y), current_z))
                if debug_mode: print(f"DEBUG: GCodeViewer: Found TIMELAPSE_TAKE_FRAME on line {line_num+1}. Recording snapshot at ({current_x:.2f}, {current_y:.2f}, {current_z:.2f})", file=sys.__stdout__)

        self.set_processed_snapshot_points(processed_snapshot_locations)
        if debug_mode: print(f"DEBUG: GCodeViewer: Finished parsing processed snapshots. Total: {len(processed_snapshot_locations)}", file=sys.__stdout__)

    def set_view_mode(self, mode):
        """
        Sets the viewing mode for the G-code preview.
        'top': Top-down view (XY plane).
        'front': Front-on view (XZ plane, looking along Y axis).
        """
        if mode in ['top', 'front']:
            if self.view_mode != mode:
                self.view_mode = mode
                print(f"GCodeViewer: View mode set to '{self.view_mode}'.", file=sys.__stdout__)
                self.update_content_bounds() # Recalculate bounds based on new view mode
                self.update() # Request a repaint
        else:
            print(f"GCodeViewer: Invalid view mode '{mode}' requested. Must be 'top' or 'front'.", file=sys.__stdout__)

    def update_content_bounds(self):
        """
        Recalculates the content bounds based on the current view mode and G-code data.
        """
        if self.view_mode == 'top':
            # For top view, bounds are determined by min/max X and Y of the toolpath or bed
            current_min_x = min(self.min_x, 0.0) if self.gcode_data else 0.0
            current_max_x = max(self.max_x, self.bed_x) if self.gcode_data else self.bed_x
            current_min_y = min(self.min_y, 0.0) if self.gcode_data else 0.0
            current_max_y = max(self.max_y, self.bed_y) if self.gcode_data else self.bed_y
            self.calculated_content_bounds = QRectF(current_min_x, current_min_y, 
                                                    current_max_x - current_min_x, 
                                                    current_max_y - current_min_y)
            print(f"GCodeViewer: Content bounds (top view) updated: ({self.calculated_content_bounds.x():.1f}, {self.calculated_content_bounds.y():.1f}, {self.calculated_content_bounds.width():.1f}, {self.calculated_content_bounds.height():.1f})", file=sys.__stdout__)
        elif self.view_mode == 'front':
            # For front view, bounds are determined by min/max X and min/max Z of the toolpath or bed height
            current_min_x = min(self.min_x, 0.0) if self.gcode_data else 0.0
            current_max_x = max(self.max_x, self.bed_x) if self.gcode_data else self.bed_x
            current_min_z = min(self.min_z, 0.0) if self.gcode_z_data else 0.0
            current_max_z = max(self.max_z, self.bed_z_max) if self.gcode_z_data else self.bed_z_max # Use bed_z_max
            self.calculated_content_bounds = QRectF(current_min_x, current_min_z, 
                                                    current_max_x - current_min_x, 
                                                    current_max_z - current_min_z)
            print(f"GCodeViewer: Content bounds (front view) updated: ({self.calculated_content_bounds.x():.1f}, {self.calculated_content_bounds.y():.1f}, {self.calculated_content_bounds.width():.1f}, {self.calculated_content_bounds.height():.1f})", file=sys.__stdout__)
        
        # Ensure calculated bounds have non-zero width/height for scale calculation
        if self.calculated_content_bounds.width() == 0:
            self.calculated_content_bounds.setWidth(1.0)
        if self.calculated_content_bounds.height() == 0:
            self.calculated_content_bounds.setHeight(1.0)


    def paintEvent(self, event):
        """
        Handles the painting of the G-code preview.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        widget_width = self.width()
        widget_height = self.height()
        
        # print(f"GCodeViewer: Widget size: {widget_width}x{widget_height}", file=sys.__stdout__) # Too verbose

        # Calculate scale and translation
        content_width = self.calculated_content_bounds.width()
        content_height = self.calculated_content_bounds.height()

        # Add a margin around the content
        margin = 20 # pixels
        scale_x = (widget_width - 2 * margin) / content_width 
        scale_y = (widget_height - 2 * margin) / content_height
        scale = min(scale_x, scale_y)
        # print(f"GCodeViewer: Scale calculated: {scale}", file=sys.__stdout__) # Too verbose

        # Apply initial translation for margin
        painter.translate(margin, margin)
        painter.scale(scale, scale)

        # Further transformations based on view mode and content bounds
        if self.view_mode == 'top':
            # Invert Y-axis so printer's Y+ (towards back) goes upwards on screen
            # Also, shift origin so (0,0) G-code maps to the bottom-left of the scaled content area.
            
            # Translate to compensate for negative min_x/min_y if any
            # Then translate to move the content to the bottom of the widget area after vertical flip
            # The + self.calculated_content_bounds.height() performs the vertical flip (Y becomes -Y)
            # and moves the content's bottom edge to what was its top edge.
            painter.translate(-self.calculated_content_bounds.x(), 
                              -self.calculated_content_bounds.y() + self.calculated_content_bounds.height())
            painter.scale(1, -1) # Flip Y-axis

            # Draw Bed Outline (XY plane)
            painter.setPen(QPen(QColor("#FFFFFF"), 1 / scale)) # Make pen thickness scale with zoom
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(0, 0, int(self.bed_x), int(self.bed_y)) # Cast to int for drawRect
            # print(f"GCodeViewer: Bed drawn (top view): 0,0 to {self.bed_x:.1f},{self.bed_y:.1f}", file=sys.__stdout__) # Too verbose

        elif self.view_mode == 'front':
            # Map G-code X to screen X, G-code Z to screen Y.
            # Z increases upwards, so we need to flip the Y-axis (which is now Z).
            # Shift origin so (0,0) G-code maps to bottom-left of drawing area.

            # Translate to compensate for negative min_x/min_z if any
            # Then translate to move the content to the bottom of the widget area after vertical flip
            # The + self.calculated_content_bounds.height() (which is max_z in this view)
            # performs the vertical flip and positions the bottom of the model at the bottom of the drawing area.
            painter.translate(-self.calculated_content_bounds.x(), 
                              -self.calculated_content_bounds.y() + self.calculated_content_bounds.height())
            painter.scale(1, -1) # Flip Y-axis (which is now Z)

            # Draw Bed Outline (XZ plane - bottom edge of the bed and max Z height line)
            painter.setPen(QPen(QColor("#FFFFFF"), 1 / scale))
            painter.setBrush(Qt.NoBrush)
            
            # Bottom edge of the bed (X from 0 to bed_x, Z=0)
            painter.drawLine(0, 0, int(self.bed_x), 0)
            
            # Line representing max Z height of the build volume
            painter.drawLine(0, int(self.bed_z_max), int(self.bed_x), int(self.bed_z_max))
            
            # Side lines (vertical edges of the build volume)
            painter.drawLine(0, 0, 0, int(self.bed_z_max))
            painter.drawLine(int(self.bed_x), 0, int(self.bed_x), int(self.bed_z_max))

            # print(f"GCodeViewer: Bed drawn (front view): X 0-{self.bed_x:.1f}, Z 0-{self.bed_z_max:.1f}", file=sys.__stdout__) # Too verbose


        # Draw G-code toolpath
        if self.gcode_data and len(self.gcode_data) == len(self.gcode_z_data) and len(self.gcode_data) > 1:
            # Changed to semi-transparent green (alpha 150 out of 255)
            painter.setPen(QPen(QColor(0, 204, 0, 150), 0.5 / scale)) # Thin semi-transparent green line
            path = QPainterPath()
            
            # Start path at the first point
            first_point_xy = self.gcode_data[0]
            first_z = self.gcode_z_data[0]
            
            if self.view_mode == 'top':
                path.moveTo(first_point_xy.x(), first_point_xy.y())
            elif self.view_mode == 'front':
                path.moveTo(first_point_xy.x(), first_z)

            for i in range(1, len(self.gcode_data)):
                current_point_xy = self.gcode_data[i]
                current_z = self.gcode_z_data[i]
                
                if self.view_mode == 'top':
                    path.lineTo(current_point_xy.x(), current_point_xy.y())
                elif self.view_mode == 'front':
                    path.lineTo(current_point_xy.x(), current_z)

            painter.drawPath(path)
            # print(f"GCodeViewer: Attempting to draw {len(self.gcode_data)} toolpath points.", file=sys.__stdout__) # Too verbose

        # Draw Snapshot Points (prioritize processed, then fallback to layer starts)
        points_to_draw = []
        if self.processed_snapshot_points:
            points_to_draw = self.processed_snapshot_points
            print(f"DEBUG: GCodeViewer: Drawing {len(points_to_draw)} processed snapshot points.", file=sys.__stdout__)
        elif self.layer_start_points:
            points_to_draw = self.layer_start_points
            print(f"DEBUG: GCodeViewer: No processed snapshots, drawing {len(points_to_draw)} layer start points (potential snapshots).", file=sys.__stdout__)
        
        if points_to_draw:
            painter.setBrush(QColor("#FF0000")) # Red dot
            painter.setPen(Qt.NoPen) # No outline for dots
            dot_size = 2.0 / scale # Size scales with zoom, so dots remain visible
            for point_xy, z_val in points_to_draw:
                if self.view_mode == 'top':
                    # Draw a small circle for each snapshot point (XY)
                    painter.drawEllipse(point_xy.x() - dot_size / 2, point_xy.y() - dot_size / 2, dot_size, dot_size)
                elif self.view_mode == 'front':
                    # Draw a small circle for each snapshot point (X and Z)
                    painter.drawEllipse(point_xy.x() - dot_size / 2, z_val - dot_size / 2, dot_size, dot_size)

        painter.end()


# For testing the GCodeViewer independently (optional)
if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = GCodeViewer()
    viewer.set_bed_dimensions(220, 220, 250) # Example bed dimensions

    # Simulate data as main.py now provides it: list of (QPointF(x,y), z_value)
    test_points_with_z = [
        (QPointF(50, 50), 0.0), (QPointF(150, 50), 0.0), (QPointF(150, 150), 0.0), (QPointF(50, 150), 0.0), (QPointF(50, 50), 0.0), # Layer 0
        (QPointF(50, 50), 0.2), (QPointF(160, 50), 0.2), (QPointF(160, 160), 0.2), (QPointF(50, 160), 0.2), (QPointF(50, 50), 0.2), # Layer 1 (Z increases by 0.2)
        (QPointF(60, 60), 0.4), (QPointF(170, 60), 0.4), (QPointF(170, 170), 0.4), (QPointF(60, 170), 0.4), (QPointF(60, 60), 0.4), # Layer 2
        (QPointF(70, 70), 0.6), (QPointF(180, 70), 0.6), (QPointF(180, 180), 0.6), (QPointF(70, 180), 0.6), (QPointF(70, 70), 0.6), # Layer 3
    ]
    
    # Simulate layer start points (e.g., the first point of each new layer)
    # In a real scenario, this list would come from GCodeParseThread in main.py
    simulated_layer_starts = [
        (QPointF(50, 50), 0.0), # Start of layer 0
        (QPointF(50, 50), 0.2), # Start of layer 1
        (QPointF(60, 60), 0.4), # Start of layer 2
        (QPointF(70, 70), 0.6), # Start of layer 3
    ]

    viewer.set_gcode_data(test_points_with_z)
    viewer.set_layer_start_points(simulated_layer_starts) # Set the layer start points

    # Test top view
    viewer.set_view_mode('top')
    viewer.setWindowTitle("GCodeViewer - Top View Test (with Snapshots)")
    viewer.show()

    # Simulate processed snapshot points (after "Go!" button clicked)
    # These would typically be a subset or modified version of layer_start_points
    simulated_processed_snapshots = [
        (QPointF(50, 50), 0.0),
        (QPointF(60, 60), 0.4) # Only layer 0 and layer 2 snapshots
    ]
    # This call is now handled by parse_and_set_processed_snapshot_points
    # viewer.set_processed_snapshot_points(simulated_processed_snapshots) 


    # Create a second viewer for front view to demonstrate
    viewer_front = GCodeViewer()
    viewer_front.set_bed_dimensions(220, 220, 250)
    viewer_front.set_gcode_data(test_points_with_z)
    viewer_front.set_layer_start_points(simulated_layer_starts) 
    # This call is now handled by parse_and_set_processed_snapshot_points
    # viewer_front.set_processed_snapshot_points(simulated_processed_snapshots) # Also pass processed points

    # Example of how parse_and_set_processed_snapshot_points would be called in main.py
    sample_processed_gcode = """
G90
G28 X Y Z
;LAYER:0
G1 X100 Y100 F6000
G1 E5 F300
G0 X110 Y110 Z0.2 F9000
G4 P500
TIMELAPSE_TAKE_FRAME
G0 X100 Y100 Z0.0 F9000
G91
G1 E-5 F2400
G90
G1 X50 Y50 Z0.2 E10 F1200
;LAYER:1
G1 X100 Y100 F6000
G0 X120 Y120 Z0.4 F9000
G4 P500
TIMELAPSE_TAKE_FRAME
G0 X100 Y100 Z0.2 F9000
G91
G1 E-5 F2400
G90
G1 X50 Y50 Z0.4 E20 F1200
;LAYER:2
G1 X100 Y100 F6000
G1 X130 Y130 Z0.6 F9000
G4 P500
TIMELAPSE_TAKE_FRAME
G0 X100 Y100 Z0.4 F9000
G91
G1 E-5 F2400
G90
G1 X50 Y50 Z0.6 E30 F1200
"""
    viewer_front.parse_and_set_processed_snapshot_points(sample_processed_gcode.splitlines(), debug_mode=True)

    viewer_front.set_view_mode('front')
    viewer_front.setWindowTitle("GCodeViewer - Front View Test (with In-Viewer Snapshot Parsing)")
    viewer_front.show() 

    sys.exit(app.exec_())
