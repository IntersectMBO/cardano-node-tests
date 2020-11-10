README for cardano-node-tests
=============================

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
# update and checkout the desired commit/tag
$ git checkout master
$ git pull origin master
$ git fetch --all --tags
$ git checkout tags/<tag>
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

Test coverage of cardano-cli commands
-------------------------------------

To get test coverage of cardano-cli commands, run tests as usual (`make tests`) and generate the coverage report JSON file with

```
$ ./cli_coverage.py -i .cli_coverage/cli_coverage_*.json -o .cli_coverage/coverage_report.json
```


Publishing testing results
--------------------------

Clone https://github.com/mkoura/cardano-node-tests-reports and see its [README](https://github.com/mkoura/cardano-node-tests-reports/blob/main/README.md).


Building documentation
----------------------

To build documentation using Sphinx, run

```
$ make doc
```

The documentation is generated to `docs/build/html`.

See documentation on https://input-output-hk.github.io/cardano-node-tests/


Contributing
------------

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the git hook scripts that will check you changes before every commit. Alternatively run `make lint` manually before pushing your changes.

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), with the exception that formatting is handled automatically by [Black](https://github.com/psf/black) (through `pre-commit` command).
