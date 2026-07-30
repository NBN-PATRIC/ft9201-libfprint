# Matching on the FT9201: why not minutiae, and what to do instead

> **Read the last section first.** This document is written in the order the work
> happened, and it changes its mind twice. Short version: minutiae extraction is a
> dead end on this sensor; correlation over subtemplates **does** discriminate
> identity (d′ = 3.28, complete separation) but has essentially **no tolerance to the
> finger being rotated**, and neither denser enrolment, Gabor enhancement, nor a
> larger scoring window recovers it — and then it turns out **rotation was never the
> problem either**: synthetic rotation is recovered to 0.98, so the search works, and
> what defeats a physically rotated finger is that a 3.2 × 4.1 mm window then sees a
> different patch of skin. The real variable is enrolment coverage, and the case that
> decides usability — natural, casual presentations rather than deliberately identical
> or deliberately exaggerated ones — is **still unmeasured**. Sections are kept in the
> order they happened because the reasoning errors are the useful part.
> Jump to [the last section](#it-was-never-rotation-it-is-that-the-window-sees-a-different-patch).

This document records why the NBIS/Bozorth3 path that libfprint gives image devices
for free does not work on this sensor, what the vendor appears to do instead, and how
a correlation-based alternative was designed, implemented, and ultimately measured to
fail. The measurements behind the minutiae half are in
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

---

# Same-sensor impostors: the approach does not work

> Added after collecting impostor data from **this** sensor, which the sections
> above repeatedly flagged as the measurement that mattered. It was, and it
> overturns their conclusion. Everything above stands as recorded work; the
> operating point it proposes does not survive.

Thirty samples were captured on the sensor itself: 12 from one finger (5 in a fixed
position, 7 deliberately rotated) and 18 from two other fingers.

## The impostor scores roughly double

| | impostors from other sensors | **impostors from this sensor** |
|---|---|---|
| mean | 0.171 | **0.375** |
| max | 0.463 | **0.744** |

At the 0.50 threshold the earlier data supported, the real numbers are **64% genuine
accepted and 33% of impostors accepted** — 6 false accepts out of 18. Reaching zero
false accepts requires 0.75, which passes only 36% of genuine attempts.

## It is not a tuning problem

Two candidate fixes were tested separately so their effects could not be confused —
removing the sensor's fixed-pattern noise (estimated as the per-pixel median across
all fingers, which correlates 0.30 with any single frame), and widening the rotation
search from ±45° to ±90°, since the measured orientation spread across presses
reaches 90°.

Separability is reported as d′, which compares the two distributions without
reference to any threshold:

| variant | d′ | genuine mean | impostor mean |
|---|---|---|---|
| baseline | 0.14 | 0.472 | 0.437 |
| rotation ±90° | 0.21 | 0.669 | 0.636 |
| fixed pattern removed | 0.24 | 0.512 | 0.461 |
| both | **0.29** | 0.671 | 0.630 |

A usable biometric needs d′ well above 1.5. At 0.2 the genuine and impostor
distributions are, for practical purposes, the same distribution. Widening the
rotation search raises both means together — it buys no separation, only inflation.
Fixed-pattern removal helps measurably but by a factor nowhere near enough.

Any "best threshold with zero false accepts" figure from these runs is an artifact of
small samples: with 7 genuine probes, a threshold landing above the highest impostor
is luck rather than signal. d′ is the honest summary.

## Why

Two fingers read by the same sensor share fixed-pattern noise, illumination profile,
and ridge-texture statistics. The scoring window is 36×36 px — **1.8 × 1.8 mm**. At
that size there is not enough distinctive structure to separate *identity* from
*sensor signature*, and normalised cross-correlation on band-passed intensity has no
mechanism to tell them apart.

## Where this leaves the project

Two approaches have now been measured on this sensor and both fail:

1. **NBIS minutiae** — at best 3 minutiae per frame, no match in any of 90 parameter
   combinations.
2. **Correlation over subtemplates** — d′ ≈ 0.2 against same-sensor impostors.

The vendor's library does work on the same hardware, so the problem is solvable; the
techniques tried here are simply not the ones that solve it. Its string table points
at a dedicated `focaltech:algorithm` component alongside `focaltech:moc`, and if this
chip can match on-chip then the host-side matcher is the wrong thing to be building
in the first place — the driver would only need the command protocol, as `elanmoc`
does upstream.

The capture side of this project is unaffected and remains sound: the protocol in
`PROTOCOL.md` is verified, image acquisition is good, and ridge structure is
resolved at an 8–12 px period.

## Reproducing

```bash
python3 ft9201-capture.py <dir> <label> <presses>   # touch-event driven capture
python3 analisar-sessao.py <dir> d1                 # per-sample verdict and cause
python3 fpn-test.py <dir> d1                        # fixed-pattern and rotation tests
```

---

# Correction: it discriminates identity — it does not tolerate rotation

The section above concluded the correlation approach fails outright. That conclusion
came from a **biased test of my own construction** and is wrong in an important way.

The enrolment used the five fixed-position samples, which left only the seven
*deliberately rotated* samples as genuine probes — while impostors came from any
presentation. Genuine was being scored on the hard case and impostor on the easy one.

