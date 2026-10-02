async (label) => {
    // Sends the sound of the recorded video (and of the page's Web Audio contexts) to the
    // audio output whose name contains `label`, e.g. "CABLE Input". Only this page is affected:
    // the rest of Chrome keeps its normal output.
    const v = window.__vrecVideo;
    if (!v || typeof v.setSinkId !== 'function' || !navigator.mediaDevices) {
        return { ok: false, reason: 'not supported by this browser' };
    }
    let devices;
    try {
        devices = await navigator.mediaDevices.enumerateDevices();
    } catch (e) {
        return { ok: false, reason: 'audio devices not readable' };
    }
    const outputs = devices.filter(d => d.kind === 'audiooutput');
    const wanted = String(label || '').toLowerCase();
    const matches = outputs.filter(d => d.label && d.label.toLowerCase().includes(wanted));
    // Prefer the device itself over Chrome's "default"/"communications" aliases, whose label also
    // contains its name when it is Windows' default output.
    const device = matches.find(d => d.deviceId !== 'default' && d.deviceId !== 'communications')
        || matches[0];
    if (!device) {
        const named = outputs.some(d => d.label);
        return { ok: false, reason: named ? `no audio output named "${label}"` : 'no permission to list audio outputs' };
    }
    try {
        await v.setSinkId(device.deviceId);
    } catch (e) {
        return { ok: false, reason: (e && e.name) || 'refused' };
    }
    window.__vrecSinkId = device.deviceId;
    let contexts = 0;
    for (const c of window.__vrecAudioContexts || []) {
        if (typeof c.setSinkId !== 'function') continue;
        try { await c.setSinkId(device.deviceId); contexts++; } catch (e) {}
    }
    return { ok: true, label: device.label, contexts };
}
