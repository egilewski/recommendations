Recommendations
===============

This is a simple REST API app that stores products, customers, and relations between them, and based on that information provides recommendation of products for a specified customer.

It was written for a coding challenge and isn't meant to be used in production. Comments in Python and AQL code list features that will make it production-ready. Considering that fully-functional alternatives already exist, I probably won't be implementing them.

Usage
-----

Run the app:

    docker-compose up

Add customer (any string can be used for "key"):

    echo '{"key": "0"}' | curl -X POST --data-binary @- -w "\n" --dump - http://localhost:8000/customers

Add product (any string can be used for "key"):

    echo '{"key": "0"}' | curl -X POST --data-binary @- -w "\n" --dump - http://localhost:8000/products

Add "viewed" interaction between a customer ("from", identified by it's "key") and product ("to"):

    echo '{"from": "0", "to": "0"}' | curl -X POST --data-binary @- -w "\n" --dump - http://localhost:8000/viewings

Add "commented" interaction between a customer ("from", identified by it's "key") and product ("to"):

    echo '{"from": "0", "to": "0"}' | curl -X POST --data-binary @- -w "\n" --dump - http://localhost:8000/commentings

Add "bought" interaction between a customer ("from", identified by it's "key") and product ("to"):

    echo '{"from": "0", "to": "0"}' | curl -X POST --data-binary @- -w "\n" --dump - http://localhost:8000/buyings

Deactivate a product:

    curl -X POST --dump - http://localhost:8000/products/0/deactivate

Activate a product:

    curl -X POST --dump - http://localhost:8000/products/0/activate

Get collaborative filtering recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/collaborative_filtering'

Get top viewings recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/top?type=viewings'

Get top commentings recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/top?type=commentings'

Get top buyings recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/top?type=buyings'

Get random recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/random'

Limit number of recommendations:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/random?max_count=5'

Exclude products this customer viewed:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/random?include_viewed=false'

Exclude products this customer commented:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/random?include_commented=false'

Exclude products this customer bounght:

    curl -w '\n' 'http://localhost:8000/customers/0/recommendations/random?include_bought=false'
