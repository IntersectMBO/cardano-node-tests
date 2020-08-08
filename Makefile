install:
	python3 -m pip install --upgrade pip
	python3 -m pip install --upgrade wheel
	python3 -m pip install -r requirements-dev.txt

# run all tests
tests:
	[ ! -e .cli_coverage ] && mkdir .cli_coverage
	pytest -m "not clean_cluster" --cli-coverage-dir .cli_coverage/ cardano_node_tests
	pytest -m "clean_cluster" --cli-coverage-dir .cli_coverage/ cardano_node_tests

lint:
	pre-commit run -a
