// Shared-prefix ("cascade") decode attention microkernels for AVX2 + AVX-VNNI CPUs.
//
// One decode step of one attention layer for B sequences that share a
// P-token prefix and each own an S-token suffix, with the OpenVINO CPU u8 KV
// layout: per token per kv head |scale f32|zero point f32|128 x u8|, 32-token
// blocks (openvino 2026.4.0 paged_attn_kernel.hpp, *_by_dim). Shapes default
// to Qwen3-4B: 32 query heads, 8 kv heads (GQA group 4), head_dim 128.
//
// Arms
//   stock         per (sequence, query head) GEMV over every block, u8 dequant
//                 fused per row: cvt + sub zp + fma per 8 MACs, 4 tokens per
//                 pass. Mirrors dot_product_block_quantized_by_dims and
//                 attn_acc_value_block_by_dim (AVX2 branch).
//   cascade       prefix: each K/V block dequantized once to an fp32 tile, then
//                 all B*G rows of the kv head run against it (fp32 FMA, 4 rows x
//                 2 tokens register block), online softmax; suffix: stock path;
//                 log-sum-exp merge.
//   vnni_gemv     ablation without cascade: stock structure, q.K on AVX-VNNI
//                 (q quantized to s8 per row, K codes u8, vpdpbusd); p.V as stock.
//   cascade_vnni  cascade, prefix q.K on AVX-VNNI for all rows of a kv head.
//
// Build: cc -O3 -mavx2 -mfma -mavxvnni -fopenmp -o cascade_kernel cascade_kernel.c -lm
// Usage: cascade_kernel check
//        cascade_kernel bench [B P S threads reps [arm]]   (defaults 8 3400 64 8 30, all arms)
// `check` runs tiny shapes against an fp64 reference and exits non-zero on a
// mismatch. `bench` prints one JSON line per arm with the median step time.

#include <immintrin.h>
#include <math.h>
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum { D = 128, BLK = 32, ROWB = 8 + D };  // row bytes: scale, zp, codes

typedef struct {
    int B, P, S, Hk, G;
    int npb;          // shared prefix blocks (P must be a multiple of BLK)
    int nsb;          // suffix blocks per sequence
    uint8_t *k, *v;   // [phys block][Hk][BLK][ROWB]
    float *q;         // [B][Hk*G][D]
    float *out;       // [B][Hk*G][D]
} Prob;

static inline uint8_t *row(uint8_t *base, const Prob *p, int blk, int hk, int t) {
    return base + (((size_t)blk * p->Hk + hk) * BLK + t) * ROWB;
}
// physical block of logical block j of sequence b
static inline int phys(const Prob *p, int b, int j) {
    return j < p->npb ? j : p->npb + b * p->nsb + (j - p->npb);
}
static inline float hsum8(__m256 v) {
    __m128 s = _mm_add_ps(_mm256_castps256_ps128(v), _mm256_extractf128_ps(v, 1));
    s = _mm_add_ps(s, _mm_movehl_ps(s, s));
    s = _mm_add_ss(s, _mm_movehdup_ps(s));
    return _mm_cvtss_f32(s);
}
static inline int hsum8i(__m256i v) {
    __m128i s = _mm_add_epi32(_mm256_castsi256_si128(v), _mm256_extracti128_si256(v, 1));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0x4e));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0xb1));
    return _mm_cvtsi128_si32(s);
}
static inline __m256 cvt8(const uint8_t *x) {
    return _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i *)x)));
}

// ------------------------------------------------------------------ partial state
typedef struct { float m, l, o[D]; } Part;  // unnormalised o = sum e^(s-m) v

static void part_init(Part *a) { a->m = -INFINITY; a->l = 0; memset(a->o, 0, sizeof a->o); }
static void part_merge(Part *a, const Part *b) {
    if (b->l == 0) return;
    float m = fmaxf(a->m, b->m), ca = a->l ? expf(a->m - m) : 0, cb = expf(b->m - m);
    for (int d = 0; d < D; d++) a->o[d] = a->o[d] * ca + b->o[d] * cb;
    a->l = a->l * ca + b->l * cb;
    a->m = m;
}

