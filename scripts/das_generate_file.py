import argparse
import os
import logging

from cache import CachedCouchbaseClient, CouchbaseClient, DocumentNotFoundException

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.management.collections import CollectionSpec
from couchbase import exceptions as cb_exceptions

from pymongo.collection import Collection
from pymongo.mongo_client import MongoClient

from util import Clock, Statistics, AccumulatorClock

logger = logging.getLogger("das")
logger.setLevel(logging.INFO)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)

formatter = logging.Formatter("[%(asctime)s %(levelname)s]: %(message)s")
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

INCOMING_COLL_NAME = 'IncomingSet'
OUTGOING_COLL_NAME = 'OutgoingSet'


clock = Clock()
incoming_clock = Clock()
outgoing_clock = Clock()
incoming_time_statistics = Statistics()
outgoing_time_statistics = Statistics()
incoming_size_statistics = Statistics()
outgoing_size_statistics = Statistics()
get_time_statistics = Statistics()
upsert_time_statistics = Statistics()
batch_clock = Clock()

acc_clock_block1 = AccumulatorClock()
acc_clock_block2 = AccumulatorClock()
acc_clock_block3 = AccumulatorClock()
acc_clock_block4 = AccumulatorClock()
acc_clock_full = AccumulatorClock()

def append(couchbase_client: CachedCouchbaseClient, key: str, new_value):
    value = []
    try:
        clock.reset()
        value = couchbase_client.get(key)
        # logger.info(result.content)
        # value = result.content
    except DocumentNotFoundException:
      pass
    finally:
      get_time_statistics.add(clock.elapsed_time_ms())

    value.extend(new_value)
    clock.reset()
    v = list(set(value))
    # print('#', key, len(value))
    couchbase_client.add(key=key, value=v, size=len(v))
    incoming_size_statistics.add(len(v))
    upsert_time_statistics.add(clock.elapsed_time_ms())


def populate_sets(fh, collection: Collection, bucket):
    outgoing_set = bucket.collection(OUTGOING_COLL_NAME)

    total = collection.count_documents({})
    cursor = collection.find({}, no_cursor_timeout=True).batch_size(100)
    count = 0
    clock.reset()
    batch_clock.reset()
    for doc in cursor:
        acc_clock_full.start()
        acc_clock_block1.start()
        _id = doc['_id']
        if 'keys' in doc:
            keys = doc['keys']
        else:
            keys = {v for k, v in doc.items() if k.startswith('key')}
        acc_clock_block1.pause()

        # print(keys)

        acc_clock_block2.start()
        outgoing_clock.reset()
        outgoing_list = list(set(keys))
        outgoing_set.upsert(_id, outgoing_list)
        outgoing_time_statistics.add(outgoing_clock.elapsed_time_ms())
        outgoing_size_statistics.add(len(outgoing_list))
        acc_clock_block2.pause()

        acc_clock_block3.start()
        incoming_dict = {}
        for key in keys:
            if key in incoming_dict:
                incoming_dict[key].append(_id)
            else:
                incoming_dict[key] = [_id]
        acc_clock_block3.pause()

        acc_clock_block4.start()
        incoming_clock.reset()
        for key, values in incoming_dict.items():
            # append(incoming_cached, key=key, new_value=values)
            for v in values:
              fh.write('{},{}\n'.format(key, v))
        incoming_time_statistics.add(incoming_clock.elapsed_time_ms())
        acc_clock_block4.pause()

        count += 1
        if count % 10000 == 0:
            logger.info('\n')
            logger.info('Documents processed: [{}/{}]'.format(count, total))
            logger.info('Batch time (sec):         {}'.format(batch_clock.elapsed_time_seconds()))
            logger.info('Block full (sec):         {}'.format(acc_clock_full.acc_seconds()))
            logger.info('Block1 (sec):             {}'.format(acc_clock_block1.acc_seconds()))
            logger.info('Block2 (sec):             {}'.format(acc_clock_block2.acc_seconds()))
            logger.info('Block3 (sec):             {}'.format(acc_clock_block3.acc_seconds()))
            logger.info('Block4 (sec):             {}'.format(acc_clock_block4.acc_seconds()))

            logger.info('Time incoming (ms):       {}'.format(incoming_time_statistics.pretty_print()))
            logger.info("Time outgoing (ms):       {}".format(outgoing_time_statistics.pretty_print()))

            logger.info('Couch incoming get (ms):  {}'.format(get_time_statistics.pretty_print()))
            logger.info('Incoming upsert (ms):     {}'.format(upsert_time_statistics.pretty_print()))

            logger.info('Size incoming:            {}'.format(incoming_size_statistics.pretty_print()))
            logger.info('Size outgoing:            {}'.format(outgoing_size_statistics.pretty_print()))

            incoming_time_statistics.reset()
            outgoing_time_statistics.reset()
            incoming_size_statistics.reset()
            outgoing_size_statistics.reset()
            get_time_statistics.reset()
            upsert_time_statistics.reset()
            batch_clock.reset()

            acc_clock_block1.reset()
            acc_clock_block2.reset()
            acc_clock_block3.reset()
            acc_clock_block4.reset()
            acc_clock_full.reset()

        acc_clock_full.pause()

    cursor.close()
    # incoming_cached.flush()


