# Oceanum Datamesh QGIS plugin — developer tasks.
#
# QGIS runs its own Python, so tests and the app must use that interpreter's
# bindings. Set QGIS_PYTHON if your QGIS Python bindings are not on the default
# path (e.g. QGIS 4 on Debian puts them under /usr/share/qgis/python).

PLUGIN        := oceanum_datamesh
QGIS_PY_PATH  ?= /usr/share/qgis/python
# QGIS 4 uses a QGIS4 profile dir; QGIS 3 uses QGIS3. Override for 3.x:
#   make deploy PLUGIN_DIR=$(HOME)/.local/share/QGIS/QGIS3/profiles/default/python/plugins
PLUGIN_DIR    ?= $(HOME)/.local/share/QGIS/QGIS4/profiles/default/python/plugins
ZIP           := $(PLUGIN)-$(shell grep -oP '(?<=^version=).*' $(PLUGIN)/metadata.txt).zip

.PHONY: help test lint format deploy undeploy zip clean run

help:
	@echo "make test     - run the offline unit tests"
	@echo "make lint     - ruff lint"
	@echo "make format   - ruff format"
	@echo "make deploy   - symlink the plugin into the QGIS plugins dir"
	@echo "make undeploy - remove the deployed symlink"
	@echo "make zip      - build an installable plugin zip"
	@echo "make run      - launch QGIS with the plugin available"

test:
	PYTHONPATH=$(QGIS_PY_PATH) python3 -m pytest

lint:
	ruff check $(PLUGIN) tests

format:
	ruff format $(PLUGIN) tests

deploy:
	mkdir -p "$(PLUGIN_DIR)"
	ln -sfn "$(CURDIR)/$(PLUGIN)" "$(PLUGIN_DIR)/$(PLUGIN)"
	@echo "Linked $(PLUGIN) into $(PLUGIN_DIR). Enable it in QGIS: Plugins > Manage and Install."

undeploy:
	rm -f "$(PLUGIN_DIR)/$(PLUGIN)"

zip: clean
	cp LICENSE README.md "$(PLUGIN)/"
	zip -r "$(ZIP)" "$(PLUGIN)" \
		-x '*/__pycache__/*' -x '*.pyc' -x '*/.*'
	@echo "Built $(ZIP)"

run: deploy
	qgis

clean:
	rm -f $(PLUGIN)-*.zip
	find $(PLUGIN) -name '__pycache__' -type d -prune -exec rm -rf {} +
