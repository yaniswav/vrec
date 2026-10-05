(elapsed) => {
    // One attempt at picking the main video (longest / largest) on the page. vrec calls it every
    // half second with the seconds spent so far, and gives up after 30 s.
    const score = v => (isFinite(v.duration) ? v.duration : 0) * 1e7
                     + v.videoWidth * v.videoHeight + v.offsetWidth * v.offsetHeight;
    const v = [...document.querySelectorAll('video')].sort((a, b) => score(b) - score(a))[0];
    if (!v) return { status: 'none' };
    if (v.readyState < 1) {
        // Some players load nothing before "play": start them muted to kick things off
        if (!v.__vrecKicked && elapsed > 3) { v.__vrecKicked = true; v.muted = true; v.play().catch(() => {}); }
        return { status: 'loading' };
    }
    window.__vrecVideo = v;
    return { status: 'found', duration: v.duration };
}
