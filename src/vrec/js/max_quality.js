(maxHeight) => {
    // Picks the best quality offered by the player (instead of "auto"), at most
    // maxHeight pixels high (0 = no cap). If every level is above the cap, the
    // smallest one is used.
    const v = window.__vrecVideo;
    const cap = maxHeight || 0;
    let method = null, target = 0;

    const chooseHeight = heights => {
        const known = heights.filter(h => h > 0);
        if (!known.length) return 0;
        if (!cap) return Math.max(...known);
        const fitting = known.filter(h => h <= cap);
        return fitting.length ? Math.max(...fitting) : Math.min(...known);
    };

    // 1. Video.js players
    try {
        const players = window.videojs && window.videojs.getPlayers
            ? Object.values(window.videojs.getPlayers()).filter(Boolean) : [];
        for (const p of players) {
            const levels = p.qualityLevels ? p.qualityLevels() : null;
            if (!levels || !levels.length) continue;
            const heights = [];
            for (let i = 0; i < levels.length; i++) heights.push(levels[i].height || 0);
            const best = chooseHeight(heights);
            for (let i = 0; i < levels.length; i++) levels[i].enabled = heights[i] === best;
            method = 'Video.js'; target = Math.max(target, best);
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
                const heights = o.levels.map(l => l.height || 0);
                const best = Math.max(0, heights.indexOf(chooseHeight(heights)));
                o.currentLevel = best;
                method = method || 'hls.js'; target = Math.max(target, heights[best]);
            } catch (e) {}
        }
    } catch (e) {}

    // 3. JW Player
    try {
        const j = window.jwplayer && window.jwplayer();
        const levels = j && j.getQualityLevels ? j.getQualityLevels() : null;
        if (levels && levels.length) {
            const heights = levels.map(l => l.height || parseInt(l.label) || 0);
            const best = chooseHeight(heights);
            if (best > 0) {
                j.setCurrentQuality(heights.indexOf(best));
                method = method || 'JW Player'; target = Math.max(target, best);
            }
        }
    } catch (e) {}

    // 4. Otherwise: click the best quality in the player's menu ("1440p", "4K"...)
    if (!method) {
        const value = text => {
            text = text.trim();
            if (text.length > 16) return 0;
            let m = text.match(/^(\d{3,4})\s*p/i);
            if (m) return +m[1];
            m = text.match(/^(\d)\s*k\b/i);
            return m ? ({ 4: 2160, 5: 2880, 6: 3240, 8: 4320 }[m[1]] || 0) : 0;
        };
        const options = [];
        for (const el of document.querySelectorAll('li, button, a, [role^=menuitem], [role=option], div, span')) {
            if (el.children.length > 1) continue;
            const h = value(el.textContent || '');
            if (h > 0) options.push({ el, h });
        }
        const best = chooseHeight(options.map(o => o.h));
        const choice = options.find(o => o.h === best);
        if (choice) {
            (choice.el.closest('li, button, a, [role^=menuitem], [role=option]') || choice.el).click();
            method = 'player menu'; target = best;
        }
    }
    return { method, target };
}
