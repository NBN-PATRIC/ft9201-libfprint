/*
 * Diagnostico: as minucias do mosaico sao reais ou artefato de costura?
 *
 * Monta dois mosaicos INDEPENDENTES a partir de subconjuntos disjuntos dos
 * frames, extrai minucias de cada um com o mesmo pipeline do driver
 * (pixman bilinear x ENLARGE + NBIS get_minutiae) e casa um contra o outro
 * com bozorth3.
 *
 * O ponto: se as minucias forem features reais da crista, dois mosaicos do
 * mesmo dedo compartilham as da regiao sobreposta e o score sobe. Se forem
 * artefato das emendas, cada mosaico inventa as suas e o score fica no chao
 * mesmo com contagem alta. Relatar a contagem de CADA lado separa as duas
 * hipoteses de "score baixo": poucas minucias vs minucias que nao batem.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <dirent.h>
#include <pixman.h>

#include "lfs.h"
#include "bozorth.h"

extern LFSPARMS g_lfsparms_V2;

#define IMG_W 64
#define IMG_H 80
#define IMG_SZ (IMG_W * IMG_H)
#define MAXF 64

/* mesmas constantes do driver */
#define ALIGN_RANGE 56
#define ALIGN_COARSE 4
#define ALIGN_MIN_OV 600
#define ALIGN_MIN_CORR 0.30
#define PPMM 19.685
static int ENLARGE = 3;         /* sobrescrito por argv[2] */

#define PAD (ALIGN_RANGE + 8)
#define CW (IMG_W + 2 * PAD)
#define CH (IMG_H + 2 * PAD)

typedef struct {
  unsigned int sum[CW * CH];
  unsigned short hits[CW * CH];
  unsigned nframes;
} Mosaic;

typedef struct { unsigned char *data; int w, h; } Img;

static void
mos_reset (Mosaic *m)
{
  memset (m, 0, sizeof *m);
}

static void
mos_paste (Mosaic *m, const unsigned char *f, int dy, int dx)
{
  for (int y = 0; y < IMG_H; y++)
    {
      int cy = y + dy;
      if (cy < 0 || cy >= CH) continue;
      for (int x = 0; x < IMG_W; x++)
        {
          int cx = x + dx;
          if (cx < 0 || cx >= CW) continue;
          m->sum[cy * CW + cx] += f[y * IMG_W + x];
          m->hits[cy * CW + cx]++;
        }
    }
  m->nframes++;
}

static double
mos_corr (const Mosaic *m, const unsigned char *f, int dy, int dx)
{
  double sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
  unsigned n = 0;

  for (int y = 0; y < IMG_H; y++)
    {
      int cy = y + dy;
      if (cy < 0 || cy >= CH) continue;
      for (int x = 0; x < IMG_W; x++)
        {
          int cx = x + dx;
          if (cx < 0 || cx >= CW) continue;
          unsigned idx = cy * CW + cx;
          if (!m->hits[idx]) continue;
          double a = (double) m->sum[idx] / m->hits[idx];
          double b = f[y * IMG_W + x];
          sa += a; sb += b; saa += a * a; sbb += b * b; sab += a * b;
          n++;
        }
    }
  if (n < ALIGN_MIN_OV) return -2.0;
  double va = saa - sa * sa / n, vb = sbb - sb * sb / n;
  if (va < 1e-6 || vb < 1e-6) return -2.0;
  return (sab - sa * sb / n) / sqrt (va * vb);
}

/* Busca grosseira -> fina, igual ao driver. */
static double
mos_best_offset (const Mosaic *m, const unsigned char *f, int *oy, int *ox)
{
  double best = -2.0;
  int by = PAD, bx = PAD, found = 0;

  for (int dy = PAD - ALIGN_RANGE; dy <= PAD + ALIGN_RANGE; dy += ALIGN_COARSE)
    for (int dx = PAD - ALIGN_RANGE; dx <= PAD + ALIGN_RANGE; dx += ALIGN_COARSE)
      { double c = mos_corr (m, f, dy, dx); if (c > best) { best = c; by = dy; bx = dx; found = 1; } }
  if (!found) return -2.0;
  for (int dy = by - ALIGN_COARSE; dy <= by + ALIGN_COARSE; dy++)
    for (int dx = bx - ALIGN_COARSE; dx <= bx + ALIGN_COARSE; dx++)
      { double c = mos_corr (m, f, dy, dx); if (c > best) { best = c; by = dy; bx = dx; } }
  *oy = by; *ox = bx;
  return best;
}

