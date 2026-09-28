// Runs set_audio_sink.js and audio_contexts.js against fake browser objects and prints JSON.
// Usage: node audio_sink_harness.mjs <js dir> <scenario>
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const [jsDir, scenario] = process.argv.slice(2);
const read = name => readFileSync(join(jsDir, name), 'utf8');

globalThis.window = globalThis;

class FakeAudioContext {
    constructor() { this.sink = ''; }
    async setSinkId(id) { this.sink = id; }
}
window.AudioContext = FakeAudioContext;

const outputs = {
    labelled: [
        { kind: 'audioinput', label: 'Microphone', deviceId: 'mic' },
        { kind: 'audiooutput', label: 'Speakers (Realtek)', deviceId: 'spk' },
        { kind: 'audiooutput', label: 'CABLE Input (VB-Audio Virtual Cable)', deviceId: 'cable' },
    ],
    unlabelled: [
        { kind: 'audiooutput', label: '', deviceId: 'a' },
        { kind: 'audiooutput', label: '', deviceId: 'b' },
    ],
};

// audio_contexts.js must run before the page creates its contexts.
eval(read('audio_contexts.js'));
const early = new window.AudioContext();

const video = {
    sink: '',
    async setSinkId(id) {
        if (scenario === 'refused') { const e = new Error('no'); e.name = 'NotAllowedError'; throw e; }
        this.sink = id;
    },
};
if (scenario !== 'unsupported') window.__vrecVideo = video;
Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { enumerateDevices: async () => outputs[scenario === 'unlabelled' ? 'unlabelled' : 'labelled'] } },
    configurable: true,
});

const setSink = eval('(' + read('set_audio_sink.js') + ')');
const label = scenario === 'missing' ? 'Nope' : 'cable input';
const result = await setSink(label);
const late = new window.AudioContext();
await new Promise(r => setTimeout(r, 0));
console.log(JSON.stringify({
    result,
    videoSink: video.sink,
    earlySink: early.sink,
    lateSink: late.sink,
    tracked: (window.__vrecAudioContexts || []).length,
    isSubclass: late instanceof FakeAudioContext,
}));
