#!/bin/sh
echo "⏳ Waiting for PostgreSQL to be ready..."
until pg_isready -h db -U genesis_user -d genesis_db > /dev/null 2>&1; do
  sleep 1
done
echo "✅ PostgreSQL is ready!"
python app.py
