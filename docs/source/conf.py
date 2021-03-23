# pylint: disable=all
# type: ignore
# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
import inspect
import os
import subprocess
import sys

import cardano_node_tests

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
sys.path.insert(0, os.path.abspath(".."))


# -- Project information -----------------------------------------------------

project = "cardano-node-tests"
copyright = "2021, Cardano QA Team"
author = "Cardano QA Team"


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    # "sphinx.ext.doctest",
    # "sphinx.ext.coverage",
    "sphinx.ext.githubpages",
    "sphinx.ext.linkcode",
    "sphinx.ext.napoleon",
    "m2r2",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# source_suffix = '.rst'
source_suffix = [".rst", ".md"]


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
# html_theme = 'alabaster'
html_theme = "sphinx_rtd_theme"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]
# Resolve function for the linkcode extension.

# store current git revision
if os.environ.get("CARDANO_TESTS_GIT_REV"):
    cardano_node_tests._git_rev = os.environ.get("CARDANO_TESTS_GIT_REV")
else:
    p = subprocess.Popen(
        ["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, __ = p.communicate()
    cardano_node_tests._git_rev = stdout.decode().strip()
if not cardano_node_tests._git_rev:
    cardano_node_tests._git_rev = "master"


def linkcode_resolve(domain, info):
    def find_source():
        # try to find the file and line number, based on code from numpy:
        # https://github.com/numpy/numpy/blob/master/doc/source/conf.py#L286
        obj = sys.modules.get(info["module"])
        if obj is None:
            return None

        for part in info["fullname"].split("."):
            try:
                obj = getattr(obj, part)
            except Exception:
                return None

        # strip decorators, which would resolve to the source of the decorator
        # possibly an upstream bug in getsourcefile, bpo-1764286
        obj = inspect.unwrap(obj)

        fn = inspect.getsourcefile(obj)
        fn = os.path.relpath(fn, start=os.path.dirname(cardano_node_tests.__file__))
        source, lineno = inspect.getsourcelines(obj)
        return fn, lineno, lineno + len(source) - 1

    if domain != "py" or not info["module"]:
        return None

    try:
        fn, l_start, l_end = find_source()
        filename = f"cardano_node_tests/{fn}#L{l_start}-L{l_end}"
        # print(filename)
    except Exception:
        filename = info["module"].replace(".", "/") + ".py"
        # print(f"EXC: {filename}")

    return (
        f"https://github.com/input-output-hk/cardano-node-tests/blob/"
        f"{cardano_node_tests._git_rev}/{filename}"
    )
