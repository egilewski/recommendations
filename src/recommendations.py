import sys
import traceback
import json
import pprint
import functools
import time
import falcon
# import requests
# import logging
# import os
import arango


def log_and_supress_exception(f):
    @functools.wraps(f)
    def wrapper(self, req, resp, **kwargs):
        try:
            return f(self, req, resp, **kwargs)
        except:
            pprint.pprint("".join(traceback.format_exception(*sys.exc_info())))
            raise falcon.HTTPError(falcon.HTTP_500)
    return wrapper


def notify_user_on_missing_parameters(f):
    @functools.wraps(f)
    def wrapper(self, req, resp, **kwargs):
        try:
            return f(self, req, resp, **kwargs)
        except MissingParametersError as e:
            resp.status = falcon.HTTP_400
            resp.body = "Missing required parameters: {}".format(
                ", ".join(e.missing_parameters))
    return wrapper


class MissingParametersError(BaseException):
    def __init__(self, missing_parameters, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing_parameters = missing_parameters


class BaseHandler(object):
    DATABASE_NAME = 'customers_products_interactions'
    FROM_COLLECTION = 'customers'
    TO_COLLECTION = 'products'
    COLLECTION_NAMES2IS_EDGES = {
        FROM_COLLECTION: False,
        TO_COLLECTION: False,
        'viewings': True,
        'commentings': True,
        'buyings': True,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Lazy. An eager one would have caused an exception, as ArangoDB
        # is starting at the same time, and much more slower than
        # gunicorn+falcon.
        self.client = arango.ArangoClient(
            protocol='http',
            host='arangodb',
            port=8529,
            username='root',
            password='4450c00e19eaa8428464ef3c36cfae5adc3d301e7333d'
            '254220eb615cdcb3d7e',
            enable_logging=True)
        self.db = None
        self.collections = {}

    def initialize_db_connection_and_populate_attributes(self):
        try:
            self.db = self.client.create_database(self.DATABASE_NAME)
        except arango.exceptions.DatabaseCreateError:
            self.db = self.client.database(self.DATABASE_NAME)
        for name, is_edge in self.COLLECTION_NAMES2IS_EDGES.items():
            try:
                self.collections[name] = self.db.create_collection(
                    name, edge=is_edge)
            except arango.exceptions.CollectionCreateError:
                self.collections[name] = self.db.collection(name)


class InsertRecordHandler(BaseHandler):
    @log_and_supress_exception
    @notify_user_on_missing_parameters
    def on_post(self, req, resp, collection_name):
        # TODO Move it somewhere DRY.
        self.initialize_db_connection_and_populate_attributes()
        if collection_name not in self.collections:
            resp.status = falcon.HTTP_400
            resp.body = "Bad collection name: {}".format(collection_name)
            return

        missing_parameters = []
        req_body = json.loads(req.bounded_stream.read().decode('utf-8'))
        new_document = {}
        if self.COLLECTION_NAMES2IS_EDGES[collection_name]:
            missing_parameters.extend(
                k for k in ('from', 'to') if not req_body.get(k))
            new_document['_from'] = '{}/{}'.format(
                self.FROM_COLLECTION, req_body.get('from'))
            new_document['_to'] = '{}/{}'.format(
                self.TO_COLLECTION, req_body.get('to'))
        else:
            if not req_body.get('key'):
                missing_parameters.append('key')
            new_document['_key'] = req_body.get('key')
        new_document['created_at'] = time.time()

        if missing_parameters:
            raise MissingParametersError(missing_parameters)
        # TODO Show meaningful error in `_key` already exists, probably
        # status 409 (at least ArangoDB uses it).
        self.collections[collection_name].insert(new_document)
        resp.status = falcon.HTTP_201


class GetRecommendationsHandler(BaseHandler):
    SUPPORTED_RECOMMENDATION_STRATEGIES = []

    @log_and_supress_exception
    def on_get(self, req, resp, customer_id, recommendations_strategy):
        # TODO Remove "populate attributes" from here.
        self.initialize_db_connection_and_populate_attributes()
        try:
            strategy_method = getattr(
                self,
                'get_{}_recommendations'.format(
                    recommendations_strategy))
        except AttributeError:
            resp.status = falcon.HTTP_400
            resp.body = 'Recommendations strategy "{}" not implemented'.format(
                recommendations_strategy)
        else:
            products = strategy_method(customer_id)
            resp.body = json.dumps(products)

    def get_collaborative_filtering_recommendations(self, customer_id):
        """Return recommendations based on collaborative filtering.

        :raises OSError: Unable to open file with the required AQL
            request.
        """
        with open('collaborative.aql') as f:
            query = "LET requested_user = 'customers/{}'\n{}".format(
                customer_id, f.read())
        cursor = self.db.aql.execute(query)
        return [product['key'] for product in cursor]


# TODO Request rate limiter like
# http://www.giantflyingsaucer.com/blog/?p=5910


# TODO Authentication.
api = falcon.API()
# TODO Maybe use PUT and receive parameters in URL, at least for
# vertices.
api.add_route('/{collection_name}', InsertRecordHandler())
api.add_route(
    '/customers/{customer_id}/recommendations/{recommendations_strategy}',
    GetRecommendationsHandler())
