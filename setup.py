#!/usr/bin/env python
# -*- coding: utf-8 -*-
from io import open
from setuptools import setup, find_packages

try:
    readme_text = open('README.rst').read()
except IOError as e:
    readme_text = ''

setup(name="gsconfig",
      version="1.1.0",
      description="GeoServer REST Configuration - with python 2 and 3 support",
      long_description=readme_text,
      keywords="GeoServer REST Configuration",
      license="MIT",
      url="https://github.com/tethysplatform/gsconfig/tree/python3",
      author="David Winslow, Sebastian Benthall, Nathan Swain",
      author_email="nswain@aquaveo.com",
      install_requires=[
          'requests>=2.19.1,<2.20.0',
          'future>=0.16.0,<0.17.0'
      ],
      package_dir={'': 'src'},
      packages=find_packages('src'),
      test_suite="test.catalogtests",
      classifiers=[
          'Intended Audience :: Developers',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: MIT License',
          'Operating System :: OS Independent',
          'Programming Language :: Python',
          'Topic :: Scientific/Engineering :: GIS',
      ]
      )
