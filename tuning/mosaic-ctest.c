/* Valida o port em C do mosaico: usa exatamente as mesmas constantes e a mesma
 * lógica do driver, sobre os PGMs já capturados, para comparar com o resultado
 * da versão Python antes de gastar toques do usuário testando no hardware. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <dirent.h>

#define IMG_W 64
#define IMG_H 80
#define IMG_SZ (IMG_W * IMG_H)

#define ALIGN_RANGE 56
#define ALIGN_COARSE 4
#define ALIGN_MIN_OV 600
#define ALIGN_MIN_CORR 0.30

#define PAD (ALIGN_RANGE + 8)
#define CW (IMG_W + 2 * PAD)
#define CH (IMG_H + 2 * PAD)

static unsigned int sum_[CW * CH];
static unsigned short hits_[CW * CH];
static unsigned int nframes_;

static void
mosaic_reset (void)
{
  memset (sum_, 0, sizeof sum_);
  memset (hits_, 0, sizeof hits_);
  nframes_ = 0;
}

static void
mosaic_paste (const unsigned char *f, int dy, int dx)
{
  for (int y = 0; y < IMG_H; y++)
    {
      int cy = y + dy;
      if (cy < 0 || cy >= CH) continue;
      for (int x = 0; x < IMG_W; x++)
        {
          int cx = x + dx;
          if (cx < 0 || cx >= CW) continue;
          sum_[cy * CW + cx] += f[y * IMG_W + x];
          hits_[cy * CW + cx]++;
        }
    }
  nframes_++;
}

static double
mosaic_corr (const unsigned char *f, int dy, int dx)
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
          if (!hits_[idx]) continue;
          double a = (double) sum_[idx] / hits_[idx];
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

static int
mosaic_merge (const unsigned char *f)
{
  double best = -2.0;
  int by = PAD, bx = PAD, found = 0;

  if (nframes_ == 0) { mosaic_paste (f, PAD, PAD); return 1; }

  for (int dy = PAD - ALIGN_RANGE; dy <= PAD + ALIGN_RANGE; dy += ALIGN_COARSE)
    for (int dx = PAD - ALIGN_RANGE; dx <= PAD + ALIGN_RANGE; dx += ALIGN_COARSE)
      { double c = mosaic_corr (f, dy, dx); if (c > best) { best = c; by = dy; bx = dx; found = 1; } }
  if (!found) return 0;
  for (int dy = by - ALIGN_COARSE; dy <= by + ALIGN_COARSE; dy++)
    for (int dx = bx - ALIGN_COARSE; dx <= bx + ALIGN_COARSE; dx++)
      { double c = mosaic_corr (f, dy, dx); if (c > best) { best = c; by = dy; bx = dx; } }

  if (best < ALIGN_MIN_CORR) { printf ("    rejeitado (corr=%.2f)\n", best); return 0; }
  printf ("    merge em (%+d,%+d) corr=%.2f\n", by - PAD, bx - PAD, best);
  mosaic_paste (f, by, bx);
  return 1;
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

int
main (int argc, char **argv)
{
  const char *dir = argc > 1 ? argv[1] : "frames";
  const char *out = argc > 2 ? argv[2] : "mosaic_c.pgm";
  char paths[64][512];
  int n = 0;
  DIR *d = opendir (dir);
  struct dirent *e;

  if (!d) { fprintf (stderr, "nao abri %s\n", dir); return 1; }
  while ((e = readdir (d)) && n < 64)
    if (strstr (e->d_name, ".pgm"))
      snprintf (paths[n++], 512, "%s/%s", dir, e->d_name);
  closedir (d);
  /* ordem estavel, igual ao sorted() do Python */
  for (int i = 0; i < n; i++)
    for (int j = i + 1; j < n; j++)
      if (strcmp (paths[i], paths[j]) > 0)
        { char t[512]; strcpy (t, paths[i]); strcpy (paths[i], paths[j]); strcpy (paths[j], t); }

  mosaic_reset ();
  unsigned char buf[IMG_SZ];
  for (int i = 0; i < n; i++)
    {
      if (load_pgm (paths[i], buf) != 0) { printf ("    (falha lendo %s)\n", paths[i]); continue; }
      printf ("  frame %d:\n", i + 1);
      mosaic_merge (buf);
    }

  int y0 = CH, y1 = -1, x0 = CW, x1 = -1;
  for (int y = 0; y < CH; y++)
    for (int x = 0; x < CW; x++)
      if (hits_[y * CW + x])
        { if (y < y0) y0 = y; if (y > y1) y1 = y; if (x < x0) x0 = x; if (x > x1) x1 = x; }

  int w = x1 - x0 + 1, h = y1 - y0 + 1;
  w -= w % 4;
  printf ("\n  composto: %dx%d de %u frames (%.2fx a area)\n",
          w, h, nframes_, (double) (w * h) / IMG_SZ);

  FILE *o = fopen (out, "wb");
  fprintf (o, "P5\n%d %d\n255\n", w, h);
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++)
      {
        unsigned idx = (y + y0) * CW + (x + x0);
        unsigned char v = hits_[idx] ? (unsigned char) (sum_[idx] / hits_[idx]) : 0;
        fputc (v, o);
      }
  fclose (o);
  printf ("  gravado em %s\n", out);
  return 0;
}
