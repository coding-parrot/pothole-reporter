import { createDetector, createSecretProvider } from "./detectors.mjs";
import { createDynamoRepository } from "./dynamo-repository.mjs";
import { createCachedGeolocator } from "./geo-cache.mjs";
import { createGeolocator } from "./geolocation.mjs";
import { createNationalCatalogue } from "./national-tenders.mjs";
import { createService } from "./core.mjs";

const repository = createDynamoRepository({
  tables: {
    installations: process.env.INSTALLATIONS_TABLE,
    control: process.env.CONTROL_TABLE,
    usage: process.env.USAGE_TABLE,
    locks: process.env.LOCATION_LOCKS_TABLE,
    potholes: process.env.POTHOLES_TABLE,
    spatial: process.env.SPATIAL_TABLE,
    records: process.env.RECORDS_TABLE,
    tenders: process.env.TENDERS_TABLE,
    metrics: process.env.METRICS_TABLE,
  },
  dedupeRadiusMetres: Number(process.env.DEDUPE_RADIUS_METRES || 30),
  quota: {
    perInstallDay: Number(process.env.DAILY_VISION_CAP || 2000),
    globalMinute: Number(process.env.GLOBAL_VISION_MINUTE_CAP || 300),
    globalDay: Number(process.env.GLOBAL_VISION_DAILY_CAP || 30_000),
    globalMonth: Number(process.env.MONTHLY_VISION_CAP || 200_000),
  },
});

const secretProvider = createSecretProvider({ secretArn: process.env.SHARED_SECRET_ARN });
const detector = createDetector({
  providerMode: process.env.SHARED_DETECTOR_PROVIDER || "openai_then_yolo",
  secretProvider,
  yoloMode: process.env.YOLO_MODE || "lambda",
  yoloFunctionName: process.env.YOLO_FUNCTION_NAME || "",
  yoloUrl: process.env.YOLO_URL || "",
  yoloModel: process.env.YOLO_MODEL || "pothole-yolo",
});
const liveGeolocator = createGeolocator({
  geocoderUrl: process.env.GEOCODER_REVERSE_URL || "",
});
const geolocator = createCachedGeolocator({ geolocator: liveGeolocator, repository });

// The national tender catalogues are staged into the package by deploy.sh (see
// tools/stage-national-tenders.mjs) at the module's default path.
const catalogue = createNationalCatalogue();

export const handler = createService({ repository, detector, geolocator, catalogue });
