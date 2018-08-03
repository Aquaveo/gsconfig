#!/usr/bin/env python

"""
gsconfig is a python library for manipulating a GeoServer instance via the GeoServer RESTConfig API.

The project is distributed under a MIT License .
"""

__author__ = "David Winslow"
__copyright__ = "Copyright 2012-2015 Boundless, Copyright 2010-2012 OpenPlans"
__license__ = "MIT"

from geoserver.catalog import Catalog

cat = Catalog("http://localhost:8080/geoserver/rest", "admin", "geoserver")

ws = cat.get_workspace("sf")
resources = cat.get_resources(workspace=ws)
if len(resources) != 0:
    assert all(r.projection == "EPSG:27613" for r in resources), ws.name
