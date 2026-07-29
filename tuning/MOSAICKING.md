# Multi-frame mosaicking — results (2026-07-29)

> **Correction notice (later the same day).** An earlier revision of this file
> reported that mosaicking raised minutiae extraction from 1–3 per frame to 35–41
> and presented that as progress toward matching. **Both halves of that claim were
> wrong**, and the numbers should not be relied on. What follows is the corrected
> measurement, including how the error happened. The capture and protocol findings
> in `../PROTOCOL.md` are unaffected — the error was confined to the matching
> analysis.

Ten frames of the same finger, captured with `batch-capture.py` across several
separate presses.

## What was wrong

**1. The headline numbers were measured at an enlargement the driver does not use.**

The 35/41 figures came from the harness sweep at **2× enlargement**. The driver
applies `FT9201_ENLARGE 3`. Running the *same* composite through the *driver's*
configuration:

| enlargement | minutiae on the 10-frame mosaic (168×149) |
|---|---|
| 1× | 26 |
| 2× | 33 |
| **3× — what the driver actually does** | **5** |

So the driver never produced the published figure. Worse, 3× is actively harmful:
the measured ridge period on this sensor is 8–12 px (below), and tripling it puts
the period at ~30 px, outside the band NBIS's block processing expects. The 3×
constant was tuned for a lone 64×80 frame, where the problem is that the image is
too *small* for NBIS's block grid — not too coarse. A mosaic already solves the
size problem, so the enlargement should have been retuned and was not.

**2. Minutiae count is not evidence of matching, and here it was actively
misleading.**

The real test is whether two *independent* mosaics of the same finger share
minutiae. Splitting the ten frames three different ways and matching A against B:

| partition | enlarge | minutiae A | minutiae B | Bozorth A→B | B→A |
|---|---|---|---|---|---|
| odd/even | 1× | 22 | 6 | 0 | 0 |
| first/second half | 1× | 16 | 16 | **0** | **0** |
| alternating blocks | 1× | 10 | 1 | 0 | 0 |
| odd/even | 2× | 32 | 9 | 0 | 0 |
| first/second half | 2× | 12 | 20 | 0 | 3 |
| alternating blocks | 2× | 15 | 4 | 0 | 0 |

Threshold is 40. The third row is the one that matters: **both sides had 16
minutiae and still scored zero.** The counts were not minutiae-starved — the
minutiae simply did not correspond. They were artifacts of the seams, not ridge
features. The earlier writeup read a score of 3 as "first non-zero score, just
needs more frames"; it was noise.

## The frames are good — the merge is what breaks

To separate "bad sensor data" from "bad processing", `ridge-quality.py` measures
whether a frame contains genuine periodic ridge structure. A ridge shows up as an
autocorrelation that dips negative at half a period and recovers at a full period;
a poorly-pressed finger just decays smoothly. The search runs over 12 orientations,
because a finger with vertical ridges would otherwise be failed by a horizontal-only
test.

Score = (peak at one period) − (trough at half a period). Sharp ridge > 1.0, blur ≈ 0.

| frame | score | direction | period | |
|---|---|---|---|---|
| 1 | 0.95 | 105° | 9 px | sharp |
| 2 | 0.71 | 90° | 10 px | weak |
| 3 | 0.33 | 150° | 9 px | blur |
| 4 | 0.38 | 0° | 9 px | blur |
| 5 | **1.22** | 45° | 11 px | sharp |
| 6 | 0.77 | 45° | 8 px | weak |
| 7 | **1.24** | 0° | 10 px | sharp |
| 8 | 0.33 | 0° | 12 px | blur |
| 9 | 0.54 | 30° | 9 px | weak |
| 10 | 0.50 | 0° | 10 px | weak |

Two things follow. The sensor **does** resolve ridges — consistently at an 8–12 px
period, which is ~500 dpi, exactly what NBIS assumes natively. And frame quality
varies a lot press to press, so a capture loop should be selecting frames, not
accepting all of them.

Now the same measurement on the composite:

