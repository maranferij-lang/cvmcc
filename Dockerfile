# GetCVmax production image (Railway or any Docker host).
# Secrets are never baked in: see docs/deploy.md. Everything the app reads comes from
# environment variables, plus an optional /app/.streamlit/secrets.toml written at start
# from the STREAMLIT_SECRETS_TOML variable (needed for the [auth] block of st.login).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# No apt packages: every dependency in requirements.txt (Pillow, pypdfium2, pyarrow,
# pydantic-core, lxml, ...) ships a manylinux wheel for cp311, so nothing is compiled.
# Checked with: pip download --only-binary=:all: -r requirements.txt

# Runtime user: uid 10001, no login shell. Its home directory (/home/app) is the only other
# place it can write; Streamlit keeps its per-user state there (~/.streamlit).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# The repo, minus everything in .dockerignore (tests, docs, site, private CVs, secrets).
# Application code stays owned by root and read-only for the runtime user (files 644,
# directories 755), so a compromised app cannot rewrite its own code. Do not add --chown here.
COPY --chmod=u=rwX,go=rX . .

# The one place under /app the runtime user writes: the entrypoint creates secrets.toml in
# .streamlit. The directory is root:app with the sticky bit (1775): the app can create and
# replace its own secrets.toml, but cannot delete or rename the root-owned config.toml.
RUN mkdir -p /app/.streamlit \
    && chown root:app /app/.streamlit \
    && chmod 1775 /app/.streamlit

# Entrypoint: writes the secrets file from STREAMLIT_SECRETS_TOML (mode 600), never prints it,
# removes the variable from the environment, then starts Streamlit.
COPY --chmod=755 <<'EOF' /usr/local/bin/cvmax-entrypoint
#!/bin/sh
# Never add "set -x" or echo of the secret here.
set -eu

SECRETS_FILE=/app/.streamlit/secrets.toml

if [ -n "${STREAMLIT_SECRETS_TOML:-}" ]; then
    umask 077
    printf '%s\n' "$STREAMLIT_SECRETS_TOML" > "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE"
    unset STREAMLIT_SECRETS_TOML
    # Fail the deploy (so the old one keeps serving) if the TOML is broken. The parser
    # message is discarded because it could quote part of the value.
    if ! python -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$SECRETS_FILE" 2>/dev/null; then
        echo "cvmax: STREAMLIT_SECRETS_TOML is not valid TOML; fix the variable and redeploy" >&2
        exit 1
    fi
    echo "cvmax: wrote $SECRETS_FILE from STREAMLIT_SECRETS_TOML"
elif [ -f "$SECRETS_FILE" ]; then
    echo "cvmax: using the mounted $SECRETS_FILE"
else
    echo "cvmax: no secrets file; Google sign-in is off, keys come from environment variables"
fi

exec streamlit run app.py \
    --server.port "${PORT:-8501}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
EOF

USER app

# EXPOSE cannot expand $PORT; this is documentation only. The app listens on $PORT (default 8501).
EXPOSE 8501

# Railway has its own healthcheck (set the path in the service settings); this one serves plain Docker hosts.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.environ.get('PORT', '8501'), timeout=4)" || exit 1

CMD ["/usr/local/bin/cvmax-entrypoint"]
