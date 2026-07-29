# Offline minutiae/matching harness

`tune-harness.c` runs **the same pipeline libfprint uses** — pixman bilinear resize, NBIS
`get_minutiae()`, and `bozorth_probe_init()` + `bozorth_to_gallery()` exactly as
`fpi_print_bz3_match()` does — over PGM frames captured beforehand.

The point is to calibrate without burning finger taps: capture once with
[`batch-capture.py`](batch-capture.py), then sweep parameters offline.

## Build

```bash
LFP=/path/to/libfprint          # a built libfprint tree
gcc -O2 -o tune-harness tune-harness.c "$LFP/libfprint/nbis/bozorth3/"*.c \
  -I"$LFP/libfprint/nbis/include" -I"$LFP/libfprint/nbis/libfprint-include" \
  -I"$LFP/libfprint" -I"$LFP" -I"$LFP/build" \
  $(pkg-config --cflags pixman-1 glib-2.0) \
  "$LFP/build/libfprint/libnbis.a" \
  $(pkg-config --libs pixman-1 glib-2.0) -lm
```

`bozorth3` is not part of `libnbis.a`, so its sources are compiled in directly.

## Capture and sweep

```bash
sudo systemctl stop fprintd
sudo python3 batch-capture.py ./frames 10 90     # 10 frames of the SAME finger
sudo systemctl start fprintd

./tune-harness ./frames
```

The sweep covers 5 enlargement factors × 6 pre-processing variants (raw, autocontrast, equalize,
and each of those inverted) × 3 `ppmm` values = 90 combinations, reporting mean minutiae per
frame, mean/max Bozorth score across all same-finger pairs, and how many pairs clear the
threshold of 40 that libfprint uses.

## Results (2026-07-29)

Both result files are checked in.

### FT9201 frames — [`results-ft9201.txt`](results-ft9201.txt)

**1 to 3 minutiae per frame in every one of the 90 combinations**, and **no pair ever scored above
0**. Enlargement, contrast handling, polarity inversion and `ppmm` made no meaningful difference.

### Control — [`results-control.txt`](results-control.txt)

Same harness, same code path, run against libfprint's own reference captures
(`tests/{elanspi,secugen,aes3500,uru4000-msv2}/capture.png`):

```
1x raw   19.685   47.0 minutiae
```

**47 minutiae per frame.** So the harness is correct and the pipeline works — the FT9201 frames
genuinely carry almost nothing that NBIS recognises as a minutia.

> The control's low match scores are expected and not a signal: those four captures are different
> fingers from different sensors. Only the minutiae count is meaningful there.

## What this means

Enrollment completing is not evidence that matching will work: libfprint accepts an enroll stage
as long as *at least one* minutia is found, while Bozorth needs a couple of dozen to produce a
usable score. That gap is exactly the state the driver is in.

The frames themselves are not bad — 64×80, full dynamic range, σ ≈ 58–76, with ridges, core,
delta and pores clearly visible. The problem is how little **area** they cover: a handful of
ridges simply does not contain many ridge endings or bifurcations.

Directions worth trying, in rough order of promise:

1. **Multi-frame mosaicking.** Stitch several captures into a larger image before detection. This
   is the standard answer for small-area sensors and would raise minutiae count by construction.
2. **A non-minutiae matcher.** The vendor driver works on the same frames, which strongly suggests
   it uses correlation/pattern matching rather than NBIS-style minutiae.
3. **Ridge-level enhancement** (Gabor filtering along local ridge orientation) before `mindtct`,
   rather than the global contrast operations swept here.
4. Sweeping `LFSPARMS` fields beyond `remove_perimeter_pts` — quality thresholds may be discarding
   candidates on such a small map.

What is *not* worth more time, based on the data: plain enlargement, global contrast/equalisation,
polarity inversion, and `ppmm` tweaks. All measured, all flat.