// ------------------------------------------------------------------ stock path
// scores for `rows` query rows of one kv head over tokens [0, n) of block rows r0
static void qk_fused(const float *q, const uint8_t *r0, int n, float scale, float *s) {
    int j = 0;
    for (; j + 4 <= n; j += 4) {
        const uint8_t *b[4] = {r0 + j * ROWB, r0 + (j + 1) * ROWB, r0 + (j + 2) * ROWB, r0 + (j + 3) * ROWB};
        __m256 acc[4] = {_mm256_setzero_ps(), _mm256_setzero_ps(), _mm256_setzero_ps(), _mm256_setzero_ps()};
        __m256 zp[4];
        for (int u = 0; u < 4; u++) zp[u] = _mm256_set1_ps(((const float *)b[u])[1]);
        for (int d = 0; d < D; d += 8) {
            __m256 qa = _mm256_loadu_ps(q + d);
            for (int u = 0; u < 4; u++)
                acc[u] = _mm256_fmadd_ps(qa, _mm256_sub_ps(cvt8(b[u] + 8 + d), zp[u]), acc[u]);
        }
        for (int u = 0; u < 4; u++) s[j + u] = hsum8(acc[u]) * ((const float *)b[u])[0] * scale;
    }
    for (; j < n; j++) {
        const uint8_t *b = r0 + j * ROWB;
        __m256 acc = _mm256_setzero_ps(), zp = _mm256_set1_ps(((const float *)b)[1]);
        for (int d = 0; d < D; d += 8) acc = _mm256_fmadd_ps(_mm256_loadu_ps(q + d), _mm256_sub_ps(cvt8(b + 8 + d), zp), acc);
        s[j] = hsum8(acc) * ((const float *)b)[0] * scale;
    }
}

static void pv_fused(float *o, const float *w, const uint8_t *r0, int n) {
    for (int j = 0; j < n; j++) {
        const uint8_t *b = r0 + j * ROWB;
        __m256 ww = _mm256_set1_ps(w[j] * ((const float *)b)[0]), zp = _mm256_set1_ps(((const float *)b)[1]);
        for (int d = 0; d < D; d += 8)
            _mm256_storeu_ps(o + d, _mm256_fmadd_ps(ww, _mm256_sub_ps(cvt8(b + 8 + d), zp), _mm256_loadu_ps(o + d)));
    }
}

// q.K on AVX-VNNI: q8 = round(q / qs) in s8, K codes u8; s = ks*qs*(q8.u - zp*sum(q8))
static void quant_q(const float *q, int8_t *q8, float *qs, int *qsum) {
    float mx = 0;
    for (int d = 0; d < D; d++) mx = fmaxf(mx, fabsf(q[d]));
    *qs = mx > 0 ? mx / 127.f : 1.f;
    int sum = 0;
    for (int d = 0; d < D; d++) { q8[d] = (int8_t)lrintf(q[d] / *qs); sum += q8[d]; }
    *qsum = sum;
}
static void qk_vnni(const int8_t *q8, float qs, int qsum, const uint8_t *r0, int n, float scale, float *s) {
    __m256i qa[4];
    for (int c = 0; c < 4; c++) qa[c] = _mm256_loadu_si256((const __m256i *)(q8 + 32 * c));
    for (int j = 0; j < n; j++) {
        const uint8_t *b = r0 + j * ROWB;
        __m256i acc = _mm256_setzero_si256();
        for (int c = 0; c < 4; c++) acc = _mm256_dpbusd_avx_epi32(acc, _mm256_loadu_si256((const __m256i *)(b + 8 + 32 * c)), qa[c]);
        const float *f = (const float *)b;
        s[j] = f[0] * qs * ((float)hsum8i(acc) - f[1] * (float)qsum) * scale;
    }
}

// Full attention of G rows (one kv head of one sequence) over logical blocks
// [j0, j1) of sequence b, as one partial per row. vnni: q.K on VNNI.
static void rows_partial(const Prob *p, int b, int hk, int j0, int j1, int vnni, float *sbuf, Part *parts) {
    float scale = 1.f / sqrtf((float)D);
    int ctx = (j1 - j0) * BLK;
    for (int g = 0; g < p->G; g++) {
        const float *q = p->q + ((size_t)b * p->Hk * p->G + hk * p->G + g) * D;
        int8_t q8[D]; float qs = 1; int qsum = 0;
        if (vnni) quant_q(q, q8, &qs, &qsum);
        for (int j = j0; j < j1; j++) {
            const uint8_t *r0 = row(p->k, p, phys(p, b, j), hk, 0);
            if (vnni) qk_vnni(q8, qs, qsum, r0, BLK, scale, sbuf + (j - j0) * BLK);
            else qk_fused(q, r0, BLK, scale, sbuf + (j - j0) * BLK);
        }
        Part *a = &parts[g];
        part_init(a);
        float m = -INFINITY, l = 0;
        for (int t = 0; t < ctx; t++) m = fmaxf(m, sbuf[t]);
        for (int t = 0; t < ctx; t++) { sbuf[t] = expf(sbuf[t] - m); l += sbuf[t]; }
        for (int j = j0; j < j1; j++) pv_fused(a->o, sbuf + (j - j0) * BLK, row(p->v, p, phys(p, b, j), hk, 0), BLK);
        a->m = m; a->l = l;
    }
}

