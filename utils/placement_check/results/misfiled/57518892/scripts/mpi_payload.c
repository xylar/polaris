/* MPI payload for the placement check.
 *
 * Reports the same things payload.sh does, but goes through MPI_Init so
 * that PMI bootstrap is exercised when several launches run at once.  A
 * shell payload would downgrade the concurrent tests from "does an MPI job
 * survive concurrency" to "does step creation survive concurrency", which
 * is a much weaker claim.
 */
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/time.h>

/* Vendor variables that say which GPUs a launch may use, in the order they
 * are looked for.  Each is recorded even when set to the empty string,
 * since an explicit "no GPUs" may be rendered exactly that way. */
static const char *GPU_VARS[] = {
    "CUDA_VISIBLE_DEVICES",
    "ROCR_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "ZE_AFFINITY_MASK",
    NULL,
};

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

static void read_gpu_env(char *buf, size_t n)
{
    size_t used = 0;
    int i;
    buf[0] = '\0';
    for (i = 0; GPU_VARS[i] != NULL; i++) {
        const char *value = getenv(GPU_VARS[i]);
        if (value == NULL) {
            continue;
        }
        used += snprintf(buf + used, used < n ? n - used : 0, "%s=%s;",
                         GPU_VARS[i], value);
        if (used >= n) {
            return;
        }
    }
}

int main(int argc, char **argv)
{
    int rank = 0;
    int size = 1;
    double start, end;
    char host[256];
    char cpus[256];
    char gpu_env[1024];
    char path[1024];
    const char *test = getenv("PLACE_TEST");
    const char *slot = getenv("PLACE_SLOT");
    const char *outdir = getenv("PLACE_OUTDIR");
    const char *sleep_s = getenv("PLACE_SLEEP");
    const char *step_gpus;
    const char *job_gpus;
    FILE *out;

    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);

    if (!test || !slot || !outdir) {
        if (rank == 0) {
            fprintf(stderr,
                    "PLACE_TEST/PLACE_SLOT/PLACE_OUTDIR must be set\n");
        }
        MPI_Finalize();
        return 1;
    }

    gethostname(host, sizeof(host));
    read_affinity(cpus, sizeof(cpus));
    read_gpu_env(gpu_env, sizeof(gpu_env));

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
    step_gpus = getenv("SLURM_STEP_GPUS");
    job_gpus = getenv("SLURM_JOB_GPUS");
    fprintf(out, "test=%s\nslot=%s\nrank=%d\nsize=%d\nhost=%s\n", test, slot,
            rank, size, host);
    fprintf(out, "payload=mpi\ncpus_allowed=%s\ngpu_env=%s\n", cpus, gpu_env);
    fprintf(out, "step_gpus=%s\n", step_gpus ? step_gpus : "");
    fprintf(out, "job_gpus=%s\n", job_gpus ? job_gpus : "");
    fprintf(out, "t_start=%.6f\nt_end=%.6f\n", start, end);
    fclose(out);

    MPI_Finalize();
    return 0;
}