/* Fusao gulosa de um subconjunto; devolve quantos frames entraram. */
static int
mos_build (Mosaic *m, unsigned char all[][IMG_SZ], const int *idx, int n, int verbose)
{
  int used[MAXF] = {0};

  mos_reset (m);
  if (n <= 0) return 0;
  mos_paste (m, all[idx[0]], PAD, PAD);
  used[0] = 1;

  for (;;)
    {
      double best = -2.0; int bk = -1, by = 0, bx = 0;
      for (int k = 0; k < n; k++)
        {
          int ly, lx;
          double c;
          if (used[k]) continue;
          c = mos_best_offset (m, all[idx[k]], &ly, &lx);
          if (c > best) { best = c; bk = k; by = ly; bx = lx; }
        }
      if (bk < 0 || best < ALIGN_MIN_CORR) break;
      if (verbose)
        printf ("      + frame %d: corr=%.2f offset=(%+d,%+d)\n",
                idx[bk] + 1, best, by - PAD, bx - PAD);
      mos_paste (m, all[idx[bk]], by, bx);
      used[bk] = 1;
    }
  return (int) m->nframes;
}

/* Recorta o retangulo preenchido e devolve a media por pixel. */
static Img
mos_flatten (const Mosaic *m)
{
  int y0 = CH, y1 = -1, x0 = CW, x1 = -1;
  Img im = {0};

  for (int y = 0; y < CH; y++)
    for (int x = 0; x < CW; x++)
      if (m->hits[y * CW + x])
        { if (y < y0) y0 = y; if (y > y1) y1 = y; if (x < x0) x0 = x; if (x > x1) x1 = x; }
  if (y1 < 0) return im;

  im.w = x1 - x0 + 1; im.h = y1 - y0 + 1;
  im.w -= im.w % 4;                      /* pixman exige stride multiplo de 4 */
  im.data = malloc ((size_t) im.w * im.h);
  for (int y = 0; y < im.h; y++)
    for (int x = 0; x < im.w; x++)
      {
        unsigned i = (y + y0) * CW + (x + x0);
        im.data[y * im.w + x] = m->hits[i] ? (unsigned char) (m->sum[i] / m->hits[i]) : 0;
      }
  return im;
}

/* Fracao de pixels cobertos por 2+ frames: mede quanto o mosaico e' media
 * de verdade e quanto e' so' frame solto colado do lado. */
static double
mos_overlap_frac (const Mosaic *m)
{
  long tot = 0, multi = 0;
  for (int i = 0; i < CW * CH; i++)
    if (m->hits[i]) { tot++; if (m->hits[i] > 1) multi++; }
  return tot ? (double) multi / tot : 0.0;
}

static Img
resize (const Img *src, int factor)
{
  Img dst = { .w = src->w * factor, .h = src->h * factor };
  pixman_image_t *o, *r;
  pixman_transform_t t;

  if (factor == 1)
    {
      dst = *src;
      dst.data = malloc ((size_t) src->w * src->h);
      memcpy (dst.data, src->data, (size_t) src->w * src->h);
      return dst;
    }
  dst.data = calloc ((size_t) dst.w * dst.h, 1);
  o = pixman_image_create_bits (PIXMAN_a8, src->w, src->h, (uint32_t *) src->data, src->w);
  r = pixman_image_create_bits (PIXMAN_a8, dst.w, dst.h, (uint32_t *) dst.data, dst.w);
  pixman_transform_init_identity (&t);
  pixman_transform_scale (NULL, &t, pixman_int_to_fixed (factor), pixman_int_to_fixed (factor));
  pixman_image_set_transform (o, &t);
  pixman_image_set_filter (o, PIXMAN_FILTER_BILINEAR, NULL, 0);
  pixman_image_composite32 (PIXMAN_OP_SRC, o, NULL, r, 0, 0, 0, 0, 0, 0, dst.w, dst.h);
  pixman_image_unref (o);
  pixman_image_unref (r);
  return dst;
}

