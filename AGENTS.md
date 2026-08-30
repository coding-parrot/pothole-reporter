# Repository instructions

## Full-frame pothole-detection invariant

- Every pothole-detection input must preserve the complete camera frame from edge to edge.
- Never spatially crop, tile, mask, extract a road band, or substitute a region of interest for the full frame in live detection, saved-frame replay, evaluation, training-data preparation, or exported evidence.
- Whole-frame orientation correction, downscaling, compression, and whole-frame low-light enhancement are allowed only when they are applied to the entire image and preserve the original field of view.
- Every temporal view must independently be a complete frame. A separate lower-road or candidate crop must never be added as model evidence.
- Tests must fail if a pothole-detection crop path or crop-specific prompt language is introduced again.
