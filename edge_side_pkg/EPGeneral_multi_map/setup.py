#!/usr/bin/env python3
from setuptools import setup

from catkin_pkg.python_setup import generate_distutils_setup


setup(**generate_distutils_setup(packages=["epgeneral_multi_map"], package_dir={"": "src"}))
