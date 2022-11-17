#!/bin/bash

setup() {
  local ATTEMPTS=$1

  for attempt in $(seq 1 $ATTEMPTS); do
    echo "INFO: Waiting for Couchbase..."

    sleep 10

    docker-compose exec couchbase couchbase-cli \
      cluster-init \
      --cluster-name DAS_Cluster \
      --services "data","index","query" \
      --cluster-index-ramsize 2048 \
      --cluster-username "${DAS_DATABASE_USERNAME}" \
      --cluster-password "${DAS_DATABASE_PASSWORD}"

    if [ "$?" == 0 ]; then
      echo "SUCCESS: Couchbase is ready."
      return
    fi

    echo "INFO: Couchbase is still being set up..."

  done

  echo "ERROR: Couchbase failed to be set up."
  return 1
}

# Couchbase initial setup (attempts=5)
setup 5
docker exec das_couchbase_1 mkdir /opt/couchbase_setup/new_das
docker exec das_couchbase_1 chmod 777 /opt/couchbase_setup/new_das
docker exec das_couchbase_1 couchbase_bucket_setup.sh &
