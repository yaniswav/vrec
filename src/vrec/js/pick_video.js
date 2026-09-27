async () => {
    // Picks the main video (longest / largest) on the page
    const pick = () => {
        const score = v => (isFinite(v.duration) ? v.duration : 0) * 1e7
                         + v.videoWidth * v.videoHeight + v.offsetWidth * v.offsetHeight;
        return [...document.querySelectorAll('video')].sort((a, b) => score(b) - score(a))[0];
    };
    const start = Date.now();
    let v = pick(), kicked = false;
    while ((!v || v.readyState < 1) && Date.now() - start < 30000) {
        // Some players load nothing before "play": start them muted to kick things off
        if (v && !kicked && Date.now() - start > 3000) { v.muted = true; v.play().catch(() => {}); kicked = true; }
        await new Promise(r => setTimeout(r, 500));
        v = pick();
    }
    if (!v) throw new Error('No video found on the page');
    if (v.readyState < 1) throw new Error('The video is not loading');
    window.__vrecVideo = v;
    return v.duration;
}
