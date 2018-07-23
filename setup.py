#!/usr/bin/env python
# -*- coding: utf-8 -*-
from io import open
from setuptools import setup, find_packages

try:
    readme_text = open('README.rst').read()
except IOError as e:
    readme_text = ''

setup(name = "gsconfig",
    version = "1.0.9",
    description = "GeoServer REST Configuration",
    long_description = readme_text,
    keywords = "GeoServer REST Configuration",
    license = "MIT",
    url = "https://github.com/boundlessgeo/gsconfig",
    author = "David Winslow, Sebastian Benthall",
    author_email = "dwinslow@opengeo.org",
    install_requires = [
        'httplib2>=0.7.4',
        'gisdata==0.5.4',
        'future>=0.16.0,<0.17.0'
    ],
    package_dir = {'':'src'},
    packages = find_packages('src'),
    test_suite = "test.catalogtests",
    classifiers = [
                 'Development Status :: 4 - Beta',
                 'Intended Audience :: Developers',
                 'Intended Audience :: Science/Research',
                 'License :: OSI Approved :: MIT License',
                 'Operating System :: OS Independent',
                 'Programming Language :: Python',
                 'Topic :: Scientific/Engineering :: GIS',
                ]
)
