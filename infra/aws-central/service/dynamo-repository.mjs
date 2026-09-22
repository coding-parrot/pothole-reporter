import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  BatchGetCommand,
  DeleteCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  TransactWriteCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";

const SHARDS = 16;
// The map is public and unauthenticated, so a bbox read may page past non-matching rows
// but not without end: at most this many pages per shard.
const MAP_PAGES_PER_SHARD = 5;
const day = (value = Date.now()) => new Date(value).toISOString().slice(0, 10);
const minute = (value = Date.now()) => new Date(value).toISOString().slice(0, 16);
const month = (value = Date.now()) => new Date(value).toISOString().slice(0, 7);
const ttl = (milliseconds) => Math.ceil(milliseconds / 1_000);
const conditionalFailure = (error) => [
  "ConditionalCheckFailedException",
  "TransactionCanceledException",
].includes(error?.name);

// Concurrent detections all write the same minute, day and month counters, so DynamoDB
// cancels some transactions with TransactionConflict while every counter is under its
// cap. That is contention, not a limit.
const QUOTA_CONFLICT_ATTEMPTS = 3;
const onlyConflicts = (error) => {
  const codes = (error?.CancellationReasons || []).map((reason) => reason?.Code);
  return codes.includes("TransactionConflict") && !codes.includes("ConditionalCheckFailed");
};
const jitter = () => new Promise((resolve) => setTimeout(resolve, 20 + Math.random() * 60));

function dates(from, to) {
  const result = [];
  for (let cursor = Date.parse(`${from}T00:00:00Z`);
    cursor <= Date.parse(`${to}T00:00:00Z`); cursor += 86_400_000) {
    result.push(day(cursor));
  }
  return result;
}

