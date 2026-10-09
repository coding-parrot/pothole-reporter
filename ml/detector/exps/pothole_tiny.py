"""YOLOX-Tiny, one class (pothole), 640 px, whole frames only.

Mosaic and mixup are off: both cut pictures up and paste the pieces together, and
AGENTS.md forbids cropping or tiling a detection input in training data too. What is
left is a left-right flip, colour jitter and resizing the whole batch between 480 and
800 px.
"""
import os

from yolox.exp import Exp as Base


class Exp(Base):
    def __init__(self):
        super().__init__()
        self.num_classes = 1
        self.depth = 0.33
        self.width = 0.375
        self.input_size = (640, 640)
        self.test_size = (640, 640)
        self.multiscale_range = 5
        self.mosaic_prob = 0.0
        self.mixup_prob = 0.0
        self.enable_mixup = False
        self.hsv_prob = 1.0
        self.flip_prob = 0.5
        self.data_dir = os.environ.get("DET_DATA", "/opt/ml/det/data")
        self.train_ann = "train.json"
        self.val_ann = "val.json"
        self.max_epoch = int(os.environ.get("DET_EPOCHS", "45"))
        self.no_aug_epochs = 5
        self.warmup_epochs = 3
        self.eval_interval = int(os.environ.get("DET_EVAL_INTERVAL", "3"))
        self.data_num_workers = int(os.environ.get("DET_WORKERS", "7"))
        self.test_conf = 0.001
        self.nmsthre = 0.65
        self.output_dir = os.environ.get("DET_RUNS", "/opt/ml/det/runs")
        self.exp_name = "pothole_tiny"

    def get_evaluator(self, batch_size, is_distributed, testdev=False, legacy=False):
        from yolox.evaluators import COCOEvaluator

        return COCOEvaluator(
            dataloader=self.get_eval_loader(batch_size, is_distributed, testdev=testdev, legacy=legacy),
            img_size=self.test_size, confthre=self.test_conf, nmsthre=self.nmsthre,
            num_classes=self.num_classes, testdev=testdev, per_class_AP=False, per_class_AR=False)
