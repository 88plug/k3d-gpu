/* 2-second DRAM triad probe for P0. Build: gcc -O3 -march=native -fopenmp stream_probe.c -o stream_probe
   Run: OMP_NUM_THREADS=8 taskset -c <P-core cpus> ./stream_probe ; then 24 threads on all cores.
   Arrays are 3 x 512 MiB (>> 36 MB LLC). Reports best triad GB/s over ~2 s (counts 3 streams, no write-allocate). */
#include <stdio.h>
#include <stdlib.h>
#include <omp.h>
#define N (64UL * 1024 * 1024)
int main(void) {
  double *a = malloc(N * 8), *b = malloc(N * 8), *c = malloc(N * 8), best = 0;
  #pragma omp parallel for
  for (size_t i = 0; i < N; i++) { a[i] = 1; b[i] = 2; c[i] = 0; }
  double t_end = omp_get_wtime() + 2.0;
  while (omp_get_wtime() < t_end) {
    double t0 = omp_get_wtime();
    #pragma omp parallel for
    for (size_t i = 0; i < N; i++) c[i] = a[i] + 3.0 * b[i];
    double gbs = 3.0 * N * 8 / (omp_get_wtime() - t0) / 1e9;
    if (gbs > best) best = gbs;
  }
  printf("triad_best_GBs %.1f threads %d check %.1f\n", best, omp_get_max_threads(), c[N / 2]);
  return 0;
}
