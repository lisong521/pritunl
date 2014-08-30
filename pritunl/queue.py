from constants import *
from exceptions import *
from descriptors import *
from messenger import Messenger
from mongo_object import MongoObject
import mongo
import pymongo
import random
import bson
import datetime
import logging

queue_types = {}
logger = logging.getLogger(APP_NAME)

class Queue(MongoObject):
    fields = {
        'state',
        'priority',
        'attemps',
        'queue_type',
        'ttl',
        'ttl_timestamp',
    }
    fields_default = {
        'state': PENDING,
        'priority': NORMAL,
        'attemps': 0,
        'ttl': MONGO_QUEUE_TTL,
    }

    def __init__(self, **kwargs):
        MongoObject.__init__(self, **kwargs)
        self.runner_id = bson.ObjectId()

    @static_property
    def collection(cls):
        return mongo.get_collection('queue')

    def start(self):
        self.ttl_timestamp = datetime.datetime.utcnow() + \
                datetime.timedelta(seconds=self.ttl)
        self.commit()
        Messenger('queue').publish('queue_update')

    def claim(self):
        response = self.collection.update({
            '_id': bson.ObjectId(self.id),
            '$or': [
                {'runner_id': self.runner_id},
                {'runner_id': {'$exists': False}},
            ],
        }, {'$set': {
            'runner_id': self.runner_id,
            'ttl_timestamp': datetime.datetime.utcnow() + \
                datetime.timedelta(seconds=self.ttl),
        }})
        return response['updatedExisting']

    def run(self):
        if not self.claim():
            return
        try:
            if self.state == PENDING:
                self.attemps += 1
                if self.attemps > MONGO_QUEUE_MAX_ATTEMPTS:
                    self.state = ROLLBACK
                    self.commit('state')
                else:
                    self.commit('attemps')

                    self.task()

                    self.state = COMMITTED
                    self.commit('state')

            if not self.claim():
                return

            if self.state == COMMITTED:
                self.post_task()
            elif self.state == ROLLBACK:
                self.rollback_task()
            self.complete()
        except:
            logger.exception('Error running task in queue. %r' % {
                'queue_id': self.id,
                'queue_type': self.queue_type,
            })

    def complete(self):
        self.remove()

    def task(self):
        pass

    def post_task(self):
        pass

    def rollback_task(self):
        pass

    @classmethod
    def iter_queues(cls, spec={}):
        for doc in cls.collection.find(spec).sort('priority'):
            yield queue_types[doc['queue_type']](doc=doc)