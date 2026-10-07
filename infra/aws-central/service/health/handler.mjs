// Lambda entry point of the scheduled health function (HealthFunction in template.yaml):
// the same package as the central function, another handler path.
//
// The AWS SDK here is the one the nodejs22.x runtime provides, so the package carries
// neither client. Nothing else in the repo may import these two packages, and this file
// does nothing but hand them to function.mjs, where the behaviour is and is tested.
import { CloudWatchLogsClient, GetQueryResultsCommand, StartQueryCommand, StopQueryCommand } from "@aws-sdk/client-cloudwatch-logs";
import { GetParameterCommand, PutParameterCommand, SSMClient } from "@aws-sdk/client-ssm";

import { readExampleImage } from "./example-image.mjs";
import { createHealthFunction } from "./function.mjs";

const logs = new CloudWatchLogsClient({});
const ssm = new SSMClient({});

export const handler = createHealthFunction({
  apiUrl: String(process.env.API_URL || "").replace(/\/$/, ""),
  logGroup: process.env.CENTRAL_LOG_GROUP,
  namespace: process.env.METRIC_NAMESPACE,
  keyParameter: process.env.CANARY_KEY_PARAMETER,
  logs: {
    startQuery: (input) => logs.send(new StartQueryCommand(input)),
    getQueryResults: (input) => logs.send(new GetQueryResultsCommand(input)),
    stopQuery: (input) => logs.send(new StopQueryCommand(input)),
  },
  parameters: {
    getParameter: (input) => ssm.send(new GetParameterCommand(input)),
    putParameter: (input) => ssm.send(new PutParameterCommand(input)),
  },
  fetch,
  readImage: readExampleImage,
  log: (text) => console.log(text),
  // Straight to stdout, which is how the Lambda documentation says to write an embedded
  // metric line by hand: console.log wraps what it is given (a timestamp and the request
  // id in front in the text log format, a JSON envelope around it in the JSON one).
  emit: (line) => process.stdout.write(`${line}\n`),
});
