() => {
    // Makes the video fill the whole window and hides the rest of the page
    // (bars, buttons, player logos), without depending on the site's fullscreen button.
    const v = window.__vrecVideo;
    v.classList.add('__vrec_fill');
    if (!document.getElementById('__vrec_fill_style')) {
        const s = document.createElement('style');
        s.id = '__vrec_fill_style';
        s.textContent = `
            html, body { background: #000 !important; overflow: hidden !important; }
            body * { visibility: hidden !important; }
            video.__vrec_fill {
                visibility: visible !important; display: block !important; opacity: 1 !important;
                position: fixed !important; inset: 0 !important; margin: 0 !important;
                width: 100vw !important; height: 100vh !important;
                max-width: none !important; max-height: none !important;
                padding: 0 !important; border: 0 !important; transform: none !important;
                object-fit: contain !important; background: #000 !important;
                z-index: 2147483647 !important;
            }`;
        document.head.appendChild(s);
    }
    // Parent elements' CSS effects can "trap" the video: neutralize them
    for (let e = v.parentElement; e && e !== document.documentElement; e = e.parentElement) {
        for (const [prop, val] of [['transform', 'none'], ['filter', 'none'], ['perspective', 'none'],
                                   ['contain', 'none'], ['container-type', 'normal'], ['will-change', 'auto'],
                                   ['backdrop-filter', 'none'], ['clip-path', 'none'], ['opacity', '1']])
            e.style.setProperty(prop, val, 'important');
    }
    const fills = () => {
        const r = v.getBoundingClientRect();
        return r.width >= innerWidth * 0.95 && r.height >= innerHeight * 0.95;
    };
    // Last resort: pull the video out of its player (if a parent is hidden)
    if (!fills()) document.body.appendChild(v);
    return fills();
}
