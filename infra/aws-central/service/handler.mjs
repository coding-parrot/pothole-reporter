import { createDetector, createSecretProvider } from "./detectors.mjs";
import { createDynamoRepository } from "./dynamo-repository.mjs";
import { createCachedGeolocator } from "./geo-cache.mjs";
import { createGeolocator } from "./geolocation.mjs";
import { createLocalAddress } from "./local-address.mjs";
import { createNationalCatalogue } from "./national-tenders.mjs";
import { createService } from "./core.mjs";
import { createWarmHandler } from "./warm.mjs";

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
    perInstallDay: Number(process.env.DAILY_VISION_CAP || 10000),
    globalMinute: Number(process.env.GLOBAL_VISION_MINUTE_CAP || 1500),
    globalDay: Number(process.env.GLOBAL_VISION_DAILY_CAP || 100_000),
    globalMonth: Number(process.env.MONTHLY_VISION_CAP || 1_000_000),
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
// Street names come from the packaged index (data/streets, staged by deploy.sh); the
// geocoder is asked only for a point the index has no street for.
const liveGeolocator = createGeolocator({
  geocoderUrl: process.env.GEOCODER_REVERSE_URL || "",
  localAddress: createLocalAddress(),
});
const geolocator = createCachedGeolocator({ geolocator: liveGeolocator, repository });

// The national tender catalogues are staged into the package by deploy.sh (see
// tools/stage-national-tenders.mjs) at the module's default path.
const catalogue = createNationalCatalogue();

const service = createService({ repository, detector, geolocator, catalogue });

// One real Bengaluru lookup at start-up loads everything a lookup reads from disk (see
// warm.mjs). The same point through the cached geolocator is a table read (its answer is
// stored, so it reads no file), which opens the connection to the table, and the detector
// secret is fetched so the first detection does not wait for it. Each is on its own: a
// store that is down must not stop the files being read. The point is the production
// canary's.
const WARM_POINT = { lat: 12.99717, lng: 77.62094 };
export const handler = createWarmHandler({
  service,
  warm: () => Promise.allSettled([
    liveGeolocator.resolve(WARM_POINT),
    geolocator.resolve(WARM_POINT),
    secretProvider(),
  ]),
  // Every minute: the map rows, the impact period and Bengaluru's tender index.
  tick: () => service.keepWarm(WARM_POINT),
});
