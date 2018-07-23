'''
gsconfig is a python library for manipulating a GeoServer instance via the GeoServer RESTConfig API.

The project is distributed under a MIT License .
'''

__author__ = "David Winslow"
__copyright__ = "Copyright 2012-2015 Boundless, Copyright 2010-2012 OpenPlans"
__license__ = "MIT"

from future import standard_library
standard_library.install_aliases()
from builtins import map
from builtins import str
from builtins import object
from past.builtins import basestring

from datetime import datetime, timedelta
import logging
import json
from geoserver.layer import Layer
from geoserver.resource import FeatureType, Coverage
from geoserver.store import coveragestore_from_index, datastore_from_index, \
    wmsstore_from_index, UnsavedDataStore, \
    UnsavedCoverageStore, UnsavedWmsStore
from geoserver.style import Style
from geoserver.support import prepare_upload_bundle, url, _decode_list, _decode_dict, JDBCVirtualTable
from geoserver.layergroup import LayerGroup, UnsavedLayerGroup
from geoserver.workspace import workspace_from_index, Workspace
import os
import httplib2
from xml.etree.ElementTree import XML
from xml.parsers.expat import ExpatError

from urllib.parse import urlparse

logger = logging.getLogger("gsconfig.catalog")

class UploadError(Exception):
    pass

class ConflictingDataError(Exception):
    pass

class AmbiguousRequestError(Exception):
    pass

class FailedRequestError(Exception):
    pass

def _name(named):
    """Get the name out of an object.  This varies based on the type of the input:
       * the "name" of a string is itself
       * the "name" of None is itself
       * the "name" of an object with a property named name is that property -
         as long as it's a string
       * otherwise, we raise a ValueError
    """
    if isinstance(named, basestring) or named is None:
        return named
    elif getattr(named, 'name', None) is not None and isinstance(named.name, basestring):
        return named.name
    else:
        raise ValueError("Can't interpret %s as a name or a configuration object" % named)

