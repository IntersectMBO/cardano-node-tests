# run all tests, artifacts created during tests run are located in the `results` dir
.ONESHELL:
tests:
	[ ! -e ./results ] && mkdir results
	cd results
	pytest -m "not clean_cluster" ../
	pytest -m "clean_cluster" ../

lint:
	pre-commit run -a