export function createDynamoRepository({
  client = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
    marshallOptions: { removeUndefinedValues: true },
  }),
  tables,
  dedupeRadiusMetres = 30,
  quota = {},
}) {
  const config = {
    perInstallDay: Number(quota.perInstallDay ?? 500),
    globalMinute: Number(quota.globalMinute ?? 60),
    globalDay: Number(quota.globalDay ?? 10_000),
    globalMonth: Number(quota.globalMonth ?? 20_000),
    feedbackPerInstallDay: Number(quota.feedbackPerInstallDay ?? 10),
  };

  async function send(command) {
    return client.send(command);
  }

  // DynamoDB applies Limit before FilterExpression and ends a page at 1 MB, so one
  // Query can come back short while matching rows remain. Follows LastEvaluatedKey
  // until `want` rows match, the key runs out, or the page budget is spent.
  async function queryPages(input, { want = Infinity, maxPages = Infinity } = {}) {
    const items = [];
    let startKey;
    for (let page = 0; page < maxPages && items.length < want; page += 1) {
      const result = await send(new QueryCommand({
        ...input,
        ...(startKey ? { ExclusiveStartKey: startKey } : {}),
      }));
      items.push(...(result.Items || []));
      startKey = result.LastEvaluatedKey;
      if (!startKey) break;
    }
    return items;
  }

  return {
    dedupeRadiusMetres,

    async registerInstallation(value) {
      const now = Date.now();
      await send(new UpdateCommand({
        TableName: tables.installations,
        Key: { id: value.id },
        UpdateExpression: "SET public_key=if_not_exists(public_key,:key), public_key_format=if_not_exists(public_key_format,:format), created_at=if_not_exists(created_at,:now), last_seen_at=:now",
        ExpressionAttributeValues: {
          ":key": value.public_key,
          ":format": value.public_key_format,
          ":now": now,
        },
      }));
    },

    async getInstallation(id) {
      const result = await send(new GetCommand({
        TableName: tables.installations,
        Key: { id },
        ConsistentRead: true,
      }));
      return result.Item || null;
    },

    async touchInstallation(id) {
      await send(new UpdateCommand({
        TableName: tables.installations,
        Key: { id },
        UpdateExpression: "SET last_seen_at=:now",
        ExpressionAttributeValues: { ":now": Date.now() },
      }));
    },

    async claimReplay(signatureHash, expiresAt) {
      try {
        await send(new PutCommand({
          TableName: tables.control,
          Item: { id: `REPLAY#${signatureHash}`, expires_at: ttl(expiresAt) },
          ConditionExpression: "attribute_not_exists(id)",
        }));
        return true;
      } catch (error) {
        if (conditionalFailure(error)) return false;
        throw error;
      }
    },

    async claimIdempotency({ id, requestHash, owner, leaseExpiresAt }) {
      const key = `IDEMP#${id}`;
      const existing = await send(new GetCommand({
        TableName: tables.control,
        Key: { id: key },
        ConsistentRead: true,
      }));
      if (existing.Item?.status === "COMPLETED") return existing.Item;
      try {
        await send(new PutCommand({
          TableName: tables.control,
          Item: {
            id: key,
            status: "IN_PROGRESS",
            request_hash: requestHash,
            owner,
            lease_expires_at: leaseExpiresAt,
            expires_at: ttl(leaseExpiresAt + 86_400_000),
          },
          ConditionExpression: "attribute_not_exists(id) OR lease_expires_at < :now",
          ExpressionAttributeValues: { ":now": Date.now() },
        }));
        return { status: "CLAIMED" };
      } catch (error) {
        if (!conditionalFailure(error)) throw error;
        const current = await send(new GetCommand({
          TableName: tables.control,
          Key: { id: key },
          ConsistentRead: true,
        }));
        return current.Item || { status: "IN_PROGRESS" };
      }
    },

    async completeIdempotency({ id, owner, requestHash, statusCode, payload, expiresAt }) {
      await send(new UpdateCommand({
        TableName: tables.control,
        Key: { id: `IDEMP#${id}` },
        UpdateExpression: "SET #status=:completed, request_hash=:hash, status_code=:code, response_json=:response, expires_at=:expires REMOVE lease_expires_at, #owner",
        ConditionExpression: "#owner=:owner AND request_hash=:hash",
        ExpressionAttributeNames: { "#status": "status", "#owner": "owner" },
        ExpressionAttributeValues: {
          ":completed": "COMPLETED",
          ":hash": requestHash,
          ":code": statusCode,
          ":response": JSON.stringify(payload),
          ":expires": ttl(expiresAt),
          ":owner": owner,
        },
      }));
    },

    async releaseIdempotency(id, owner) {
      try {
        await send(new DeleteCommand({
          TableName: tables.control,
          Key: { id: `IDEMP#${id}` },
          ConditionExpression: "#owner=:owner AND #status=:progress",
          ExpressionAttributeNames: { "#owner": "owner", "#status": "status" },
          ExpressionAttributeValues: { ":owner": owner, ":progress": "IN_PROGRESS" },
        }));
      } catch (error) {
        if (!conditionalFailure(error)) throw error;
      }
    },

    async takeVisionQuota(installId, now = Date.now()) {
      const limits = [
        [`minute#${minute(now)}`, config.globalMinute, "shared_rate_limit"],
        [`day#${day(now)}`, config.globalDay, "shared_daily_budget_reached"],
        [`month#${month(now)}`, config.globalMonth, "shared_budget_reached"],
        [`install#${installId}#${day(now)}`, config.perInstallDay, "daily_vision_limit"],
      ];
      const blocked = limits.find(([, limit]) => !Number.isInteger(limit) || limit <= 0);
      if (blocked) return { ok: false, code: blocked[2], limit: blocked[1] };
      for (let attempt = 1; ; attempt += 1) {
        try {
          await send(new TransactWriteCommand({
            TransactItems: limits.map(([key, limit]) => ({
              Update: {
                TableName: tables.usage,
                Key: { id: key },
                UpdateExpression: "ADD used :one SET expires_at=:expires",
                ConditionExpression: "attribute_not_exists(used) OR used < :limit",
                ExpressionAttributeValues: {
                  ":one": 1,
                  ":limit": limit,
                  ":expires": ttl(now + 400 * 86_400_000),
                },
              },
            })),
          }));
          return { ok: true, limit: config.perInstallDay };
        } catch (error) {
          if (!conditionalFailure(error)) throw error;
          if (!onlyConflicts(error)) break;
          // The app stops a drive on any code naming credit, budget or quota, so the
          // busy code must name none of them.
          if (attempt >= QUOTA_CONFLICT_ATTEMPTS) {
            return { ok: false, code: "vision_counters_busy", retryable: true };
          }
          await jitter();
        }
      }
      for (const [key, limit, code] of limits) {
        const current = await send(new GetCommand({
          TableName: tables.usage,
          Key: { id: key },
          ConsistentRead: true,
        }));
        if (Number(current.Item?.used || 0) >= limit) return { ok: false, code, limit };
      }
      return { ok: false, code: "shared_rate_limit", limit: config.globalMinute };
    },

    // Gives back a unit takeVisionQuota took at the same `now` when the detector failed
    // on the server side. Best effort: each counter is decremented on its own and never
    // below zero, so a counter that has expired or been reset is left alone.
    async refundVisionQuota(installId, now = Date.now()) {
      const keys = [
        `minute#${minute(now)}`,
        `day#${day(now)}`,
        `month#${month(now)}`,
        `install#${installId}#${day(now)}`,
      ];
      const results = await Promise.allSettled(keys.map((key) => send(new UpdateCommand({
        TableName: tables.usage,
        Key: { id: key },
        UpdateExpression: "ADD used :minusOne",
        ConditionExpression: "used > :zero",
        ExpressionAttributeValues: { ":minusOne": -1, ":zero": 0 },
      }))));
      const failed = results.find((item) => item.status === "rejected"
        && !conditionalFailure(item.reason));
      if (failed) throw failed.reason;
    },

    async takeFeedbackQuota(installId, now = Date.now()) {
      try {
        await send(new UpdateCommand({
          TableName: tables.usage,
          Key: { id: `feedback#${installId}#${day(now)}` },
          UpdateExpression: "ADD used :one SET expires_at=:expires",
          ConditionExpression: "attribute_not_exists(used) OR used < :limit",
          ExpressionAttributeValues: {
            ":one": 1,
            ":limit": config.feedbackPerInstallDay,
            ":expires": ttl(now + 2 * 86_400_000),
          },
        }));
        return { ok: true, limit: config.feedbackPerInstallDay };
      } catch (error) {
        if (!conditionalFailure(error)) throw error;
        return { ok: false, limit: config.feedbackPerInstallDay };
      }
    },

    async putFeedback(item) {
      await send(new PutCommand({
        TableName: tables.records,
        Item: {
          pk: `FEEDBACK#${item.install_id}`,
          sk: `${String(item.created_at).padStart(15, "0")}#${item.request_id}`,
          ...item,
        },
      }));
    },

    async putReceipt(receipt) {
      await send(new PutCommand({
        TableName: tables.control,
        Item: {
          id: `RECEIPT#${receipt.id}`,
          ...receipt,
          expires_at: ttl(receipt.expiresAt),
        },
        ConditionExpression: "attribute_not_exists(id)",
      }));
    },

    async getReceipt(id) {
      const result = await send(new GetCommand({
        TableName: tables.control,
        Key: { id: `RECEIPT#${id}` },
        ConsistentRead: true,
      }));
      return result.Item || null;
    },

    async acquireLocationLocks(cells, owner, now = Date.now()) {
      try {
        await send(new TransactWriteCommand({
          TransactItems: cells.map((cell) => ({
            Update: {
              TableName: tables.locks,
              Key: { cell },
              UpdateExpression: "SET lease_owner=:owner, lease_expires_at=:expires, expires_at=:ttl",
              ConditionExpression: "attribute_not_exists(lease_expires_at) OR lease_expires_at < :now OR lease_owner=:owner",
              ExpressionAttributeValues: {
                ":owner": owner,
                ":now": now,
                ":expires": now + 15_000,
                ":ttl": ttl(now + 86_400_000),
              },
            },
          })),
        }));
        return true;
      } catch (error) {
        if (conditionalFailure(error)) return false;
        throw error;
      }
    },

    async releaseLocationLocks(cells, owner) {
      try {
        await send(new TransactWriteCommand({
          TransactItems: cells.map((cell) => ({
            Delete: {
              TableName: tables.locks,
              Key: { cell },
              ConditionExpression: "lease_owner=:owner",
              ExpressionAttributeValues: { ":owner": owner },
            },
          })),
        }));
      } catch (error) {
        if (!conditionalFailure(error)) throw error;
      }
    },

    async findNearby(cells) {
      const rows = await Promise.all(cells.map((cell) => send(new QueryCommand({
        TableName: tables.spatial,
        KeyConditionExpression: "cell=:cell",
        ExpressionAttributeValues: { ":cell": cell },
        ProjectionExpression: "pothole_id",
        Limit: 25,
      }))));
      const ids = [...new Set(rows.flatMap((row) => row.Items || [])
        .map((item) => item.pothole_id))];
      if (!ids.length) return [];
      const output = [];
      for (let index = 0; index < ids.length; index += 100) {
        const batch = await send(new BatchGetCommand({
          RequestItems: {
            [tables.potholes]: {
              Keys: ids.slice(index, index + 100).map((id) => ({ id })),
              ConsistentRead: true,
            },
          },
        }));
        output.push(...(batch.Responses?.[tables.potholes] || []));
      }
      return output;
    },

    async createPothole(pothole, cell) {
      try {
        await send(new TransactWriteCommand({
          TransactItems: [
            {
              Put: {
                TableName: tables.potholes,
                Item: pothole,
                ConditionExpression: "attribute_not_exists(id)",
              },
            },
            {
              Put: {
                TableName: tables.spatial,
                Item: { cell, pothole_id: pothole.id },
                ConditionExpression: "attribute_not_exists(cell) AND attribute_not_exists(pothole_id)",
              },
            },
          ],
        }));
        return true;
      } catch (error) {
        if (conditionalFailure(error)) return false;
        throw error;
      }
    },

    async attachObservation({ potholeId, observation, receiptId = null }) {
      const observationItem = {
        pk: `OBS#${observation.install_id}#${observation.client_observation_id}`,
        sk: "META",
        ...observation,
        pothole_id: potholeId,
      };
      const observerItem = {
        pk: `POTHOLE#${potholeId}`,
        sk: `OBSERVER#${observation.install_id}`,
        first_seen_at: observation.observed_at,
      };
      const receiptUpdate = receiptId ? [{
        Update: {
          TableName: tables.control,
          Key: { id: `RECEIPT#${receiptId}` },
          UpdateExpression: "SET consumed_at=:now, consumed_observation_id=:observation",
          ConditionExpression: "attribute_exists(id) AND (attribute_not_exists(consumed_at) OR consumed_observation_id=:observation)",
          ExpressionAttributeValues: {
            ":now": Date.now(),
            ":observation": observation.client_observation_id,
          },
        },
      }] : [];
      const updatePothole = (newObserver) => ({
        Update: {
          TableName: tables.potholes,
          Key: { id: potholeId },
          UpdateExpression: `ADD observation_count :one${newObserver ? ", complaint_count :one" : ""}`,
          ConditionExpression: "attribute_exists(id)",
          ExpressionAttributeValues: { ":one": 1 },
        },
      });
      // Offline queues deliver observations out of order, and a condition inside the
      // transaction would cancel the counters with it. So the seen times move on their
      // own, only ever outwards, and a re-send moves them again harmlessly.
      const widenSeen = () => Promise.all([
        ["last_seen_at", "<"],
        ["first_seen_at", ">"],
      ].map(([field, direction]) => send(new UpdateCommand({
        TableName: tables.potholes,
        Key: { id: potholeId },
        UpdateExpression: `SET ${field}=:seen`,
        ConditionExpression: `${field} ${direction} :seen`,
        ExpressionAttributeValues: { ":seen": observation.observed_at },
      })).catch((error) => {
        if (!conditionalFailure(error)) throw error;
      })));
      try {
        await send(new TransactWriteCommand({
          TransactItems: [
            { Put: {
              TableName: tables.records,
              Item: observationItem,
              ConditionExpression: "attribute_not_exists(pk)",
            } },
            { Put: {
              TableName: tables.records,
              Item: observerItem,
              ConditionExpression: "attribute_not_exists(pk)",
            } },
            updatePothole(true),
            ...receiptUpdate,
          ],
        }));
        await widenSeen();
        return { newObserver: true };
      } catch (error) {
        if (!conditionalFailure(error)) throw error;
      }
      const existingObservation = await send(new GetCommand({
        TableName: tables.records,
        Key: { pk: observationItem.pk, sk: observationItem.sk },
        ConsistentRead: true,
      }));
      if (existingObservation.Item) {
        await widenSeen();
        return { alreadyStored: true, newObserver: false };
      }
      await send(new TransactWriteCommand({
        TransactItems: [
          { Put: {
            TableName: tables.records,
            Item: observationItem,
            ConditionExpression: "attribute_not_exists(pk)",
          } },
          updatePothole(false),
          ...receiptUpdate,
        ],
      }));
      await widenSeen();
      return { newObserver: false };
    },

    async getPothole(id) {
      const result = await send(new GetCommand({
        TableName: tables.potholes,
        Key: { id },
        ConsistentRead: true,
      }));
      return result.Item || null;
    },

    async listPotholes({ since, bbox, limit }) {
      const rows = await Promise.all([...Array(SHARDS).keys()].map((shard) => {
        const values = { ":shard": String(shard).padStart(2, "0"), ":since": since };
        let filter;
        if (bbox) {
          Object.assign(values, {
            ":south": bbox[1], ":north": bbox[3], ":west": bbox[0], ":east": bbox[2],
          });
          filter = "lat BETWEEN :south AND :north AND lng BETWEEN :west AND :east";
        }
        return queryPages({
          TableName: tables.potholes,
          IndexName: "MapIndex",
          KeyConditionExpression: "map_shard=:shard AND last_seen_at>=:since",
          ExpressionAttributeValues: values,
          FilterExpression: filter,
          ScanIndexForward: false,
          Limit: limit,
        }, { want: limit, maxPages: MAP_PAGES_PER_SHARD });
      }));
      return rows.flat()
        .sort((left, right) => right.last_seen_at - left.last_seen_at)
        .slice(0, limit);
    },

    async queryTenders(bodyLgd) {
      const codes = [bodyLgd, ...(bodyLgd === "305850" || /^30585[0-4]$/.test(bodyLgd)
        ? ["BLR"] : [])];
      const rows = await Promise.all(codes.map((code) => queryPages({
        TableName: tables.tenders,
        KeyConditionExpression: "body_lgd=:body",
        ExpressionAttributeValues: { ":body": code },
        Limit: 2_000,
      }, { want: 2_000 })));
      return rows.map((items) => items.slice(0, 2_000)).flat();
    },

    async recordRequest({ route, outcome, visionMode, installId, failed = false }) {
      const today = day();
      const tasks = [send(new UpdateCommand({
        TableName: tables.metrics,
        Key: { day: today, metric: `request#${route}#${outcome}#${visionMode}` },
        UpdateExpression: "ADD request_count :one",
        ExpressionAttributeValues: { ":one": 1 },
      }))];
      if (installId) {
        tasks.push(send(new UpdateCommand({
          TableName: tables.metrics,
          Key: { day: today, metric: `active#${installId}` },
          UpdateExpression: "ADD request_count :one SET last_seen_at=:now",
          ExpressionAttributeValues: { ":one": 1, ":now": Date.now() },
        })));
        tasks.push(send(new UpdateCommand({
          TableName: tables.metrics,
          Key: { day: today, metric: `install#${installId}#${route}#${outcome}#${failed ? "error" : "ok"}` },
          UpdateExpression: "ADD request_count :one",
          ExpressionAttributeValues: { ":one": 1 },
        })));
      }
      await Promise.all(tasks);
    },

    async recordCapture({ captureSource, locationSource, visionMode, outcome }) {
      await send(new UpdateCommand({
        TableName: tables.metrics,
        Key: {
          day: day(),
          metric: `capture#${captureSource}#${locationSource}#${visionMode}#${outcome}`,
        },
        UpdateExpression: "ADD request_count :one",
        ExpressionAttributeValues: { ":one": 1 },
      }));
    },

    async recordReport({ newPothole, verification }) {
      await send(new UpdateCommand({
        TableName: tables.metrics,
        Key: { day: day(), metric: "summary" },
        UpdateExpression: `ADD observations :one, ${verification === "server_verified_shared" ? "server_verified_shared" : "client_attested"} :one${newPothole ? ", new_potholes :one" : ""}`,
        ExpressionAttributeValues: { ":one": 1 },
      }));
    },

    async impact({ from, to }) {
      // A day holds a row per install, route and outcome, which passes 1 MB well before
      // it passes anything else, so every day is read to its last page.
      const rows = await Promise.all(dates(from, to).map((date) => queryPages({
        TableName: tables.metrics,
        KeyConditionExpression: "#day=:day",
        ExpressionAttributeNames: { "#day": "day" },
        ExpressionAttributeValues: { ":day": date },
      })));
      const items = rows.flat();
      const requests = new Map();
      const captures = new Map();
      const active = new Set();
      const summary = { new_potholes: 0, observations: 0, server_verified_shared: 0, client_attested: 0 };
      for (const item of items) {
        if (item.metric.startsWith("active#")) active.add(item.metric.slice(7));
        else if (item.metric.startsWith("request#")) {
          const [, route, outcome, visionMode] = item.metric.split("#");
          const key = `${route}#${outcome}#${visionMode}`;
          requests.set(key, (requests.get(key) || 0) + Number(item.request_count || 0));
        } else if (item.metric.startsWith("capture#")) {
          const [, captureSource, locationSource, visionMode, outcome] = item.metric.split("#");
          const key = `${captureSource}#${locationSource}#${visionMode}#${outcome}`;
          captures.set(key, (captures.get(key) || 0) + Number(item.request_count || 0));
        } else if (item.metric === "summary") {
          for (const key of Object.keys(summary)) summary[key] += Number(item[key] || 0);
        }
      }
      return {
        activeInstallations: active.size,
        requests: [...requests].map(([key, count]) => {
          const [route, outcome, vision_mode] = key.split("#");
          return { route, outcome, vision_mode, count };
        }),
        captures: [...captures].map(([key, count]) => {
          const [capture_source, location_source, vision_mode, outcome] = key.split("#");
          return { capture_source, location_source, vision_mode, outcome, count };
        }),
        summary,
      };
    },
  };
}
