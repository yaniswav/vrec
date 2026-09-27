() => {
    const v = window.__vrecVideo;
    v.loop = false; v.muted = false; v.volume = 1;
    v.pause(); v.currentTime = 0;
}
