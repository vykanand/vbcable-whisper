"""
test_loopback.py  -  Verify VB-Audio Cable loopback works.

Plays a 440 Hz tone into "CABLE Input" (playback) and measures whether
"CABLE Output" (recording) captures it. CABLE Output is what SYS will use
in main.py. Also prints the PyAudio index to pass as SYS.

Modes:
  python test_loopback.py            # CABLE loopback (SYS) test
  python test_loopback.py --scan     # record every input device for 3s each;
                                     # speak continuously - shows which device is live
  python test_loopback.py --mic      # read from the auto-picked MIC for 5s
                                     # and print maxRMS so you can see if the
                                     # microphone delivers audio when you speak.
  python test_loopback.py --mic --idx 1   # override device index
"""
import numpy as np
import pyaudio
import sys

RATE = 16000
CHUNK = 1024


def pyaudio_index(name_substr, want_input):
    p = pyaudio.PyAudio()
    n = p.get_host_api_info_by_index(0).get("deviceCount")
    idx = None
    for i in range(n):
        d = p.get_device_info_by_index(i)
        nm = (d.get("name") or "").lower()
        mi = d.get("maxInputChannels", 0)
        mo = d.get("maxOutputChannels", 0)
        if name_substr.lower() in nm and ((want_input and mi > 0) or (not want_input and mo > 0)):
            idx = i
            break
    p.terminate()
    return idx


def test_mic(dev_idx=None, seconds=5):
    from server import find_devices, RATE, CHUNK_SIZE  # reuse project constants
    import pyaudio as _pa
    p = _pa.PyAudio()
    if dev_idx is None:
        mi, _ = find_devices()
        dev_idx = mi
    try:
        info = p.get_device_info_by_index(dev_idx)
    except Exception as e:
        print(f"[!] Cannot get device {dev_idx}: {e}")
        p.terminate()
        sys.exit(1)
    rate = int(info.get("defaultSampleRate") or RATE) or RATE
    print(f"PyAudio MIC index = {dev_idx} ({info.get('name')}) @ native {rate} Hz")
    if info.get("maxInputChannels", 0) <= 0:
        print("[!] Selected device has no input channels. Pick another with --idx <n>.")
        p.terminate()
        sys.exit(1)

    st = p.open(format=_pa.paInt16, channels=1, rate=rate, input=True,
                input_device_index=dev_idx, frames_per_buffer=CHUNK_SIZE)
    if not st.is_active() and not st.is_stopped():
        st.start_stream()
    print(f"Listening {seconds}s on MIC (speak into the mic now)... maxRMS will print below.")
    maxr = 0.0
    n = int(seconds * rate / CHUNK_SIZE)
    try:
        for _ in range(n):
            a = np.frombuffer(st.read(CHUNK, exception_on_overflow=False), dtype=np.int16).astype(np.float32)
            r = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0
            maxr = max(maxr, r)
            print(f"\rMIC RMS={r:7.1f}  max={maxr:7.1f}", end="", flush=True)
    finally:
        print()
        st.stop_stream(); st.close(); p.terminate()

    if maxr > 50:
        print(f"[OK] MIC delivers audio (maxRMS={maxr:.1f}). Device {dev_idx} is good.")
    else:
        print(f"[!] MIC is SILENT (maxRMS={maxr:.1f}).")
        print("    Check Windows: mic gain/volume up, not muted, set as")
        print("    Default Input Device, and microphone access enabled in")
        print("    Settings > Privacy & security > Microphone for this app.")
    return maxr


def scan_inputs(seconds=3):
    """Open every input device for a few seconds and report maxRMS per device.
    Speak continuously while this runs - the live mic will light up."""
    p = pyaudio.PyAudio()
    n = p.get_host_api_info_by_index(0).get("deviceCount", 0)
    devs = []
    for i in range(n):
        d = p.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            devs.append((i, d))
    print(f">>> SPEAK CONTINUOUSLY <<<  testing {len(devs)} input devices ({seconds}s each)")
    best = (None, 0.0)
    for i, d in devs:
        rate = int(d.get("defaultSampleRate") or RATE) or RATE
        try:
            st = p.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                        input_device_index=i, frames_per_buffer=CHUNK)
        except Exception as e:
            print(f"  [{i}] {str(d.get('name'))[:45]:45s} @{rate}Hz  OPEN FAILED: {e}")
            continue
        mx = 0.0
        for _ in range(int(seconds * rate / CHUNK)):
            try:
                a = np.frombuffer(st.read(CHUNK, exception_on_overflow=False), dtype=np.int16).astype(np.float32)
                mx = max(mx, float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0)
            except Exception:
                break
        try:
            st.stop_stream(); st.close()
        except Exception:
            pass
        tag = "  <-- LIVE" if mx > 50 else ""
        print(f"  [{i}] {str(d.get('name'))[:45]:45s} @{rate}Hz  maxRMS={mx:7.1f}{tag}")
        if mx > best[1]:
            best = (i, mx)
    p.terminate()
    if best[0] is not None and best[1] > 50:
        print(f"[OK] Live microphone is device {best[0]} (maxRMS={best[1]:.1f}).")
    else:
        print("[!] No input device captured speech. Check Windows mic privacy/gain/mute.")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--scan":
        secs = 3
        if "--secs" in args:
            secs = int(args[args.index("--secs") + 1])
        scan_inputs(secs)
        return
    if args and args[0] in ("--mic", "-m"):
        dev = None
        if "--idx" in args:
            dev = int(args[args.index("--idx") + 1])
        if "--test-mic" in args:
            dev = int(args[args.index("--test-mic") + 1])
        test_mic(dev)
        return

    ci = pyaudio_index("CABLE Input", want_input=False)
    co = pyaudio_index("CABLE Output", want_input=True)
    print(f"PyAudio indices -> CABLE Input(playback)={ci}, CABLE Output(capture)={co}")

    if ci is None or co is None:
        print("[!] Could not find CABLE Input/Output in PyAudio. Is VB-Audio Cable installed?")
        sys.exit(1)

    p = pyaudio.PyAudio()
    sin = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, output=True,
                 output_device_index=ci, frames_per_buffer=CHUNK)
    sout = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                  input_device_index=co, frames_per_buffer=CHUNK)

    t = np.linspace(0, 5, int(5 * RATE), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    maxr = 0.0
    for i in range(int(5 * RATE / CHUNK)):
        sin.write(tone[i * CHUNK:(i + 1) * CHUNK].tobytes())
        a = np.frombuffer(sout.read(CHUNK, exception_on_overflow=False), dtype=np.int16).astype(np.float32)
        r = np.sqrt(np.mean(a ** 2)) if len(a) else 0
        maxr = max(maxr, r)
    sin.close(); sout.close(); p.terminate()

    print(f"CABLE Output captured maxRMS = {maxr:.1f}")
    if maxr > 50:
        print("[OK] Loopback works. Use SYS =", co, "in main.py (or run with auto-pick).")
    else:
        print("[!] Loopback NOT captured.")
        print("    Make sure 'CABLE Input' is the DEFAULT playback device")
        print("    (run setup_vb_cable.ps1), and that audio is playing to it.")


if __name__ == "__main__":
    main()
