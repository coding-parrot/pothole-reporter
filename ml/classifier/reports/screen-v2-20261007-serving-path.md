# Screen v2 scorecard (screen-v2-20261007)

Thresholds come from the validation split only. Recall is of frames the teacher called damaged; cleared is the share of undamaged frames the screen would answer itself.

## At the validation-98% threshold

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.1% (561/566), 42.3% | 98.5% (135/137), 84.8% | 99.3% (426/429), 7.0% | 99.0% (2440/2464), 7.9% | 94.4% (168/178), 25.3% | 98.8% (3169/3208), 29.5% |
| served_road-screen-v2-mobilenetv3l-448 | 92.0% (521/566), 84.2% | 93.4% (128/137), 93.7% | 91.6% (393/429), 76.3% | 99.1% (2441/2464), 20.9% | 96.1% (171/178), 58.3% | 97.7% (3133/3208), 61.4% |

Stable frames only (the teacher gave the same answer twice):

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.2% (514/518), 43.4% | 99.2% (123/124), 86.1% | 99.2% (391/394), 7.2% | 99.1% (2350/2371), 8.8% | 95.0% (153/161), 26.2% | 98.9% (3017/3050), 31.1% |
| served_road-screen-v2-mobilenetv3l-448 | 92.5% (479/518), 86.0% | 92.7% (115/124), 94.7% | 92.4% (364/394), 78.6% | 99.2% (2352/2371), 22.9% | 96.3% (155/161), 61.0% | 97.9% (2986/3050), 64.4% |

## At the validation-99% threshold

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.6% (564/566), 37.5% | 98.5% (135/137), 78.6% | 100.0% (429/429), 3.5% | 99.6% (2453/2464), 6.5% | 97.2% (173/178), 12.2% | 99.4% (3190/3208), 24.7% |
| served_road-screen-v2-mobilenetv3l-448 | 96.1% (544/566), 77.4% | 97.8% (134/137), 91.5% | 95.6% (410/429), 65.7% | 99.8% (2458/2464), 10.1% | 97.8% (174/178), 45.7% | 99.0% (3176/3208), 52.6% |

Stable frames only (the teacher gave the same answer twice):

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.8% (517/518), 38.6% | 99.2% (123/124), 79.7% | 100.0% (394/394), 3.6% | 99.6% (2362/2371), 7.3% | 97.5% (157/161), 12.9% | 99.5% (3036/3050), 26.1% |
| served_road-screen-v2-mobilenetv3l-448 | 96.1% (498/518), 79.2% | 97.6% (121/124), 92.7% | 95.7% (377/394), 67.7% | 99.8% (2367/2371), 11.0% | 98.1% (158/161), 47.9% | 99.1% (3023/3050), 55.3% |

## At the threshold that keeps 98% recall on every validation source

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.6% (564/566), 37.2% | 98.5% (135/137), 78.1% | 100.0% (429/429), 3.3% | 99.6% (2454/2464), 6.4% | 97.2% (173/178), 11.5% | 99.5% (3191/3208), 24.4% |
| served_road-screen-v2-mobilenetv3l-448 | 97.5% (552/566), 74.7% | 98.5% (135/137), 90.9% | 97.2% (417/429), 61.4% | 99.9% (2461/2464), 7.0% | 98.3% (175/178), 38.1% | 99.4% (3188/3208), 49.2% |

Stable frames only (the teacher gave the same answer twice):

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 99.8% (517/518), 38.2% | 99.2% (123/124), 79.3% | 100.0% (394/394), 3.4% | 99.6% (2362/2371), 7.2% | 97.5% (157/161), 12.1% | 99.5% (3036/3050), 25.8% |
| served_road-screen-v2-mobilenetv3l-448 | 97.7% (506/518), 76.5% | 98.4% (122/124), 92.1% | 97.5% (384/394), 63.3% | 99.9% (2368/2371), 7.7% | 98.1% (158/161), 40.0% | 99.4% (3032/3050), 51.7% |

## Validation, by source, at the validation-98% threshold

| Variant | drive_video | rad_bengaluru | rdd2022_czech | rdd2022_india | rome_road_damage |
|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 0.0% (0/1), 91.2% | 94.1% (302/321), 22.3% | 97.5% (236/242), 8.5% | 100.0% (485/485), 6.8% | 98.4% (1199/1218), 7.8% |
| served_road-screen-v2-mobilenetv3l-448 | 0.0% (0/1), 100.0% | 99.1% (318/321), 49.7% | 91.3% (221/242), 66.0% | 96.1% (466/485), 77.2% | 99.9% (1217/1218), 2.5% |

## Annotated potholes, owner labels (validation-98% threshold)

| Variant | Annotated-pothole frames flagged, per slice | Owner potholes flagged | Owner not_pothole cleared | Owner video events flagged |
|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | old_test_all: 264/266; old_test_rdd_india: 264/266; new_test_irdd_iraq: 1484/1495; all_test: 1748/1761 | 8/9 | 1/2 | 2/2 |
| served_road-screen-v2-mobilenetv3l-448 | old_test_all: 230/266; old_test_rdd_india: 230/266; new_test_irdd_iraq: 1487/1495; all_test: 1717/1761 | 9/9 | 1/2 | 2/2 |

## The teacher against itself

| Slice | Damaged, first answer | Damaged both times | Teacher-as-screen recall | Undamaged both times |
|---|---|---|---|---|
| old_test_all | 566 | 518 | 91.5% | 97.1% |
| old_test_drive | 137 | 124 | 90.5% | 98.4% |
| old_test_rdd_india | 429 | 394 | 91.8% | 96.1% |
| new_test_irdd_iraq | 2464 | 2371 | 96.2% | 89.3% |
| new_test_rad_bengaluru | 178 | 161 | 90.4% | 94.9% |
| all_test | 3208 | 3050 | 95.1% | 94.5% |

