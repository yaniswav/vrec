# Windows setup

One-time setup for vrec. Do these steps in order; each one is quick, and you won't need to repeat it
unless you reinstall something.

## 1. Virtual display

vrec needs a second display that Chrome can be placed on, invisible to your normal desktop use. See
[virtual-display.md](virtual-display.md) for the available options and detailed instructions.

Once you have one set up, in Windows display settings:

- Virtual display: **Extend desktop to this display**, 3840 x 2160, 60 Hz.
- Your real screen: **Make this my main display**.

If you also have a real second monitor connected, see
[Choosing the right screen](virtual-display.md#choosing-the-right-screen) in virtual-display.md —
`[display] screen = "auto"` (the default) can otherwise pick that monitor instead of the virtual one.

## 2. VB-CABLE (virtual audio cable)

VB-CABLE lets OBS capture the video's audio without it ever reaching your speakers.

1. Download "VB-CABLE Driver" from [vb-audio.com](https://vb-audio.com/Cable/) and unzip it.
2. Right-click `VBCABLE_Setup_x64.exe` and choose **Run as administrator**, then **Install Driver**.
3. Restart your PC.

By default, vrec sends only the recorded video's own sound to VB-CABLE's playback side ("CABLE
Input") itself, for each video (feature `audio_sink`), using a brief microphone permission it grants
itself on the page and revokes right after — it only needs that permission to look up the audio
output device by name, it never actually reads or uses the microphone. With this on (the default),
you shouldn't need to touch the Windows volume mixer at all.

### Fallback: routing Chrome through the Windows volume mixer

Only needed if you turn the `audio_sink` feature off, or vrec warns something like `Couldn't send the
video's sound to CABLE Input (...): using the Windows audio setup.` for a particular site:

1. Play any video in the Chrome window opened by `launch_chrome.bat` (see step 5 below), then open
   **Settings > System > Sound > Volume mixer**, find the Chrome entry, and set its output to
   **CABLE Input**. Windows remembers this per application afterwards.

Side effect: this setting applies to *every* Chrome window, including your everyday browser — it will
be muted too. To get its sound back, go to the volume mixer and set that Chrome entry's output back to
**Default**.

## 3. Python

1. Open a terminal and run:

   ```
   winget install Python.Python.3.12
   ```

2. Run `scripts\windows\install.bat`. It installs vrec and its dependencies with pip.

## 4. OBS

By default, vrec starts OBS itself if it isn't already open when you run it (feature
`auto_start_obs`), and leaves it open afterwards. It looks for `obs64.exe` in the Windows registry,
then in its default install location; set `[obs] path` in `config.toml` if it can't find it (see
[config.example.toml](../config.example.toml)). You can still open OBS yourself first if you prefer.

1. **Tools > WebSocket Server Settings**, check **Enable WebSocket server**.
2. In your scene, add a **Display Capture** source pointed at the virtual display, with
   **Capture Cursor** unchecked.
3. **Settings > Video**: base (canvas) resolution **3840x2160**, FPS 60 (or 30).
4. **Settings > Output**: encoder **NVENC HEVC**, recording quality **not** "Same as stream" (see
   below), format **Hybrid MP4** (or MKV if hybrid MP4 isn't available).

   Recording quality must not be "Same as stream" because vrec needs to be able to pause the OBS
   recording independently while a video buffers; that mode does not support pausing.

vrec manages audio automatically (feature `obs_audio_routing`): it creates a "Chrome Audio (VB-CABLE)"
source, mutes your desktop audio and microphone while recording, and restores everything afterwards.

### 360-degree videos

For videos whose image is twice as wide as it is tall:

1. **Settings > Video**: set both base and output resolution to **3840x1920**.
2. Right-click the Display Capture source > **Filters > "+" > Crop/Pad**: top **120**, bottom **120**.
3. Right-click the source > **Transform > Fit to screen**.

Without this, the recorded file has black bars at the top and bottom, and the image looks distorted in
a headset.

## 5. Chrome

By default, vrec starts the recording Chrome itself if its debug port doesn't answer (feature
`auto_start_chrome`), on the same dedicated profile `launch_chrome.bat` uses, and leaves it open
afterwards. You still need `launch_chrome.bat` once, the first time, to log in to any site you need
to in that profile:

1. Double-click `scripts\windows\launch_chrome.bat`. It opens a separate Chrome window on a dedicated
   profile (so it doesn't interfere with your everyday browsing).
2. Log in to any site you need to, in that window.
3. By default, vrec moves this window to the virtual display and maximizes it there itself, before
   each run, and puts it back afterwards (feature `auto_place_window`). If you turn that feature off,
   or it doesn't work for you, do it by hand instead: click the window, press **Win + Shift + Right
   Arrow** (repeat until it lands on the virtual display), then maximize it.
4. Never minimize the window while vrec is running — a minimized window can't be captured or
   controlled correctly.

You're ready to go — run `vrec --doctor` to check everything above is in place, then see the main
[README](../README.md#quick-start) for day-to-day usage.
