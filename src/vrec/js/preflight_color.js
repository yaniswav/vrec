(color) => {
    // Pre-flight check: fills the test page with one solid color ("#rrggbb").
    document.documentElement.style.background = color;
    document.body.style.background = color;
}
