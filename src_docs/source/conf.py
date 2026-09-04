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
from pathlib import Path

from docutils import nodes

# Mock testing environment if needed
if not os.environ.get("CARDANO_NODE_SOCKET_PATH"):
    os.environ["CARDANO_NODE_SOCKET_PATH"] = "/nonexistent"
    mockdir = Path(__file__).parent / "mocks"
    os.environ["PATH"] = f"{mockdir}:{os.environ['PATH']}"

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. Prepend the repo root so that the
# checkout this doc is built from wins over any installed (e.g. editable)
# version of the package. Must happen before `cardano_node_tests` is imported.
sys.path.insert(0, str(Path(__file__).parents[2]))

import cardano_node_tests

# -- Project information -----------------------------------------------------

project = "cardano-node-tests"
author = "Cardano Test Engineering Team"
# copyright is overriden by 'css/copyright.css'
# see https://github.com/readthedocs/sphinx_rtd_theme/issues/828
copyright = ""


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
    "sphinxemoji.sphinxemoji",
    "sphinx_mdinclude",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# `sphinx_mdinclude` provides only the `mdinclude` directive, it doesn't register
# a parser for markdown source files.
source_suffix = {
    ".rst": "restructuredtext",
}


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
# html_theme = 'alabaster'
html_theme = "sphinx_rtd_theme"

html_theme_options = {
    "logo_only": False,
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    # Toc options
    "collapse_navigation": False,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}

html_logo = "_static/images/Cardano-Crypto-Logo-128.png"

html_context = {
    "display_github": True,  # Add 'Edit on Github' link instead of 'View page source'
    "github_user": "IntersectMBO",
    "github_repo": "cardano-node-tests",
    "github_version": "master",
    "conf_py_path": "/src_docs/source/",
    "source_suffix": source_suffix,
}

html_favicon = (
    "https://user-images.githubusercontent.com/2352619/"
    "223086153-522289f3-9902-4f63-ad7b-a7d9c5789db0.png"
)

html_extra_path = ["CNAME"]

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# These paths are either relative to html_static_path
# or fully qualified paths (eg. https://...)
html_css_files = [
    "css/copyright.css",
    "css/tables.css",
]

# Clear tokens from the output
os.environ["GITHUB_TOKEN"] = "token_XXXXXXXXXXXXXXXXXXXX"

# Resolve function for the linkcode extension.

# store current git revision
if os.environ.get("CARDANO_TESTS_GIT_REV"):
    cardano_node_tests._git_rev = os.environ.get("CARDANO_TESTS_GIT_REV")
else:
    with subprocess.Popen(
        ["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as p:
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
        fn = os.path.relpath(fn, start=os.path.dirname(cardano_node_tests.__file__))  # noqa: PTH120
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
        f"https://github.com/IntersectMBO/cardano-node-tests/blob/"
        f"{cardano_node_tests._git_rev}/{filename}"
    )


# -- Markdown heading anchors ------------------------------------------------

# `mdinclude` turns a markdown link like `[text](#anchor)` into a `:ref:` on the
# label `anchor`, but the sections it generates only carry implicit docutils ids.
# Register the ids of every section as `std` labels, under both the docutils id
# and the GitHub-flavored slug of the title, so that anchors that work in the
# markdown files as rendered by GitHub also resolve in the built documentation.


def _github_slug(title):
    """Return the anchor GitHub generates for a markdown heading."""
    slug = "".join(c for c in title.lower() if c.isalnum() or c in " -_")
    return slug.replace(" ", "-")


def _register_section_labels(app, document):
    """Add a `std` domain label for each section anchor in `document`."""
    docname = app.env.docname
    labels = app.env.domaindata["std"]["labels"]
    anonlabels = app.env.domaindata["std"]["anonlabels"]

    for section in document.findall(nodes.section):
        if not section["ids"]:
            continue
        title = section.next_node(nodes.title)
        if title is None:
            continue
        section_id = section["ids"][0]
        title_text = title.astext()
        for name in {section_id, _github_slug(title_text)}:
            if name in labels:
                continue
            labels[name] = (docname, section_id, title_text)
            anonlabels[name] = (docname, section_id)


def setup(app):
    """Register the Sphinx extension points defined in this file."""
    app.connect("doctree-read", _register_section_labels)
