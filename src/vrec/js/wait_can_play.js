() => new Promise(ok => {
    const v = window.__vrecVideo;
    if (v.readyState >= 4) return ok(true);
    v.addEventListener('canplaythrough', () => ok(true), { once: true });
    setTimeout(() => ok(false), 20000);
})
