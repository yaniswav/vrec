() => {
    // Picks the best quality offered by the player (instead of "auto")
    const v = window.__vrecVideo;
    let method = null, target = 0;

    // 1. Video.js players
    try {
        const players = window.videojs && window.videojs.getPlayers
            ? Object.values(window.videojs.getPlayers()).filter(Boolean) : [];
        for (const p of players) {
            const levels = p.qualityLevels ? p.qualityLevels() : null;
            if (!levels || !levels.length) continue;
            let max = 0;
            for (let i = 0; i < levels.length; i++) max = Math.max(max, levels[i].height || 0);
            for (let i = 0; i < levels.length; i++) levels[i].enabled = (levels[i].height || 0) === max;
            method = 'Video.js'; target = Math.max(target, max);
        }
    } catch (e) {}

    // 2. hls.js players
    try {
        const candidates = [v.hls, v._hls];
        for (const k of Object.getOwnPropertyNames(window)) {
            try { const o = window[k]; if (o && typeof o === 'object') candidates.push(o, o.hls); } catch (e) {}
        }
        for (const o of candidates) {
            try {
                if (!o || o.media !== v || !Array.isArray(o.levels) || !o.levels.length) continue;
                let best = 0;
                o.levels.forEach((l, i) => { if ((l.height || 0) > (o.levels[best].height || 0)) best = i; });
                o.currentLevel = best;
                method = method || 'hls.js'; target = Math.max(target, o.levels[best].height || 0);
            } catch (e) {}
        }
    } catch (e) {}

    // 3. JW Player
    try {
        const j = window.jwplayer && window.jwplayer();
        const levels = j && j.getQualityLevels ? j.getQualityLevels() : null;
        if (levels && levels.length) {
            let best = -1, max = 0;
            levels.forEach((l, i) => { const h = l.height || parseInt(l.label) || 0; if (h > max) { max = h; best = i; } });
            if (best >= 0) { j.setCurrentQuality(best); method = method || 'JW Player'; target = Math.max(target, max); }
        }
    } catch (e) {}

    // 4. Otherwise: click the highest quality in the player's menu ("1440p", "4K"...)
    if (!method) {
        const value = text => {
            text = text.trim();
            if (text.length > 16) return 0;
            let m = text.match(/^(\d{3,4})\s*p/i);
            if (m) return +m[1];
            m = text.match(/^(\d)\s*k\b/i);
            return m ? ({ 4: 2160, 5: 2880, 6: 3240, 8: 4320 }[m[1]] || 0) : 0;
        };
        let best = null, max = 0;
        for (const el of document.querySelectorAll('li, button, a, [role^=menuitem], [role=option], div, span')) {
            if (el.children.length > 1) continue;
            const h = value(el.textContent || '');
            if (h > max) { max = h; best = el; }
        }
        if (best) {
            (best.closest('li, button, a, [role^=menuitem], [role=option]') || best).click();
            method = 'player menu'; target = max;
        }
    }
    return { method, target };
}
