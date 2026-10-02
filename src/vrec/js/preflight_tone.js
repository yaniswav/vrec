async ([label, seconds]) => {
    // Pre-flight check: plays a 440 Hz tone for `seconds`, sent to the audio output whose
    // name contains `label` when possible (like the recorded pages). Returns where it played.
    const ctx = new AudioContext();
    let sink = 'default';
    try {
        if (label && typeof ctx.setSinkId === 'function' && navigator.mediaDevices) {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const wanted = label.toLowerCase();
            const matches = devices.filter(
                d => d.kind === 'audiooutput' && d.label && d.label.toLowerCase().includes(wanted));
            // Prefer the device itself over Chrome's "default"/"communications" aliases, whose label
            // also contains its name when it is Windows' default output.
            const out = matches.find(d => d.deviceId !== 'default' && d.deviceId !== 'communications')
                || matches[0];
            if (out) {
                await ctx.setSinkId(out.deviceId);
                sink = out.label;
            }
        }
    } catch (e) {
        // keep the default output
    }
    await ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 440;
    gain.gain.value = 0.5;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    await new Promise(r => setTimeout(r, seconds * 1000));
    osc.stop();
    await ctx.close();
    return { sink };
}
