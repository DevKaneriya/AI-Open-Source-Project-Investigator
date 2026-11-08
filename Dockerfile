# Hugging Face Spaces (Docker SDK) image: FastAPI backend + Streamlit UI in
# one container. Only Streamlit's port is public; the API stays internal.
# See docs/DEPLOYMENT.md.
FROM python:3.12-slim

ARG TORCH_VERSION=2.15.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Spaces run containers as uid 1000.
RUN useradd --create-home --uid 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VECTOR_BACKEND=pgvector \
    DEMO_MODE=true \
    API_BASE_URL=http://127.0.0.1:8000

WORKDIR $HOME/app

# Dependencies first so code changes don't reinstall them. CPU-only torch
# keeps the image small; the constraints pin everything else to tested versions.
COPY --chown=user requirements.txt deploy/constraints.txt ./deploy-reqs/
COPY --chown=user frontend/requirements.txt ./deploy-reqs/frontend-requirements.txt
RUN pip install --user "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --user \
        -r deploy-reqs/requirements.txt \
        -r deploy-reqs/frontend-requirements.txt \
        -c deploy-reqs/constraints.txt

# Bake the embedding model into the image so a cold start doesn't download it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY --chown=user . .
# Guard against Windows line endings breaking the start script.
RUN sed -i 's/\r$//' deploy/start.sh

EXPOSE 7860
CMD ["bash", "deploy/start.sh"]
