() => {
    const v = window.__vrecVideo;
    if (!v || !v.isConnected) return null;
    let buffer = 0;
    for (let i = 0; i < v.buffered.length; i++)
        if (v.buffered.start(i) <= v.currentTime + 0.1 && v.buffered.end(i) >= v.currentTime)
            buffer = v.buffered.end(i) - v.currentTime;
    return { ended: v.ended, t: v.currentTime, d: v.duration, paused: v.paused,
             w: v.videoWidth, h: v.videoHeight, buffer };
}