static int
extract (const Img *im, struct xyt_struct *xyt)
{
  MINUTIAE *minutiae = NULL;
  int *qmap = NULL, *dmap = NULL, *lcmap = NULL, *lfmap = NULL, *hcmap = NULL;
  int mw = 0, mh = 0, bw = 0, bh = 0, bd = 0;
  unsigned char *bin = NULL, *copy;
  LFSPARMS p = g_lfsparms_V2;
  int n, r;

  p.remove_perimeter_pts = FALSE;
  copy = malloc ((size_t) im->w * im->h);
  memcpy (copy, im->data, (size_t) im->w * im->h);
  r = get_minutiae (&minutiae, &qmap, &dmap, &lcmap, &lfmap, &hcmap,
                    &mw, &mh, &bin, &bw, &bh, &bd,
                    copy, im->w, im->h, 8, PPMM, &p);
  free (copy);
  if (r) return -1;

  n = minutiae->num;
  if (xyt)
    {
      int k = n > MAX_BOZORTH_MINUTIAE ? MAX_BOZORTH_MINUTIAE : n;
      xyt->nrows = k;
      for (int i = 0; i < k; i++)
        {
          xyt->xcol[i] = minutiae->list[i]->x;
          xyt->ycol[i] = minutiae->list[i]->y;
          xyt->thetacol[i] = (int) (minutiae->list[i]->direction * 11.25 + 0.5);
        }
    }
  free_minutiae (minutiae);
  free (qmap); free (dmap); free (lcmap); free (lfmap); free (hcmap); free (bin);
  return n;
}

static int
match (struct xyt_struct *a, struct xyt_struct *b)
{
  int plen = bozorth_probe_init (a);
  return bozorth_to_gallery (plen, a, b);
}

static int
load_pgm (const char *p, unsigned char *out)
{
  FILE *f = fopen (p, "rb");
  char m[3] = {0}; int w, h, mx;
  if (!f) return -1;
  if (fscanf (f, "%2s %d %d %d", m, &w, &h, &mx) != 4 || w != IMG_W || h != IMG_H)
    { fclose (f); return -1; }
  fgetc (f);
  size_t r = fread (out, 1, IMG_SZ, f);
  fclose (f);
  return r == IMG_SZ ? 0 : -1;
}

/* Monta um mosaico com um subconjunto, extrai minucias e relata. */
static int
eval_subset (unsigned char all[][IMG_SZ], const int *idx, int n,
             const char *label, struct xyt_struct *xyt, int verbose)
{
  static Mosaic m;
  Img flat, big;
  int used, nmin;

  used = mos_build (&m, all, idx, n, verbose);
  flat = mos_flatten (&m);
  if (!flat.data) { printf ("  %s: mosaico vazio\n", label); return -1; }
  big = resize (&flat, ENLARGE);
  nmin = extract (&big, xyt);

  printf ("  %-10s %2d/%d frames  %3dx%-3d (%.2fx area)  sobrep=%2.0f%%  minucias=%d\n",
          label, used, n, flat.w, flat.h,
          (double) (flat.w * flat.h) / IMG_SZ, mos_overlap_frac (&m) * 100.0,
          nmin);

  free (flat.data); free (big.data);
  return nmin;
}

