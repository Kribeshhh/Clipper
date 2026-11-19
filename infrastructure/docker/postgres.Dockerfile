FROM postgres:17-bookworm
# Compose starts as postgres directly; the root-to-postgres helper is unused.
RUN rm /usr/local/bin/gosu
USER postgres
