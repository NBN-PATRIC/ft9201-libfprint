#!/usr/bin/env python3
"""Captura N frames do FT9201 para calibração offline das minúcias."""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ft9201_read import FT9201, IMG_LEN, IMG_W, IMG_H

OUTDIR = sys.argv[1] if len(sys.argv) > 1 else "/home/patric/.claude/jobs/a611524c/tmp/frames"
WANT = int(sys.argv[2]) if len(sys.argv) > 2 else 10
WINDOW = int(sys.argv[3]) if len(sys.argv) > 3 else 90

os.makedirs(OUTDIR, exist_ok=True)
d = FT9201()
print(f"[i] sensor aberto — coletando ate {WANT} frames em {WINDOW}s", flush=True)
d.read_status()

got, t0, last = 0, time.time(), 0.0
print(">>> ENCOSTE E LEVANTE O DEDO REPETIDAMENTE <<<", flush=True)
try:
    while got < WANT and time.time() - t0 < WINDOW:
        try:
            p = d.finger_present()
        except Exception:
            time.sleep(0.05); continue

        # re-arma periodicamente, senao a deteccao adormece
        if time.time() - last > 1.0:
            try: d.read_status()
            except Exception: pass
            last = time.time()

        if p == 0x00:
            time.sleep(0.03); continue

        try:
            img = d.read_image()
        except Exception as e:
            print(f"    (falha na leitura: {e})", flush=True); continue

        if len(img) != IMG_LEN:
            continue
        uniq, mean = len(set(img)), sum(img) / len(img)
        var = sum((b - mean) ** 2 for b in img) / len(img)
        std = var ** 0.5
        if uniq < 40 or std < 25:        # descarta frame sem dedo real
            continue

        got += 1
        path = os.path.join(OUTDIR, f"frame{got:02d}.pgm")
        with open(path, "wb") as f:
            f.write(b"P5\n%d %d\n255\n" % (IMG_W, IMG_H))
            f.write(img)
        print(f"    ✅ frame {got}/{WANT}  media={mean:.0f} desvio={std:.0f}", flush=True)
        time.sleep(0.6)   # espera levantar o dedo
finally:
    d.close()

print(f"[=] {got} frames em {OUTDIR}", flush=True)
