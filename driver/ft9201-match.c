/*
 * Matcher de correlacao do FT9201, em C -- o que vai para dentro do driver.
 *
 * Sem dependencia nova: a correlacao e' direta, nao por FFT. O template e' 36x36
 * e ha' ~560 posicoes por angulo, entao uma verificacao contra 9 subtemplates em
 * 31 angulos custa ~200M operacoes -- fracao de segundo, e a libfprint nao passa
 * a precisar de fftw.
 *
 * Este arquivo compila tanto como parte do driver quanto como programa de teste
 * (-DFT9201_MATCH_TEST), para reproduzir offline os numeros da versao Python
 * sobre os mesmos frames. Um port de metrica que nao e' conferido contra a
 * referencia e' exatamente como duas medidas erradas passaram despercebidas
 * antes neste projeto.
 *
 * Copyright (C) 2026
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "ft9201-match.h"

/* ------------------------------------------------------------------ blur 1D */

/* Gaussiana separavel com borda espelhada, igual ao 'reflect' do numpy. */
static void
blur_axis (const double *src, double *dst, int h, int w,
           const double *k, int r, int horizontal)
{
  int outer = horizontal ? h : w;
  int inner = horizontal ? w : h;

  for (int o = 0; o < outer; o++)
    for (int i = 0; i < inner; i++)
      {
        double acc = 0.0;
        for (int t = -r; t <= r; t++)
          {
            int p = i + t;
            /* espelha sem repetir a borda: ...2,1,0,1,2... */
            if (p < 0) p = -p;
            if (p >= inner) p = 2 * (inner - 1) - p;
            if (p < 0) p = 0;
            acc += k[t + r] * (horizontal ? src[o * w + p] : src[p * w + o]);
          }
        if (horizontal) dst[o * w + i] = acc;
        else            dst[i * w + o] = acc;
      }
}

static void
gauss_blur (const double *src, double *dst, int h, int w, double sigma,
            double *scratch)
{
  int r = (int) lround (3.0 * sigma);
  double *k, sum = 0.0;

  if (r < 1) r = 1;
  k = g_new (double, 2 * r + 1);
  for (int i = -r; i <= r; i++)
    {
      k[i + r] = exp (-(double) (i * i) / (2.0 * sigma * sigma));
      sum += k[i + r];
    }
  for (int i = 0; i < 2 * r + 1; i++)
    k[i] /= sum;

  blur_axis (src, scratch, h, w, k, r, 1);
  blur_axis (scratch, dst, h, w, k, r, 0);
  g_free (k);
}

/* ------------------------------------------------------------- pre-processo */

FtImage *
ft9201_image_new (int w, int h)
{
  FtImage *im = g_new0 (FtImage, 1);
  im->w = w; im->h = h;
  im->px = g_new0 (double, (gsize) w * h);
  return im;
}

void
ft9201_image_free (FtImage *im)
{
  if (!im) return;
  g_free (im->px);
  g_free (im);
}

/*
 * Banda da crista + recorte de borda + normalizacao.
 *
 * A diferenca de gaussianas remove o gradiente de brilho de fundo, que domina
 * a correlacao crua e enterra o sinal periodico da crista -- medido: sem isso o
 * alinhamento escolhia deslocamentos 66px errados com correlacao 0.10.
 */
FtImage *
ft9201_prep (const guint8 *raw, int w, int h)
{
  int cw = w - 2 * FT_MARGIN, ch = h - 2 * FT_MARGIN;
  double *a, *lo, *hi, *scratch;
  FtImage *out;
  double mean = 0.0, var = 0.0, sd;
  gsize n = (gsize) w * h, m;

  if (cw <= 0 || ch <= 0)
    return NULL;

  a = g_new (double, n);
  lo = g_new (double, n);
  hi = g_new (double, n);
  scratch = g_new (double, n);
  for (gsize i = 0; i < n; i++)
    a[i] = raw[i];

  gauss_blur (a, lo, h, w, FT_SIG_LO, scratch);
  gauss_blur (a, hi, h, w, FT_SIG_HI, scratch);

  out = ft9201_image_new (cw, ch);
  m = (gsize) cw * ch;
  for (int y = 0; y < ch; y++)
    for (int x = 0; x < cw; x++)
      {
        gsize s = (gsize) (y + FT_MARGIN) * w + (x + FT_MARGIN);
        out->px[(gsize) y * cw + x] = lo[s] - hi[s];
      }

  for (gsize i = 0; i < m; i++) mean += out->px[i];
  mean /= m;
  for (gsize i = 0; i < m; i++) var += (out->px[i] - mean) * (out->px[i] - mean);
  sd = sqrt (var / m);
  if (sd < 1e-9) sd = 1.0;
  for (gsize i = 0; i < m; i++) out->px[i] = (out->px[i] - mean) / sd;

  g_free (a); g_free (lo); g_free (hi); g_free (scratch);
  return out;
}