| input | mean quality in | quality of the resulting mosaic |
|---|---|---|
| all 10 frames | 0.70 | **0.26** |
| the 3 sharpest only | 1.22 | **0.53** |

**Merging destroys the ridge structure it is supposed to preserve.** Even starting
from only the best material, 1.22 collapses to 0.53. The period survives (still
10 px at 105°) but the contrast does not — the signature of averaging ridges that
are half a period out of register.

## Root cause: the alignment metric, not the averaging

Averaging is not to blame. Sweeping every offset for pairs of sharp frames and
measuring *both* the driver's alignment metric and the resulting ridge quality:

| pair | driver's offset | its corr | its quality | ridge-optimal offset | quality | error |
|---|---|---|---|---|---|---|
| 1+5 | (−2,+40) | 0.39 | 0.94 | (0,+32) | 0.99 | 8 px |
| 1+7 | (−36,−40) | 0.17 | 0.41 | (−30,0) | **0.99** | **40 px** |
| 5+7 | (+12,−40) | 0.10 | 0.61 | (−40,0) | **1.15** | **66 px** |

At the correct offset the composite scores 0.99–1.15 — **as good as or better than
the input frames**. Mosaicking is sound. What fails is choosing the offset:
Pearson correlation over the overlap picks offsets with correlation 0.10–0.17,
i.e. there is no peak at all, and the winners sit at the edge of the search window
where the overlap is smallest. With `MIN_OV` at 600 px out of 5120, a 600-px
overlap reaches a high correlation by chance, so the search is structurally biased
toward minimal-overlap answers.

Band-pass filtering to the ridge band (difference of Gaussians, σ 1.2/5.0) before
correlating was the obvious fix and **did not work** — mean composite quality
0.42 either way, against 0.42 for raw pixels and 1.10 for the input frames.

A likely reason the search has nothing to lock onto: these ten frames come from
*separate presses*, so they differ by rotation as well as translation, and the
alignment is translation-only. Frames from within a single press would align, but
they also overlap almost completely and so add no new area — which is the real
tension any mosaicking scheme here has to resolve.

## Where this leaves minutiae matching

Independent of mosaicking, on the **three frames measured to be sharp**, the full
90-combination sweep (5 enlargements × 6 pre-processings × 3 `ppmm`) yields at
best **3 minutiae per frame and a match score of 0 in every combination**.

A 64×80 frame at 500 dpi covers 3.2 × 4.1 mm. At typical minutiae density that is
barely a dozen minutiae before edge effects, and Bozorth3 needs substantially more
to clear a threshold of 40. This is consistent with the vendor's own driver, which
matches successfully on these same frames and therefore is almost certainly doing
correlation/pattern matching rather than NBIS-style minutiae extraction.

## Status

Mosaicking is **not disproven — it is blocked on alignment**, and the alignment is
measurably broken (up to 66 px off, choosing offsets with 0.10 correlation). Fixing
it is the open path, and the ridge-quality metric above gives an objective target
to tune against, which raw correlation did not.

What should change in the driver, none of which is done yet:

1. Drop the 3× enlargement for composites, or scale it to the composite's size.
2. Gate frames on ridge quality before merging instead of accepting all of them.
3. Replace the alignment score, and require far more overlap than `MIN_OV` 600.
4. Consider rotation, or restrict merging to frames within one press.

Until then the driver's mosaicking path should be treated as experimental and not
as a working matching solution.

## Reproducing

```bash
# ridge structure per frame
python3 ridge-quality.py ./frames

# mosaic partitions: minutiae per side and A-vs-B score, at a given enlargement
gcc -O2 -o mosaic-eval mosaic-eval.c $LIBFPRINT/libfprint/nbis/bozorth3/*.c \
    -I... $(pkg-config --cflags --libs pixman-1 glib-2.0) libnbis.a -lm
./mosaic-eval ./frames 1     # then 2, then 3

# driver's chosen offset vs the ridge-preserving one
python3 align-probe.py
python3 align-bandpass.py
```
