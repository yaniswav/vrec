// Runs state.js, pick_video.js, wait_can_play.js and resolution.js against fake video elements and prints JSON.
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

// pick_video.js: one attempt per call; the longest video wins and is stored in window.__vrecVideo
const small = video({ duration: 30, offsetWidth: 10, offsetHeight: 10, tag: 'small' });
const main = video({ duration: 600, tag: 'main' });
const live = video({ duration: Infinity, videoWidth: 100, videoHeight: 100, tag: 'live' });
const pickVideo = load('pick_video.js');
globalThis.document = { querySelectorAll: sel => (sel === 'video' ? [small, live, main] : []) };
window.__vrecVideo = undefined;
out.pick = pickVideo(0);
out.pickedTag = window.__vrecVideo.tag;

globalThis.document = { querySelectorAll: () => [] };
out.pickNone = pickVideo(40);

// a video that doesn't load: kicked (muted play) once, and only after 3 s
let plays = 0;
const stuck = video({ readyState: 0, tag: 'stuck', play() { plays++; return Promise.resolve(); } });
globalThis.document = { querySelectorAll: () => [stuck] };
window.__vrecVideo = undefined;
out.pickStuck = [pickVideo(0), pickVideo(2.5)];
out.playsBefore = plays;
out.pickStuckLate = [pickVideo(3.5), pickVideo(4), pickVideo(30)];
out.playsAfter = plays;
out.stuckMuted = stuck.muted;
out.stuckNotStored = window.__vrecVideo === undefined;

// wait_can_play.js
window.__vrecVideo = video({ readyState: 3 });
out.canPlayNot = load('wait_can_play.js')();
window.__vrecVideo = video({ readyState: 4 });
out.canPlay = load('wait_can_play.js')();

// resolution.js
const resolution = load('resolution.js');
window.__vrecVideo = video();
out.resolutionFile = resolution();
window.__vrecVideo = video({ currentSrc: 'blob:https://x.test/abc', videoWidth: 1920, videoHeight: 960 });
out.resolutionBlob = resolution();
window.__vrecVideo = video({ currentSrc: '' });
out.resolutionNoSrc = resolution();

console.log(JSON.stringify(out));
