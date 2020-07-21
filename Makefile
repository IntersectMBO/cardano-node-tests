# run all tests
tests:
	pytest -m "not clean_cluster"
	pytest -m "clean_cluster"

lint:
	pre-commit run -a