static void write_out(const Prob *p, int b, int hk, Part *parts) {
    for (int g = 0; g < p->G; g++) {
        float *o = p->out + ((size_t)b * p->Hk * p->G + hk * p->G + g) * D;
        for (int d = 0; d < D; d++) o[d] = parts[g].o[d] / parts[g].l;
    }
}

static void run_stock(const Prob *p, int vnni) {
    int nb = p->npb + p->nsb;
#pragma omp parallel
    {
        float *sbuf = malloc(sizeof(float) * nb * BLK);
        Part parts[8];
#pragma omp for collapse(2) schedule(static)
        for (int b = 0; b < p->B; b++)
            for (int hk = 0; hk < p->Hk; hk++) {
                rows_partial(p, b, hk, 0, nb, vnni, sbuf, parts);
                write_out(p, b, hk, parts);
            }
        free(sbuf);
    }
}

// ------------------------------------------------------------------ cascade path
// One kv head, prefix blocks [j0, j1): all R = B*G rows against each block once.
static void cascade_prefix(const Prob *p, int hk, int j0, int j1, int vnni, Part *parts /* [R] */) {
    const int R = p->B * p->G;
    float scale = 1.f / sqrtf((float)D);
    float kt[BLK][D] __attribute__((aligned(32))), vt[BLK][D] __attribute__((aligned(32)));
    float s[64][BLK];  // R <= 64
    int8_t q8[64][D] __attribute__((aligned(32)));
    float qs[64]; int qsum[64];
    const float *qrow[64];
    for (int r = 0; r < R; r++) {
        int b = r / p->G, g = r % p->G;
        qrow[r] = p->q + ((size_t)b * p->Hk * p->G + hk * p->G + g) * D;
        if (vnni) quant_q(qrow[r], q8[r], &qs[r], &qsum[r]);
        part_init(&parts[r]);
    }
    for (int j = j0; j < j1; j++) {
        const uint8_t *kr = row(p->k, p, j, hk, 0), *vr = row(p->v, p, j, hk, 0);
        if (vnni) {
            for (int t = 0; t < BLK; t += 2) {
                for (int r = 0; r < R; r += 4) {  // 4 rows x 2 tokens register block
                    const int nu = R - r < 4 ? R - r : 4;
                    __m256i acc[8];
                    for (int u = 0; u < 8; u++) acc[u] = _mm256_setzero_si256();
                    for (int c = 0; c < 4; c++) {
                        __m256i k0 = _mm256_loadu_si256((const __m256i *)(kr + t * ROWB + 8 + 32 * c));
                        __m256i k1 = _mm256_loadu_si256((const __m256i *)(kr + (t + 1) * ROWB + 8 + 32 * c));
                        for (int u = 0; u < nu; u++) {
                            __m256i qa = _mm256_load_si256((const __m256i *)(q8[r + u] + 32 * c));
                            acc[2 * u] = _mm256_dpbusd_avx_epi32(acc[2 * u], k0, qa);
                            acc[2 * u + 1] = _mm256_dpbusd_avx_epi32(acc[2 * u + 1], k1, qa);
                        }
                    }
                    for (int u = 0; u < nu; u++)
                        for (int w = 0; w < 2; w++) {
                            const float *f = (const float *)(kr + (t + w) * ROWB);
                            s[r + u][t + w] = f[0] * qs[r + u] * ((float)hsum8i(acc[2 * u + w]) - f[1] * (float)qsum[r + u]) * scale;
                        }
                }
            }
        } else {
            for (int t = 0; t < BLK; t++) {  // dequantize the K block once
                const uint8_t *b = kr + t * ROWB;
                __m256 sc = _mm256_set1_ps(((const float *)b)[0]), zp = _mm256_set1_ps(((const float *)b)[1]);
                for (int d = 0; d < D; d += 8) _mm256_store_ps(&kt[t][d], _mm256_mul_ps(_mm256_sub_ps(cvt8(b + 8 + d), zp), sc));
            }
            for (int r = 0; r < R; r += 4)
                for (int t = 0; t < BLK; t += 2) {
                    const int nu = R - r < 4 ? R - r : 4;
                    __m256 acc[8];
                    for (int u = 0; u < 8; u++) acc[u] = _mm256_setzero_ps();
                    for (int d = 0; d < D; d += 8) {
                        __m256 k0 = _mm256_load_ps(&kt[t][d]), k1 = _mm256_load_ps(&kt[t + 1][d]);
                        for (int u = 0; u < nu; u++) {
                            __m256 qa = _mm256_loadu_ps(qrow[r + u] + d);
                            acc[2 * u] = _mm256_fmadd_ps(qa, k0, acc[2 * u]);
                            acc[2 * u + 1] = _mm256_fmadd_ps(qa, k1, acc[2 * u + 1]);
                        }
                    }
                    for (int u = 0; u < nu; u++) {
                        s[r + u][t] = hsum8(acc[2 * u]) * scale;
                        s[r + u][t + 1] = hsum8(acc[2 * u + 1]) * scale;
                    }
                }
        }
        for (int t = 0; t < BLK; t++) {  // dequantize the V block once
            const uint8_t *b = vr + t * ROWB;
            __m256 sc = _mm256_set1_ps(((const float *)b)[0]), zp = _mm256_set1_ps(((const float *)b)[1]);
            for (int d = 0; d < D; d += 8) _mm256_store_ps(&vt[t][d], _mm256_mul_ps(_mm256_sub_ps(cvt8(b + 8 + d), zp), sc));
        }
        for (int r = 0; r < R; r++) {  // online softmax update, then o += p V
            Part *a = &parts[r];
            float m = a->m;
            for (int t = 0; t < BLK; t++) m = fmaxf(m, s[r][t]);
            float c = a->l ? expf(a->m - m) : 0, l = a->l * c;
            for (int t = 0; t < BLK; t++) { s[r][t] = expf(s[r][t] - m); l += s[r][t]; }
            a->m = m; a->l = l;
            __m256 cc = _mm256_set1_ps(c);
            for (int d = 0; d < D; d += 32) {
                __m256 o0 = _mm256_mul_ps(_mm256_loadu_ps(a->o + d), cc), o1 = _mm256_mul_ps(_mm256_loadu_ps(a->o + d + 8), cc);
                __m256 o2 = _mm256_mul_ps(_mm256_loadu_ps(a->o + d + 16), cc), o3 = _mm256_mul_ps(_mm256_loadu_ps(a->o + d + 24), cc);
                for (int t = 0; t < BLK; t++) {
                    __m256 w = _mm256_set1_ps(s[r][t]);
                    o0 = _mm256_fmadd_ps(w, _mm256_load_ps(&vt[t][d]), o0);
                    o1 = _mm256_fmadd_ps(w, _mm256_load_ps(&vt[t][d + 8]), o1);
                    o2 = _mm256_fmadd_ps(w, _mm256_load_ps(&vt[t][d + 16]), o2);
                    o3 = _mm256_fmadd_ps(w, _mm256_load_ps(&vt[t][d + 24]), o3);
                }
                _mm256_storeu_ps(a->o + d, o0); _mm256_storeu_ps(a->o + d + 8, o1);
                _mm256_storeu_ps(a->o + d + 16, o2); _mm256_storeu_ps(a->o + d + 24, o3);
            }
        }
    }
}

