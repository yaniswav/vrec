// Node harness for src/vrec/js/quality_filter.js: stubs the bits of a page (window,
// XMLHttpRequest, fetch) that the script patches, then feeds it sample HLS/DASH
// manifests at a few height caps and prints the results as JSON on stdout.
//
// Usage: node quality_filter_harness.mjs <path-to-quality_filter.js>

import { readFileSync } from "node:fs";

const jsPath = process.argv[2];
if (!jsPath) {
  console.error("usage: node quality_filter_harness.mjs <path-to-quality_filter.js>");
  process.exit(1);
}
const src = readFileSync(jsPath, "utf8");

globalThis.window = globalThis;

// Minimal XMLHttpRequest stub: quality_filter.js only needs its prototype to exist
// and to carry configurable responseText/response getters it can wrap.
class FakeXHR {}
Object.defineProperty(FakeXHR.prototype, "responseText", {
  configurable: true,
  get() {
    return "";
  },
});
Object.defineProperty(FakeXHR.prototype, "response", {
  configurable: true,
  get() {
    return null;
  },
});
globalThis.XMLHttpRequest = FakeXHR;

let body = "";
let contentType = "application/vnd.apple.mpegurl";
// Like a real server, manifests come with a content-length unless a test overrides the response.
let override = null;
globalThis.fetch = async () =>
  override ??
  new Response(body, {
    headers: { "content-type": contentType, "content-length": String(Buffer.byteLength(body)) },
  });

eval(src);

const hls = `#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x960
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=2880x1440
mid.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=30000000,RESOLUTION=4320x2160
high.m3u8
`;

const dash = `<?xml version="1.0"?><MPD><Period><AdaptationSet>
<Representation id="low" width="1920" height="960" bandwidth="1"/>
<Representation id="mid" width="2880" height="1440" bandwidth="2"/>
<Representation id="high" width="4320" height="2160" bandwidth="3"/>
</AdaptationSet><AdaptationSet><Representation id="audio" bandwidth="128000"/></AdaptationSet></Period></MPD>`;

const singleHls = `#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720
only.m3u8
`;

const results = [];

async function runManifest(format, text, cap, contentTypeForRun, keepPattern) {
  body = text;
  contentType = contentTypeForRun;
  window.__vrecMaxHeight = cap;
  window.__vrecForcedQuality = undefined;
  const response = await window.fetch("https://example.test/manifest");
  const out = await response.text();
  const kept = [...out.matchAll(keepPattern)].map((m) => m[1]);
  results.push({
    format,
    cap,
    kept,
    forced: window.__vrecForcedQuality ?? null,
    unchanged: out === text,
  });
}

for (const cap of [0, 2159, 1439, 500]) {
  await runManifest("hls", hls, cap, "application/vnd.apple.mpegurl", /(low|mid|high)\.m3u8/g);
}
for (const cap of [0, 2159, 1439, 500]) {
  await runManifest("dash", dash, cap, "application/dash+xml", /id="(low|mid|high|audio)"/g);
}

// A single-variant manifest has nothing to choose between: left untouched.
await runManifest("hls-single", singleHls, 0, "application/vnd.apple.mpegurl", /(only)\.m3u8/g);

// A non-manifest JSON response is left untouched too.
{
  const jsonBody = JSON.stringify({ foo: 1 });
  body = jsonBody;
  contentType = "application/json";
  window.__vrecMaxHeight = 0;
  window.__vrecForcedQuality = undefined;
  const response = await window.fetch("https://example.test/data.json");
  const out = await response.text();
  results.push({
    format: "json",
    cap: 0,
    kept: [],
    forced: window.__vrecForcedQuality ?? null,
    unchanged: out === jsonBody,
  });
}

// A streaming response (SSE / long-poll / chunked, no content-length) must come back at once and
// untouched, even though its body never ends.
{
  const stream = new ReadableStream({ start() {} });
  override = new Response(stream, { headers: { "content-type": "text/event-stream" } });
  const sse = override;
  const response = await window.fetch("https://example.test/events");
  results.push({
    format: "stream",
    cap: 0,
    kept: [],
    forced: window.__vrecForcedQuality ?? null,
    unchanged: response === sse,
  });
  // Same for a manifest-typed response that declares no length.
  override = new Response(new ReadableStream({ start() {} }), {
    headers: { "content-type": "application/vnd.apple.mpegurl" },
  });
  const live = override;
  const noLength = await window.fetch("https://example.test/live.m3u8");
  results.push({ format: "no-length", cap: 0, kept: [], forced: null, unchanged: noLength === live });
  override = null;
}

// An octet-stream segment is returned untouched; an octet-stream .m3u8 is still filtered.
{
  window.__vrecMaxHeight = 0;
  body = hls;
  contentType = "application/octet-stream";
  const segment = await window.fetch("https://example.test/seg1.ts?x=1");
  results.push({
    format: "octet-segment",
    cap: 0,
    kept: [],
    forced: window.__vrecForcedQuality ?? null,
    unchanged: (await segment.text()) === hls,
  });
  const manifest = await window.fetch("https://example.test/master.m3u8?token=1");
  const out = await manifest.text();
  results.push({
    format: "octet-manifest",
    cap: 0,
    kept: [...out.matchAll(/(low|mid|high)\.m3u8/g)].map((m) => m[1]),
    forced: window.__vrecForcedQuality ?? null,
    unchanged: out === hls,
  });
}

console.log(JSON.stringify(results));