## AUC and the best possible clearing at 98% slice recall

| Variant | old_test_all | old_test_drive | old_test_rdd_india | new_test_irdd_iraq | new_test_rad_bengaluru | all_test |
|---|---|---|---|---|---|---|
| served_road-screen-v1-mobilenetv3l-448 | 0.882, 49.0% | 0.954, 85.5% | 0.865, 25.4% | 0.710, 12.2% | 0.703, 7.5% | 0.801, 33.9% |
| served_road-screen-v2-mobilenetv3l-448 | 0.952, 72.7% | 0.977, 91.4% | 0.934, 47.5% | 0.873, 34.9% | 0.918, 45.7% | 0.932, 59.5% |

## v2 against v1

Both at the validation-98% threshold of the same validation split (the gate), then against v1 as deployed today. A slice is a win only when recall and cleared share are both not lower. The matched column moves v1's threshold until it has v2's recall on that slice, to show which model clears more at equal recall.

### served_road-screen-v2-mobilenetv3l-448, gate: v1 calibrated on the same validation: beats v1 on every slice: False

| Slice | v2 recall, cleared | v1 recall, cleared | v1 cleared at v2's recall | Verdict |
|---|---|---|---|---|
| slices:old_test_all | 92.0%, 84.2% | 99.1%, 42.3% | 60.5% | mixed |
| slices:old_test_drive | 93.4%, 93.7% | 98.5%, 84.8% | 90.6% | mixed |
| slices:old_test_rdd_india | 91.6%, 76.3% | 99.3%, 7.0% | 56.2% | mixed |
| slices:new_test_irdd_iraq | 99.1%, 20.9% | 99.0%, 7.9% | 7.7% | v2 wins |
| slices:new_test_rad_bengaluru | 96.1%, 58.3% | 94.4%, 25.3% | 13.7% | v2 wins |
| slices:all_test | 97.7%, 61.4% | 98.8%, 29.5% | 35.5% | mixed |
| stable_slices:old_test_all | 92.5%, 86.0% | 99.2%, 43.4% |  | mixed |
| stable_slices:old_test_drive | 92.7%, 94.7% | 99.2%, 86.1% |  | mixed |
| stable_slices:old_test_rdd_india | 92.4%, 78.6% | 99.2%, 7.2% |  | mixed |
| stable_slices:new_test_irdd_iraq | 99.2%, 22.9% | 99.1%, 8.8% |  | v2 wins |
| stable_slices:new_test_rad_bengaluru | 96.3%, 61.0% | 95.0%, 26.2% |  | v2 wins |
| stable_slices:all_test | 97.9%, 64.4% | 98.9%, 31.1% |  | mixed |
| annotated_pothole:old_test_all | 86.5%, n/a | 99.2%, n/a |  | v1 wins |
| annotated_pothole:old_test_rdd_india | 86.5%, n/a | 99.2%, n/a |  | v1 wins |
| annotated_pothole:new_test_irdd_iraq | 99.5%, n/a | 99.3%, n/a |  | v2 wins |
| annotated_pothole:all_test | 97.5%, n/a | 99.3%, n/a |  | v1 wins |
| owner_images | [9, 1] | [8, 1] |  | v2 wins |
| owner_video_events | 2 | 2 |  | tie |

### served_road-screen-v2-mobilenetv3l-448, v1 as deployed: beats v1 on every slice: False

| Slice | v2 recall, cleared | v1 recall, cleared | v1 cleared at v2's recall | Verdict |
|---|---|---|---|---|
| slices:old_test_all | 92.0%, 84.2% | 94.0%, 56.5% | 60.5% | mixed |
| slices:old_test_drive | 93.4%, 93.7% | 82.5%, 93.1% | 90.6% | v2 wins |
| slices:old_test_rdd_india | 91.6%, 76.3% | 97.7%, 26.2% | 56.2% | mixed |
| slices:new_test_irdd_iraq | 99.1%, 20.9% | 92.9%, 24.5% | 7.7% | mixed |
| slices:new_test_rad_bengaluru | 96.1%, 58.3% | 68.5%, 63.9% | 13.7% | mixed |
| slices:all_test | 97.7%, 61.4% | 91.7%, 47.7% | 35.5% | v2 wins |
| stable_slices:old_test_all | 92.5%, 86.0% | 94.0%, 57.9% |  | mixed |
| stable_slices:old_test_drive | 92.7%, 94.7% | 83.1%, 94.2% |  | v2 wins |
| stable_slices:old_test_rdd_india | 92.4%, 78.6% | 97.5%, 27.1% |  | mixed |
| stable_slices:new_test_irdd_iraq | 99.2%, 22.9% | 93.1%, 26.7% |  | mixed |
| stable_slices:new_test_rad_bengaluru | 96.3%, 61.0% | 73.3%, 64.5% |  | mixed |
| stable_slices:all_test | 97.9%, 64.4% | 92.2%, 49.8% |  | v2 wins |
| annotated_pothole:old_test_all | 86.5%, n/a | 95.1%, n/a |  | v1 wins |
| annotated_pothole:old_test_rdd_india | 86.5%, n/a | 95.1%, n/a |  | v1 wins |
| annotated_pothole:new_test_irdd_iraq | 99.5%, n/a | 93.8%, n/a |  | v2 wins |
| annotated_pothole:all_test | 97.5%, n/a | 94.0%, n/a |  | v2 wins |
| owner_images | [9, 1] | [8, 2] |  | mixed |
| owner_video_events | 2 | 2 |  | tie |

