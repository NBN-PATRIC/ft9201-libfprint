# Matching on the FT9201: why not minutiae, and what to do instead

This document records why the NBIS/Bozorth3 path that libfprint gives image devices
for free does not work on this sensor, what the vendor appears to do instead, and an
offline measurement showing that the alternative is viable. It is the design basis
for the matcher work; the measurements behind the negative half are in
[`tuning/MOSAICKING.md`](tuning/MOSAICKING.md).

## The ceiling on minutiae, measured

A 64×80 frame at the sensor's ~500 dpi covers **3.2 × 4.1 mm**. On frames that were
independently measured to contain sharp ridge structure, the full 90-combination
sweep (5 enlargements × 6 pre-processings × 3 `ppmm`) extracts **at best 3 minutiae
per frame and never scores a match**. Bozorth3's threshold is 40.

Mosaicking several frames into a larger image is the standard answer for small
sensors, and it was tried at length. It raises the minutiae *count* but the extra
minutiae are seam artifacts: two independent composites of the same finger, each
carrying 16 minutiae, score **zero** against each other. The cause is now isolated —
the frames differ by rotation as well as translation between presses, and getting
rigid registration accurate enough that averaging does not cancel ridges is an open
problem. Details and numbers in `tuning/MOSAICKING.md`.

## What the vendor does

Kali ships `libfprint-2-2` version `1:1.94.11+tod1-kali1`, which despite the `tod1`
suffix has **no TOD module directory and no TOD support strings**. Instead the
FocalTech sources are linked directly into the library — `focaltech.c`,
`focal_base.c`, `focal_fp_spi.c`, `ft_protocol.c`, `ft_sensor.c`, `ft_moc.c`,
`ft9366.c`, `ft9362_image_processing.c` all appear in its string table, alongside
driver names `focaltech`, `focaltech:moc`, `focaltech:ft9366` and
`focaltech:algorithm`.

The design-relevant strings are these:

```
FtEnrollByTemplate...gEnrolledTemplate[%d]->currentSubtemplatesNum(=%d)
    >= MAX_SUBTEMPLATES_PER_FINGER(=%d) or gSensorInfor.enrollMaxTplCount(=%d)
FtEnrollByTemplate...g_sensor_infor.alg_max_tpl_count = %d
```

**Multiple subtemplates per finger.** The vendor does not stitch a large image — it
stores a set of partial views per enrolled finger and matches a probe against the
set. That is the standard approach for small-area sensors, and it sidesteps the
registration problem entirely: nothing is ever averaged, so nothing cancels.

This is an observation of a shipped binary's string table for interoperability
purposes. No vendor code is reproduced or redistributed here, and this project
remains a clean-room implementation from the protocol in `PROTOCOL.md`.

## Measuring whether correlation is enough

If the scheme is "keep N views, match against all", the question is whether a
correlation score separates same-finger from different-finger comparisons well
enough that *best-of-N* is reliable.

`tuning/ncc2.py` measures exactly that. Design points, each forced by an earlier
failure:

- **Correlate in the ridge band, not on raw pixels.** The background brightness
  gradient dominates raw correlation and buries the ridge signal — the same effect
  that made mosaic alignment choose offsets 66 px off at 0.10 correlation. Input is
  a difference of Gaussians (σ 1.0 / 4.0).
- **Search rotation as well as translation.** Measured ridge directions across
  presses span 0°–105°; the finger rotates.
- **Score over a fixed-size window.** A first attempt scored over whatever region
  happened to overlap, and its maxima landed almost entirely at ±60°, the edge of
  the rotation search: shrinking the overlap inflates correlation. Every score here
  uses exactly 36×36 = 1296 valid pixels, so all scores are comparable and there is
  nothing to gain by reducing area.