static void run_cascade(const Prob *p, int vnni) {
    const int R = p->B * p->G, nth = omp_get_max_threads();
    // split each kv head's prefix into chunks so Hk * chunks >= threads
    int chunks = (nth + p->Hk - 1) / p->Hk;
    if (chunks > p->npb) chunks = p->npb > 0 ? p->npb : 1;
    Part *pp = malloc(sizeof(Part) * (size_t)p->Hk * chunks * R);
#pragma omp parallel for collapse(2) schedule(static)
    for (int hk = 0; hk < p->Hk; hk++)
        for (int c = 0; c < chunks; c++) {
            int j0 = p->npb * c / chunks, j1 = p->npb * (c + 1) / chunks;
            Part *dst = pp + ((size_t)hk * chunks + c) * R;
            if (j1 > j0) cascade_prefix(p, hk, j0, j1, vnni, dst);
            else for (int r = 0; r < R; r++) part_init(&dst[r]);
        }
#pragma omp parallel
    {
        float *sbuf = malloc(sizeof(float) * (p->nsb + 1) * BLK);
        Part suf[8];
#pragma omp for collapse(2) schedule(static)
        for (int b = 0; b < p->B; b++)
            for (int hk = 0; hk < p->Hk; hk++) {
                if (p->nsb) rows_partial(p, b, hk, p->npb, p->npb + p->nsb, vnni, sbuf, suf);
                else for (int g = 0; g < p->G; g++) part_init(&suf[g]);
                for (int g = 0; g < p->G; g++) {
                    int r = b * p->G + g;
                    for (int c = 0; c < chunks; c++) part_merge(&suf[g], &pp[((size_t)hk * chunks + c) * R + r]);
                }
                write_out(p, b, hk, suf);
            }
        free(sbuf);
    }
    free(pp);
}

