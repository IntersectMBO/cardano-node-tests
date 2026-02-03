# install cardano_node_tests and its dependencies into a virtual environment
.PHONY: install
install:
	./scripts/setup_dev_venv.sh

# check if development environment is set up correctly
.PHONY: check-dev-env
check-dev-env:
	@./scripts/check_dev_env.sh

# update cardano-node binaries from a given git repository
.PHONY: update-node-bins
update-node-bins:
	@if [ -z "$(repo)" ]; then \
		echo "Usage: make update-node-bins repo=/path/to/cardano-node-repo" >&2; \
		exit 1; \
	fi
	@./scripts/update_node_bins.sh "$(repo)"

# reinstall cardano-clusterlib-py in editable mode from a given git repository
.PHONY: reinstall-editable
reinstall-editable:
	@if [ -z "$(repo)" ]; then \
		echo "Usage: make reinstall-editable repo=/path/to/cardano-clusterlib-py" >&2; \
		exit 1; \
	fi
	@./scripts/clusterlib_reinstall_editable.sh "$(repo)"

# set up test environment
.PHONY: test-env
test-env:
	@./scripts/setup_test_env.sh conway

# update uv lockfile
.PHONY: update-lockfile
update-lockfile:
	@exit_code=0; \
	./scripts/uv_update_lock.sh || exit_code=$$?; \
	if [ $$exit_code -ne 0 ] && [ $$exit_code -ne 10 ]; then \
		echo "uv lockfile update failed. Retrying without cache..." >&2; \
		./scripts/uv_update_lock.sh --refresh; \
	else \
		exit $$exit_code; \
	fi

# initialize linters
.PHONY: init-lint
init-lint:
	pre-commit clean
	pre-commit gc
	find . -path '*/.mypy_cache/*' -delete
	pre-commit uninstall
	pre-commit install --install-hooks

# run linters
.PHONY: lint
lint:
	pre-commit run -a --show-diff-on-failure --color=always

# build sphinx documentation
.PHONY: build-doc
build-doc:
	mkdir -p src_docs/build
	$(MAKE) -C src_docs clean
	$(MAKE) -C src_docs html

# build and deploy sphinx documentation
.PHONY: doc
doc:
	./scripts/deploy_doc.sh
