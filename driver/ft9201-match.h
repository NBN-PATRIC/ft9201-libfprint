/*
 * Matcher de correlacao do FT9201.
 *
 * O sensor entrega 64x80 (3.2 x 4.1 mm). Nessa area o NBIS extrai no maximo 3
 * minucias e nunca casa, o que foi medido nas 90 combinacoes de parametro. Este
 * matcher segue o caminho que o proprio fabricante usa: varias vistas parciais
 * por dedo, comparadas por correlacao, em vez de minucias sobre uma imagem
 * costurada.
 *
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#ifndef FT9201_MATCH_H
#define FT9201_MATCH_H

#include <glib.h>

/* Recorte de borda antes de qualquer coisa: a moldura do sensor nao tem crista. */
#define FT_MARGIN       6

/* Diferenca de gaussianas isolando a banda da crista (periodo medido: 8-14 px). */
#define FT_SIG_LO       1.0
#define FT_SIG_HI       4.0

/* Janela de pontuacao. Fixa de proposito: pontuar sobre a sobreposicao variavel
 * faz a busca preferir as posicoes de pouca area, onde a correlacao sobe por
 * acaso -- foi o que quebrou a primeira versao. */
#define FT_TPL          36

/* O dedo gira entre toques: direcoes de crista medidas de 0 a 105 graus. */
#define FT_ROT_RANGE    45
#define FT_ROT_STEP     3

/* Faixa de periodo de crista aceita, em px (o sensor le' a ~500 dpi). */
#define FT_PMIN         6
#define FT_PMAX         14
#define FT_NDIR         12

/* Cadastro: recusa frame sem crista, e recusa vista quase igual a uma ja'
 * guardada -- senao o conjunto empilha na mesma regiao e N cresce sem cobrir
 * mais dedo. */
#define FT_Q_GATE       0.45
#define FT_DUP_NCC      0.75
#define FT_N_MAX        12

/* Limiar de aceitacao. PROVISORIO: medido contra impostores de OUTROS sensores,
 * que pontuam mais baixo que dois dedos no mesmo sensor. Nao usar em producao
 * sem uma linha de base do proprio sensor. */
#define FT_THRESHOLD    0.50

typedef struct {
  int     w, h;
  double *px;
} FtImage;

FtImage  *ft9201_image_new       (int w, int h);
void      ft9201_image_free      (FtImage *im);

/* Banda da crista + recorte + normalizacao. Devolve NULL se a imagem for pequena. */
FtImage  *ft9201_prep            (const guint8 *raw, int w, int h);

/* Contraste periodico na melhor direcao. >1.0 crista nitida, ~0 borrao. */
double    ft9201_ridge_quality   (const FtImage *im, int *orientation_deg);

/* Recorta o centro FT_TPL x FT_TPL normalizado. FALSE se nao couber ou for liso. */
gboolean  ft9201_center_template (const FtImage *im, double *out);

/* Melhor NCC entre a sonda e qualquer subtemplate, varrendo rotacao e posicao. */
double    ft9201_match           (const double *templates, int n_templates,
                                  const FtImage *probe);

#endif /* FT9201_MATCH_H */
