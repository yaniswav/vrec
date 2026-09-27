(() => {
    // Runs in every page BEFORE the video player. When the player fetches the
    // list of available qualities (HLS or DASH), only the best one is kept, so
    // it loads the max quality from the first second. Detected by content,
    // regardless of the file's URL.
    if (window.__vrecQualityFilter) return;
    window.__vrecQualityFilter = true;

    const filterHls = t => {
        if (!t.includes('#EXT-X-STREAM-INF')) return null;
        const lines = t.split(/\r?\n/), variants = [];
        for (let i = 0; i < lines.length; i++) {
            if (!lines[i].startsWith('#EXT-X-STREAM-INF')) continue;
            let j = i + 1;
            while (j < lines.length && (!lines[j].trim() || lines[j].startsWith('#'))) j++;
            const r = lines[i].match(/RESOLUTION=(\d+)x(\d+)/), b = lines[i].match(/[:,]BANDWIDTH=(\d+)/);
            variants.push({ start: i, end: j, w: r ? +r[1] : 0, h: r ? +r[2] : 0, bitrate: b ? +b[1] : 0 });
            i = j;
        }
        if (variants.length < 2) return null;
        const maxHeight = Math.max(...variants.map(v => v.h));
        const keep = maxHeight ? variants.filter(v => v.h === maxHeight)
                               : [variants.reduce((a, b) => b.bitrate > a.bitrate ? b : a)];
        if (keep.length === variants.length) return null;
        const remove = new Set();
        for (const v of variants) if (!keep.includes(v)) for (let k = v.start; k <= v.end; k++) remove.add(k);
        window.__vrecForcedQuality = maxHeight ? keep[0].w + 'x' + keep[0].h : 'max bitrate';
        return lines.filter((_, k) => !remove.has(k)).join('\n') + '\n';
    };

    const filterDash = t => {
        if (!t.includes('<MPD')) return null;
        const pattern = /<Representation\b[^>]*?\bheight="(\d+)"[^>]*?(?:\/>|>[\s\S]*?<\/Representation>)/g;
        const representations = [...t.matchAll(pattern)];
        const heights = new Set(representations.map(m => +m[1]));
        if (heights.size < 2) return null;
        const maxHeight = Math.max(...heights);
        for (const m of representations.reverse()) if (+m[1] < maxHeight) t = t.slice(0, m.index) + t.slice(m.index + m[0].length);
        const w = t.match(new RegExp('<Representation\\b[^>]*?\\bwidth="(\\d+)"[^>]*?\\bheight="' + maxHeight + '"'));
        window.__vrecForcedQuality = w ? w[1] + 'x' + maxHeight : maxHeight + 'p';
        return t;
    };

    const filter = t => {
        try { return typeof t === 'string' ? (filterHls(t) ?? filterDash(t)) : null; } catch (e) { return null; }
    };
    const looksLikeManifest = buf => {
        const start = new TextDecoder().decode(buf.slice(0, 256));
        return start.includes('#EXTM3U') || start.includes('<MPD') || start.includes('<?xml');
    };

    // --- XMLHttpRequest requests (hls.js, Video.js...) ---
    const XHR = XMLHttpRequest.prototype;
    const responseTextDescriptor = Object.getOwnPropertyDescriptor(XHR, 'responseText');
    const responseDescriptor = Object.getOwnPropertyDescriptor(XHR, 'response');
    const cache = new WeakMap();
    const read = xhr => {
        if (xhr.readyState !== 4) return null;
        if (cache.has(xhr)) return cache.get(xhr);
        let res = null;
        try {
            const type = xhr.responseType;
            if (type === '' || type === 'text') {
                res = filter(responseTextDescriptor.get.call(xhr));
            } else if (type === 'arraybuffer') {
                const buf = responseDescriptor.get.call(xhr);
                if (buf && buf.byteLength < 5e6 && looksLikeManifest(buf)) {
                    const f = filter(new TextDecoder().decode(buf));
                    if (f !== null) res = new TextEncoder().encode(f).buffer;
                }
            }
        } catch (e) {}
        cache.set(xhr, res);
        return res;
    };
    Object.defineProperty(XHR, 'responseText', { configurable: true, get() {
        const r = read(this); return typeof r === 'string' ? r : responseTextDescriptor.get.call(this); } });
    Object.defineProperty(XHR, 'response', { configurable: true, get() {
        const r = read(this); return r !== null ? r : responseDescriptor.get.call(this); } });

    // --- fetch requests (Shaka, dash.js, and other modern players...) ---
    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        const response = await originalFetch.apply(this, args);
        try {
            const type = (response.headers.get('content-type') || '').toLowerCase();
            const size = +(response.headers.get('content-length') || 0);
            if (/^(video|audio|image|font)\//.test(type) || size > 5e6) return response;
            const buf = await response.clone().arrayBuffer();
            if (buf.byteLength > 5e6 || !looksLikeManifest(buf)) return response;
            const f = filter(new TextDecoder().decode(buf));
            if (f === null) return response;
            const headers = new Headers(response.headers);
            headers.delete('content-length'); headers.delete('content-encoding');
            const rewritten = new Response(f, { status: response.status, statusText: response.statusText, headers });
            Object.defineProperty(rewritten, 'url', { value: response.url });
            return rewritten;
        } catch (e) { return response; }
    };
})()
