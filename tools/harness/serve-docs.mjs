#!/usr/bin/env node
// Serve docs/ exactly as the harness does, for running one suite by hand.
//
//   node tools/harness/serve-docs.mjs &
//   .venv/bin/python tests/<one>_test.py
//
// docs/ is the document root because it carries the data packs. The hosted site also
// exposes the app under /web-app/, and five suites request it that way, so a plain
// `python3 -m http.server` in docs/ makes them time out waiting for a page that 404s.
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../docs");
const port = Number(process.env.HARNESS_PORT || 8765);
const types = {
  ".html": "text/html", ".js": "text/javascript", ".json": "application/json",
  ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
  ".webp": "image/webp", ".pt": "application/octet-stream", ".mp4": "video/mp4",
  ".webm": "video/webm", ".gpx": "application/gpx+xml", ".css": "text/css",
};

createServer((request, response) => {
  let path = decodeURIComponent(new URL(request.url, "http://x").pathname);
  if (path === "/web-app" || path.startsWith("/web-app/")) {
    path = path.slice("/web-app".length) || "/";
  }
  const file = resolve(root, `.${path === "/" ? "/index.html" : path}`);
  if (!file.startsWith(root) || !existsSync(file)) {
    response.writeHead(404).end("not found");
    return;
  }
  const extension = file.slice(file.lastIndexOf("."));
  response.writeHead(200, { "content-type": types[extension] || "application/octet-stream" });
  response.end(readFileSync(file));
}).listen(port, () => console.log(`serving docs/ on http://localhost:${port} (/web-app/ aliased)`));
