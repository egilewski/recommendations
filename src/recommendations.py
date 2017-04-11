import falcon
# import requests
import json
# import logging
# import os


class MainHandler(object):
    def on_get(self, req, resp):
        try:
            resp.content_type = 'application/json'
            resp.status = falcon.HTTP_200
            resp.body = json.dumps({'message': 'hello'})
        except Exception as e:
            print(e)
            resp.body = "error"


# TODO Request rate limiter
# like http://www.giantflyingsaucer.com/blog/?p=5910


api = falcon.API()
api.add_route('/', MainHandler())
