#!/bin/bash
set -e
npm ci
npm run push --workspace=@workspace/db
