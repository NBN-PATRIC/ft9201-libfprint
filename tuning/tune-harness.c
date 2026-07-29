/*
 * Harness offline de calibração do FT9201.
 *
 * Roda o MESMO pipeline do libfprint (pixman bilinear + NBIS get_minutiae +
 * bozorth_main) sobre frames PGM já capturados, varrendo fator de ampliação e
 * pré-processamento. Mede minúcias extraídas e, o que realmente importa,
 * o score de match entre frames do mesmo dedo.
 *
 * Assim dá para escolher os parâmetros sem gastar toques no sensor.
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

#define MAX_FRAMES 32

typedef struct {
  unsigned char *data;
  int w, h;
} Img;

static int
load_pgm (const char *path, Img *out)
{
  FILE *f = fopen (path, "rb");
  char magic[3] = {0};
  int w, h, maxv;

  if (!f)
    return -1;
  if (fscanf (f, "%2s", magic) != 1 || strcmp (magic, "P5") != 0)
    { fclose (f); return -1; }
  if (fscanf (f, "%d %d %d", &w, &h, &maxv) != 3)
    { fclose (f); return -1; }
  fgetc (f);

  out->w = w; out->h = h;
  out->data = malloc ((size_t) w * h);
  if (fread (out->data, 1, (size_t) w * h, f) != (size_t) w * h)
    { fclose (f); free (out->data); return -1; }
  fclose (f);
  return 0;
}

/* Mesma escala do fpi_image_resize(): pixman, filtro bilinear. */
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

/* Equalização de histograma. */
static void
equalize (Img *im)
{
  long hist[256] = {0}, cdf = 0;
  size_t n = (size_t) im->w * im->h;
  unsigned char map[256];
  long cdfmin = 0;

  for (size_t i = 0; i < n; i++) hist[im->data[i]]++;
  for (int i = 0; i < 256; i++) if (hist[i]) { cdfmin = hist[i]; break; }
  for (int i = 0; i < 256; i++)
    {
      cdf += hist[i];
      map[i] = (unsigned char) roundf ((float) (cdf - cdfmin) / (float) (n - cdfmin) * 255.0f);
    }
  for (size_t i = 0; i < n; i++) im->data[i] = map[im->data[i]];
}

/* Autocontraste simples (estica a faixa entre os percentis 2% e 98%). */
static void
autocontrast (Img *im)
{
  long hist[256] = {0};
  size_t n = (size_t) im->w * im->h, acc = 0;
  int lo = 0, hi = 255;

  for (size_t i = 0; i < n; i++) hist[im->data[i]]++;
  for (int i = 0; i < 256; i++) { acc += hist[i]; if (acc > n * 2 / 100) { lo = i; break; } }
  acc = 0;
  for (int i = 255; i >= 0; i--) { acc += hist[i]; if (acc > n * 2 / 100) { hi = i; break; } }
  if (hi <= lo) return;
  for (size_t i = 0; i < n; i++)
    {
      int v = (im->data[i] - lo) * 255 / (hi - lo);
      im->data[i] = (unsigned char) (v < 0 ? 0 : v > 255 ? 255 : v);
    }
}

/* Inverte a polaridade (NBIS espera cristas escuras). */
static void
invert (Img *im)
{
  size_t n = (size_t) im->w * im->h;
  for (size_t i = 0; i < n; i++) im->data[i] = 255 - im->data[i];
}

