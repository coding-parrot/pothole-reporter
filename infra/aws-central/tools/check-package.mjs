#!/usr/bin/env node
// Resolves one real point with the staged package's own code and data, with the network
// cut, before deploy.sh uploads it.
//
// On 7 Oct 2026 a `cp -R region/ dest/` flattened every street region into one directory
// (BSD cp copies a directory's contents when its name ends in a slash). The package
// uploaded, the stack went live, and only the post-deploy canary saw that no region was
// packaged. Everything this point needs (the ownership layers, the ward polygons, the
// street index) is read from files, so a package that cannot answer it here cannot
// answer it in production either.
//
// Usage: node tools/check-package.mjs <package directory>

import path from "node:path";
import { pathToFileURL } from "node:url";

const packageDirectory = path.resolve(process.argv[2] || "");
if (!process.argv[2]) {
  console.error("usage: check-package.mjs <package directory>");
  process.exit(2);
}
const service = (name) => import(pathToFileURL(path.join(packageDirectory, "infra/aws-central/service", name)).href);
const { createGeolocator } = await service("geolocation.mjs");
const { createLocalAddress } = await service("local-address.mjs");

const errors = [];
const logger = { error: (line) => errors.push(String(line)), warn() {}, log() {}, info() {} };
const geolocator = createGeolocator({
  localAddress: createLocalAddress({ logger }),
  logger,
  fetchImpl: async (url) => { throw new Error(`the package asked the network for ${String(url).slice(0, 80)}`); },
});
// MM Road beside the production canary point: GBA Central, Cox Town ward.
const answer = await geolocator.resolve({ lat: 12.99717, lng: 77.62094 });
const expected = {
  road_ownership: "municipal",
  "lookup.local": "municipal_polygon",
  "lookup.ward": "resolved",
  ward_name: "Cox Town",
  address_source: "packaged_streets",
  "lookup.streets": "street",
  "address_parts.road": "MM Road",
  state_code: "KA",
};
const read = (key) => key.split(".").reduce((value, part) => value?.[part], answer);
const wrong = Object.entries(expected).filter(([key, value]) => read(key) !== value)
  .map(([key, value]) => `${key} is ${JSON.stringify(read(key))}, expected ${JSON.stringify(value)}`);
if (wrong.length) {
  console.error(`package check FAILED for ${packageDirectory}:`);
  for (const line of wrong) console.error(`  ${line}`);
  for (const line of errors.slice(0, 5)) console.error(`  logged: ${line.slice(0, 300)}`);
  process.exit(1);
}
console.log(`package check ok: ${answer.address}; ward ${answer.ward_name}; no network used`);
