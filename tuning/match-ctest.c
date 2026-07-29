/* Reproduz offline os numeros do subtpl.py usando o matcher em C.
 *
 * Se os dois discordarem, o port esta' errado -- e um port de metrica nao
 * conferido e' exatamente como duas medidas erradas ja' passaram despercebidas
 * neste projeto.
 *
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <glib.h>

#include "ft9201-match.h"

#define MAXF 64
#define TSZ (FT_TPL * FT_TPL)

typedef struct {
  char     name[256];
  guint8  *raw;
  int      w, h;
  FtImage *prep;
  double   q;
  int      ang;
} Frame;

static int
load_pgm (const char *p, guint8 **out, int *w, int *h)
{
  FILE *f = fopen (p, "rb");
  char m[3] = {0};
  int mx;

  if (!f) return -1;
  if (fscanf (f, "%2s %d %d %d", m, w, h, &mx) != 4 || strcmp (m, "P5"))
    { fclose (f); return -1; }
  fgetc (f);
  *out = g_malloc ((gsize) *w * *h);
  if (fread (*out, 1, (gsize) *w * *h, f) != (gsize) *w * *h)
    { fclose (f); g_free (*out); return -1; }
  fclose (f);
  return 0;
}

static int
load_dir (const char *dir, Frame *fr, int max)
{
  DIR *d = opendir (dir);
  struct dirent *e;
  char paths[MAXF][512];
  int n = 0, got = 0;

  if (!d) return 0;
  while ((e = readdir (d)) && n < max)
    if (strstr (e->d_name, ".pgm"))
      snprintf (paths[n++], sizeof paths[0], "%s/%s", dir, e->d_name);
  closedir (d);
  for (int i = 0; i < n; i++)
    for (int j = i + 1; j < n; j++)
      if (strcmp (paths[i], paths[j]) > 0)
        { char t[512]; strcpy (t, paths[i]); strcpy (paths[i], paths[j]); strcpy (paths[j], t); }

  for (int i = 0; i < n; i++)
    {
      Frame *f = &fr[got];
      if (load_pgm (paths[i], &f->raw, &f->w, &f->h) != 0)
        continue;
      f->prep = ft9201_prep (f->raw, f->w, f->h);
      if (!f->prep) { g_free (f->raw); continue; }
      f->q = ft9201_ridge_quality (f->prep, &f->ang);
      g_strlcpy (f->name, strrchr (paths[i], '/') + 1, sizeof f->name);
      got++;
    }
  return got;
}

/* Cadastro: qualidade decrescente, descartando quase-duplicatas. */
static int
enroll (Frame *fr, int n, const int *skip_idx, double *tpls, char *used, gsize used_sz)
{
  int order[MAXF], cnt = 0, kept = 0;

  for (int i = 0; i < n; i++)
    {
      if (skip_idx && *skip_idx == i) continue;
      if (fr[i].q < FT_Q_GATE) continue;
      order[cnt++] = i;
    }
  for (int i = 0; i < cnt; i++)
    for (int j = i + 1; j < cnt; j++)
      if (fr[order[j]].q > fr[order[i]].q)
        { int t = order[i]; order[i] = order[j]; order[j] = t; }

  if (used) used[0] = '\0';
  for (int k = 0; k < cnt && kept < FT_N_MAX; k++)
    {
      int i = order[k];
      double cand[TSZ];
      int dup = 0;

      if (!ft9201_center_template (fr[i].prep, cand))
        continue;
      for (int t = 0; t < kept; t++)
        if (ft9201_match (tpls + (gsize) t * TSZ, 1, fr[i].prep) >= FT_DUP_NCC)
          { dup = 1; break; }
      if (dup)
        continue;
      memcpy (tpls + (gsize) kept * TSZ, cand, sizeof cand);
      kept++;
      if (used)
        { g_strlcat (used, fr[i].name, used_sz); g_strlcat (used, " ", used_sz); }
    }
  return kept;
}

