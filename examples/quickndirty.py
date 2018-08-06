#!/usr/bin/env python

"""
gsconfig is a python library for manipulating a GeoServer instance via the GeoServer RESTConfig API.

The project is distributed under a MIT License .
"""

__author__ = "David Winslow"
__copyright__ = "Copyright 2012-2015 Boundless, Copyright 2010-2012 OpenPlans"
__license__ = "MIT"

import httplib2
import subprocess
import tempfile

http = httplib2.Http()
http.add_credentials("admin", "geoserver")
url = "http://localhost:8080/geoserver/rest/workspaces/topp/datastores/states_shapefile/featuretypes/states.xml"

headers, body = http.request(url)

__, temp = tempfile.mkstemp()
with open(temp, 'w') as f:
    f.write(body)

subprocess.call(['vim', temp])

headers = {"content-type": "application/xml"}
http.request(url,
             method="PUT", headers=headers, body=open(temp).read())