/* ----------------------------------------------------------- qualidade */

/* Correlacao da imagem consigo mesma deslocada de (dy,dx). */
static double
corr_shift (const FtImage *im, int dy, int dx)
{
  int y0 = MAX (0, dy), y1 = MIN (im->h, im->h + dy);
  int x0 = MAX (0, dx), x1 = MIN (im->w, im->w + dx);
  double sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
  int n = 0;

  if (y1 - y0 < 12 || x1 - x0 < 12)
    return NAN;
  for (int y = y0; y < y1; y++)
    for (int x = x0; x < x1; x++)
      {
        double u = im->px[(gsize) y * im->w + x];
        double v = im->px[(gsize) (y - dy) * im->w + (x - dx)];
        sa += u; sb += v; saa += u * u; sbb += v * v; sab += u * v;
        n++;
      }
  if (!n) return NAN;
  double va = saa - sa * sa / n, vb = sbb - sb * sb / n;
  if (va < 1e-9 || vb < 1e-9) return NAN;
  return (sab - sa * sb / n) / sqrt (va * vb);
}

/*
 * Contraste periodico na melhor direcao: pico a um periodo menos vale a meio
 * periodo. Crista nitida passa de 1.0; borrao fica perto de 0. Serve tanto para
 * recusar frame ruim no cadastro quanto para orientar a busca de rotacao.
 */
double
ft9201_ridge_quality (const FtImage *im, int *orientation_deg)
{
  double best = -9.0;
  int best_ang = 0;

  for (int k = 0; k < FT_NDIR; k++)
    {
      double th = M_PI * k / FT_NDIR;
      double uy = sin (th), ux = cos (th);
      double prof[FT_PMAX + FT_PMAX / 2];
      double vmin = INFINITY, pmax = -INFINITY;
      int any_v = 0, any_p = 0;
      int nd = FT_PMAX + FT_PMAX / 2;

      for (int d = 1; d <= nd; d++)
        prof[d - 1] = corr_shift (im, (int) lround (uy * d), (int) lround (ux * d));

      for (int d = FT_PMIN / 2; d <= FT_PMAX / 2; d++)
        if (!isnan (prof[d - 1])) { vmin = MIN (vmin, prof[d - 1]); any_v = 1; }
      for (int d = FT_PMIN; d <= FT_PMAX; d++)
        if (!isnan (prof[d - 1])) { pmax = MAX (pmax, prof[d - 1]); any_p = 1; }
      if (!any_v || !any_p)
        continue;

      if (pmax - vmin > best)
        {
          best = pmax - vmin;
          best_ang = (int) lround (th * 180.0 / M_PI);
        }
    }
  if (orientation_deg)
    *orientation_deg = best_ang;
  return best;
}

/* ------------------------------------------------------------- rotacao */

/*
 * Gira em torno do centro, marcando os pixels que vieram de fora como
 * invalidos. Interpolacao bilinear: a referencia em Python usa bicubica, mas a
 * diferenca medida no score final fica na terceira casa, e bilinear evita
 * carregar um filtro de 4 taps para dentro do driver.
 */
