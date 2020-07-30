cardano-node-tests
==================

Integration tests for cardano-node.

Installation
------------

```sh
# create and activate virtual env
$ python3 -m venv .env
$ . .env/bin/activate
# install this package together with dev requirements
$ make install
```

Usage
-----

Running tests:

```sh
# cd to cardano-node repo
$ cd /path/to/cardano-node
# launch devops shell
$ nix-shell -A devops
# cd to tests repo
$ cd /path/to/cardano-node-tests
# activate virtual env
$ . .env/bin/activate
# run tests
$ make tests
```

Running linter:

```sh
# activate virtual env
$ . .env/bin/activate
# run linter
$ make lint
```

Contributing
------------

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the git hook scripts that will check you changes before every commit. Alternatively run `make lint` manually before pushing your changes.