def create_collections(bucket, collections_names=None):
    if collections_names is None:
        collections_names = []
    # Creating Couchbase collections
    coll_manager = bucket.collections()
    for name in collections_names:
        logger.info(f'Creating Couchbase collection: "{name}"...')
        try:
            coll_manager.create_collection(CollectionSpec(name))
        except cb_exceptions.CollectionAlreadyExistsException as _:
            logger.info(f'Collection exists!')
            pass
        except Exception as e:
            logger.error(f'[create_collections] Failed: {e}')


def get_mongodb(mongo_hostname, mongo_port, mongo_username, mongo_password, mongo_database):
    client = MongoClient(f"mongodb://{mongo_username}:{mongo_password}@{mongo_hostname}:{mongo_port}")
    return client[mongo_database]


def main(mongo_hostname, mongo_port, mongo_username, mongo_password, mongo_database):
    cluster = Cluster(
        'couchbase://localhost',
        authenticator = PasswordAuthenticator(mongo_username, mongo_password))
    bucket = cluster.bucket('das')

    create_collections(
        bucket=bucket,
        collections_names=[INCOMING_COLL_NAME, OUTGOING_COLL_NAME])

    db = get_mongodb(mongo_hostname, mongo_port, mongo_username, mongo_password, mongo_database)

    with open('./all_pairs.txt', 'w') as fh:
      logger.info("Indexing links_1")
      populate_sets(fh, db['links_1'], bucket)
      logger.info("Indexing links_2")
      populate_sets(fh, db['links_2'], bucket)
      logger.info("Indexing links_3")
      populate_sets(fh, db['links_3'], bucket)
      logger.info("Indexing links")
      populate_sets(fh, db['links'], bucket)


def run():
    parser = argparse.ArgumentParser(
        "Indexes DAS to Couchbase", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--mongo-hostname",
        type=str,
        default=os.environ.get("DAS_MONGO_HOSTNAME", "localhost"),
        metavar="HOSTNAME",
        dest="mongo_hostname",
        help="mongo hostname to connect to",
    )
    parser.add_argument(
        "--mongo-port",
        type=int,
        default=os.environ.get("DAS_MONGO_PORT", "27017"),
        metavar="PORT",
        dest="mongo_port",
        help="mongo port to connect to",
    )
    parser.add_argument(
        "--mongo-database",
        type=str,
        default="das",
        metavar="NAME",
        dest="mongo_database",
        help="mongo database name to connect to",
    )
    parser.add_argument(
        "--mongo-username",
        type=str,
        default=os.environ.get("DAS_MONGO_USERNAME", "mongoadmin"),
        metavar="USERNAME",
        dest="mongo_username",
        help="mongo username",
    )
    parser.add_argument(
        "--mongo-password",
        type=str,
        default=os.environ.get("DAS_MONGO_PASSWORD", "das#secret"),
        metavar="PASSWORD",
        dest="mongo_password",
        help="mongo password",
    )
    args = parser.parse_args()
    main(**vars(args))


if __name__ == '__main__':
    run()