int
main (int argc, char **argv)
{
  const char *gen_dir = argc > 1 ? argv[1] : "frames";
  static Frame gen[MAXF];
  static double tpls[FT_N_MAX * TSZ];
  int n;
  double sum = 0, lo = 9, hi = -9;

  n = load_dir (gen_dir, gen, MAXF);
  if (n < 2) { fprintf (stderr, "preciso de >=2 frames em %s\n", gen_dir); return 1; }

  printf ("matcher em C -- %d frames de %s/\n", n, gen_dir);
  printf ("janela %dx%d, rotacao +-%ddeg passo %d, portao %.2f, anti-dup %.2f\n\n",
          FT_TPL, FT_TPL, FT_ROT_RANGE, FT_ROT_STEP, FT_Q_GATE, FT_DUP_NCC);

  printf ("qualidade por frame:\n");
  for (int i = 0; i < n; i++)
    printf ("  %-14s q=%5.2f  %3ddeg\n", gen[i].name, gen[i].q, gen[i].ang);

  printf ("\n== LEAVE-ONE-OUT ==\n");
  printf ("%-14s%10s%13s\n", "retido", "N subtpl", "melhor NCC");
  printf ("---------------------------------------\n");
  for (int i = 0; i < n; i++)
    {
      int k = enroll (gen, n, &i, tpls, NULL, 0);
      double s;
      if (!k) { printf ("%-14s%10d   (vazio)\n", gen[i].name, 0); continue; }
      s = ft9201_match (tpls, k, gen[i].prep);
      sum += s; lo = MIN (lo, s); hi = MAX (hi, s);
      printf ("%-14s%10d%13.3f\n", gen[i].name, k, s);
    }
  printf ("---------------------------------------\n");
  printf ("genuino: media %.3f  min %.3f  max %.3f\n", sum / n, lo, hi);

  {
    char used[1024];
    int k = enroll (gen, n, NULL, tpls, used, sizeof used);
    printf ("\ncadastro completo: %d subtemplates\n  %s\n", k, used);

    if (argc > 2)
      {
        static double imp[512];
        int ni = 0;
        DIR *d = opendir (argv[2]);
        struct dirent *e;

        /* Recorta 64x80 das imagens de controle, em grade 3x3: sem isso um
         * probe maior teria mais posicoes onde casar por acaso e inflaria o
         * score do impostor. */
        while (d && (e = readdir (d)) && ni < 500)
          {
            char path[512];
            guint8 *raw; int w, h;
            if (!strstr (e->d_name, ".pgm")) continue;
            snprintf (path, sizeof path, "%s/%s", argv[2], e->d_name);
            if (load_pgm (path, &raw, &w, &h) != 0) continue;
            if (h < 80 || w < 64) { g_free (raw); continue; }
            for (int gy = 0; gy < 3; gy++)
              for (int gx = 0; gx < 3; gx++)
                {
                  guint8 tile[64 * 80];
                  int oy = (h - 80) * gy / 2, ox = (w - 64) * gx / 2;
                  FtImage *pi;
                  for (int y = 0; y < 80; y++)
                    memcpy (tile + y * 64, raw + (gsize) (y + oy) * w + ox, 64);
                  pi = ft9201_prep (tile, 64, 80);
                  if (!pi) continue;
                  imp[ni++] = ft9201_match (tpls, k, pi);
                  ft9201_image_free (pi);
                }
            g_free (raw);
          }
        if (d) closedir (d);

        if (ni)
          {
            double isum = 0, imax = -9;
            for (int i = 0; i < ni; i++)
              { isum += imp[i]; imax = MAX (imax, imp[i]); }
            printf ("\n== IMPOSTORES: %d sondas de %s/ ==\n", ni, argv[2]);
            printf ("  media %.3f  max %.3f\n", isum / ni, imax);

            printf ("\n%8s%17s%18s\n", "limiar", "aceita genuino", "aceita impostor");
            printf ("--------------------------------------------\n");
            for (double thr = 0.30; thr < 0.75; thr += 0.05)
              {
                int tg = 0, ti = 0;
                for (int i = 0; i < n; i++)
                  {
                    int kk = enroll (gen, n, &i, tpls, NULL, 0);
                    if (kk && ft9201_match (tpls, kk, gen[i].prep) >= thr) tg++;
                  }
                enroll (gen, n, NULL, tpls, NULL, 0);
                for (int i = 0; i < ni; i++) if (imp[i] >= thr) ti++;
                printf ("%8.2f%16.0f%%%17.0f%%\n", thr,
                        100.0 * tg / n, 100.0 * ti / ni);
              }
            printf ("\nlimiar em uso no header (FT_THRESHOLD) = %.2f\n", FT_THRESHOLD);
          }
      }
  }

  for (int i = 0; i < n; i++)
    { ft9201_image_free (gen[i].prep); g_free (gen[i].raw); }
  return 0;
}