// ------------------------------------------------------------------ setup, reference
static uint32_t rng_s = 12345;
static float frand(void) { rng_s = rng_s * 1664525u + 1013904223u; return (float)((rng_s >> 8) & 0xffff) / 65536.f - 0.5f; }

static void fill_rows(uint8_t *base, size_t nrows) {
    for (size_t i = 0; i < nrows; i++) {
        uint8_t *b = base + i * ROWB;
        float x[D], lo = 1e9f, hi = -1e9f;
        for (int d = 0; d < D; d++) { x[d] = 2.f * frand(); lo = fminf(lo, x[d]); hi = fmaxf(hi, x[d]); }
        float sc = (hi - lo) / 255.f, zp = roundf(-lo / sc);
        ((float *)b)[0] = sc; ((float *)b)[1] = zp;
        for (int d = 0; d < D; d++) { int u = (int)lrintf(x[d] / sc + zp); b[8 + d] = (uint8_t)(u < 0 ? 0 : u > 255 ? 255 : u); }
    }
}

static Prob make(int B, int P, int S, int Hk, int G) {
    Prob p = {B, P, S, Hk, G, P / BLK, (S + BLK - 1) / BLK, 0, 0, 0, 0};
    size_t nphys = (size_t)p.npb + (size_t)B * p.nsb, rows = nphys * Hk * BLK;
    p.k = aligned_alloc(64, ((rows * ROWB + 63) / 64) * 64);
    p.v = aligned_alloc(64, ((rows * ROWB + 63) / 64) * 64);
    p.q = malloc(sizeof(float) * B * Hk * G * D);
    p.out = malloc(sizeof(float) * B * Hk * G * D);
    fill_rows(p.k, rows); fill_rows(p.v, rows);
    for (size_t i = 0; i < (size_t)B * Hk * G * D; i++) p.q[i] = 4.f * frand();
    return p;
}
static void release(Prob *p) { free(p->k); free(p->v); free(p->q); free(p->out); }

