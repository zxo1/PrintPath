import sys
from PyQt5.QtWidgets import QWidget, QApplication, QMenu
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QTransform
from PyQt5.QtCore import Qt, QRectF, QPointF # Corrected: QPointF imported from QtCore

class GCodeViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gcode_data = []  # Stores list of (QPointF(x, y), z_value) for toolpath
        self.layer_start_points = [] # Stores (QPointF(x,y), z_value) for initial layer moves
        self.processed_snapshot_points = [] # Stores (QPointF(x,y), z_value) for script-inserted snapshots

        self.bed_x = 220.0
        self.bed_y = 220.0
        self.max_z = 250.0 # Maximum Z height of the print bed/volume

        self.view_mode = 'top' # 'top' for XY view, 'front' for XZ view

        self.scale_factor = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.panning = False
        self.last_pos = None

        self.setMouseTracking(True) # Enable mouse tracking for hover effects

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Set strong focus policy to receive key events
        self.setFocusPolicy(Qt.StrongFocus)

        self.debug_mode = False # Internal debug mode, controllable by main app

    def set_debug_mode(self, enabled):
        """Enables or disables internal debug logging."""
        self.debug_mode = enabled
        if self.debug_mode:
            print("GCodeViewer: Debug mode enabled.", file=sys.__stdout__)
        else:
            print("GCodeViewer: Debug mode disabled.", file=sys.__stdout__)
        self.update() # Redraw to reflect any debug-related visual changes

    def log_debug(self, message):
        """Logs a debug message if debug_mode is enabled."""
        if self.debug_mode:
            print(f"GCodeViewer DEBUG: {message}", file=sys.__stdout__)

    def set_bed_dimensions(self, x, y, max_z=250.0):
        """Sets the bed dimensions and maximum Z height for scaling."""
        self.bed_x = max(1.0, float(x)) # Ensure dimensions are positive
        self.bed_y = max(1.0, float(y))
        self.max_z = max(1.0, float(max_z))
        self.log_debug(f"Bed dimensions set to X:{self.bed_x:.1f}, Y:{self.bed_y:.1f}, Max Z:{self.max_z:.1f}")
        self.fit_to_view() # Recalculate scale and offset
        self.update() # Request a repaint

    def set_gcode_data(self, data):
        """
        Sets the G-code toolpath data.
        Data is expected to be a list of (QPointF(x, y), z_value) tuples.
        """
        self.gcode_data = data
        self.log_debug(f"G-code toolpath data set with {len(data)} points.")
        self.fit_to_view() # Recalculate scale and offset to fit new data
        self.update() # Request a repaint

    def set_layer_start_points(self, points):
        """
        Sets the layer start points for visualization.
        Data is expected to be a list of (QPointF(x, y), z_value) tuples.
        """
        self.layer_start_points = points
        self.log_debug(f"Layer start points set with {len(points)} points.")
        self.update()

    def set_processed_snapshot_points(self, points):
        """
        Sets the processed snapshot points for visualization from the script.
        Data is expected to be a list of (x_coord, y_coord, z_value) tuples.
        """
        # Convert raw (x,y,z) tuples from script to (QPointF(x,y), z) for internal use
        self.processed_snapshot_points = []
        for x, y, z in points:
            self.processed_snapshot_points.append((QPointF(x, y), z))
        self.log_debug(f"Processed snapshot points set with {len(self.processed_snapshot_points)} points.")
        self.update()

    def set_view_mode(self, mode):
        """Sets the view mode ('top' or 'front')."""
        if mode in ['top', 'front']:
            self.view_mode = mode
            self.log_debug(f"View mode set to: {mode}")
            self.fit_to_view() # Recalculate scale and offset for new view
            self.update()
        else:
            self.log_debug(f"Invalid view mode: {mode}. Must be 'top' or 'front'.")

    def fit_to_view(self):
        """Calculates scale and offset to fit the entire bed/print into the view."""
        if self.width() <= 0 or self.height() <= 0:
            return # Avoid division by zero

        padding_ratio = 0.95 # Use 95% of the widget size for content

        if self.view_mode == 'top':
            # Fit XY plane
            content_width = self.bed_x
            content_height = self.bed_y
        else: # 'front' view
            # Fit XZ plane (or YZ if preferred, but XZ is common for front view)
            content_width = self.bed_x
            content_height = self.max_z # Use max_z for height in front view

        if content_width <= 0 or content_height <= 0: # Avoid division by zero
            self.scale_factor = 1.0
            self.offset_x = 0
            self.offset_y = 0
            return

        # Calculate scale based on the smaller of width/height ratios to ensure fit
        scale_x = (self.width() * padding_ratio) / content_width
        scale_y = (self.height() * padding_ratio) / content_height
        self.scale_factor = min(scale_x, scale_y)

        # Calculate offset to center the content initially
        self.offset_x = (self.width() - content_width * self.scale_factor) / 2.0
        self.offset_y = (self.height() - content_height * self.scale_factor) / 2.0

        self.log_debug(f"Fit to view: Scale={self.scale_factor:.2f}, Offset=({self.offset_x:.2f}, {self.offset_y:.2f}) for mode '{self.view_mode}'.")


    def paintEvent(self, event):
        """Paints the G-code toolpath and bed."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Fill background
        painter.fillRect(self.rect(), QColor("#222222")) # Dark background

        full_transform = QTransform()

        # Step 1: Translate to center the content (based on offset_x, offset_y from fit_to_view)
        full_transform.translate(self.offset_x, self.offset_y)

        # Step 2: Apply the overall content scaling
        full_transform.scale(self.scale_factor, self.scale_factor)

        # Step 3: Apply view-specific transformations (flip and Z-origin shift for front view)
        if self.view_mode == 'front':
            # Scale Y by -1 to flip (positive world Z goes up on screen)
            full_transform.scale(1, -1)
            # Translate so that Z=0 (bed) appears at the bottom of the scaled height.
            # After scaling and flipping, world_Z=0 is at Y=0, world_Z=max_Z is at Y=-max_Z.
            # To move Z=0 (which is currently at Y=0 of the flipped/scaled content)
            # to the bottom of the *scaled total height* (max_z * scale_factor),
            # we need to translate the origin downwards by this amount.
            full_transform.translate(0, -self.max_z) # This translates by the world max_z.
                                                      # When scaled later this becomes max_z * scale_factor.
                                                      # It means Z=0 (which is at Y=0 in the flipped space)
                                                      # is moved down by self.max_z. This places Z=0 at the
                                                      # bottom of the transformed space.


        painter.setTransform(full_transform)

        # Draw print bed
        painter.setPen(QPen(QColor("#666666"), 1 / self.scale_factor)) # Scale pen width
        painter.setBrush(Qt.NoBrush)

        if self.view_mode == 'top':
            bed_rect = QRectF(0, 0, self.bed_x, self.bed_y)
            painter.drawRect(bed_rect)
            # Draw center lines for bed
            painter.drawLine(int(self.bed_x / 2), 0, int(self.bed_x / 2), int(self.bed_y))
            painter.drawLine(0, int(self.bed_y / 2), int(self.bed_x), int(self.bed_y / 2))
            self.log_debug(f"Drawing top view bed: {self.bed_x}x{self.bed_y}")
        else: # 'front' view
            # In front view (XZ), bed is a line at Z=0
            painter.drawLine(0, 0, int(self.bed_x), 0)
            self.log_debug(f"Drawing front view bed line: {self.bed_x} at Z=0")


        # Draw G-code toolpath
        if self.gcode_data:
            painter.setPen(QPen(QColor("#00FFFF"), 0.5 / self.scale_factor)) # Cyan for toolpath
            last_point = None
            for point_qpointf, z_val in self.gcode_data:
                # Decide which coordinates to use based on view mode
                if self.view_mode == 'top':
                    current_point_display = point_qpointf
                else: # 'front' view
                    # X remains X, Y becomes Z
                    current_point_display = QPointF(point_qpointf.x(), z_val)

                if last_point:
                    painter.drawLine(last_point, current_point_display)
                last_point = current_point_display
            self.log_debug(f"Drawing {len(self.gcode_data)} toolpath points in {self.view_mode} view.")

        # Draw detected layer start points (before processing)
        if self.layer_start_points:
            painter.setPen(QPen(QColor("#FFFF00"), 1 / self.scale_factor)) # Yellow circles
            dot_size = 3 / self.scale_factor # Make dot size scale with zoom
            for point_qpointf, z_val in self.layer_start_points:
                if self.view_mode == 'top':
                    display_point = point_qpointf
                else: # 'front' view
                    display_point = QPointF(point_qpointf.x(), z_val)
                
                # Draw a small circle
                # drawEllipse expects int for x, y, w, h
                painter.drawEllipse(int(display_point.x() - dot_size / 2), int(display_point.y() - dot_size / 2), int(dot_size), int(dot_size))
            self.log_debug(f"Drawing {len(self.layer_start_points)} layer start points in {self.view_mode} view.")

        # Draw processed snapshot points (after script processing)
        if self.processed_snapshot_points:
            painter.setPen(QPen(QColor("#FF00FF"), 1 / self.scale_factor)) # Magenta circles
            dot_size = 5 / self.scale_factor # Slightly larger dots for processed points
            font_size = max(1, int(10 / self.scale_factor)) # Scale font size
            font = QFont("Arial", font_size)
            painter.setFont(font)

            for point_xy, z_val in self.processed_snapshot_points:
                if self.view_mode == 'top':
                    display_point = point_xy
                else: # 'front' view
                    display_point = QPointF(point_xy.x(), z_val)

                # Draw a larger circle for snapshots
                # drawEllipse expects int for x, y, w, h
                painter.drawEllipse(int(display_point.x() - dot_size / 2), int(display_point.y() - dot_size / 2), int(dot_size), int(dot_size))
                
                # Optionally draw a number next to the snapshot point (for debugging/identification)
                # Not crucial for normal operation, can be commented out if too cluttered
                # painter.drawText(display_point.x() + dot_size, display_point.y() + dot_size, f"Z:{z_val:.1f}")
            self.log_debug(f"Drawing {len(self.processed_snapshot_points)} processed snapshot points in {self.view_mode} view.")


        painter.end()


    def resizeEvent(self, event):
        """Handles widget resize events."""
        self.log_debug(f"Resize event: New size {event.size().width()}x{event.size().height()}")
        self.fit_to_view() # Recalculate scale and offset on resize
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        """Handles mouse press for panning."""
        if event.button() == Qt.LeftButton:
            self.panning = True
            self.last_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            self.log_debug("Mouse pressed: Starting panning.")

    def mouseReleaseEvent(self, event):
        """Handles mouse release, ending panning."""
        if event.button() == Qt.LeftButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
            self.log_debug("Mouse released: Panning ended.")

    def mouseMoveEvent(self, event):
        """Handles mouse move for panning."""
        if self.panning:
            delta_x = event.x() - self.last_pos.x()
            delta_y = event.y() - self.last_pos.y()
            self.offset_x += delta_x
            self.offset_y += delta_y
            self.last_pos = event.pos()
            self.update() # Request repaint for smooth panning
            self.log_debug(f"Panning: Delta=({delta_x}, {delta_y}), New Offset=({self.offset_x:.2f}, {self.offset_y:.2f})")
        
        # You could add hover coordinates here if desired
        # self.log_debug(f"Mouse moved to: {event.pos().x()}, {event.pos().y()}")


    def wheelEvent(self, event):
        """Handles mouse wheel for zooming."""
        zoom_factor = 1.15 # Zoom by 15%
        # Zoom around the mouse cursor position (a more intuitive zoom)
        mouse_x = event.pos().x()
        mouse_y = event.pos().y()

        # Convert mouse position to "world" coordinates before scaling
        # current_world_x = (mouse_x - self.offset_x) / self.scale_factor
        # current_world_y = (mouse_y - self.offset_y) / self.scale_factor

        if event.angleDelta().y() > 0: # Zoom in
            self.scale_factor *= zoom_factor
            self.log_debug(f"Zooming in. New scale: {self.scale_factor:.2f}")
        else: # Zoom out
            self.scale_factor /= zoom_factor
            self.log_debug(f"Zooming out. New scale: {self.scale_factor:.2f}")
        
        # Re-adjust offset to keep mouse cursor at the same "world" spot
        # new_offset_x = mouse_x - current_world_x * self.scale_factor
        # new_offset_y = mouse_y - current_world_y * self.scale_factor
        # This more complex zoom-to-cursor logic is sometimes tricky with 2D transforms and coordinate systems
        # For simplicity, for now, we'll just zoom around the center and let the user pan.
        # Alternatively, recalculate offset based on top-left of the bounding box of the content
        # For now, just call fit_to_view after zoom to keep it centered if desired, or let panning handle it.
        # Calling fit_to_view would reset the user's pan. For better UX, only fit_to_view on initial load/resize.
        
        self.update() # Request repaint for smooth zooming

    def keyPressEvent(self, event):
        """Handles key presses for additional controls (e.g., reset view)."""
        if event.key() == Qt.Key_R:
            self.log_debug("Key 'R' pressed: Resetting view.")
            self.fit_to_view() # Reset zoom and pan
            self.update()
        super().keyPressEvent(event)


    def show_context_menu(self, pos):
        """Displays a context menu on right-click."""
        menu = QMenu(self)
        reset_action = menu.addAction("Reset View (R)")
        
        action = menu.exec_(self.mapToGlobal(pos))
        if action == reset_action:
            self.fit_to_view()
            self.update()


# This part is typically for testing the viewer in isolation
if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = GCodeViewer()
    viewer.set_debug_mode(True) # Enable debug logging for standalone testing
    viewer.setWindowTitle("G-code Viewer Standalone Test")
    viewer.setGeometry(100, 100, 800, 600)

    # Example G-code data (X,Y,Z) tuples
    # These would normally come from your G-code parser
    example_gcode_data = []
    # Simple line from (10,10,0) to (100,100,10)
    for i in range(101):
        x = 10 + i * 0.9
        y = 10 + i * 0.9
        z = i * 0.1
        example_gcode_data.append((QPointF(x, y), z))
    
    # Simulate a second layer
    for i in range(101):
        x = 100 - i * 0.5
        y = 100 + i * 0.2
        z = 10 + (i * 0.05) # Z starts at 10, goes to 15
        example_gcode_data.append((QPointF(x, y), z))

    viewer.set_gcode_data(example_gcode_data)
    
    # Example layer start points (QPointF, Z)
    example_layer_starts = [
        (QPointF(10, 10), 0.0),
        (QPointF(100, 100), 10.0),
        (QPointF(50, 50), 20.0), # Hypothetical layer start at Z=20
    ]
    viewer.set_layer_start_points(example_layer_starts)

    # Example processed snapshot points (raw x, y, z from script output)
    example_snapshots = [
        (20.0, 20.0, 5.0),
        (50.0, 150.0, 12.0),
        (150.0, 50.0, 18.0),
        (110.0, 110.0, 25.0) # Example snapshot in middle, higher up
    ]
    viewer.set_processed_snapshot_points(example_snapshots)


    viewer.show()
    sys.exit(app.exec_())
