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
        except MissingOrInvalidParametersError as e:
            resp.status = falcon.HTTP_400
            notifications = []
            if e.missing_parameters:
                notifications.append("Missing required parameters: {}".format(
                    ", ".join(e.missing_parameters)))
            if e.invalid_parameters:
                notifications.append(
                    "Invalid values for parameters: {}".format(
                        ", ".join(e.invalid_parameters)))
            resp.body = "\n\n".join(notifications)
    return wrapper


class MissingOrInvalidParametersError(BaseException):
    def __init__(self, missing_parameters=None, invalid_parameters=None, *args,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.missing_parameters = missing_parameters
        self.invalid_parameters = invalid_parameters


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

        is_edge = self.COLLECTION_NAMES2IS_EDGES[collection_name]
        missing_parameters = []
        req_body = json.loads(req.bounded_stream.read().decode('utf-8'))
        new_document = {}
        if is_edge:
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
            raise MissingOrInvalidParametersError(
                missing_parameters=missing_parameters)
        # TODO Show meaningful error in `_key` already exists, probably
        # status 409 (at least ArangoDB uses it).
        self.collections[collection_name].insert(new_document)
        if is_edge:
            query = '''
                FOR product in products
                    FILTER product._id == '{product_key}'
                    UPDATE product WITH {{
                        {counter_name}: product.{counter_name} + 1
                    }} IN products
                '''.format(product_key=new_document['_to'],
                           counter_name="{}_count".format(collection_name))
            self.db.aql.execute(query)
        resp.status = falcon.HTTP_201


class GetRecommendationsHandler(BaseHandler):
    SUPPORTED_RECOMMENDATION_STRATEGIES = []
    DEFAULT_MAX_COUNT = 5

    @log_and_supress_exception
    @notify_user_on_missing_parameters
    def on_get(self, req, resp, customer_id, recommendations_strategy,
               mode=None):
        # TODO Remove "populate attributes" from here.
        self.initialize_db_connection_and_populate_attributes()
        try:
            strategy_method = getattr(
                self,
                'get_{}_recommendations'.format(
                    recommendations_strategy))
        except AttributeError:
            resp.status = falcon.HTTP_400
            # TODO Output all errrors in JSON.
            resp.body = 'Recommendations strategy "{}" not implemented'.format(
                recommendations_strategy)
        else:
            products = strategy_method(customer_id, req.params)
            resp.body = json.dumps(products)

    def get_collaborative_filtering_recommendations(self, customer_id, params):
        """Return recommendations based on collaborative filtering.

        :raises OSError: Unable to open file with the required AQL
            request.
        """
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_id, params)
        with open('collaborative.aql') as f:
            query_template = f.read()
        query = query_template.format(
            requested_customer_setter="LET requested_customer = 'customers/{}'"
            .format(customer_id),
            select_products_to_exclude=select_products_to_exclude,
            filter_out_products_clause=filter_clause)
        cursor = self.db.aql.execute(query)
        return [product['key'] for product in cursor]

    def get_top_recommendations(self, customer_id, params):
        collection_name = params.get('mode')
        if not collection_name:
            raise MissingOrInvalidParametersError(missing_parameters=['mode'])
        # TODO Preferably also forbid to use collection that is filtered
        # out. Currently that will lead to empty result, which isn't a
        # bug, but is confusing.
        if collection_name not in self.collections:
            raise MissingOrInvalidParametersError(invalid_parameters=['mode'])
        # TODO Validate.
        max_count = params.get('max_count', self.DEFAULT_MAX_COUNT)
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_id, params)
        query = '''
            {select_products_to_exclude}
            FOR product IN products
                {filter}
                FILTER product.{counter_name} != 0
                FILTER product.{counter_name} != NULL
                SORT product.{counter_name}
                LIMIT {max_count}
                RETURN product._key
            '''.format(select_products_to_exclude=select_products_to_exclude,
                       counter_name="{}_count".format(params['mode']),
                       filter=filter_clause, max_count=max_count)
        print(query)
        cursor = self.db.aql.execute(query)
        return list(cursor)

    def get_random_recommendations(self, customer_id, params):
        # TODO Validate.
        max_count = params.get('max_count', self.DEFAULT_MAX_COUNT)
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_id, params)
        query = '''
            {select_products_to_exclude}
            FOR product IN products
                {filter}
                SORT RAND()
                LIMIT {max_count}
                RETURN product._key
            '''.format(
                select_products_to_exclude=select_products_to_exclude,
                filter=filter_clause, max_count=max_count)
        cursor = self.db.aql.execute(query)
        return list(cursor)

    def get_exclusion_subquery_and_filter_clause(self, customer_id, params):
        # TODO Include commented and bought unless excluded even if they
        # are also viewed (which is most likely the case).
        select_products_to_exclude = ''
        filter_clause = ''
        collections_for_exclusion_by = [
            collection_name for param_name, collection_name in (
                ('include_viewed', 'viewings'),
                ('include_commented', 'commentings'),
                ('include_bought', 'buyings'))
            if params.get(param_name, "true").lower() == "false"]
        if collections_for_exclusion_by:
            select_products_to_exclude += '''
                LET products_to_exclude = (
                    FOR product IN OUTBOUND
                    'customers/{requested_customer_id}'
                    {collections_for_exclusion_by}
                    RETURN product)
                '''.format(requested_customer_id=customer_id,
                            collections_for_exclusion_by=", ".join(
                                collections_for_exclusion_by))
            filter_clause = "FILTER product NOT IN products_to_exclude"
        return select_products_to_exclude, filter_clause


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
# TODO Maybe later, I don't want to complicate the code now.
# api.add_route(
    # '/customers/{customer_id}/recommendations/{recommendations_strategy}/'
    # '{mode}',
    # GetRecommendationsHandler())