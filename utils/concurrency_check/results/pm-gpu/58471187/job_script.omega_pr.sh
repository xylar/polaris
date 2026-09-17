#!/bin/bash
#SBATCH --job-name=polaris_omega_pr
#SBATCH --account=e3sm
#SBATCH --nodes=6
#SBATCH --output=polaris_omega_pr.o%j
#SBATCH --exclusive
#SBATCH --time=01:30:00
#SBATCH --qos=regular
#SBATCH --constraint=gpu
#SBATCH --gpus-per-node=4

cd $SLURM_SUBMIT_DIR
source load_polaris_env.sh
polaris serial omega_pr