/* Extrai minúcias e devolve o struct xyt para o bozorth. */
static int
extract (const Img *im, double ppmm, struct xyt_struct *xyt)
{
  MINUTIAE *minutiae = NULL;
  int *qmap = NULL, *dmap = NULL, *lcmap = NULL, *lfmap = NULL, *hcmap = NULL;
  int mw = 0, mh = 0, bw = 0, bh = 0, bd = 0;
  unsigned char *bin = NULL, *copy;
  LFSPARMS p = g_lfsparms_V2;
  int n, r;

  /* Mesma escolha do fp-image.c: sem a flag PARTIAL a libfprint desliga a
   * remocao de pontos de perimetro. Num frame de 64x80 quase toda minutia esta
   * perto da borda, entao deixar ligado apaga praticamente tudo. */
  p.remove_perimeter_pts = FALSE;

  copy = malloc ((size_t) im->w * im->h);
  memcpy (copy, im->data, (size_t) im->w * im->h);

  r = get_minutiae (&minutiae, &qmap, &dmap, &lcmap, &lfmap, &hcmap,
                    &mw, &mh, &bin, &bw, &bh, &bd,
                    copy, im->w, im->h, 8, ppmm, &p);
  free (copy);
  if (r)
    return -1;

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

int
main (int argc, char **argv)
{
  const char *dir = argc > 1 ? argv[1] : ".";
  char paths[MAX_FRAMES][512];
  Img frames[MAX_FRAMES];
  int nframes = 0;
  DIR *d = opendir (dir);
  struct dirent *e;

  if (!d) { fprintf (stderr, "nao abri %s\n", dir); return 1; }
  while ((e = readdir (d)) && nframes < MAX_FRAMES)
    {
      if (!strstr (e->d_name, ".pgm")) continue;
      snprintf (paths[nframes], sizeof paths[0], "%s/%s", dir, e->d_name);
      if (load_pgm (paths[nframes], &frames[nframes]) == 0) nframes++;
    }
  closedir (d);

  if (!nframes) { fprintf (stderr, "nenhum .pgm em %s\n", dir); return 1; }
  printf ("frames: %d  (%dx%d)\n\n", nframes, frames[0].w, frames[0].h);

  const char *prep_names[] = { "raw", "autocontrast", "equalize",
                               "invert", "invert+autoc", "invert+equal" };
  double ppmms[] = { 19.685, 12.0, 8.0 };
  const int NPREP = 6;

  printf ("%-5s %-13s %-7s %8s %9s %9s %7s\n",
          "ampl", "pre-proc", "ppmm", "minucias", "match_med", "match_max", "ok>=40");
  puts ("--------------------------------------------------------------------");

  for (int f = 1; f <= 5; f++)
    for (int pp = 0; pp < NPREP; pp++)
      for (int pi = 0; pi < 3; pi++)
        {
          struct xyt_struct xyts[MAX_FRAMES];
          int have[MAX_FRAMES], mins = 0, nmin = 0;

          for (int i = 0; i < nframes; i++)
            {
              Img w = resize (&frames[i], f);
              if (pp == 1) autocontrast (&w);
              else if (pp == 2) equalize (&w);
              else if (pp == 3) invert (&w);
              else if (pp == 4) { invert (&w); autocontrast (&w); }
              else if (pp == 5) { invert (&w); equalize (&w); }
              int n = extract (&w, ppmms[pi], &xyts[i]);
              free (w.data);
              have[i] = (n > 0);
              if (n > 0) { mins += n; nmin++; }
            }

          /* Matching todos-contra-todos usando a MESMA sequencia do
           * fpi_print_bz3_match(): probe_init + to_gallery. Como todos os
           * frames sao do mesmo dedo, scores altos sao o esperado. */
          long sum = 0; int cnt = 0, best = 0, good = 0;
          for (int i = 0; i < nframes; i++)
            {
              int plen;
              if (!have[i]) continue;
              plen = bozorth_probe_init (&xyts[i]);
              for (int j = i + 1; j < nframes; j++)
                {
                  if (!have[j]) continue;
                  int s = bozorth_to_gallery (plen, &xyts[i], &xyts[j]);
                  sum += s; cnt++;
                  if (s > best) best = s;
                  if (s >= 40) good++;      /* 40 = limiar padrao do bz3 na libfprint */
                }
            }

          printf ("%4dx %-13s %7.3f %8.1f %9.1f %9d %4d/%d\n",
                  f, prep_names[pp], ppmms[pi],
                  nmin ? (double) mins / nmin : 0.0,
                  cnt ? (double) sum / cnt : 0.0,
                  best, good, cnt);
        }

  return 0;
}