class Catalog(object):
    """
    The GeoServer catalog represents all of the information in the GeoServer
    configuration.    This includes:
    - Stores of geospatial data
    - Resources, or individual coherent datasets within stores
    - Styles for resources
    - Layers, which combine styles with resources to create a visible map layer
    - LayerGroups, which alias one or more layers for convenience
    - Workspaces, which provide logical grouping of Stores
    - Maps, which provide a set of OWS services with a subset of the server's
        Layers
    - Namespaces, which provide unique identifiers for resources
    """

    def __init__(self, service_url, username="admin", password="geoserver", disable_ssl_certificate_validation=False):
        self.service_url = service_url
        if self.service_url.endswith("/"):
            self.service_url = self.service_url.strip("/")
        self.username = username
        self.password = password
        self.disable_ssl_cert_validation = disable_ssl_certificate_validation
        self.http = None
        self.setup_connection()

        self._cache = dict()
        self._version = None

    def __getstate__(self):
        '''http connection cannot be pickled'''
        state = dict(vars(self))
        state.pop('http', None)
        state['http'] = None
        return state

    def __setstate__(self, state):
        '''restore http connection upon unpickling'''
        self.__dict__.update(state)
        self.setup_connection()

    @property
    def gs_base_url(self):
        return self.service_url.rstrip("rest")

    def setup_connection(self):
        self.http = httplib2.Http(
            disable_ssl_certificate_validation=self.disable_ssl_cert_validation)
        self.http.add_credentials(self.username, self.password)
        netloc = urlparse(self.service_url).netloc
        self.http.authorizations.append(
            httplib2.BasicAuthentication(
                (self.username, self.password),
                netloc,
                self.service_url,
                {},
                None,
                None,
                self.http
            ))

    def about(self):
        '''return the about information as a formatted html'''
        about_url = self.service_url + "/about/version.html"
        response, content = self.http.request(about_url, "GET")
        if response.status == 200:
            return content
        raise FailedRequestError('Unable to determine version: %s' %
                                 (content or response.status))

    def gsversion(self):
        '''obtain the version or just 2.2.x if < 2.3.x
        Raises:
            FailedRequestError: If the request fails.
        '''
        if self._version: return self._version
        about_url = self.service_url + "/about/version.xml"
        response, content = self.http.request(about_url, "GET")
        version = None
        if response.status == 200:
            dom = XML(content)
            resources = dom.findall("resource")
            for resource in resources:
                if resource.attrib["name"] == "GeoServer":
                    try:
                        version = resource.find("Version").text
                        break
                    except:
                        pass

        #This will raise an exception if the catalog is not available
        #If the catalog is available but could not return version information,
        #it is an old version that does not support that
        if version is None:
            self.get_workspaces()
            # just to inform that version < 2.3.x
            version = "2.2.x"
        self._version = version
        return version

    def delete(self, config_object, purge=None, recurse=False):
        """
        send a delete request
        XXX [more here]
        """
        rest_url = config_object.href

        #params aren't supported fully in httplib2 yet, so:
        params = []

        # purge deletes the SLD from disk when a style is deleted
        if purge:
            params.append("purge=" + str(purge))

        # recurse deletes the resource when a layer is deleted.
        if recurse:
            params.append("recurse=true")

        if params:
            rest_url = rest_url + "?" + "&".join(params)

        headers = {
            "Content-type": "application/xml",
            "Accept": "application/xml"
        }
        response, content = self.http.request(rest_url, "DELETE", headers=headers)
        self._cache.clear()

        if response.status == 200:
            return (response, content)
        else:
            raise FailedRequestError("Tried to make a DELETE request to %s but got a %d status code: \n%s" % (rest_url, response.status, content))

    def get_xml(self, rest_url):
        logger.debug("GET %s", rest_url)

        cached_response = self._cache.get(rest_url)

        def is_valid(cached_response):
            return cached_response is not None and datetime.now() - cached_response[0] < timedelta(seconds=5)

        def parse_or_raise(xml):
            try:
                return XML(xml)
            except (ExpatError, SyntaxError) as e:
                msg = "GeoServer gave non-XML response for [GET %s]: %s"
                msg = msg % (rest_url, xml)
                raise Exception(msg, e)

        if is_valid(cached_response):
            raw_text = cached_response[1]
            return parse_or_raise(raw_text)
        else:
            response, content = self.http.request(rest_url)
            if response.status == 200:
                self._cache[rest_url] = (datetime.now(), content)
                return parse_or_raise(content)
            elif response.status == 404:
                return None
            else:
                raise FailedRequestError("Tried to make a GET request to %s but got a %d status code: \n%s" % (rest_url, response.status, content))

    def reload(self):
        reload_url = url(self.service_url, ['reload'])
        response = self.http.request(reload_url, "POST")
        self._cache.clear()
        return response

    def reset(self):
        reload_url = url(self.service_url, ['reset'])
        response = self.http.request(reload_url, "POST")
        self._cache.clear()
        return response

    def save(self, obj, content_type="application/xml"):
        """
        saves an object to the REST service
        gets the object's REST location and the data from the object,
        then POSTS the request.
        """
        rest_url = obj.href
        message = obj.message()

        headers = {
            "Content-type": content_type,
            "Accept": content_type
        }
        logger.debug("%s %s", obj.save_method, obj.href)
        response = self.http.request(rest_url, obj.save_method, message, headers)
        headers, body = response
        self._cache.clear()
        if 400 <= int(headers['status']) < 600:
            raise FailedRequestError("Error code (%s) from GeoServer: %s" %
                (headers['status'], body))
        return response

    def get_store(self, name, workspace=None):
        '''
          Returns a single store object.
          Will return None if no store is found.
          Will raise an error if more than one store with the same name is found.
        '''

        stores = self.get_stores(workspace=workspace, names=name)

        if stores.__len__() == 0:
            return None
        elif stores.__len__() > 1:
            multiple_stores = []
            for s in stores:
                multiple_stores.append("{workspace_name}:{store_name}".format(workspace_name=s.workspace.name, store_name=s.name))

            raise AmbiguousRequestError("Multiple stores found named {name} - {stores}".format(name=name, stores=", ".join(multiple_stores)))
        else:
            return stores[0]

    def get_stores(self, names=None, workspace=None):
        '''
          Returns a list of stores in the catalog. If workspace is specified will only return stores in that workspace.
          If names is specified, will only return stores that match.
          names can either be a comma delimited string or an array.
          If names is specified will only return stores that match the name.
          Will return an empty list if no stores are found.
        '''

        workspaces = []
        if workspace is not None:
            if isinstance(workspace, basestring):
                ws = self.get_workspaces(workspace)
                if ws:
                    # There can only be one workspace with this name
                    workspaces.append(ws[0])
            elif getattr(workspace, 'resource_type', None) is not None and workspace.resource_type == "workspace":
              workspaces.append(workspace)
        else:
            workspaces = self.get_workspaces()

        stores = []
        if workspaces:
            for ws in workspaces:
                ds_list = self.get_xml(ws.datastore_url)
                cs_list = self.get_xml(ws.coveragestore_url)
                wms_list = self.get_xml(ws.wmsstore_url)
                stores.extend([datastore_from_index(self, ws, n) for n in ds_list.findall("dataStore")])
                stores.extend([coveragestore_from_index(self, ws, n) for n in cs_list.findall("coverageStore")])
                stores.extend([wmsstore_from_index(self, ws, n) for n in wms_list.findall("wmsStore")])

        if names is None:
            names = []
        elif isinstance(names, basestring):
            names = list(map(str.strip, str(names).split(',')))
        if stores and names:
            named_stores = []
            for store in stores:
                if store.name in names:
                    named_stores.append(store)
            return named_stores

        return stores

    def create_datastore(self, name, workspace=None):
        if isinstance(workspace, basestring):
            workspace = self.get_workspace(workspace)
        elif workspace is None:
            workspace = self.get_default_workspace()
        return UnsavedDataStore(self, name, workspace)

    def create_coveragestore2(self, name, workspace = None):
        """
        Hm we already named the method that creates a coverage *resource*
        create_coveragestore... time for an API break?
        """
        if isinstance(workspace, basestring):
            workspace = self.get_workspace(workspace)
        elif workspace is None:
            workspace = self.get_default_workspace()
        return UnsavedCoverageStore(self, name, workspace)

    def create_wmsstore(self, name, workspace = None, user = None, password = None):
        if workspace is None:
            workspace = self.get_default_workspace()
        return UnsavedWmsStore(self, name, workspace, user, password)

    def create_wmslayer(self, workspace, store, name, nativeName=None):
        headers = {
            "Content-type": "text/xml",
            "Accept": "application/xml"
        }
        # if not provided, fallback to name - this is what geoserver will do
        # anyway but nativeName needs to be provided if name is invalid xml
        # as this will cause verification errors since geoserver 2.6.1
        if nativeName is None:
            nativeName = name

        wms_url = store.href.replace('.xml', '/wmslayers')
        data = "<wmsLayer><name>%s</name><nativeName>%s</nativeName></wmsLayer>" % (name, nativeName)
        headers, response = self.http.request(wms_url, "POST", data, headers)

        self._cache.clear()
        if headers.status < 200 or headers.status > 299: raise UploadError(response)
        return self.get_resource(name, store=store, workspace=workspace)

    def add_data_to_store(self, store, name, data, workspace=None, overwrite = False, charset = None):
        if isinstance(store, basestring):
            store = self.get_store(store, workspace=workspace)
        if workspace is not None:
            workspace = _name(workspace)
            assert store.workspace.name == workspace, "Specified store (%s) is not in specified workspace (%s)!" % (store, workspace)
        else:
            workspace = store.workspace.name
        store = store.name

        if isinstance(data, dict):
            bundle = prepare_upload_bundle(name, data)
        else:
            bundle = data

        params = dict()
        if overwrite:
            params["update"] = "overwrite"
        if charset is not None:
            params["charset"] = charset
        params["filename"] = "{}.zip".format(name)
        params["target"] = "shp"
        # params["configure"] = "all"

        headers = { 'Content-Type': 'application/zip', 'Accept': 'application/xml' }
        upload_url = url(self.service_url,
            ["workspaces", workspace, "datastores", store, "file.shp"], params)

        try:
            with open(bundle, "rb") as f:
                data = f.read()
                headers, response = self.http.request(upload_url, "PUT", data, headers)
                self._cache.clear()
                if headers.status != 201:
                    raise UploadError(response)
        finally:
            # os.unlink(bundle)
            pass

    def create_featurestore(self, name, data, workspace=None, overwrite=False, charset=None):
        if not overwrite:
            store = self.get_store(name, workspace)
            if store is not None:
                msg = "There is already a store named " + name
                if workspace:
                    msg += " in " + str(workspace)
                raise ConflictingDataError(msg)

        if workspace is None:
            workspace = self.get_default_workspace()
        workspace = _name(workspace)
        params = dict()
        if charset is not None:
            params['charset'] = charset
        ds_url = url(self.service_url,
            ["workspaces", workspace, "datastores", name, "file.shp"], params)

        # PUT /workspaces/<ws>/datastores/<ds>/file.shp
        headers = {
            "Content-type": "application/zip",
            "Accept": "application/xml"
        }
        if isinstance(data,dict):
            logger.debug('Data is NOT a zipfile')
            archive = prepare_upload_bundle(name, data)
        else:
            logger.debug('Data is a zipfile')
            archive = data
        message = open(archive, 'rb')
        try:
            # response = self.requests.post(ds_url, files={archive: open(archive, 'rb')})
            headers, response = self.http.request(ds_url, "PUT", message, headers)
            self._cache.clear()
            if headers.status != 201:
                raise UploadError(response)
        finally:
            message.close()
            os.unlink(archive)

    def create_imagemosaic(self, name, data, configure=None, workspace=None, overwrite=False, charset=None):
        if not overwrite:
            store = self.get_store(name, workspace)
            if store is not None:
                msg = "There is already a store named " + name
                if workspace:
                    msg += " in " + str(workspace)
                raise ConflictingDataError(msg)

        if workspace is None:
            workspace = self.get_default_workspace()
        workspace = _name(workspace)
        params = dict()
        if charset is not None:
            params['charset'] = charset
        if configure is not None:
            params['configure'] = "none"

        if isinstance(data, file) or os.path.splitext(data)[-1] == ".zip":
            store_type = "file.imagemosaic"
            contet_type = "application/zip"
            if isinstance(data, basestring):
                upload_data = open(data, 'rb')
            elif isinstance(data, file):
                # Adding this check only to pass tests. We should drop support for passing a file object
                upload_data = data
            else:
                raise ValueError("ImageMosaic Dataset or directory: {data} is incorrect".format(data=data))
        else:
            store_type = "external.imagemosaic"
            contet_type = "text/plain"
            if isinstance(data, basestring):
                upload_data = data if data.startswith("file:") else "file:{data}".format(data=data)
            else:
                raise ValueError("ImageMosaic Dataset or directory: {data} is incorrect".format(data=data))

        cs_url = url(
            self.service_url,
            [
                "workspaces",
                workspace,
                "coveragestores",
                name,
                store_type
            ],
            params
        )

        # PUT /workspaces/<ws>/coveragestores/<name>/file.imagemosaic?configure=none
        req_headers = {
            "Content-type": contet_type,
            "Accept": "application/xml"
        }

        try:
            resp_headers, response = self.http.request(cs_url, "PUT", upload_data, req_headers)
            self._cache.clear()
            if resp_headers.status != 201:
                raise UploadError(response)
        finally:
            if getattr(upload_data, "close", None) is not None:
                upload_data.close()

        return "Image Mosaic created"

    def create_coveragestore(self, name, data, workspace=None, overwrite=False):
        self._create_coveragestore(name, data, workspace, overwrite)

    def create_coveragestore_external_geotiff(self, name, data, workspace=None, overwrite=False):
        self._create_coveragestore(name, data, workspace=workspace, overwrite=overwrite, external=True)

    def _create_coveragestore(self, name, data, workspace=None, overwrite=False, external=False):
        if not overwrite:
            store = self.get_store(name, workspace)
            if store is not None:
                msg = "There is already a store named " + name
                if workspace:
                    msg += " in " + str(workspace)
                raise ConflictingDataError(msg)

        if workspace is None:
            workspace = self.get_default_workspace()

        archive = None
        ext = "geotiff"
        contet_type = "image/tiff" if not external else "text/plain"
        store_type = "file." if not external else "external."

        headers = {
            "Content-type": contet_type,
            "Accept": "application/xml"
        }

        message = data
        if not external:
            if isinstance(data, dict):
                archive = prepare_upload_bundle(name, data)
                message = open(archive, 'rb')
                if "tfw" in data:
                    # If application/archive was used, server crashes with a 500 error
                    # read in many sites that application/zip will do the trick. Successfully tested
                    headers['Content-type'] = 'application/zip'
                    ext = "worldimage"
            elif isinstance(data, basestring):
                message = open(data, 'rb')
            else:
                message = data


        cs_url = url(self.service_url,
            ["workspaces", workspace.name, "coveragestores", name, store_type + ext],
            { "configure" : "first", "coverageName" : name})

        try:
            headers, response = self.http.request(cs_url, "PUT", message, headers)
            self._cache.clear()
            if headers.status != 201:
                raise UploadError(response)
        finally:
            if getattr(message, "close", None) is not None:
                message.close()
            if archive is not None:
                os.unlink(archive)

    def add_granule(self, data, store, workspace=None):
        '''Harvest/add a granule into an existing imagemosaic'''
        ext = os.path.splitext(data)[-1]
        if ext == ".zip":
            type = "file.imagemosaic"
            upload_data = open(data, 'rb')
            headers = {
              "Content-type": "application/zip",
              "Accept": "application/xml"
            }
        else:
            type = "external.imagemosaic"
            upload_data = data if data.startswith("file:") else "file:{data}".format(data=data)
            headers = {
                "Content-type": "text/plain",
                "Accept": "application/xml"
            }

        params = dict()
        workspace_name = workspace
        if isinstance(store, basestring):
            store_name = store
        else:
            store_name = store.name
            workspace_name = store.workspace.name

        if workspace_name is None: raise ValueError("Must specify workspace")

        cs_url = url(
            self.service_url,
            [
                "workspaces",
                workspace_name,
                "coveragestores",
                store_name,
                type
            ],
            params
        )

        try:
            headers, response = self.http.request(cs_url, "POST", upload_data, headers)
            if headers.status != 202:
                raise UploadError(response)
        finally:
            if getattr(upload_data, "close", None) is not None:
                  upload_data.close()

        self._cache.clear()
        return "Added granule"

    def delete_granule(self, coverage, store, granule_id, workspace=None):
        '''Deletes a granule of an existing imagemosaic'''
        params = dict()

        workspace_name = workspace
        if isinstance(store, basestring):
            store_name = store
        else:
            store_name = store.name
            workspace_name = store.workspace.name

        if workspace_name is None: raise ValueError("Must specify workspace")

        cs_url = url(
            self.service_url,
            [
                "workspaces",
                workspace_name,
                "coveragestores",
                store_name,
                "coverages",
                coverage,
                "index/granules",
                granule_id,
                ".json"
            ],
            params
        )

        # DELETE /workspaces/<ws>/coveragestores/<name>/coverages/<coverage>/index/granules/<granule_id>.json
        headers = {
            "Content-type": "application/json",
            "Accept": "application/json"
        }

        headers, response = self.http.request(cs_url, "DELETE", None, headers)
        if headers.status != 200:
            raise FailedRequestError(response)
        self._cache.clear()
        return "Deleted granule"

    def list_granules(self, coverage, store, workspace=None, filter=None, limit=None, offset=None):
        '''List granules of an imagemosaic'''
        params = dict()

        if filter is not None:
            params['filter'] = filter
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset

        workspace_name = workspace
        if isinstance(store, basestring):
            store_name = store
        else:
            store_name = store.name
            workspace_name = store.workspace.name

        if workspace_name is None: raise ValueError("Must specify workspace")

        cs_url = url(
            self.service_url,
            [
                "workspaces",
                workspace_name,
                "coveragestores",
                store_name,
                "coverages",
                coverage,
                "index/granules.json"
            ],
            params
        )

        # GET /workspaces/<ws>/coveragestores/<name>/coverages/<coverage>/index/granules.json
        headers = {
            "Content-type": "application/json",
            "Accept": "application/json"
        }

        headers, response = self.http.request(cs_url, "GET", None, headers)
        if headers.status != 200:
            raise FailedRequestError(response)
        self._cache.clear()
        granules = json.loads(response, object_hook=_decode_dict)
        return granules

    def harvest_externalgranule(self, data, store):
        '''Harvest a granule into an existing imagemosaic'''
        self.add_granule(data, store)

    def harvest_uploadgranule(self, data, store):
        '''Harvest a granule into an existing imagemosaic'''
        self.add_granule(data, store)

    def mosaic_coverages(self, store):
        '''Returns all coverages in a coverage store'''
        params = dict()
        cs_url = url(
            self.service_url,
            [
                "workspaces",
                store.workspace.name,
                "coveragestores",
                store.name,
                "coverages.json"
            ],
            params
        )
        # GET /workspaces/<ws>/coveragestores/<name>/coverages.json
        headers = {
            "Content-type": "application/json",
            "Accept": "application/json"
        }

        headers, response = self.http.request(cs_url, "GET", None, headers)
        if headers.status != 200:
            raise FailedRequestError(response)
        self._cache.clear()
        coverages = json.loads(response, object_hook=_decode_dict)
        return coverages

    def mosaic_coverage_schema(self, coverage, store, workspace):
        '''Returns the schema of a coverage in a coverage store'''
        params = dict()
        cs_url = url(
            self.service_url,
            [
                "workspaces",
                workspace,
                "coveragestores",
                store,
                "coverages",
                coverage,
                "index.json"
            ],
            params
        )
        # GET /workspaces/<ws>/coveragestores/<name>/coverages/<coverage>/index.json

        headers = {
            "Content-type": "application/json",
            "Accept": "application/json"
        }

        headers, response = self.http.request(cs_url, "GET", None, headers)
        if headers.status != 200:
            raise FailedRequestError(response)
        self._cache.clear()
        schema = json.loads(response, object_hook=_decode_dict)
        return schema

    def mosaic_granules(self, coverage, store, filter=None, limit=None, offset=None):
        '''List granules of an imagemosaic'''
        return self.list_granules(coverage, store, filter=None, limit=None, offset=None)

    def mosaic_delete_granule(self, coverage, store, granule_id):
        '''Deletes a granule of an existing imagemosaic'''
        self.delete_granule(coverage, store, granule_id)

    def publish_featuretype(self, name, store, native_crs, srs=None, jdbc_virtual_table=None):
        '''Publish a featuretype from data in an existing store'''
        # @todo native_srs doesn't seem to get detected, even when in the DB
        # metadata (at least for postgis in geometry_columns) and then there
        # will be a misconfigured layer
        if native_crs is None: raise ValueError("must specify native_crs")
        srs = srs or native_crs
        feature_type = FeatureType(self, store.workspace, store, name)
        # because name is the in FeatureType base class, work around that
        # and hack in these others that don't have xml properties
        feature_type.dirty['name'] = name
        feature_type.dirty['srs'] = srs
        feature_type.dirty['nativeCRS'] = native_crs
        feature_type.enabled = True
        feature_type.title = name
        headers = {
            "Content-type": "application/xml",
            "Accept": "application/xml"
        }

        resource_url=store.resource_url
        if jdbc_virtual_table is not None:
            feature_type.metadata=({'JDBC_VIRTUAL_TABLE':jdbc_virtual_table})
            params = dict()
            resource_url=url(self.service_url,
                ["workspaces", store.workspace.name, "datastores", store.name, "featuretypes.json"], params)

        headers, response = self.http.request(resource_url, "POST", feature_type.message(), headers)
        feature_type.fetch()
        return feature_type

    def get_resource(self, name, store=None, workspace=None):
        if store is not None and workspace is not None:
            if isinstance(workspace, basestring):
                workspace = self.get_workspace(workspace)
            if isinstance(store, basestring):
                store = self.get_store(store, workspace)
            if store is not None:
                return store.get_resources(name)

        if store is not None:
            candidates = [s for s in self.get_resources(store) if s.name == name]
            if len(candidates) == 0:
                return None
            elif len(candidates) > 1:
                raise AmbiguousRequestError
            else:
                return candidates[0]

        if workspace is not None:
            for store in self.get_stores(workspace=workspace):
                resource = self.get_resource(name, store)
                if resource is not None:
                    return resource
            return None

        for ws in self.get_workspaces():
            resource = self.get_resource(name, workspace=ws)
            if resource is not None:
                return resource
        return None

    def get_resource_by_url(self, url):
        xml = self.get_xml(url)
        name = xml.find("name").text
        resource = None
        if xml.tag == 'featureType':
            resource = FeatureType
        elif xml.tag == 'coverage':
            resource = Coverage
        else:
            raise Exception('drat')
        return resource(self, None, None, name, href=url)

    def get_resources(self, store=None, workspace=None):
        if isinstance(workspace, basestring):
            workspace = self.get_workspace(workspace)
        if isinstance(store, basestring):
            store = self.get_store(store, workspace)
        if store is not None:
            return store.get_resources()
        if workspace is not None:
            resources = []
            for store in self.get_stores(workspace=workspace):
                resources.extend(self.get_resources(store))
            return resources
        resources = []
        for ws in self.get_workspaces():
            resources.extend(self.get_resources(workspace=ws))
        return resources

    def get_layer(self, name):
        try:
            lyr = Layer(self, name)
            lyr.fetch()
            return lyr
        except FailedRequestError:
            return None

    def get_layers(self, resource=None):
        if isinstance(resource, basestring):
            resource = self.get_resource(resource)
        layers_url = url(self.service_url, ["layers.xml"])
        description = self.get_xml(layers_url)
        lyrs = [Layer(self, l.find("name").text) for l in description.findall("layer")]
        if resource is not None:
            lyrs = [l for l in lyrs if l.resource.href == resource.href]
        # TODO: Filter by style
        return lyrs

    def get_layergroup(self, name=None, workspace=None):
        try:
            path_parts = ["layergroups", name + ".xml"]
            if workspace is not None:
                wks_name = _name(workspace)
                path_parts = ['workspaces', wks_name] + path_parts

            group_url = url(self.service_url, path_parts)
            group = self.get_xml(group_url)
            wks_name = group.find("workspace").find("name").text if group.find("workspace") else None
            return LayerGroup(self, group.find("name").text, wks_name)
        except FailedRequestError:
            return None

    def get_layergroups(self, workspace=None):
        wks_name = None
        path_parts = ['layergroups.xml']
        if workspace is not None:
            wks_name = _name(workspace)
            path_parts = ['workspaces', wks_name] + path_parts

        groups_url = url(self.service_url, path_parts)
        groups = self.get_xml(groups_url)
        return [LayerGroup(self, g.find("name").text, wks_name) for g in groups.findall("layerGroup")]

    def create_layergroup(self, name, layers = (), styles = (), bounds = None, workspace = None):
        if any(g.name == name for g in self.get_layergroups()):
            raise ConflictingDataError("LayerGroup named %s already exists!" % name)
        else:
            return UnsavedLayerGroup(self, name, layers, styles, bounds,
                                     workspace)

    def get_style(self, name, workspace=None):
        '''Find a Style in the catalog if one exists that matches the given name.
        If name is fully qualified in the form of `workspace:name` the workspace
        may be ommitted.

        :param name: name of the style to find
        :param workspace: optional workspace to search in
        '''
        style = None
        if ':' in name:
            workspace, name = name.split(':', 1)
        try:
            style = Style(self, name, _name(workspace))
            style.fetch()
        except FailedRequestError:
            style = None
        return style

    def get_style_by_url(self, style_workspace_url):
        try:
            dom = self.get_xml(style_workspace_url)
        except FailedRequestError:
            return None
        rest_parts = style_workspace_url.replace(self.service_url, '').split('/')
        # check for /workspaces/<ws>/styles/<stylename>
        workspace = None
        if 'workspaces' in rest_parts:
            workspace = rest_parts[rest_parts.index('workspaces') + 1]
        return Style(self, dom.find("name").text, workspace)

    def get_styles(self, workspace=None):
        styles_xml = "styles.xml"

        if workspace is not None:
            styles_xml = "workspaces/{0}/styles.xml".format(_name(workspace))

        styles_url = url(self.service_url, [styles_xml])
        description = self.get_xml(styles_url)
        return [Style(self, s.find('name').text) for s in description.findall("style")]

    def create_style(self, name, data, overwrite = False, workspace=None, style_format="sld10", raw=False):
        style = self.get_style(name, workspace)
        if not overwrite and style is not None:
            raise ConflictingDataError("There is already a style named %s" % name)

        if not overwrite or style is None:
            headers = {
                "Content-type": "application/xml",
                "Accept": "application/xml"
            }
            xml = "<style><name>{0}</name><filename>{0}.sld</filename></style>".format(name)
            style = Style(self, name, workspace, style_format)
            headers, response = self.http.request(style.create_href, "POST", xml, headers)
            if headers.status < 200 or headers.status > 299: raise UploadError(response)

        headers = {
            "Content-type": style.content_type,
            "Accept": "application/xml"
        }

        body_href = style.body_href
        if raw:
            body_href += "?raw=true"
        headers, response = self.http.request(body_href, "PUT", data, headers)
        if headers.status < 200 or headers.status > 299: raise UploadError(response)

        self._cache.pop(style.href, None)
        self._cache.pop(style.body_href, None)

    def create_workspace(self, name, uri):
        xml = ("<namespace>"
            "<prefix>{name}</prefix>"
            "<uri>{uri}</uri>"
            "</namespace>").format(name=name, uri=uri)
        headers = { "Content-Type": "application/xml" }
        workspace_url = self.service_url + "/namespaces/"

        headers, response = self.http.request(workspace_url, "POST", xml, headers)
        assert 200 <= headers.status < 300, "Tried to create workspace but got " + str(headers.status) + ": " + response
        self._cache.pop("%s/workspaces.xml" % self.service_url, None)
        workspaces = self.get_workspaces(name)
        # Can only have one workspace with this name
        return workspaces[0] if workspaces else None

    def get_workspaces(self, names=None):
        '''
          Returns a list of workspaces in the catalog.
          If names is specified, will only return workspaces that match.
          names can either be a comma delimited string or an array.
          Will return an empty list if no workspaces are found.
        '''
        if names is None:
            names = []
        elif isinstance(names, basestring):
            names = list(map(str.strip, str(names).split(',')))

        description = self.get_xml("%s/workspaces.xml" % self.service_url)
        workspaces = []
        workspaces.extend([workspace_from_index(self, node) for node in description.findall("workspace")])

        if workspaces and names:
            named_workspaces = []
            for ws in workspaces:
                if ws.name in names:
                    named_workspaces.append(ws)
            return named_workspaces

        return workspaces

    def get_workspace(self, name):
        '''
          returns a single workspace object.
          Will return None if no workspace is found.
          Will raise an error if more than one workspace with the same name is found.
        '''

        workspaces = self.get_workspaces(name)

        if len(workspaces) == 0:
            return None
        elif len(workspaces) > 1:
            raise AmbiguousRequestError()
        else:
            return workspaces[0]

    def get_default_workspace(self):
        ws = Workspace(self, "default")
        # must fetch and resolve the 'real' workspace from the response
        ws.fetch()
        return workspace_from_index(self, ws.dom)

    def set_default_workspace(self, name):
        if getattr(name, 'name', None) is not None:
            name = name.name
        workspace = self.get_workspace(name)
        if workspace is not None:
            headers = { "Content-Type": "application/xml" }
            default_workspace_url = self.service_url + "/workspaces/default.xml"
            msg = "<workspace><name>%s</name></workspace>" % name
            headers, response = self.http.request(default_workspace_url, "PUT", msg, headers)
            assert 200 <= headers.status < 300, "Error setting default workspace: " + str(headers.status) + ": " + response
            self._cache.pop(default_workspace_url, None)
            self._cache.pop("%s/workspaces.xml" % self.service_url, None)
        else:
            raise FailedRequestError("no workspace named '%s'" % name)
