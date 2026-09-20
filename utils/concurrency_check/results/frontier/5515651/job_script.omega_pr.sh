#!/bin/bash
#SBATCH --job-name=polaris_omega_pr
#SBATCH --account=cli115
#SBATCH --nodes=4
#SBATCH --output=polaris_omega_pr.o%j
#SBATCH --exclusive
#SBATCH --time=01:00:00
#SBATCH --partition=batch
#SBATCH --qos=normal
#SBATCH --gpus-per-node=8

cd $SLURM_SUBMIT_DIR
source load_polaris_env.sh
polaris serial omega_pr