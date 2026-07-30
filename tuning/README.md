# Offline harnesses and captured measurements

Everything here exists to answer a question about matching **without burning finger taps**:
capture once, measure offline. The conclusions are in [`../MATCHING.md`](../MATCHING.md) and
[`MOSAICKING.md`](MOSAICKING.md); this file says what each tool is for, and in what order they
were written — because several were built to correct an error in the one before.

## Capture

| tool | purpose |
|---|---|
| [`ft9201-capture.py`](ft9201-capture.py) | **Use this one.** Touch-event driven: waits for a finger, reads while it is down, debounces the release, ready again — no fixed cadence. Scores ridge quality on each touch so a bad press is known immediately, and writes a manifest row per sample (finger, phase, press, timestamp, quality, orientation, mean, σ). |
| [`capture-finger.py`](capture-finger.py) | Earlier version: quality gate, but a paced loop. |
| [`batch-capture.py`](batch-capture.py) | The original — fixed cadence, filters on σ only. Kept because the first ten reference frames came from it. |

The sensor delivers **one image per touch**: the vendor flow waits for lift after a read, and
re-arming inside a press does not produce a second frame. One sample equals one touch.

`../tools/sessao/` drives multi-finger sessions. Free the sensor first with
`../tools/ft9201-mode free` — note that `ft9201-mode dev` *starts* an fprintd, which then claims
the USB device and blocks direct access.

## Minutiae

| tool | purpose |
|---|---|
| [`tune-harness.c`](tune-harness.c) | libfprint's exact pipeline — pixman resize → NBIS `get_minutiae()` → `bozorth_probe_init()` + `bozorth_to_gallery()` — over 5 enlargements × 6 pre-processings × 3 `ppmm`. [`results-ft9201.txt`](results-ft9201.txt), control in [`results-control.txt`](results-control.txt). |
| [`ridge-quality.py`](ridge-quality.py) | Does a frame carry real periodic ridge structure, or just blur? Autocorrelation dip at half a period, recovery at one period, searched over 12 orientations. Sharp > 1.0, blur ≈ 0. |

**Result:** 1–3 minutiae per frame in all 90 combinations, no pair ever above 0. The control run
over libfprint's own captures gives 47 per frame, so the harness is right and these frames simply
carry almost nothing NBIS recognises. Enrollment completing is not evidence of anything —
libfprint accepts a stage when *one* minutia is found; Bozorth needs dozens.

## Mosaicking — blocked, see [`MOSAICKING.md`](MOSAICKING.md)

| tool | purpose |
|---|---|
| [`mosaic.py`](mosaic.py), [`mosaic-ctest.c`](mosaic-ctest.c), [`mosaic-ctest-greedy.c`](mosaic-ctest-greedy.c) | Pearson-on-overlap alignment, arrival order then greedy. The C ports check the driver's exact arithmetic before spending taps. |
| [`mosaic-eval.c`](mosaic-eval.c) | The test that mattered: two *independent* composites of one finger, matched against each other. Both carried 16 minutiae and scored **zero** — the extra minutiae are seam artifacts. |
| [`align-probe.py`](align-probe.py) | The offset the driver picks vs the one that preserves ridges. Up to 66 px apart. |
| [`align-bandpass.py`](align-bandpass.py) | Rules out the obvious fix: filtering to the ridge band before correlating changes nothing. |
| [`mosaic-rq.py`](mosaic-rq.py), [`mosaic-rot.py`](mosaic-rot.py) | Align by ridge quality rather than correlation; then rotate to a common orientation first. Better overlaps, composite still degrades. |

## Correlation matcher — measured to fail, see [`../MATCHING.md`](../MATCHING.md)

| tool | purpose |
|---|---|
| [`ncc-match.py`](ncc-match.py) | First attempt. **Its numbers are wrong**, and it is kept as the illustration: scoring over a variable overlap makes the search prefer minimum-overlap offsets, so its maxima landed at the edge of the rotation range. |
| [`ncc2.py`](ncc2.py) | Fixed 36×36 scoring window, so every score uses the same pixel count. [`results-ncc.txt`](results-ncc.txt) |
| [`subtpl.py`](subtpl.py) | The whole scheme: enrolment selects subtemplates by quality and rejects near-duplicates, verification takes best-of-N, evaluated leave-one-out. [`results-subtemplates.txt`](results-subtemplates.txt) |
| [`match-ctest.c`](match-ctest.c) | Reproduces that with the C matcher in [`../driver/ft9201-match.c`](../driver/ft9201-match.c), to confirm the port. [`results-c-matcher.txt`](results-c-matcher.txt) |
| [`analisar-sessao.py`](analisar-sessao.py) | Per-sample verdict with a **cause** for each failure — quality, rotation past the search range, or region not covered — instead of a bare pass rate. |
| [`fpn-test.py`](fpn-test.py) | Fixed-pattern-noise removal and rotation range as *separate* columns, so their effects cannot be confused. Reports d′. |
| [`gabor.py`](gabor.py) | Ridge enhancement: orientation field → oriented filter bank. Did not help, and the reason is instructive. |
| [`area-test.py`](area-test.py) | Sweeps the scoring window 24→48 px to test whether area is the bottleneck. It is not. |

## Two habits these tools enforce

**Report d′, not a threshold.** Separability compares two distributions without reference to any
cut-off. On small samples a threshold that happens to land above the top impostor is luck; d′ is
not. Below roughly 1.5 there is no usable operating point, whatever the table says.

**Validate a metric before trusting it.** Two measurements in this project turned out to be
measuring something other than what they claimed, and both survived a while because the numbers
looked plausible. So the FFT sliding correlation in `subtpl.py` is checked against brute force
before use (worst disagreement 2e-16), and the C matcher against the Python reference.

## Building the C harnesses

```bash
LFP=/path/to/libfprint          # a built libfprint tree
gcc -O2 -o tune-harness tune-harness.c "$LFP/libfprint/nbis/bozorth3/"*.c \
  -I"$LFP/libfprint/nbis/include" -I"$LFP/libfprint/nbis/libfprint-include" \
  -I"$LFP/libfprint" -I"$LFP" -I"$LFP/build" \
  $(pkg-config --cflags pixman-1 glib-2.0) \
  "$LFP/build/libfprint/libnbis.a" \
  $(pkg-config --libs pixman-1 glib-2.0) -lm
```

`bozorth3` is not part of `libnbis.a`, so its sources are compiled in directly. The correlation
matcher needs no libfprint tree:

```bash
gcc -O2 -o match-ctest match-ctest.c ../driver/ft9201-match.c \
  $(pkg-config --cflags --libs glib-2.0) -lm
```

Python harnesses need only `numpy` and `Pillow`.

## Note on data

No fingerprint images are committed. Biometric data cannot be rotated like a password, and
`.gitignore` blocks `*.pgm`, `*.png`, `*.raw` and `*.bin` so it cannot happen by accident. The
`results-*.txt` files carry the aggregate numbers the conclusions rest on.
