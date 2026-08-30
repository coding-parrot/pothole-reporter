#!/usr/bin/env python3
"""Focused offline tests for the RAD image-only dataset adapter."""

import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

from PIL import Image


ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "rad_dataset", ROOT / "eval" / "rad_dataset.py"
)
rad = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rad)


DATA_YAML = """train: ../train/images
val: ../valid/images
test: ../test/images

nc: 6
names: ['HMV', 'LMV', 'Pedestrian', 'RoadDamages', 'SpeedBump', 'UnsurfacedRoad']
"""


class RadDatasetTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.dataset = self.root / "images"
        self.dataset.mkdir()
        (self.dataset / "data.yaml").write_text(DATA_YAML, encoding="utf-8")
        for split in ("train", "valid", "test"):
            (self.dataset / split / "images").mkdir(parents=True)
            (self.dataset / split / "labels").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def add_frame(self, source, number, label, split="train", colour=None):
        suffix = f"{number:032x}"[-32:]
        stem = f"{source}-{number}_jpg.rf.{suffix}"
        image_path = self.dataset / split / "images" / f"{stem}.jpg"
        label_path = self.dataset / split / "labels" / f"{stem}.txt"
        Image.new(
            "RGB",
            (16, 12),
            colour or ((number * 31) % 255, 40, 90),
        ).save(image_path, format="JPEG", quality=92)
        label_path.write_text(label, encoding="utf-8")
        return image_path, label_path

    def test_strict_yaml_and_yolo_semantics(self):
        names = rad.parse_data_yaml(self.dataset / "data.yaml")
        label = self.root / "labels.txt"
        label.write_text("3 0.5 0.5 0.25 0.25\n4 0.5 0.5 1 1\n")
        boxes = rad.parse_yolo_labels(label, names)
        self.assertEqual(
            [box["semantic"] for box in boxes],
            ["unreviewed_road_anomaly", "speed_breaker"],
        )
        self.assertNotIn("pothole", {box["semantic"] for box in boxes})

        label.write_text("6 0.5 0.5 0.25 0.25\n")
        with self.assertRaisesRegex(rad.RadDatasetError, "unknown class id"):
            rad.parse_yolo_labels(label, names)
        label.write_text("3 0.1 0.1 0.5 0.5\n")
        with self.assertRaisesRegex(rad.RadDatasetError, "leaves the full frame"):
            rad.parse_yolo_labels(label, names)

        bad_yaml = self.root / "bad.yaml"
        bad_yaml.write_text(DATA_YAML.replace("RoadDamages", "Pothole"))
        with self.assertRaisesRegex(rad.RadDatasetError, "class schema changed"):
            rad.parse_data_yaml(bad_yaml)

    def test_index_builds_only_genuine_full_frame_events(self):
        safe_source = "100_10-07-2023_mp4"
        source_bytes = []
        for number, class_id in ((10, 3), (11, 4), (12, 3), (14, 3)):
            image, _ = self.add_frame(
                safe_source,
                number,
                f"{class_id} 0.5 0.5 0.25 0.25\n",
            )
            source_bytes.append((image, image.read_bytes()))

        # These three numeric frames cannot be assigned to one raw Drive because
        # RAD discarded the folder that distinguished two same-named videos.
        ambiguous = "01_13-06-2023_mp4"
        for number in (1, 2, 3):
            self.add_frame(ambiguous, number, "3 0.5 0.5 0.25 0.25\n")

        # A second Roboflow variant of the same source still must be consolidated,
        # never inserted twice into a chronological event.
        self.add_frame(safe_source, 11, "4 0.5 0.5 0.25 0.25\n", split="test")

        index = rad.build_index(self.root, require_complete=False)
        self.assertEqual(len(index["events"]), 1)
        event = index["events"][0]
        self.assertEqual(event["source_video"], safe_source)
        self.assertEqual(event["frame_numbers"], [10, 11, 12])
        self.assertEqual(len(set(event["frame_ids"])), 3)
        self.assertTrue(event["full_frame"])
        self.assertNotIn(
            ambiguous, {candidate["source_video"] for candidate in index["events"]}
        )
        self.assertTrue(index["input_policy"]["full_frame_only"])
        self.assertTrue(index["input_policy"]["boxes_are_metadata_only"])
        self.assertEqual(index["input_policy"]["spatial_transforms"], [])
        self.assertEqual(
            index["input_policy"]["duplicate_target_semantics"],
            "union across all source-frame variants",
        )
        self.assertNotIn("pothole", set(index["class_semantics"].values()))
        self.assertEqual(
            index["dataset"]["label_limitation"],
            "RoadDamages combines road anomalies and is not pothole ground truth; "
            "it remains unreviewed_road_anomaly until human relabelling.",
        )

        safe_frames = [
            frame for frame in index["frames"] if frame["source_video"] == safe_source
        ]
        self.assertEqual(len(safe_frames), 4)
        duplicate = next(frame for frame in safe_frames if frame["frame_number"] == 11)
        self.assertEqual(duplicate["source_variant_count"], 2)
        self.assertEqual(
            {frame["evaluation_split"] for frame in safe_frames},
            {rad.evaluation_split(safe_source)},
        )
        for path, before in source_bytes:
            self.assertEqual(path.read_bytes(), before, "indexing transformed an input image")

    def test_index_is_deterministic_and_tamper_evident(self):
        source = "100_10-07-2023_mp4"
        for number in (1, 2, 3):
            self.add_frame(source, number, "3 0.5 0.5 0.25 0.25\n")
        first = rad.build_index(self.root, require_complete=False)
        second = rad.build_index(self.root, require_complete=False)
        self.assertEqual(first, second)
        first_path = self.root / "first.json"
        second_path = self.root / "second.json"
        rad.write_index(first_path, first)
        rad.write_index(second_path, second)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(rad.load_index(first_path), first)

        tampered = copy.deepcopy(first)
        tampered["frames"][0]["frame_number"] = 999
        first_path.write_text(json.dumps(tampered))
        with self.assertRaisesRegex(rad.RadDatasetError, "does not match"):
            rad.load_index(first_path)

    def test_release_index_requires_the_exact_official_v3_seal(self):
        source = "100_10-07-2023_mp4"
        for number in (1, 2, 3):
            self.add_frame(source, number, "3 0.5 0.5 0.25 0.25\n")
        fake = rad.build_index(self.root, require_complete=False)
        fake["source_dataset_complete"] = True
        fake["source_dataset_counts"] = copy.deepcopy(rad.EXPECTED_V3_COUNTS)
        fake["counts"] = copy.deepcopy(rad.EXPECTED_V3_INDEX_COUNTS)
        fake.pop("index_sha256")
        fake = rad._sealed(fake, "index_sha256")
        with self.assertRaisesRegex(rad.RadDatasetError, "index seal"):
            rad.validate_official_release_index(fake)

        wrong_counts = copy.deepcopy(fake)
        wrong_counts["counts"]["events"] -= 1
        with self.assertRaisesRegex(rad.RadDatasetError, "index counts"):
            rad.validate_official_release_index(wrong_counts)

        wrong_identity = copy.deepcopy(fake)
        wrong_identity["dataset"]["ref"] = "somebody/fake-rad"
        with self.assertRaisesRegex(rad.RadDatasetError, "dataset identity"):
            rad.validate_official_release_index(wrong_identity)

    def test_duplicate_variant_targets_are_unioned_fail_closed(self):
        source = "200_10-07-2023_mp4"
        for number in (1, 2, 3):
            self.add_frame(source, number, "4 0.5 0.5 0.25 0.25\n", split="test")
        # The lexicographically selected test copy says SpeedBump only, while
        # another full-frame copy says RoadDamages.  It must not enter the eval
        # as a pure speed-breaker hard negative merely because of path order.
        self.add_frame(source, 2, "3 0.5 0.5 0.25 0.25\n", split="train")

        index = rad.build_index(self.root, require_complete=False)
        middle = next(frame for frame in index["frames"] if frame["frame_number"] == 2)
        self.assertEqual(middle["selected_variant_semantic_labels"], ["speed_breaker"])
        self.assertEqual(
            middle["semantic_labels"],
            ["speed_breaker", "unreviewed_road_anomaly"],
        )
        self.assertTrue(middle["target_semantics_are_variant_union"])
        self.assertEqual(middle["source_variant_count"], 2)
        self.assertEqual(
            index["events"][0]["semantic_labels"],
            ["speed_breaker", "unreviewed_road_anomaly"],
        )

    def test_missing_or_orphaned_labels_fail_closed(self):
        image, label = self.add_frame(
            "100_10-07-2023_mp4", 1, "3 0.5 0.5 0.25 0.25\n"
        )
        label.unlink()
        with self.assertRaisesRegex(rad.RadDatasetError, "pairing failed"):
            rad.build_index(self.root, require_complete=False)
        image.unlink()
        orphan = self.dataset / "train" / "labels" / "orphan.txt"
        orphan.write_text("")
        with self.assertRaisesRegex(rad.RadDatasetError, "pairing failed"):
            rad.build_index(self.root, require_complete=False)

    def test_default_index_rejects_an_incomplete_but_paired_subset(self):
        source = "100_10-07-2023_mp4"
        for number in (1, 2, 3):
            self.add_frame(source, number, "3 0.5 0.5 0.25 0.25\n")
        with self.assertRaisesRegex(rad.RadDatasetError, "incomplete or has changed"):
            rad.build_index(self.root)
        development_index = rad.build_index(self.root, require_complete=False)
        self.assertFalse(development_index["source_dataset_complete"])
        self.assertEqual(development_index["source_dataset_counts"]["images"], 3)

    def test_remote_inventory_is_image_only_and_detects_ambiguous_sources(self):
        pages = []
        raw_videos = []
        for number in range(1, 30):
            basename = f"{number:02d}_13-06-2023.mp4"
            raw_videos.extend([
                {"name": f"videos_without_audio/a/{basename}", "totalBytes": 100},
                {"name": f"videos_without_audio/b/{basename}", "totalBytes": 200},
            ])
        pages.append({
            "datasetFiles": [
                {"name": "images/data.yaml", "totalBytes": 155},
                {
                    "name": (
                        "images/train/images/100_10-07-2023_mp4-1_jpg.rf."
                        "00000000000000000000000000000001.jpg"
                    ),
                    "totalBytes": 123,
                },
            ],
            "nextPageToken": "next",
        })
        pages.append({
            "datasetFiles": [
                {
                    "name": (
                        "images/train/labels/100_10-07-2023_mp4-1_jpg.rf."
                        "00000000000000000000000000000001.txt"
                    ),
                    # Kaggle omits totalBytes for an empty label file.
                },
                *raw_videos,
            ]
        })
        calls = []

        def fake_get(url):
            calls.append(url)
            return pages[len(calls) - 1]

        inventory = rad.list_remote_inventory(get_json=fake_get, require_complete=False)
        self.assertEqual(len(calls), 2)
        self.assertIn("datasetVersionNumber=3", calls[0])
        self.assertIn("pageToken=next", calls[1])
        self.assertEqual(inventory["file_count"], 3)
        self.assertEqual(inventory["total_bytes"], 278)
        self.assertTrue(all(
            item["name"].startswith("images/") for item in inventory["files"]
        ))
        self.assertFalse(any(item["name"].endswith(".mp4") for item in inventory["files"]))
        self.assertEqual(
            set(inventory["ambiguous_source_videos"]),
            set(rad.KNOWN_AMBIGUOUS_SOURCE_VIDEOS),
        )
        rad._verify_seal(inventory, "inventory_sha256")

    def test_download_paths_and_size_verification_are_fail_closed(self):
        with self.assertRaises(rad.RadDatasetError):
            rad.validate_remote_image_name("../images/data.yaml")
        with self.assertRaises(rad.RadDatasetError):
            rad.validate_remote_image_name("images/raw.mp4")
        with self.assertRaises(rad.RadDatasetError):
            rad.validate_remote_image_name("videos_without_audio/drive.mp4")

        entry = {"name": "images/data.yaml", "bytes": 3}
        inventory = rad._sealed({
            "schema_version": rad.INVENTORY_SCHEMA_VERSION,
            "dataset_ref": rad.DATASET_REF,
            "dataset_version": rad.DATASET_VERSION,
            "license": rad.DATASET_LICENSE,
            "scope": "images/** only",
            "files": [entry],
            "file_count": 1,
            "total_bytes": 3,
            "ambiguous_source_videos": sorted(rad.KNOWN_AMBIGUOUS_SOURCE_VIDEOS),
        }, "inventory_sha256")
        target = self.root / "images" / "data.yaml"
        target.write_bytes(b"abc")
        self.assertEqual(rad._download_one(self.root, entry), "verified")
        self.assertEqual(rad.verify_download(self.root, inventory)["total_bytes"], 3)
        target.write_bytes(b"ab")
        with self.assertRaisesRegex(rad.RadDatasetError, "wrong-size"):
            rad.verify_download(self.root, inventory)

        empty_entry = {
            "name": (
                "images/train/labels/100_10-07-2023_mp4-1_jpg.rf."
                "00000000000000000000000000000001.txt"
            ),
            "bytes": 0,
        }
        self.assertEqual(rad._download_one(self.root, empty_entry), "created_empty")
        empty_target = self.root.joinpath(*pathlib.PurePosixPath(empty_entry["name"]).parts)
        self.assertTrue(empty_target.is_file())
        self.assertEqual(empty_target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
