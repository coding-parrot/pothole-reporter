import assert from "node:assert/strict";
import test from "node:test";

import { decode, fitGeometry } from "../locator.mjs";

test("a frame is scaled to fit from the top-left, as ValTransform does", () => {
  assert.deepEqual(fitGeometry(1280, 720, 640), { ratio: 0.5, width: 640, height: 360 });
  const tall = fitGeometry(720, 1280, 640);
  assert.equal(tall.ratio, 0.5);
  assert.deepEqual([tall.width, tall.height], [360, 640]);
  // int() truncates: 600 x 601 at 640 is 638.9 wide, 638 after truncation.
  assert.equal(fitGeometry(600, 601, 640).width, 638);
});

const row = (cx, cy, w, h, objectness, score) => [cx, cy, w, h, objectness, score];

test("boxes come back in the original frame as fractions, best first", () => {
  // A 1280 x 720 frame is halved: a box centred at (320, 180) of 64 x 36 in model pixels
  // is centred at (640, 360) and 128 x 72 in the frame.
  const rows = new Float32Array([
    ...row(320, 180, 64, 36, 0.9, 0.8),
    ...row(100, 100, 20, 20, 0.5, 0.5),
    ...row(500, 300, 10, 10, 0.2, 0.2),   // 0.04: under the floor
  ]);
  const boxes = decode(rows, { ratio: 0.5, frameWidth: 1280, frameHeight: 720 });
  assert.equal(boxes.length, 2);
  assert.deepEqual(boxes[0], { x: 0.45, y: 0.45, w: 0.1, h: 0.1, score: 0.72 });
  assert.equal(boxes[1].score, 0.25);
});

test("two boxes on the same pothole are one mark", () => {
  const rows = new Float32Array([
    ...row(320, 180, 64, 36, 0.9, 0.9),
    ...row(322, 181, 64, 36, 0.8, 0.8),   // nearly the same box
    ...row(100, 300, 40, 40, 0.7, 0.7),   // another pothole
  ]);
  const boxes = decode(rows, { ratio: 0.5, frameWidth: 1280, frameHeight: 720 });
  assert.deepEqual(boxes.map((box) => box.score), [0.81, 0.49]);
});

test("a box that runs past the frame's edge is cut to it, and one wholly in the padding is dropped", () => {
  const rows = new Float32Array([
    ...row(630, 100, 60, 40, 0.9, 0.9),   // 600..660 wide in a 640 square
    ...row(320, 500, 40, 40, 0.9, 0.9),   // below the 360 rows the frame occupies
  ]);
  const boxes = decode(rows, { ratio: 0.5, frameWidth: 1280, frameHeight: 720 });
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].x + boxes[0].w, 1);
});

test("no candidate over the floor is an empty list", () => {
  assert.deepEqual(decode(new Float32Array([...row(1, 1, 1, 1, 0.1, 0.1)]),
    { ratio: 1, frameWidth: 640, frameHeight: 640 }), []);
});

test("the resize is cv2's two-tap one, into BGR planes with grey padding", async () => {
  const { resizeToPlanes } = await import("../locator.mjs");
  // A 4 x 2 frame, red channel 0, 40, 80, 120 along x, into a 4 x 4 square at half size.
  const rgb = new Uint8Array(4 * 2 * 3);
  for (let y = 0; y < 2; y += 1) for (let x = 0; x < 4; x += 1) rgb.set([x * 40, 10, 200], (y * 4 + x) * 3);
  const planes = resizeToPlanes(rgb, 4, 2, { width: 2, height: 1, ratio: 0.5 }, 4);
  // Output pixel 0 is centred on source 0.5, pixel 1 on source 2.5.
  assert.deepEqual([planes[32 + 0], planes[32 + 1]], [20, 100]);      // red is the third plane
  assert.deepEqual([planes[0], planes[16]], [200, 10]);               // blue first, green second
  assert.equal(planes[2], 114);                                       // right of the frame
  assert.equal(planes[4], 114);                                       // below it
});
