/* Minimal MPI payload for the launcher spike.
 *
 * Same reporting as payload.sh, but goes through MPI_Init so that PMI
 * bootstrap is exercised when several launches run at once.
 */
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/time.h>

static double now(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + 1.0e-6 * tv.tv_usec;
}

static void read_affinity(char *buf, size_t n)
{
    FILE *f = fopen("/proc/self/status", "r");
    char line[512];
    snprintf(buf, n, "unknown");
    if (!f) {
        return;
    }
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "Cpus_allowed_list:", 18) == 0) {
            char *p = line + 18;
            while (*p == ' ' || *p == '\t') {
                p++;
            }
            p[strcspn(p, "\n")] = '\0';
            snprintf(buf, n, "%s", p);
            break;
        }
    }
    fclose(f);
}

int main(int argc, char **argv)
{
    int rank = 0;
    int size = 1;
    double start, end;
    char host[256];
    char cpus[256];
    char path[1024];
    const char *test = getenv("SPIKE_TEST");
    const char *slot = getenv("SPIKE_SLOT");
    const char *outdir = getenv("SPIKE_OUTDIR");
    const char *sleep_s = getenv("SPIKE_SLEEP");
    const char *gpu = getenv("ZE_AFFINITY_MASK");
    FILE *out;

    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);

    if (!test || !slot || !outdir) {
        if (rank == 0) {
            fprintf(stderr, "SPIKE_TEST/SPIKE_SLOT/SPIKE_OUTDIR must be set\n");
        }
        MPI_Finalize();
        return 1;
    }
    if (!gpu) {
        /* Aurora uses ZE_AFFINITY_MASK, Perlmutter CUDA_VISIBLE_DEVICES and
         * Frontier ROCR_VISIBLE_DEVICES, so check each in turn. */
        static const char *gpu_vars[] = {
            "CUDA_VISIBLE_DEVICES",
            "ROCR_VISIBLE_DEVICES",
            "HIP_VISIBLE_DEVICES",
            NULL,
        };
        int i;
        for (i = 0; gpu_vars[i] != NULL; i++) {
            gpu = getenv(gpu_vars[i]);
            if (gpu != NULL && gpu[0] != '\0') {
                break;
            }
        }
    }
    if (!gpu) {
        gpu = "";
    }

    gethostname(host, sizeof(host));
    read_affinity(cpus, sizeof(cpus));

    /* A barrier before the timed region so t_start reflects the point at
     * which the whole launch is actually running, not rank 0's head start. */
    MPI_Barrier(MPI_COMM_WORLD);
    start = now();
    sleep(sleep_s ? atoi(sleep_s) : 15);
    end = now();

    snprintf(path, sizeof(path), "%s/%s/slot%s_rank%d.kv", outdir, test,
             slot, rank);
    out = fopen(path, "w");
    if (!out) {
        fprintf(stderr, "could not write %s\n", path);
        MPI_Finalize();
        return 1;
    }
    fprintf(out, "test=%s\nslot=%s\nrank=%d\nsize=%d\nhost=%s\n", test, slot,
            rank, size, host);
    fprintf(out, "cpus_allowed=%s\ngpu_env=%s\n", cpus, gpu);
    fprintf(out, "t_start=%.6f\nt_end=%.6f\n", start, end);
    fclose(out);

    MPI_Finalize();
    return 0;
}
