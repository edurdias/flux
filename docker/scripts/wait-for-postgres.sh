#!/bin/bash
# Wait for PostgreSQL to be ready

set -e

host="$1"
port="$2"
user="$3"
database="$4"
shift 4
cmd="$@"

if [ -z "$host" ] || [ -z "$port" ] || [ -z "$user" ] || [ -z "$database" ]; then
    echo "Usage: $0 host port user database [command...]"
    exit 1
fi

echo "Waiting for PostgreSQL at $host:$port..."

until PGPASSWORD=$POSTGRES_PASSWORD psql -h "$host" -p "$port" -U "$user" -d "$database" -c '\q' 2>/dev/null; do
  >&2 echo "PostgreSQL is unavailable - sleeping"
  sleep 1
done

>&2 echo "PostgreSQL is up - executing command"
if [ $# -gt 0 ]; then
    exec $cmd
fi