// fp64 reference over the dequantised KV. Suffix blocks are full (S rounds up to
// whole blocks) in both the kernels and the reference.
static double check_arm(const Prob *p, void (*fn)(const Prob *, int), int vnni) {
    fn(p, vnni);
    int nb = p->npb + p->nsb, ctx = nb * BLK;
    double *s = malloc(sizeof(double) * ctx), worst = 0;
    for (int b = 0; b < p->B; b++)
        for (int h = 0; h < p->Hk * p->G; h++) {
            int hk = h / p->G;
            const float *q = p->q + ((size_t)b * p->Hk * p->G + h) * D;
            double m = -1e300, l = 0, o[D] = {0};
            for (int t = 0; t < ctx; t++) {
                const uint8_t *r = row(p->k, p, phys(p, b, t / BLK), hk, t % BLK);
                const float *f = (const float *)r;
                double acc = 0;
                for (int d = 0; d < D; d++) acc += (double)q[d] * ((r[8 + d] - f[1]) * (double)f[0]);
                s[t] = acc / sqrt((double)D);
                if (s[t] > m) m = s[t];
            }
            for (int t = 0; t < ctx; t++) {
                const uint8_t *r = row(p->v, p, phys(p, b, t / BLK), hk, t % BLK);
                const float *f = (const float *)r;
                double w = exp(s[t] - m);
                l += w;
                for (int d = 0; d < D; d++) o[d] += w * ((r[8 + d] - f[1]) * (double)f[0]);
            }
            const float *got = p->out + ((size_t)b * p->Hk * p->G + h) * D;
            for (int d = 0; d < D; d++) {
                double e = fabs(got[d] - o[d] / l);
                if (e > worst) worst = e;
            }
        }
    free(s);
    return worst;
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

typedef struct { const char *name; void (*fn)(const Prob *, int); int vnni; double tol; } Arm;
static const Arm ARMS[] = {
    {"stock", run_stock, 0, 2e-4},
    {"cascade", run_cascade, 0, 2e-4},
    {"vnni_gemv", run_stock, 1, 5e-3},
    {"cascade_vnni", run_cascade, 1, 5e-3},
};

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "check")) {
        omp_set_num_threads(3);
        int shapes[][5] = {{1, 32, 32, 1, 1}, {3, 64, 40, 2, 4}, {8, 96, 64, 2, 4}, {4, 0, 32, 2, 4}, {2, 128, 0, 1, 4}};
        int bad = 0;
        for (size_t i = 0; i < sizeof shapes / sizeof shapes[0]; i++) {
            int *s = shapes[i];
            for (size_t a = 0; a < sizeof ARMS / sizeof ARMS[0]; a++) {
                Prob p = make(s[0], s[1], s[2], s[3], s[4]);
                double err = check_arm(&p, ARMS[a].fn, ARMS[a].vnni);
                int ok = err <= ARMS[a].tol;
                bad |= !ok;
                printf("{\"B\":%d,\"P\":%d,\"S\":%d,\"Hk\":%d,\"G\":%d,\"arm\":\"%s\",\"max_abs_err\":%.3g,\"ok\":%s}\n",
                       s[0], s[1], s[2], s[3], s[4], ARMS[a].name, err, ok ? "true" : "false");
                release(&p);
            }
        }
        return bad;
    }
    if (argc >= 2 && !strcmp(argv[1], "bench")) {
        int B = argc > 2 ? atoi(argv[2]) : 8, P = argc > 3 ? atoi(argv[3]) : 3400, S = argc > 4 ? atoi(argv[4]) : 64;
        int th = argc > 5 ? atoi(argv[5]) : 8, reps = argc > 6 ? atoi(argv[6]) : 30;
        const char *only = argc > 7 ? argv[7] : NULL;  // one arm, for interleaved runs
        if (reps < 1) { fprintf(stderr, "reps must be >= 1\n"); return 2; }
        P = P / BLK * BLK;  // prefix caching shares full blocks only
        if (B < 1 || B > 16) { fprintf(stderr, "B must be 1..16 (B*G <= 64 rows)\n"); return 2; }
        omp_set_num_threads(th);
        Prob p = make(B, P, S, 8, 4);
        double *t = malloc(sizeof(double) * reps);
        for (size_t a = 0; a < sizeof ARMS / sizeof ARMS[0]; a++) {
            if (only && strcmp(only, ARMS[a].name)) continue;
            for (int w = 0; w < 3; w++) ARMS[a].fn(&p, ARMS[a].vnni);
            for (int r = 0; r < reps; r++) { double t0 = now(); ARMS[a].fn(&p, ARMS[a].vnni); t[r] = now() - t0; }
            qsort(t, reps, sizeof(double), cmpd);
            double macs = 2.0 * B * (P + p.nsb * BLK) * 32 * D;  // q.K + p.V, one layer
            printf("{\"arm\":\"%s\",\"B\":%d,\"P\":%d,\"S\":%d,\"threads\":%d,\"reps\":%d,"
                   "\"layer_ms_p50\":%.4f,\"layer_ms_p10\":%.4f,\"layer_ms_p90\":%.4f,\"step_ms_36_layers\":%.2f,\"gmac_s\":%.1f}\n",
                   ARMS[a].name, B, P, S, th, reps, t[reps / 2] * 1e3, t[reps / 10] * 1e3, t[reps * 9 / 10] * 1e3,
                   t[reps / 2] * 36e3, macs / t[reps / 2] / 1e9);
        }
        free(t);
        release(&p);
        return 0;
    }
    fprintf(stderr, "usage: %s check | bench [B P S threads reps]\n", argv[0]);
    return 2;
}
