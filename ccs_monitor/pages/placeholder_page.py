from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, page_name: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel(f"{page_name}功能开发中")
        label.setObjectName("placeholderText")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

