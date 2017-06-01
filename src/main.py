"""Main Falcon handlers of the recommendations app."""
import json
import functools
import time
import logging

import falcon
import arango


# TODO There's probably a more Falconic way to do this.
def log_and_supress_exceptions(f):
    """Decorator that logs unhandled exceptions and return HTTP 500.

    Usage of thie on HTTP handler methods prevents the web app from
    crashing.

    Has to be further from the function being wrapped than decorators
    that can handle exceptions in more meaningful way.
    """
    @functools.wraps(f)
    def wrapper(self, req, resp, **kwargs):
        try:
            return f(self, req, resp, **kwargs)
        except:
            logging.exception("Unhandled exception")
            raise falcon.HTTPError(falcon.HTTP_500)
    return wrapper


def handle_MissingOrInvalidParametersError(f):
    """Decorator that handles `MissingOrInvalidParametersError`.

    Return to user notifications that the parameters specified in the
    exception are missing and/or invalid.
    """
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

    """Exception for reporting missing and/or invalid parameters."""

    def __init__(self, missing_parameters=None, invalid_parameters=None, *args,
                 **kwargs):
        """Save passed missing and invalid parameters for later use.

        :param list(str)|None missing_parameters: Parameters that
            weren't provided by the user, or were empty.
        :param list(str)|None invalid_parameters: Parameters that were
            provided by the user, but had invalid values.

        Other positional and keyword parameters are passed to
        `super().__init__`.
        """
        super().__init__(*args, **kwargs)
        self.missing_parameters = missing_parameters
        self.invalid_parameters = invalid_parameters


class BaseResource():

    """Base class for Falcon resources.

    Provides some basic app-wide constants.

    Upon instantiation creates lazy connection to the database:
    `self.db_client`.

    Provides method for initializing the database:
    `self.initialize_db_and_set_related_attributes`.
    """

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
        """Create lazy connection to the database: `self.db_client`.

        Also initialize some attributes.

        Other positional and keyword parameters are passed to
        `super().__init__`.
        """
        super().__init__(*args, **kwargs)
        # Lazy. An eager one would have caused an exception, as ArangoDB
        # is starting at the same time, and much more slower than
        # gunicorn+falcon.
        self.db_client = arango.ArangoClient(
            protocol='http',
            host='arangodb',
            port=8529,
            username='root',
            password='4450c00e19eaa8428464ef3c36cfae5adc3d301e7333d'
            '254220eb615cdcb3d7e',
            enable_logging=True)
        self.db = None
        self.collections = {}

    def initialize_db_and_set_related_attributes(self):
        """Ensure the DB is initialized, and set DB-related attributes.

        `self.db_client` created in `self__init__` is lazy, this method
        actually initialized the connection.

        Attributes `self.db` with `arango.database.Database` object and
        `self.initialize_db_and_set_related_attributes` with list of
        `arango.collections.Collection` objects for each documents
        (vertices) and edges collections the app uses.
        """
        try:
            self.db = self.db_client.create_database(self.DATABASE_NAME)
        except arango.exceptions.DatabaseCreateError:
            self.db = self.db_client.database(self.DATABASE_NAME)
        for name, is_edge in self.COLLECTION_NAMES2IS_EDGES.items():
            try:
                self.collections[name] = self.db.create_collection(
                    name, edge=is_edge)
            except arango.exceptions.CollectionCreateError:
                self.collections[name] = self.db.collection(name)


class InsertRecordResource(BaseResource):

    """Falcon resource for inserting vertices and edges into DB."""

    @log_and_supress_exceptions
    @handle_MissingOrInvalidParametersError
    def on_post(self, req, resp, collection_name):
        """Responder for inserting a new record into a collection.

        :param falcon.Request req: Request object. In it's attribute
            `stream` (file-like object) there should be a JSON dict with
            keys "key" for a vertices collection (identified by
            `collection_name`), or "from" and "to" for edges collection.
        :param falcon.Response resp: Response object.
        :param str collection_name: Name of the collection to insert a
            new record (document) into.
        :raises MissingOrInvalidParametersError: If some parameters in
            `req.stream` were missing or invalid.
        """
        # TODO Move it somewhere DRY.
        self.initialize_db_and_set_related_attributes()
        missing_parameters = []
        if collection_name not in self.collections:
            resp.status = falcon.HTTP_400
            resp.body = "Bad collection name: {}".format(collection_name)
            return

        is_edge = self.COLLECTION_NAMES2IS_EDGES[collection_name]
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
        try:
            self.collections[collection_name].insert(new_document)
        except arango.exceptions.DocumentInsertError:
            resp.status = falcon.HTTP_409
            return
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


