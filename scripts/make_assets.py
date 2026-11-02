"""Generates the built-in notification tones and PWA icons (no third-party assets, no licensing concerns)."""
import math, struct, wave, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "public"
SR = 22050

def tone(notes, path):
    frames = []
    for freq, dur, vol in notes:
        n = int(SR * dur)
        for i in range(n):
            t = i / SR
            env = min(1.0, i / (SR * 0.01)) * math.exp(-3.2 * t / dur)
            s = 0.0 if freq == 0 else vol * env * (math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t))
            frames.append(int(max(-1, min(1, s * 0.6)) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))

(ROOT / "sounds").mkdir(parents=True, exist_ok=True)
tone([(880, 0.18, 0.8), (1318.5, 0.42, 0.7)], ROOT / "sounds/chime.wav")
tone([(1046.5, 0.9, 0.8)], ROOT / "sounds/bell.wav")
tone([(660, 0.12, 0.7), (0, 0.06, 0), (660, 0.12, 0.7), (0, 0.06, 0), (660, 0.12, 0.7)], ROOT / "sounds/pulse.wav")
tone([(988, 0.15, 1.0), (740, 0.15, 1.0)] * 3, ROOT / "sounds/alert.wav")
tone([(523.3, 0.25, 0.5)], ROOT / "sounds/soft.wav")

def png(size, path):
    # Rounded square in the accent colour with a white "speech bubble" dot pattern.
    acc = (47, 91, 211)
    rows = []
    r = size * 0.22
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx = max(r - x, 0, x - (size - 1 - r)); dy = max(r - y, 0, y - (size - 1 - r))
            inside = dx * dx + dy * dy <= r * r
            cx, cy = x - size / 2, y - size / 2
            dot = any((cx - ox) ** 2 + cy ** 2 <= (size * 0.07) ** 2 for ox in (-size * 0.2, 0, size * 0.2))
            ring = abs(math.hypot(cx, cy) - size * 0.34) < size * 0.035
            if not inside:
                row += bytes((0, 0, 0, 0))
            elif dot or ring:
                row += bytes((255, 255, 255, 255))
            else:
                row += bytes((*acc, 255))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    path.write_bytes(data)

(ROOT / "icons").mkdir(parents=True, exist_ok=True)
png(192, ROOT / "icons/icon-192.png")
png(512, ROOT / "icons/icon-512.png")
(ROOT / "icons/icon.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#2f5bd3"/>'
    '<circle cx="32" cy="32" r="21.5" fill="none" stroke="#fff" stroke-width="4.5"/>'
    '<circle cx="19.2" cy="32" r="4.5" fill="#fff"/><circle cx="32" cy="32" r="4.5" fill="#fff"/><circle cx="44.8" cy="32" r="4.5" fill="#fff"/></svg>')
print("assets written")
