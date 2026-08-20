"""
test_loopback.py  -  Verify VB-Audio Cable loopback works.

Plays a 440 Hz tone into "CABLE Input" (playback) and measures whether
"CABLE Output" (recording) captures it. CABLE Output is what SYS will use
in main.py. Also prints the PyAudio index to pass as SYS.
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


def main():
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