class ModifyRecordResource(BaseResource):

    """Falcon resource for modifying vertices and edges in DB."""

    @log_and_supress_exceptions
    @handle_MissingOrInvalidParametersError
    def on_post(self, req, resp, collection_name, key, action):
        """Responder for modifying a vertex.

        :param falcon.Request req: Request object. In it's attribute
            `stream` (file-like object) there should be a JSON dict with
            keys "key" for a vertices collection (identified by
            `collection_name`), or "from" and "to" for edges collection.
        :param falcon.Response resp: Response object.
        :param str collection_name: Name of the collection to which the
            record belongs. Currently only "products" is a valid value.
        :param str key: DB key of the record.
        :param str action: modification action to perform. Supported
            values are:
            "deactivate": Mark the record as inactive. Inactive records
            may affect results of queries to the DB, but won't be
            returned.
            "activate": Mark the record as active. Records are active by
            default.
        :raises MissingOrInvalidParametersError: If some parameters in
            `req.stream` were missing or invalid.
        """
        # TODO Move it somewhere DRY.
        self.initialize_db_and_set_related_attributes()
        if collection_name not in ("products",):
            resp.status = falcon.HTTP_400
            resp.body = "Bad collection name: {}".format(collection_name)
            return

        if action not in ('deactivate', 'activate'):
            raise MissingOrInvalidParametersError(
                invalid_parameters=['action'])

        update = {'active': "false"} if action == 'deactivate' \
            else {'active': None}
        try:
            self.collections[collection_name].update_match(
                {'_key': key}, update, keep_none=False)
        except arango.exceptions.DocumentUpdateError:
            resp.status = falcon.HTTP_404
            return
        resp.status = falcon.HTTP_204


