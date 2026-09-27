(() => {
    // Runs in every page before its scripts. Keeps track of the page's Web Audio contexts,
    // so vrec can send their sound to the same output as the video (see set_audio_sink.js):
    // some players play the sound through Web Audio instead of the <video> element.
    if (window.__vrecAudioContexts) return;
    const contexts = window.__vrecAudioContexts = [];
    for (const name of ['AudioContext', 'webkitAudioContext']) {
        const Original = window[name];
        if (typeof Original !== 'function') continue;
        window[name] = class extends Original {
            constructor(...args) {
                super(...args);
                contexts.push(this);
                if (window.__vrecSinkId && typeof this.setSinkId === 'function') {
                    this.setSinkId(window.__vrecSinkId).catch(() => {});
                }
            }
        };
    }
})()
