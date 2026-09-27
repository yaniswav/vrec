() => [...new Set(performance.getEntriesByType('resource')
    .filter(e => ['xmlhttprequest', 'fetch', 'other'].includes(e.initiatorType))
    .map(e => { try { const u = new URL(e.name); return (u.hostname + u.pathname).slice(0, 110); }
                catch (x) { return e.name.slice(0, 110); } }))].slice(-12)
