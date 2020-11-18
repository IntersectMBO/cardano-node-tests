from setuptools import find_packages
from setuptools import setup

setup(
    name="cardano-node-tests",
    packages=find_packages(),
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
)