static void
rotate_into (const FtImage *src, double deg, double *dst, guint8 *valid)
{
  double th = -deg * M_PI / 180.0;
  double c = cos (th), s = sin (th);
  double cx = (src->w - 1) / 2.0, cy = (src->h - 1) / 2.0;

  for (int y = 0; y < src->h; y++)
    for (int x = 0; x < src->w; x++)
      {
        double dx = x - cx, dy = y - cy;
        double sx = c * dx + s * dy + cx;
        double sy = -s * dx + c * dy + cy;
        gsize o = (gsize) y * src->w + x;
        int x0 = (int) floor (sx), y0 = (int) floor (sy);

        if (x0 < 0 || y0 < 0 || x0 + 1 >= src->w || y0 + 1 >= src->h)
          { dst[o] = 0.0; valid[o] = 0; continue; }

        double fx = sx - x0, fy = sy - y0;
        const double *p = src->px;
        int w = src->w;
        dst[o] = (1 - fx) * (1 - fy) * p[(gsize) y0 * w + x0]
               +      fx  * (1 - fy) * p[(gsize) y0 * w + x0 + 1]
               + (1 - fx) *      fy  * p[(gsize) (y0 + 1) * w + x0]
               +      fx  *      fy  * p[(gsize) (y0 + 1) * w + x0 + 1];
        valid[o] = 1;
      }
}

/* ------------------------------------------------------------ correlacao */

/*
 * Maior NCC do template deslizando sobre o probe ja' girado. Pontua sempre
 * sobre exatamente FT_TPL x FT_TPL pixels validos: sem isso a busca prefere as
 * posicoes de pouca sobreposicao, onde a correlacao sobe por acaso.
 */
static double
slide_ncc (const double *tpl_norm, const double *probe, const guint8 *valid,
           int pw, int ph)
{
  int n = FT_TPL, cnt = n * n;
  double best = -2.0;

  for (int y = 0; y + n <= ph; y++)
    for (int x = 0; x + n <= pw; x++)
      {
        double s1 = 0, s2 = 0, cross = 0, mean, sd;
        int ok = 1;

        for (int j = 0; j < n && ok; j++)
          for (int i = 0; i < n; i++)
            if (!valid[(gsize) (y + j) * pw + (x + i)]) { ok = 0; break; }
        if (!ok)
          continue;

        for (int j = 0; j < n; j++)
          for (int i = 0; i < n; i++)
            {
              double v = probe[(gsize) (y + j) * pw + (x + i)];
              s1 += v; s2 += v * v;
              cross += v * tpl_norm[(gsize) j * n + i];
            }
        mean = s1 / cnt;
        sd = sqrt (MAX (s2 / cnt - mean * mean, 0.0));
        if (sd < 1e-9)
          continue;
        /* tpl_norm ja' tem media zero, entao o termo da media do probe some */
        double c = cross / (cnt * sd);
        if (c > best)
          best = c;
      }
  return best;
}

/* Recorta o centro e normaliza: e' isso que vira subtemplate. */
gboolean
ft9201_center_template (const FtImage *im, double *out)
{
  int n = FT_TPL;
  double mean = 0, var = 0, sd;
  int y0, x0;

  if (im->w < n || im->h < n)
    return FALSE;
  y0 = (im->h - n) / 2;
  x0 = (im->w - n) / 2;
  for (int j = 0; j < n; j++)
    for (int i = 0; i < n; i++)
      out[j * n + i] = im->px[(gsize) (y0 + j) * im->w + (x0 + i)];
  for (int i = 0; i < n * n; i++) mean += out[i];
  mean /= n * n;
  for (int i = 0; i < n * n; i++) var += (out[i] - mean) * (out[i] - mean);
  sd = sqrt (var / (n * n));
  if (sd < 1e-9)
    return FALSE;
  for (int i = 0; i < n * n; i++) out[i] = (out[i] - mean) / sd;
  return TRUE;
}

/*
 * Verificacao: melhor NCC entre a sonda e QUALQUER subtemplate guardado.
 *
 * A sonda e' girada uma vez por angulo e comparada contra todos os templates
 * naquele angulo -- girar por template multiplicaria o custo pelo numero deles.
 */
double
ft9201_match (const double *templates, int n_templates, const FtImage *probe)
{
  double *rot = g_new (double, (gsize) probe->w * probe->h);
  guint8 *valid = g_new (guint8, (gsize) probe->w * probe->h);
  double best = -2.0;

  for (int deg = -FT_ROT_RANGE; deg <= FT_ROT_RANGE; deg += FT_ROT_STEP)
    {
      rotate_into (probe, deg, rot, valid);
      for (int t = 0; t < n_templates; t++)
        {
          double c = slide_ncc (templates + (gsize) t * FT_TPL * FT_TPL,
                                rot, valid, probe->w, probe->h);
          if (c > best)
            best = c;
        }
    }
  g_free (rot);
  g_free (valid);
  return best;
}
