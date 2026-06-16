# Build a minimal Apptainer/Singularity image for the pilot project.
# UL HPC does not run the Docker daemon; build locally and convert to .sif.
#
#   docker build -t pilot-hpc .
#   singularity build pilot-hpc.sif docker-daemon://pilot-hpc:latest
#
# Then upload pilot-hpc.sif to the HPC remote directory and run:
#
#   ulhpc-submit --container pilot-hpc.sif python main.py

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py data.txt ./

ENTRYPOINT ["python", "main.py"]