class GetRecommendationsResource(BaseResource):

    """Falcon resource for getting recommendations."""

    SUPPORTED_RECOMMENDATION_STRATEGIES = []
    DEFAULT_MAX_COUNT = 5

    @log_and_supress_exceptions
    @handle_MissingOrInvalidParametersError
    def on_get(self, req, resp, customer_key, recommendation_strategy):
        """Responder for getting recommendations.

        :param falcon.Request req: Request object. In it's attribute
            `params` (dict) there should be keys required for the chosen
            recommendation strategy.
        :param falcon.Response resp: Response object.
        :param str customer_key: DB key of the customer for whom to
            provide recommendations.
        :param str recommendation_strategy: Name of the recommendation
            strategy to use.
        :raises MissingOrInvalidParametersError: If some parameters in
            `req.stream` were missing or invalid.
        """
        # TODO Remove "set related attributes" from here.
        self.initialize_db_and_set_related_attributes()
        try:
            strategy_method = getattr(
                self,
                'get_{}_recommendations'.format(
                    recommendation_strategy))
        except AttributeError:
            resp.status = falcon.HTTP_400
            # TODO Output all errrors in JSON.
            resp.body = 'Recommendation strategy "{}" not implemented'.format(
                recommendation_strategy)
        else:
            try:
                max_count = req.params.get('max_count', self.DEFAULT_MAX_COUNT)
            except ValueError:
                raise MissingOrInvalidParametersError(
                    invalid_parameters=['max_count'])
            products = strategy_method(customer_key, req.params, max_count)
            resp.body = json.dumps(products)

    def get_collaborative_filtering_recommendations(self, customer_key,
                                                    params, max_count):
        """Return recommendations based on collaborative filtering.

        :param str customer_key: DB key of the customer for whom to
            provide recommendations.
        :param dict params: Parameters. Used ones are:
            "max_count": Maximum number of products to return.
            For other used ones see
            `self.get_exclusion_subquery_and_filter_clause`.
        :return: Product keys.
        :rtype: list(str)
        :raises OSError: Unable to open file with the required AQL
            request.
        """
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_key, params)
        with open('collaborative.aql') as f:
            query_template = f.read()
        query = query_template.format(
            requested_customer_setter="LET requested_customer = 'customers/{}'"
            .format(customer_key),
            select_products_to_exclude=select_products_to_exclude,
            filter_out_products_clause=filter_clause,
            max_count=max_count)
        cursor = self.db.aql.execute(query)
        return [product['key'] for product in cursor]

    def get_top_recommendations(self, customer_key, params, max_count):
        """Return products with most connections of a certain type.

        :param str customer_key: DB key of the customer for whom to
            provide recommendations.
        :param dict params: Parameters. Used ones are:
            "type": a name of an edges collection to to return top for.
            "max_count": Maximum number of products to return.
            For other used ones see
            `self.get_exclusion_subquery_and_filter_clause`.
        :return: Product keys.
        :rtype: list(str)
        :raises OSError: Unable to open file with the required AQL
            request.
        """
        collection_name = params.get('type')
        if not collection_name:
            raise MissingOrInvalidParametersError(missing_parameters=['type'])
        if not self.COLLECTION_NAMES2IS_EDGES.get(collection_name, False):
            raise MissingOrInvalidParametersError(invalid_parameters=['type'])
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_key, params)
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
                       counter_name="{}_count".format(params['type']),
                       filter=filter_clause, max_count=max_count)
        cursor = self.db.aql.execute(query)
        return list(cursor)

    def get_random_recommendations(self, customer_key, params, max_count):
        """Return random products.

        :param str customer_key: DB key of the customer for whom to
            provide recommendations.
        :param dict params: Parameters. Used ones are:
            "max_count": Maximum number of products to return.
            For other used ones see
            `self.get_exclusion_subquery_and_filter_clause`.
        :return: Product keys.
        :rtype: list(str)
        :raises OSError: Unable to open file with the required AQL
            request.
        """
        select_products_to_exclude, filter_clause = \
            self.get_exclusion_subquery_and_filter_clause(customer_key, params)
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

    def get_exclusion_subquery_and_filter_clause(self, customer_key, params):
        """Return AQL fragments filtering out products customer knows.

        Allows filtering out products the customer already interacted
        with, by the provided types of interactions.

        :param str customer_key: DB key of the customer for whom to
            provide recommendations.
        :param dict params: Parameters. Missing ones are assumed to be
            true. Used ones are:
            "include_viewed": Include products user already viewed.
            "include_commented": Include products user already
            commented.
            "include_bought": Include products user already bought.
        :rtype tuple(str, str):
        :return: "LET" AQL fragment that selects products to ignore, and
            "FILTER" AQL clause that filters out those products.
        """
        # TODO Include commented and bought unless excluded even if they
        # are also viewed (which is most likely the case).
        select_products_to_exclude = ''
        filter_clause = ''
        # TODO Get rid of the mapping, or at least extract it and place
        # where collection names are defined.
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
                    'customers/{requested_customer_key}'
                    {collections_for_exclusion_by}
                    RETURN product)
                '''.format(requested_customer_key=customer_key,
                           collections_for_exclusion_by=", ".join(
                               collections_for_exclusion_by))
            filter_clause = "FILTER product NOT IN products_to_exclude"
        return select_products_to_exclude, filter_clause


# TODO Request rate limiter like
# http://www.giantflyingsaucer.com/blog/?p=5910


# TODO Authentication, HTTPS.
api = falcon.API()
# TODO Maybe use PUT and receive parameters in URL, at least for
# vertices.
api.add_route('/{collection_name}', InsertRecordResource())
api.add_route('/{collection_name}/{key}/{action}', ModifyRecordResource())
api.add_route(
    '/customers/{customer_key}/recommendations/{recommendation_strategy}',
    GetRecommendationsResource())
# TODO Maybe later, I don't want to complicate the code now.
# api.add_route(
#     '/customers/{customer_key}/recommendations/{recommendation_strategy}/'
#     '{type}',
#     GetRecommendationsResource())
