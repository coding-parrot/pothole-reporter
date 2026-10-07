// The staged Lambda package, asked whether the scheduled health function can run from
// it: its entry point is there, its modules load with nothing else of the repo present,
// and the photograph its canary sends is where the package's own code looks for it.
// tools/check-package.mjs runs this before deploy.sh uploads anything.
//
// The entry point itself is not imported: it needs the two AWS SDK clients the Lambda
// runtime provides, which are in no package on purpose. tools/prove-health-function.mjs
// invokes the deployed function once for that.

import { existsSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

export async function checkStagedHealth(packageDirectory) {
  const health = path.join(packageDirectory, "infra/aws-central/service/health");
  if (!existsSync(path.join(health, "handler.mjs"))) {
    return { photographBytes: null, problems: ["infra/aws-central/service/health/handler.mjs is not in the package; the health function has no entry point"] };
  }
  const load = (name) => import(pathToFileURL(path.join(health, name)).href);
  try {
    await load("function.mjs");
  } catch (error) {
    return { photographBytes: null, problems: [`the health function cannot be loaded from the package: ${error.message}`] };
  }
  try {
    const { readExampleImage } = await load("example-image.mjs");
    return { photographBytes: readExampleImage().length, problems: [] };
  } catch (error) {
    return { photographBytes: null, problems: [error.code === "ENOENT"
      ? "docs/example-pothole.jpg is not in the package; deploy.sh copies it for the health function's canary"
      : error.message] };
  }
}
