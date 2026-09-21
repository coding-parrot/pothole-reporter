import { createDetector, createSecretProvider } from "./detectors.mjs";
import { createDynamoRepository } from "./dynamo-repository.mjs";
import { createGeolocator } from "./geolocation.mjs";
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
    perInstallDay: Number(process.env.DAILY_VISION_CAP || 500),
    globalMinute: Number(process.env.GLOBAL_VISION_MINUTE_CAP || 60),
    globalDay: Number(process.env.GLOBAL_VISION_DAILY_CAP || 2_000),
    globalMonth: Number(process.env.MONTHLY_VISION_CAP || 20_000),
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
const geolocator = createGeolocator({
  geocoderUrl: process.env.GEOCODER_REVERSE_URL || "",
});

export const handler = createService({ repository, detector, geolocator });
