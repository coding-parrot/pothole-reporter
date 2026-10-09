"""YOLOX-S, one class (pothole), 640 px. The same recipe as pothole_tiny.py, wider."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pothole_tiny import Exp as Tiny  # noqa: E402


class Exp(Tiny):
    def __init__(self):
        super().__init__()
        self.width = 0.50
        self.exp_name = "pothole_s"