Running the fair test — leave-one-out **within** the fixed-position samples:

| genuine probes | mean | min | d′ vs impostor |
|---|---|---|---|
| **same position** | **0.985** | 0.966 | **+3.28** |
| rotated | 0.472 | 0.152 | +0.14 |
| *(impostor, for reference)* | 0.437 | — max 0.777 | — |

Worst same-position genuine 0.966 against best impostor 0.777: **complete separation
with a margin of 0.19.** Correlation discriminates identity on this sensor. What it
does not do is survive the finger being placed differently.

## The real failure is rotation tolerance, and more views do not fix it

If sparse enrolment were the problem, adding the rotated samples to the enrolment
should rescue the rotated probes. Tested, leave-one-out over the rotated set so the
probe is never enrolled:

| enrolment | views | genuine | impostor | d′ | separates |
|---|---|---|---|---|---|
| fixed position only | 5 | 0.472 | 0.437 | 0.14 | no |
| fixed + rotated | 12 | 0.696 | 0.638 | 0.47 | no |
| rotated only | 7 | 0.629 | 0.614 | 0.10 | no |

Adding views raises genuine — and raises impostor almost as much, because best-of-N
gives an impostor more chances too. The seven rotated views span 180° with roughly
25° between neighbours, and correlation cannot bridge even that gap: a 25°-rotated
view of the same finger scores in the same range as a different finger.

## What this means

The diagnosis is much narrower than "correlation does not work":

- **Identity discrimination: solved.** d′ = 3.28, complete separation, when the
  presentation is close to an enrolled one.
- **Rotation invariance: absent.** The explicit rotation search over the probe does
  not recover it. Rotating a 1.8 mm window resamples it, and what little distinctive
  structure it holds does not survive.
- **Coverage does not substitute for invariance.** Denser enrolment inflates both
  distributions together.

So the missing piece is a rotation-invariant representation, not more data, not more
enrolment views, and not a better threshold. That also reframes the vendor's 13
enrolment stages: they are certainly covering presentation space, but coverage alone
is measurably not enough, so their algorithm must carry invariance of its own.

Two things that did **not** help, both worth recording so they are not retried:

- **Gabor ridge enhancement** (orientation field → oriented filter bank, coherence
  0.77–0.87, so the field is confident). d′ stays at 0.19–0.30 and goes *negative* in
  two variants. Enhancement makes every fingerprint look like clean parallel ridges,
  which at this scale makes different fingers look more alike, not less.
- **A larger scoring window.** Sweeping 24→48 px (1.5→6.0 mm²): d′ goes
  0.04 → 0.09 → 0.14 → −0.16 → −0.17. It does not rise with area, and beyond 36 px it
  falls, because a larger window leaves less room to slide and genuine matches lose
  their alignment before impostors do. Area is not the bottleneck.

## Reproducing

```bash
python3 area-test.py <dir> d1 45      # scoring window vs separability
python3 gabor.py <dir> <out>          # ridge enhancement, then re-run the tests
python3 fpn-test.py <dir> d1          # fixed-pattern removal and rotation range
```

---

# It was never rotation. It is that the window sees a different patch

The section above concludes the missing piece is a rotation-invariant representation.
Before building one — log-polar, Fourier-Mellin — the assumption was worth testing
directly: rotate a sample **synthetically** and see whether the existing search
recovers it. Same content, only the angle changes.

| synthetic rotation | best NCC |
|---|---|
| 0° | 0.981 |
| 5° | 0.984 |
| 10° | 0.972 |
| 15° | 0.981 |
| 20° | 0.985 |
| 30° | 0.981 |
| 45° | 0.981 |
| 60° | 0.798 |
| 90° | 0.981 |

*(the 60° dip is the valid-pixel mask clipping at that angle, not a search failure)*

Against 0.472 for a physically rotated finger. **The rotation search already works.**
Rotation invariance was never the missing piece, and a Fourier-Mellin or log-polar
descriptor would have solved a problem this sensor does not have.

What actually happens when the finger turns on a 3.2 × 4.1 mm window is that a
*different patch of skin* comes into view — plus elastic deformation of the skin
against the platen. Two views of different regions of one finger have no reason to
correlate, which is the same fact that defeated mosaicking at the start of this
document, arriving from the other direction.

## What this changes about the diagnosis

The problem is neither identity discrimination (d′ = 3.28) nor rotation (recovered
to 0.98 synthetically). It is **coverage**: the enrolled set has to contain a view
close to whatever the user actually presents.

And the coverage test earlier in this document — adding rotated views raises genuine
and impostor together — was run on a deliberately adversarial set. The instruction
for those captures was to exaggerate the angle, one touch straight, one tilted left,
one tilted right, two extreme. In leave-one-out over seven views spanning 180°, the
held-out probe never has a near neighbour by construction.

That is not how the sensor gets used. Someone unlocking a machine puts the same
finger down roughly the same way each time. So the measurement that actually decides
whether this is usable has not been made yet: **natural, casual presentations** —
neither deliberately identical nor deliberately rotated.

Both existing sets are artificial extremes, and the honest position is that the
practical case sits between them and is currently unmeasured.