Genuine set: 45 pairs from 10 frames of one finger. Impostor set: 360 comparisons
against 64×80 crops of other fingers (libfprint's reference captures). Synthetic
floor: the same frames with 6×6 blocks shuffled, which destroys ridge structure but
preserves local statistics.

| | n | mean | p50 | p95 | max |
|---|---|---|---|---|---|
| **genuine** (same finger) | 45 | **0.387** | 0.376 | 0.701 | 0.791 |
| impostor (other fingers) | 360 | 0.171 | 0.229 | 0.347 | 0.463 |
| synthetic (shuffled) | 10 | 0.226 | 0.223 | 0.299 | 0.308 |

Pairwise the distributions overlap, and they should: two partial views of *different
regions* of the same finger have no reason to correlate. What matters is best-of-N.
Taking the most conservative possible threshold — the **maximum** impostor score,
0.463 — a single genuine pair clears it 38% of the time, so:

| subtemplates N | P(at least one matches) |
|---|---|
| 1 | 0.38 |
| 3 | 0.76 |
| 5 | 0.91 |
| **8** | **0.98** |
| 12 | 1.00 |

At a threshold set to the impostor p95 (0.347) instead, N = 5 already reaches 0.98.

## Limits of this measurement

Stated plainly, because the numbers above are encouraging enough to be misread:

1. **The impostor set is from other sensors, not this one.** Two fingers read by the
   *same* sensor share fixed-pattern noise and optical characteristics, so
   same-sensor impostor scores will be higher than 0.171. This is the single biggest
   weakness, and it makes the table above optimistic. A second finger captured on
   this sensor is needed before any threshold is trustworthy.
2. **One finger, ten frames.** Enough to show the effect, not enough to set an
   operating point.
3. **The best-of-N column assumes independence between pairs**, which is false —
   the pairs share frames. Treat it as an upper bound on the benefit, not a
   prediction.

So: the approach is *supported*, not *validated*. The next measurement that matters
is a same-sensor impostor baseline.

## The whole scheme, measured

The table above is pairwise statistics. `tuning/subtpl.py` builds the actual system —
enrollment selects subtemplates, verification scores a probe against all of them and
accepts on the best — and measures it leave-one-out: for each frame, enrol from the
other nine and verify with the held-out one.

Enrollment does not keep the first N frames. It keeps those that pass a ridge-quality
gate **and** are not near-duplicates (NCC ≥ 0.75) of one already stored, so the set
spans the finger instead of piling up on one spot. On the reference frames it kept 9
of 10, dropping one as a duplicate on its own.

| | n | mean | min | max |
|---|---|---|---|---|
| genuine (leave-one-out) | 10 | **0.612** | 0.386 | 0.791 |
| impostor vs full enrolment | 36 | 0.318 | — | 0.463 |

| threshold | genuine accepted | impostor accepted |
|---|---|---|
| 0.40 | 90% | 8% |
| 0.45 | 90% | 6% |
| **0.50** | **90%** | **0%** |
| 0.55 | 70% | 0% |

So the scheme works on this data: **90% true accept at 0% false accept.** Note the
impostor mean rises from 0.171 (pairwise) to 0.318 here, because an impostor probe
now also gets best-of-9 — which is the honest way to measure it.

Two things this does **not** show. The single genuine failure is a frame the
quality measure had already flagged as blurred, so the tail is a capture-quality
problem as much as a matcher problem. And 36 impostor samples support no claim
stronger than FAR < 1/36; real use needs several orders of magnitude more, on
same-sensor data.

`tuning/capture-finger.py` exists to collect that data: it scores ridge quality on
each touch as it happens and tells the operator to press harder rather than letting a
whole session turn out unusable. Its gate is set at 0.45, measured to reject exactly
the three frames the diagnostics called blurred and none of the useful ones.

## The matcher in C

`driver/ft9201-match.c` + `.h` is the implementation the driver will use. No new
dependency: the correlation is direct rather than FFT-based, because a 36×36 template
over ~560 positions × 31 angles × 9 subtemplates is roughly 200 M operations — a
fraction of a second, and libfprint does not have to grow an fftw dependency for it.

`tuning/match-ctest.c` reproduces the Python evaluation with it. Per-frame ridge
quality comes out **identical** to the reference (1.57, 1.56, 1.47, 1.41, 1.34, 1.33,
1.32, 1.31, 1.12, 1.04), and with the Python switched to the same interpolation the
leave-one-out scores agree to three decimals on 8 of 10 frames, with an identical
subtemplate count on all ten:

| frame | Python (bilinear) | C |
|---|---|---|
| 02 | 0.791 | 0.791 |
| 03 | 0.762 | 0.762 |
| 06 | 0.868 | 0.868 |
| 08 | 0.784 | 0.784 |
| 10 | 0.850 | 0.851 |
| 05 | 0.535 | 0.496 |
| 09 | 0.645 | 0.516 |

The two outliers are a real residual difference, not noise: the C valid-pixel mask is
more conservative at the rotated border and rejects positions PIL accepts, so a
near-tie flips. It matters — frame 5 lands at 0.496, just under the 0.50 threshold —
so the operating point below is measured on the C implementation rather than
inherited from the Python one.

C uses bilinear interpolation where the reference used bicubic. That choice is
deliberate (it avoids carrying a 4-tap filter into the driver) but it is not free:
bilinear smooths, smoothing raises correlation, and before the interpolations were
matched the C scores read ~0.06 high across the board.

| threshold | genuine accepted | impostor accepted |
|---|---|---|
| 0.40 | 100% | 8% |
| 0.45 | 100% | 3% |
| **0.50** | **90%** | **0%** |
| 0.55 | 60% | 0% |

`FT_THRESHOLD` is set to 0.50 and marked provisional in the header, for the reasons
in the limits section above: 36 impostor samples from *other sensors* cannot fix an
operating point for real use.

## Implementation sketch

Not yet written. Recorded so the design is not re-derived later.

libfprint's `FpImageDevice` hardcodes the NBIS pipeline, so a correlation matcher
cannot live under it. The driver would subclass `FpDevice` directly and implement
`enroll` / `verify` / `identify`, storing its own template via `fpi_print_set_type
(print, FPI_PRINT_RAW)` — the same route the on-chip matchers such as `elanmoc`
already take upstream.

Template: N band-passed subtemplates per finger plus, for each, its measured ridge
quality and dominant orientation. Enrollment keeps frames that pass a quality gate
and are not near-duplicates of one already stored, so the set spans the finger
instead of piling up on one spot. Verification band-passes the probe, searches
rotation and translation against each subtemplate, and accepts on the best score.
Orientation stored per subtemplate lets the rotation search start near the right
angle rather than sweeping blind.

Open questions: the operating threshold (blocked on same-sensor impostor data), N
and the quality gate, and whether `focaltech:moc` means this chip can match on-chip
— which would be a better path than any host-side matcher.
