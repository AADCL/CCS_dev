#!/usr/bin/env python3
from setuptools import setup
from catkin_pkg.python_setup import generate_distutils_setup

setup(**generate_distutils_setup(packages=["ros_task_control"], package_dir={"": "src"}))
