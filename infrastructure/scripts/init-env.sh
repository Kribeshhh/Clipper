#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
umask 077
set -C
{
  for key in POSTGRES_ADMIN_PASSWORD POSTGRES_PASSWORD REDIS_PASSWORD OPERATOR_KEY; do
    value=$(openssl rand -hex 32)
    printf '%s=%s\n' "$key" "$value"
  done
  key=$(openssl rand -base64 32 | tr '/+' '_-')
  printf 'OAUTH_ENCRYPTION_KEY=%s\n' "$key"
  sed '/^OAUTH_ENCRYPTION_KEY=/d; /^#/d; /_PASSWORD=/d; /^OPERATOR_KEY=/d' .env.example
} > .env