int
main (int argc, char **argv)
{
  const char *dir = argc > 1 ? argv[1] : "frames";
  char paths[MAXF][512];
  if (argc > 2) ENLARGE = atoi (argv[2]);
  static unsigned char all[MAXF][IMG_SZ];
  int n = 0, nf = 0;
  DIR *d = opendir (dir);
  struct dirent *e;

  if (!d) { fprintf (stderr, "nao abri %s\n", dir); return 1; }
  while ((e = readdir (d)) && n < MAXF)
    if (strstr (e->d_name, ".pgm"))
      snprintf (paths[n++], 512, "%s/%s", dir, e->d_name);
  closedir (d);
  for (int i = 0; i < n; i++)
    for (int j = i + 1; j < n; j++)
      if (strcmp (paths[i], paths[j]) > 0)
        { char t[512]; strcpy (t, paths[i]); strcpy (paths[i], paths[j]); strcpy (paths[j], t); }
  for (int i = 0; i < n; i++)
    if (load_pgm (paths[i], all[nf]) == 0) nf++;

  if (nf < 4) { fprintf (stderr, "preciso de ao menos 4 frames (achei %d)\n", nf); return 1; }
  printf ("frames: %d de %dx%d em %s\n\n", nf, IMG_W, IMG_H, dir);

  /* Referencia: quanto um frame solto rende, e quanto casa com outro frame. */
  puts ("== baseline: frames isolados ==");
  {
    struct xyt_struct xs[MAXF];
    int have[MAXF], tot = 0, cnt = 0;
    for (int i = 0; i < nf; i++)
      {
        Img one = { .data = all[i], .w = IMG_W, .h = IMG_H };
        Img big = resize (&one, ENLARGE);
        int k = extract (&big, &xs[i]);
        free (big.data);
        have[i] = k > 0;
        if (k > 0) { tot += k; cnt++; }
      }
    printf ("  minucias por frame: media %.1f (em %d/%d frames com >0)\n",
            cnt ? (double) tot / cnt : 0.0, cnt, nf);
    long sum = 0; int c = 0, best = 0;
    for (int i = 0; i < nf; i++)
      for (int j = i + 1; j < nf; j++)
        {
          int s;
          if (!have[i] || !have[j]) continue;
          s = match (&xs[i], &xs[j]);
          sum += s; c++; if (s > best) best = s;
        }
    printf ("  match frame-vs-frame: media %.1f  max %d  (em %d pares)\n\n",
            c ? (double) sum / c : 0.0, best, c);
  }

  /* Mosaico completo: teto do que da' para extrair com esses dados. */
  puts ("== mosaico completo (teto) ==");
  {
    int idx[MAXF];
    struct xyt_struct xa;
    for (int i = 0; i < nf; i++) idx[i] = i;
    eval_subset (all, idx, nf, "todos", &xa, 1);
    putchar ('\n');
  }

  /* O teste que importa: dois mosaicos independentes do mesmo dedo.
   * Tres particoes diferentes, porque com poucos frames a escolha do
   * subconjunto muda muito o resultado. */
  puts ("== mosaicos independentes: A vs B ==");
  {
    const char *pnames[] = { "par/impar", "1a/2a metade", "blocos alternados" };
    for (int p = 0; p < 3; p++)
      {
        int ia[MAXF], ib[MAXF], na = 0, nb = 0;
        struct xyt_struct xa, xb;
        int ma, mb;

        for (int i = 0; i < nf; i++)
          {
            int toA;
            if (p == 0) toA = (i % 2) == 0;
            else if (p == 1) toA = i < nf / 2;
            else toA = ((i / 2) % 2) == 0;
            if (toA) ia[na++] = i; else ib[nb++] = i;
          }
        if (na < 2 || nb < 2) continue;

        printf ("\n  particao '%s'  (A=%d frames, B=%d frames)\n", pnames[p], na, nb);
        ma = eval_subset (all, ia, na, "A", &xa, 0);
        mb = eval_subset (all, ib, nb, "B", &xb, 0);
        if (ma > 0 && mb > 0)
          {
            int ab = match (&xa, &xb), ba = match (&xb, &xa);
            printf ("  -> score A->B = %d   B->A = %d   (limiar libfprint = 40)\n", ab, ba);
            printf ("     teto teorico do score ~= min(minucias) = %d\n", ma < mb ? ma : mb);
          }
      }
  }
  putchar ('\n');
  return 0;
}
