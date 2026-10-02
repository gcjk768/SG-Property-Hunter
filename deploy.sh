#!/bin/sh
# Copy the committed code to the NAS and rebuild the container.
# data/ (database, heartbeat, claude HOME) and .env are never overwritten.
set -e
NAS="${NAS:-James Koh@192.168.1.27}"
DIR=/volume1/docker/sg-property-hunter
# Made here as James (uid 1000): if Docker created the bind mount folder it would be owned by root.
VAULT="/volume1/James/Obsidian/SG Property Hunter"
git -c core.autocrlf=false archive HEAD | ssh "$NAS" "mkdir -p '$VAULT' $DIR/data/home && cd $DIR && tar xf - --exclude=data && docker compose up -d --build --force-recreate"
