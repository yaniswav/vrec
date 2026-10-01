// Runs state.js, pick_video.js and resolution.js against fake video elements and prints JSON.
// Usage: node player_scripts_harness.mjs <js dir>
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const jsDir = process.argv[2];
// Each bundled script is a single arrow function expression, evaluated like page.evaluate() does.
const load = name => (0, eval)('(' + readFileSync(join(jsDir, name), 'utf8') + ')');

globalThis.window = globalThis;

const ranges = list => ({
    length: list.length,
    start: i => list[i][0],
    end: i => list[i][1],
});

function video(over = {}) {
    return {
        isConnected: true, ended: false, currentTime: 12.5, duration: 100, paused: false,
        videoWidth: 3840, videoHeight: 1920, offsetWidth: 1920, offsetHeight: 1080,
        readyState: 4, currentSrc: 'https://cdn.test/v.mp4', muted: false,
        buffered: ranges([[0, 42.5]]),
        play() { return Promise.resolve(); },
        ...over,
    };
}

const out = {};

// state.js
const state = load('state.js');
window.__vrecVideo = undefined;
out.stateNoVideo = state();
window.__vrecVideo = video({ isConnected: false });
out.stateDetached = state();
window.__vrecVideo = video();
out.state = state();
window.__vrecVideo = video({ buffered: ranges([]), paused: true, ended: true });
out.stateNoBuffer = state();
window.__vrecVideo = video({ buffered: ranges([[0, 5], [10, 30]]), currentTime: 12 });
out.stateSecondRange = state();

// pick_video.js: the longest video wins, and the winner is stored in window.__vrecVideo
const small = video({ duration: 30, offsetWidth: 10, offsetHeight: 10, tag: 'small' });
const main = video({ duration: 600, tag: 'main' });
const live = video({ duration: Infinity, videoWidth: 100, videoHeight: 100, tag: 'live' });
globalThis.document = { querySelectorAll: sel => (sel === 'video' ? [small, live, main] : []) };
window.__vrecVideo = undefined;
out.pickDuration = await load('pick_video.js')();
out.pickedTag = window.__vrecVideo.tag;

globalThis.document = { querySelectorAll: () => [] };
try {
    // no video: the script polls for 30 s, so shorten the clock instead of waiting
    const realNow = Date.now;
    let fake = realNow();
    Date.now = () => (fake += 40000);
    await load('pick_video.js')();
    Date.now = realNow;
    out.pickNone = 'no error';
} catch (e) {
    out.pickNone = e.message;
}

const stuck = video({ readyState: 0, tag: 'stuck' });
globalThis.document = { querySelectorAll: () => [stuck] };
try {
    const realNow = Date.now;
    let fake = realNow();
    Date.now = () => (fake += 40000);
    await load('pick_video.js')();
    Date.now = realNow;
    out.pickStuck = 'no error';
} catch (e) {
    out.pickStuck = e.message;
}

// resolution.js
const resolution = load('resolution.js');
window.__vrecVideo = video();
out.resolutionFile = resolution();
window.__vrecVideo = video({ currentSrc: 'blob:https://x.test/abc', videoWidth: 1920, videoHeight: 960 });
out.resolutionBlob = resolution();
window.__vrecVideo = video({ currentSrc: '' });
out.resolutionNoSrc = resolution();

console.log(JSON.stringify(out));
