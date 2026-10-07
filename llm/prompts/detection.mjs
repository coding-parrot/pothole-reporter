export const detectionPrompt = Object.freeze({
  id: "road_damage_detection",
  version: "road-damage-v5",
  schemaVersion: 4,
  role: "user",
  schemaName: "road_damage_assessment",
  base: `Inspect the single supplied road image for a civic complaint app.

Classify visible damage to the road surface or track. A road may be asphalt, concrete, gravel, dirt, or mud. Genuine damage still counts when it is at the edge of the road, beside a kerb, or where the road meets a footpath. Do not reject road damage merely because of its surface material or position.

First identify the actual road or track boundary. Edge damage counts only when the visible cavity, breakup, rut, or depression physically affects that road surface. Rubble, excavated soil, building work, or damage to a footpath, gutter, drain, shoulder, or plot beyond an intact road boundary does not become road damage merely because it is adjacent to the road.

Do not classify an intact footpath, kerb, drain, manhole, speed breaker, shadow, stain, water patch, loose roadside debris, or damage confined outside the road as road damage. Use the structured-output field descriptions for the exact definitions and examples.`,
  // The detector is told what it is looking at: one whole frame. The native contract
  // and AGENTS.md carry the same sentence, so a crop cannot be introduced on one side
  // of the app without contradicting the prompt on the other.
  captureLayouts: Object.freeze({
    manual: "\nCapture source: one user-framed image."
      + " No image is cropped, tiled, masked, or limited to a region of interest.",
    drive: "\nCapture source: one automatically selected Drive Mode frame."
      + " No image is cropped, tiled, masked, or limited to a region of interest.",
  }),
  languageSuffixes: Object.freeze({
    en: "",
    kn: "\nWrite the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ).",
  }),
  evaluationVariants: Object.freeze({}),
  schema: Object.freeze({
    type: "object",
    description: "A binary road-damage assessment for one image. Every field is required; use null only where its field definition permits it.",
    additionalProperties: false,
    required: ["image_quality", "assessment", "damage_type", "size", "description"],
    properties: {
      image_quality: {
        type: "string",
        enum: ["acceptable", "rejected"],
        description: "Whether the image shows enough of the relevant road for a reliable decision. acceptable includes moderate blur or low light when the surface remains identifiable. rejected means severe blur, darkness, glare, obstruction, or distance prevents judgment. Example: a visible cavity with slightly blurred surroundings is acceptable; a road fully hidden by a vehicle is rejected.",
      },
      assessment: {
        type: "string",
        enum: ["damaged", "undamaged"],
        description: "Whether visible road-surface damage exists. Road-edge damage and potholes on asphalt, concrete, gravel, dirt, or mud roads are damaged when actually visible within the road or track boundary. Roadside rubble, construction soil, or damage confined beyond an intact edge to a footpath, gutter, drain, shoulder, or plot is undamaged. Never infer road damage merely from a rough roadside area. When image_quality is rejected and damage cannot be judged, use undamaged. Example: an open cavity beside the kerb but visibly within the road is damaged; broken paving behind an intact kerb while the road remains smooth is undamaged.",
      },
      damage_type: {
        type: ["string", "null"],
        enum: [
          "pothole_cavity", "failed_patch", "surface_breakup",
          "rut_or_depression", "other_road_damage", null,
        ],
        description: "The best factual subtype when image_quality is acceptable and assessment is damaged; otherwise null. pothole_cavity is a localized hole or missing material, including on an unpaved road. failed_patch is a prior road repair that has broken, sunk, or opened. surface_breakup is broad crumbling or material loss visibly affecting the road. rut_or_depression is a visibly sunken wheel track or road area without an open cavity, not merely the normal drop into a gutter or unpaved roadside. other_road_damage is serious visible road damage outside those types. Example: a tyre-width hole in a dirt road is pothole_cavity; loose construction rubble behind a kerb has damage_type null.",
      },
      size: {
        type: ["string", "null"],
        enum: ["small", "medium", "large", null],
        description: "Estimated maximum width or cluster extent: small below 30 cm, medium 30 to 60 cm, and large above 60 cm. Use null when scale is not defensible or assessment is undamaged. Example: a 20 cm cavity is small, a roughly 45 cm cavity is medium, and a broken area spanning most of a traffic lane is large.",
      },
      description: {
        type: "string",
        description: "At most eight words naming the defect and where it is, or the decisive non-damage cue. Example: Open cavity at the left road edge.",
      },
    },
  }),
});
