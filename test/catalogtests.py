from __future__ import print_function
from __future__ import absolute_import
from builtins import next
from builtins import str
import os
import sys
import subprocess
import atexit
import signal
import tempfile
import time
import re
import gisdata
from geoserver.catalog import Catalog
from geoserver.catalog import ConflictingDataError
from geoserver.catalog import UploadError
from geoserver.catalog import FailedRequestError
from geoserver.support import ResourceInfo, url
from geoserver.support import DimensionInfo
from geoserver.support import JDBCVirtualTable
from geoserver.support import JDBCVirtualTableGeometry
from geoserver.layergroup import LayerGroup
from geoserver.util import shapefile_and_friends
from .utils import DBPARAMS
from .utils import GSPARAMS

if sys.version_info.major == 2 and sys.version_info.minor < 7:
    import unittest2 as unittest
else:
    import unittest

try:
    import psycopg2

    # only used for connection sanity if present
    conn = psycopg2.connect(('dbname=%(database)s user=%(user)s password=%(passwd)s'
                             ' port=%(port)s host=%(host)s'
                             ) % DBPARAMS)
except ImportError:
    pass

# support resetting geoserver datadir
if GSPARAMS['GEOSERVER_HOME']:
    dest = GSPARAMS['DATA_DIR']
    data = os.path.join(GSPARAMS['GEOSERVER_HOME'], 'data/release', '')
    if dest:
        os.system('rsync -v -a --delete %s %s' %
                  (data, os.path.join(dest, '')))
    else:
        os.system('git clean -dxf -- %s' % data)
    os.system('curl -XPOST --user ' + GSPARAMS['GSUSER'] + ':' +
              GSPARAMS['GSPASSWORD'] + ' ' + GSPARAMS['GSURL'] + '/reload')

# set GS_VERSION to None in order to skip the GeoServer setup
global child_pid
child_pid = None
GS_BASE_DIR = tempfile.gettempdir()
# use "master" in order to test against the latest GeoServer version
if GSPARAMS['GS_VERSION']:
    subprocess.Popen(["rm", "-rf", GSPARAMS['GS_BASE_DIR'] + "/gs"]).communicate()
    subprocess.Popen(["mkdir", GSPARAMS['GS_BASE_DIR'] + "/gs"]).communicate()
    subprocess.Popen(["wget",
                      "http://repo2.maven.org/maven2/org/mortbay/jetty/jetty-runner/8.1.8.v20121106/jetty-runner-8.1.8.v20121106.jar",  # noqa: E501
                      "-P", GSPARAMS['GS_BASE_DIR'] + "/gs"]).communicate()
    subprocess.Popen(["wget",
                      "http://ares.boundlessgeo.com/geoserver/" + GSPARAMS['GS_VERSION'] + "/geoserver-" + GSPARAMS[
                          'GS_VERSION'] + "-latest-war.zip",
                      "-P", GSPARAMS['GS_BASE_DIR'] + "/gs"]).communicate()
    subprocess.Popen(["unzip", "-o", "-d", GSPARAMS['GS_BASE_DIR'] + "/gs",
                      GSPARAMS['GS_BASE_DIR'] + "/gs/geoserver-" + GSPARAMS[
                          'GS_VERSION'] + "-latest-war.zip"]).communicate()
    FNULL = open(os.devnull, 'w')
    proc = subprocess.Popen(["java", "-Xmx512m", "-XX:MaxPermSize=256m",
                             "-Dorg.eclipse.jetty.server.webapp.parentLoaderPriority=true",
                             "-jar", GSPARAMS['GS_BASE_DIR'] + "/gs/jetty-runner-8.1.8.v20121106.jar",
                             "--path", "/geoserver", GSPARAMS['GS_BASE_DIR'] + "/gs/geoserver.war"],
                            stdout=FNULL, stderr=subprocess.STDOUT)
    child_pid = proc.pid
    print("Sleep (90)...")
    time.sleep(90)


def kill_child():
    if child_pid is None:
        pass
    else:
        subprocess.Popen(["rm", "-Rf", GSPARAMS['GS_BASE_DIR'] + "/gs"]).communicate()
        print("KILLING PROCESS: " + str(child_pid))
        os.kill(child_pid, signal.SIGTERM)


atexit.register(kill_child)


def drop_table(table):
    def outer(func):
        def inner(*args):
            try:
                func(*args)
            finally:
                try:
                    if conn:
                        conn.cursor().execute('DROP TABLE %s' % table)
                except Exception as e:
                    print('ERROR dropping table')
                    print(e)

        return inner

    return outer


class NonCatalogTests(unittest.TestCase):

    def testDimensionInfo(self):
        inf = DimensionInfo(*(None,) * 6)
        # make sure these work with no resolution set
        self.assertTrue(inf.resolution_millis() is None)
        self.assertTrue(inf.resolution_str() is None)

        inf = lambda r: DimensionInfo(None, None, None, r, None, None)  # noqa: E731

        def assertEqualResolution(spec, millis):
            self.assertEqual(millis, inf(spec).resolution_millis())
            self.assertEqual(spec, inf(millis).resolution_str())

        assertEqualResolution('0.5 seconds', 500)
        assertEqualResolution('7 days', 604800000)
        assertEqualResolution('10 years', 315360000000000)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.cat = Catalog(GSPARAMS['GSURL'], username=GSPARAMS['GSUSER'], password=GSPARAMS['GSPASSWORD'])

    def testAbout(self):
        about_html = self.cat.about()
        self.assertTrue('<html xmlns="http://www.w3.org/1999/xhtml"' in str(about_html))

    def testGSVersion(self):
        version = self.cat.gsversion()
        pat = re.compile('\d\.\d+(\.[\dx]|-SNAPSHOT)')
        self.assertTrue(pat.match('2.2.x'))
        self.assertTrue(pat.match('2.3.2'))
        self.assertTrue(pat.match('2.3-SNAPSHOT'))
        self.assertTrue(pat.match('2.10.1'))
        self.assertFalse(pat.match('2.3.y'))
        self.assertFalse(pat.match('233'))
        self.assertTrue(pat.match(version))

    def testWorkspaces(self):
        self.assertEqual(7, len(self.cat.get_workspaces()))
        # marking out test since geoserver default workspace is not consistent
        # self.assertEqual("cite", self.cat.get_default_workspace().name)
        self.assertEqual("topp", self.cat.get_workspace("topp").name)
        self.assertEqual("topp", self.cat.get_workspaces("topp")[-1].name)
        self.assertEqual(2, len(self.cat.get_workspaces(names=['topp', 'sde'])))
        self.assertEqual(2, len(self.cat.get_workspaces(names='topp, sde')))

    def testStores(self):
        self.assertEqual(0, len(self.cat.get_stores("nonexistentstore")))
        topp = self.cat.get_workspace("topp")
        sf = self.cat.get_workspace("sf")
        self.assertEqual(9, len(self.cat.get_stores()))
        self.assertEqual(2, len(self.cat.get_stores(workspace=topp)))
        self.assertEqual(2, len(self.cat.get_stores(workspace=sf)))
        self.assertEqual(2, len(self.cat.get_stores(workspace='sf')))
        self.assertEqual(2, len(self.cat.get_stores(names='states_shapefile, sfdem')))
        self.assertEqual(2, len(self.cat.get_stores(names=['states_shapefile', 'sfdem'])))
        self.assertEqual("sfdem", self.cat.get_stores("sfdem")[-1].name)
        self.assertEqual("states_shapefile", self.cat.get_store("states_shapefile", topp).name)
        self.assertEqual("states_shapefile", self.cat.get_store("states_shapefile").name)
        self.assertEqual("states_shapefile", self.cat.get_store("states_shapefile", "topp").name)
        self.assertEqual("sfdem", self.cat.get_store("sfdem", sf).name)
        self.assertEqual("sfdem", self.cat.get_store("sfdem").name)

    def testResources(self):
        topp = self.cat.get_workspace("topp")
        sf = self.cat.get_workspace("sf")
        states = self.cat.get_store("states_shapefile", topp)
        sfdem = self.cat.get_store("sfdem", sf)
        self.assertEqual(19, len(self.cat.get_resources()))
        self.assertEqual(1, len(self.cat.get_resources(states)))
        self.assertEqual(5, len(self.cat.get_resources(workspace=topp)))
        self.assertEqual(1, len(self.cat.get_resources(sfdem)))
        self.assertEqual(6, len(self.cat.get_resources(workspace=sf)))

        self.assertEqual("states", self.cat.get_resource("states", states).name)
        self.assertEqual("states", self.cat.get_resource("states", workspace=topp).name)
        self.assertEqual("states", self.cat.get_resource("states").name)
        states = self.cat.get_resource("states")

        fields = [
            states.title,
            states.abstract,
            states.native_bbox,
            states.latlon_bbox,
            states.projection,
            states.projection_policy
        ]

        self.assertFalse(None in fields, str(fields))
        self.assertFalse(len(states.keywords) == 0)
        self.assertFalse(len(states.attributes) == 0)
        self.assertTrue(states.enabled)

        self.assertEqual("sfdem", self.cat.get_resource("sfdem", sfdem).name)
        self.assertEqual("sfdem", self.cat.get_resource("sfdem", workspace=sf).name)
        self.assertEqual("sfdem", self.cat.get_resource("sfdem").name)

    def testResourcesUpdate(self):
        res_dest = self.cat.get_resources()
        count = 0

        for rd in res_dest:
            # only wms layers
            if rd.resource_type != "wmsLayer":
                continue

            # looking for same name
            ro = self.cat.get_resource(rd.name)

            if ro is not None:
                rd.title = ro.title
                rd.abstract = ro.abstract
                rd.keywords = ro.keywords
                rd.projection = ro.projection
                rd.native_bbox = ro.native_bbox
                rd.latlon_bbox = ro.latlon_bbox
                rd.projection_policy = ro.projection_policy
                rd.enabled = ro.enabled
                rd.advertised = ro.advertised
                rd.metadata_links = ro.metadata_links or None

                self.cat.save(rd)
                self.cat.reload()

                # print "Updated layer: " + rd.name
                count += 1

        # print "Total updated layers: " + str(count)

    def testLayers(self):
        expected = set(["Arc_Sample", "Pk50095", "Img_Sample", "mosaic", "sfdem",
                        "bugsites", "restricted", "streams", "archsites", "roads",
                        "tasmania_roads", "tasmania_water_bodies", "tasmania_state_boundaries",
                        "tasmania_cities", "states", "poly_landmarks", "tiger_roads", "poi",
                        "giant_polygon"
                        ])
        actual = set(l.name for l in self.cat.get_layers())
        missing = expected - actual
        extras = actual - expected
        message = "Actual layer list did not match expected! (Extras: %s) (Missing: %s)" % (extras, missing)
        self.assertTrue(len(expected ^ actual) == 0, message)

        states = self.cat.get_layer("states")

        self.assertEqual("states", states.name)
        self.assertIsInstance(states.resource, ResourceInfo)
        self.assertEqual(set(s.name for s in states.styles), set(['pophatch', 'polygon']))
        self.assertEqual(states.default_style.name, "population")

    def testLayerGroups(self):
        expected = set(["tasmania", "tiger-ny", "spearfish"])
        actual = set(l.name for l in self.cat.get_layergroups())
        missing = expected - actual
        extras = actual - expected
        message = "Actual layergroup list did not match expected! (Extras: %s) (Missing: %s)" % (extras, missing)
        self.assertTrue(len(expected ^ actual) == 0, message)

        tas = self.cat.get_layergroup("tasmania")

        self.assertEqual("tasmania", tas.name)
        self.assertIsInstance(tas, LayerGroup)
        self.assertEqual(tas.layers,
                         ['tasmania_state_boundaries', 'tasmania_water_bodies', 'tasmania_roads', 'tasmania_cities'],
                         tas.layers)
        self.assertEqual(tas.styles, [None, None, None, None], tas.styles)

        # Try to create a new Layer Group into the "topp" workspace
        self.assertIsNotNone(self.cat.get_workspace("topp"))
        tas2 = self.cat.create_layergroup("tasmania_reloaded", tas.layers, workspace="topp")
        self.cat.save(tas2)
        self.assertIsNone(self.cat.get_layergroup("tasmania_reloaded"))
        self.assertIsNotNone(self.cat.get_layergroup("tasmania_reloaded", "topp"))
        tas2 = self.cat.get_layergroup("tasmania_reloaded", "topp")
        self.assertEqual("tasmania_reloaded", tas2.name)
        self.assertIsInstance(tas2, LayerGroup)
        self.assertEqual(tas2.workspace, "topp", tas2.workspace)
        self.assertEqual(tas2.layers,
                         ['tasmania_state_boundaries', 'tasmania_water_bodies', 'tasmania_roads', 'tasmania_cities'],
                         tas2.layers)
        self.assertEqual(tas2.styles, [None, None, None, None], tas2.styles)

    def testStyles(self):
        self.assertEqual("population", self.cat.get_style("population").name)
        self.assertEqual("popshade.sld", self.cat.get_style("population").filename)
        self.assertEqual("population", self.cat.get_style("population").sld_name)
        self.assertIsNone(self.cat.get_style('non-existing-style'))

    def testEscaping(self):
        # GSConfig is inconsistent about using exceptions vs. returning None
        # when a resource isn't found.
        # But the basic idea is that none of them should throw HTTP errors from
        # misconstructed URLS
        self.cat.get_style("best style ever")
        self.cat.get_workspace("best workspace ever")
        self.assertEqual(self.cat.get_store(workspace="best workspace ever", name="best store ever"), None)
        self.cat.get_layer("best layer ever")
        self.cat.get_layergroup("best layergroup ever")

    def testUnicodeUrl(self):
        """
        Tests that the geoserver.support.url function support unicode strings.
        """

        # Test the url function with unicode
        seg = ['workspaces', 'test', 'datastores', u'operaci\xf3n_repo', 'featuretypes.xml']
        u = url(base=self.cat.service_url, seg=seg)
        self.assertEqual(u, self.cat.service_url + "/workspaces/test/datastores/operaci%C3%B3n_repo/featuretypes.xml")

        # Test the url function with normal string
        seg = ['workspaces', 'test', 'datastores', 'test-repo', 'featuretypes.xml']
        u = url(base=self.cat.service_url, seg=seg)
        self.assertEqual(u, self.cat.service_url + "/workspaces/test/datastores/test-repo/featuretypes.xml")


class ModifyingTests(unittest.TestCase):

    def setUp(self):
        self.cat = Catalog(GSPARAMS['GSURL'], username=GSPARAMS['GSUSER'], password=GSPARAMS['GSPASSWORD'])

    def testFeatureTypeSave(self):
        # test saving round trip
        rs = self.cat.get_resource("bugsites")
        old_abstract = rs.abstract
        new_abstract = "Not the original abstract"
        enabled = rs.enabled

        # Change abstract on server
        rs.abstract = new_abstract
        self.cat.save(rs)
        rs = self.cat.get_resource("bugsites")
        self.assertEqual(new_abstract, rs.abstract)
        self.assertEqual(enabled, rs.enabled)

        # Change keywords on server
        rs.keywords = ["bugsites", "gsconfig"]
        enabled = rs.enabled
        self.cat.save(rs)
        rs = self.cat.get_resource("bugsites")
        self.assertEqual(["bugsites", "gsconfig"], rs.keywords)
        self.assertEqual(enabled, rs.enabled)

        # Change metadata links on server
        rs.metadata_links = [("text/xml", "TC211", "http://example.com/gsconfig.test.metadata")]
        enabled = rs.enabled
        self.cat.save(rs)
        rs = self.cat.get_resource("bugsites")
        self.assertEqual(
            [("text/xml", "TC211", "http://example.com/gsconfig.test.metadata")],
            rs.metadata_links)
        self.assertEqual(enabled, rs.enabled)

        # Restore abstract
        rs.abstract = old_abstract
        self.cat.save(rs)
        rs = self.cat.get_resource("bugsites")
        self.assertEqual(old_abstract, rs.abstract)

    def testDataStoreCreate(self):
        ds = self.cat.create_datastore("vector_gsconfig")
        ds.connection_parameters.update(**DBPARAMS)
        self.cat.save(ds)

    def testPublishFeatureType(self):
        # Use the other test and store creation to load vector data into a database
        # @todo maybe load directly to database?
        try:
            self.testDataStoreCreateAndThenAlsoImportData()
        except FailedRequestError:
            pass
        try:
            lyr = self.cat.get_layer('import')
            # Delete the existing layer and resource to allow republishing.
            self.cat.delete(lyr)
            self.cat.delete(lyr.resource)
            ds = self.cat.get_store("gsconfig_import_test")
            # make sure it's gone
            self.assertIsNone(self.cat.get_layer('import'))
            self.cat.publish_featuretype("import", ds, native_crs="EPSG:4326")
            # and now it's not
            self.assertIsNotNone(self.cat.get_layer('import'))
        finally:
            # tear stuff down to allow the other test to pass if we run first
            ds = self.cat.get_store("gsconfig_import_test")
            lyr = self.cat.get_layer('import')
            # Delete the existing layer and resource to allow republishing.
            try:
                if lyr:
                    self.cat.delete(lyr)
                    self.cat.delete(lyr.resource)
                if ds:
                    self.cat.delete(ds)
            except Exception:
                pass

    def testDataStoreModify(self):
        ds = self.cat.get_store("sf")
        self.assertFalse("foo" in ds.connection_parameters)
        ds.connection_parameters = ds.connection_parameters
        ds.connection_parameters["foo"] = "bar"
        orig_ws = ds.workspace.name
        self.cat.save(ds)
        ds = self.cat.get_store("sf")
        self.assertTrue("foo" in ds.connection_parameters)
        self.assertEqual("bar", ds.connection_parameters["foo"])
        self.assertEqual(orig_ws, ds.workspace.name)

    @drop_table('import')
    def testDataStoreCreateAndThenAlsoImportData(self):
        ds = self.cat.create_datastore("gsconfig_import_test")
        ds.connection_parameters.update(**DBPARAMS)
        self.cat.save(ds)
        ds = self.cat.get_store("gsconfig_import_test")
        self.cat.add_data_to_store(ds, "import", {
            'shp': 'test/data/states.shp',
            'shx': 'test/data/states.shx',
            'dbf': 'test/data/states.dbf',
            'prj': 'test/data/states.prj'
        })

    @drop_table('import2')
    def testVirtualTables(self):
        ds = self.cat.create_datastore("gsconfig_import_test2")
        ds.connection_parameters.update(**DBPARAMS)
        self.cat.save(ds)
        ds = self.cat.get_store("gsconfig_import_test2")
        self.cat.add_data_to_store(ds, "import2", {
            'shp': 'test/data/states.shp',
            'shx': 'test/data/states.shx',
            'dbf': 'test/data/states.dbf',
            'prj': 'test/data/states.prj'
        })
        store = self.cat.get_store("gsconfig_import_test2")
        geom = JDBCVirtualTableGeometry('the_geom', 'MultiPolygon', '4326')
        ft_name = 'my_jdbc_vt_test'
        epsg_code = 'EPSG:4326'
        sql = "select * from import2 where 'STATE_NAME' = 'Illinois'"
        keyColumn = None
        parameters = None

        jdbc_vt = JDBCVirtualTable(ft_name, sql, 'false', geom, keyColumn, parameters)
        self.cat.publish_featuretype(ft_name, store, epsg_code, jdbc_virtual_table=jdbc_vt)

    # DISABLED; this test works only in the very particular case
    # "mytiff.tiff" is already present into the GEOSERVER_DATA_DIR
    # def testCoverageStoreCreate(self):
    #     ds = self.cat.create_coveragestore2("coverage_gsconfig")
    #     ds.data_url = "file:test/data/mytiff.tiff"
    #     self.cat.save(ds)

    def testCoverageStoreModify(self):
        cs = self.cat.get_store("sfdem")
        self.assertEqual("GeoTIFF", cs.type)
        cs.type = "WorldImage"
        self.cat.save(cs)
        cs = self.cat.get_store("sfdem")
        self.assertEqual("WorldImage", cs.type)

        # not sure about order of test runs here, but it might cause problems
        # for other tests if this layer is misconfigured
        cs.type = "GeoTIFF"
        self.cat.save(cs)

    def testCoverageSave(self):
        # test saving round trip
        rs = self.cat.get_resource("Arc_Sample")
        old_abstract = rs.abstract
        new_abstract = "Not the original abstract"

        # # Change abstract on server
        rs.abstract = new_abstract
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(new_abstract, rs.abstract)

        # Restore abstract
        rs.abstract = old_abstract
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(old_abstract, rs.abstract)

        # Change metadata links on server
        rs.metadata_links = [("text/xml", "TC211", "http://example.com/gsconfig.test.metadata")]
        enabled = rs.enabled
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(
            [("text/xml", "TC211", "http://example.com/gsconfig.test.metadata")],
            rs.metadata_links)
        self.assertEqual(enabled, rs.enabled)

        srs_before = set(['EPSG:4326'])
        srs_after = set(['EPSG:4326', 'EPSG:3785'])
        formats = set(['ARCGRID', 'ARCGRID-GZIP', 'GEOTIFF', 'PNG', 'GIF', 'TIFF'])
        formats_after = set(["PNG", "GIF", "TIFF"])

        # set and save request_srs_list
        self.assertEqual(set(rs.request_srs_list), srs_before, str(rs.request_srs_list))
        rs.request_srs_list = rs.request_srs_list + ['EPSG:3785']
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(set(rs.request_srs_list), srs_after, str(rs.request_srs_list))

        # set and save response_srs_list
        self.assertEqual(set(rs.response_srs_list), srs_before, str(rs.response_srs_list))
        rs.response_srs_list = rs.response_srs_list + ['EPSG:3785']
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(set(rs.response_srs_list), srs_after, str(rs.response_srs_list))

        # set and save supported_formats
        self.assertEqual(set(rs.supported_formats), formats, str(rs.supported_formats))
        rs.supported_formats = ["PNG", "GIF", "TIFF"]
        self.cat.save(rs)
        rs = self.cat.get_resource("Arc_Sample")
        self.assertEqual(set(rs.supported_formats), formats_after, str(rs.supported_formats))

    def testWmsStoreCreate(self):
        ws = self.cat.create_wmsstore("wmsstore_gsconfig")
        ws.capabilitiesURL = "http://mesonet.agron.iastate.edu/cgi-bin/wms/iowa/rainfall.cgi?VERSION=1.1.1&REQUEST=GetCapabilities&SERVICE=WMS&"  # noqa: E501
        ws.type = "WMS"
        self.cat.save(ws)

    def testWmsLayer(self):
        self.cat.create_workspace("wmstest", "http://example.com/wmstest")
        wmstest = self.cat.get_workspace("wmstest")
        wmsstore = self.cat.create_wmsstore("wmsstore", wmstest)
        wmsstore.capabilitiesURL = "http://mesonet.agron.iastate.edu/cgi-bin/wms/iowa/rainfall.cgi?VERSION=1.1.1&REQUEST=GetCapabilities&SERVICE=WMS&"  # noqa: E501
        wmsstore.type = "WMS"
        self.cat.save(wmsstore)
        wmsstore = self.cat.get_store("wmsstore")
        self.assertEqual(1, len(self.cat.get_stores(workspace=wmstest)))
        available_layers = wmsstore.get_resources(available=True)
        for layer in available_layers:
            # sanitize the layer name - validation will fail on newer geoservers
            name = layer.replace(':', '_')
            self.cat.create_wmslayer(wmstest, wmsstore, name, nativeName=layer)
        added_layers = wmsstore.get_resources()
        self.assertEqual(len(available_layers), len(added_layers))

        changed_layer = added_layers[0]
        self.assertEqual(True, changed_layer.advertised)
        self.assertEqual(True, changed_layer.enabled)
        changed_layer.advertised = False
        changed_layer.enabled = False
        self.cat.save(changed_layer)
        self.cat._cache.clear()
        changed_layer = wmsstore.get_resources()[0]
        changed_layer.fetch()
        self.assertEqual(False, changed_layer.advertised)
        self.assertEqual(False, changed_layer.enabled)

        # Testing projection and projection policy changes
        changed_layer.projection = "EPSG:900913"
        changed_layer.projection_policy = "REPROJECT_TO_DECLARED"
        self.cat.save(changed_layer)
        self.cat._cache.clear()
        layer = self.cat.get_layer(changed_layer.name)
        self.assertEqual(layer.resource.projection_policy, changed_layer.projection_policy)
        self.assertEqual(layer.resource.projection, changed_layer.projection)

    def testFeatureTypeCreate(self):
        shapefile_plus_sidecars = shapefile_and_friends("test/data/states")
        expected = {
            'shp': 'test/data/states.shp',
            'shx': 'test/data/states.shx',
            'dbf': 'test/data/states.dbf',
            'prj': 'test/data/states.prj'
        }

        self.assertEqual(len(expected), len(shapefile_plus_sidecars))
        for k, v in expected.items():
            self.assertEqual(v, shapefile_plus_sidecars[k])

        sf = self.cat.get_workspace("sf")
        self.cat.create_featurestore("states_test", shapefile_plus_sidecars, sf)

        self.assertIsNotNone(self.cat.get_resource("states_test", workspace=sf))

        self.assertRaises(
            ConflictingDataError,
            lambda: self.cat.create_featurestore("states_test", shapefile_plus_sidecars, sf)
        )

        self.assertRaises(
            UploadError,
            lambda: self.cat.create_coveragestore("states_raster_test", shapefile_plus_sidecars, sf)
        )

        bogus_shp = {
            'shp': 'test/data/Pk50095.tif',
            'shx': 'test/data/Pk50095.tif',
            'dbf': 'test/data/Pk50095.tfw',
            'prj': 'test/data/Pk50095.prj'
        }

        self.assertRaises(
            UploadError,
            lambda: self.cat.create_featurestore("bogus_shp", bogus_shp, sf)
        )

        lyr = self.cat.get_layer("states_test")
        self.cat.delete(lyr)
        self.assertIsNone(self.cat.get_layer("states_test"))

    def testCoverageCreate(self):
        tiffdata = {
            'tiff': 'test/data/Pk50095.tif',
            'tfw': 'test/data/Pk50095.tfw',
            'prj': 'test/data/Pk50095.prj'
        }

        sf = self.cat.get_workspace("sf")
        self.cat.create_coveragestore("Pk50095", tiffdata, sf)

        self.assertIsNotNone(self.cat.get_resource("Pk50095", workspace=sf))

        self.assertRaises(
            ConflictingDataError,
            lambda: self.cat.create_coveragestore("Pk50095", tiffdata, sf)
        )

        self.assertRaises(
            UploadError,
            lambda: self.cat.create_featurestore("Pk50095_vector", tiffdata, sf)
        )

        bogus_tiff = {
            'tiff': 'test/data/states.shp',
            'tfw': 'test/data/states.shx',
            'prj': 'test/data/states.prj'
        }

        self.assertRaises(
            UploadError,
            lambda: self.cat.create_coveragestore("states_raster", bogus_tiff)
        )

        self.cat.create_coveragestore_external_geotiff("Pk50095_ext", 'file:test/data/Pk50095.tif', sf)

    def testLayerSave(self):
        # test saving round trip
        lyr = self.cat.get_layer("states")
        old_attribution = lyr.attribution
        new_attribution = {
            'title': 'Not the original attribution',
            'width': '123',
            'height': '321',
            'href': 'http://www.georchestra.org',
            'url': 'https://www.cigalsace.org/portail/cigal/documents/page/mentions-legales/Logo_geOrchestra.jpg',
            'type': 'image/jpeg'
        }

        # change attribution on server
        lyr.attribution = new_attribution
        self.cat.save(lyr)
        lyr = self.cat.get_layer("states")
        self.assertEqual(new_attribution, lyr.attribution)

        # Restore attribution
        lyr.attribution = old_attribution
        self.cat.save(lyr)
        lyr = self.cat.get_layer("states")
        self.assertEqual(old_attribution, lyr.attribution)

        self.assertEqual(lyr.default_style.name, "population")

        old_default_style = lyr.default_style
        lyr.default_style = next((s for s in lyr.styles if s.name == "pophatch"))
        lyr.styles = [old_default_style]
        self.cat.save(lyr)
        lyr = self.cat.get_layer("states")
        self.assertEqual(lyr.default_style.name, "pophatch")
        self.assertEqual([s.name for s in lyr.styles], ["population"])

    def testStyles(self):
        # check count before tests (upload)
        count = len(self.cat.get_styles())

        # upload new style, verify existence
        with open("test/fred.sld") as fred_sld:
            self.cat.create_style("fred", fred_sld.read())
        fred = self.cat.get_style("fred")
        self.assertIsNotNone(fred)
        self.assertEqual("Fred", fred.sld_title)

        # replace style, verify changes
        with open("test/ted.sld") as ted_sld:
            self.cat.create_style("fred", ted_sld.read(), overwrite=True)
        fred = self.cat.get_style("fred")
        self.assertIsNotNone(fred)
        self.assertEqual("Ted", fred.sld_title)

        # delete style, verify non-existence
        self.cat.delete(fred, purge=True)
        self.assertIsNone(self.cat.get_style("fred"))

        # attempt creating new style
        with open("test/fred.sld") as fred_sld:
            self.cat.create_style("fred", fred_sld.read())
        fred = self.cat.get_style("fred")
        self.assertEqual("Fred", fred.sld_title)

        # verify it can be found via URL and check the name
        f = self.cat.get_style_by_url(fred.href)
        self.assertIsNotNone(f)
        self.assertEqual(f.name, fred.name)

        # compare count after upload
        self.assertEqual(count + 1, len(self.cat.get_styles()))

        # attempt creating a new style without "title"
        with open("test/notitle.sld") as notitle_sld:
            self.cat.create_style("notitle", notitle_sld.read())
        notitle = self.cat.get_style("notitle")
        self.assertEqual(None, notitle.sld_title)

    def testWorkspaceStyles(self):
        # upload new style, verify existence
        with open("test/fred.sld") as fred_sld:
            self.cat.create_style("jed", fred_sld.read(), workspace="topp")

        jed = self.cat.get_style("jed", workspace="blarny")
        self.assertIsNone(jed)
        jed = self.cat.get_style("jed", workspace="topp")
        self.assertIsNotNone(jed)
        self.assertEqual("Fred", jed.sld_title)
        jed = self.cat.get_style("topp:jed")
        self.assertIsNotNone(jed)
        self.assertEqual("Fred", jed.sld_title)

        # replace style, verify changes
        with open("test/ted.sld") as ted_sld:
            self.cat.create_style("jed", ted_sld.read(), overwrite=True, workspace="topp")
        jed = self.cat.get_style("jed", workspace="topp")
        self.assertIsNotNone(jed)
        self.assertEqual("Ted", jed.sld_title)

        # delete style, verify non-existence
        self.cat.delete(jed, purge=True)
        self.assertIsNone(self.cat.get_style("jed", workspace="topp"))

        # attempt creating new style
        with open("test/fred.sld") as fred_sld:
            self.cat.create_style("jed", fred_sld.read(), workspace="topp")
        jed = self.cat.get_style("jed", workspace="topp")
        self.assertEqual("Fred", jed.sld_title)

        # verify it can be found via URL and check the full name
        f = self.cat.get_style_by_url(jed.href)
        self.assertIsNotNone(f)
        self.assertEqual(f.fqn, jed.fqn)

    def testLayerWorkspaceStyles(self):
        # upload new style, verify existence
        with open("test/fred.sld") as fred_sld:
            self.cat.create_style("ned", fred_sld.read(), overwrite=True, workspace="topp")
        with open("test/fred.sld") as ted_sld:
            self.cat.create_style("zed", ted_sld.read(), overwrite=True, workspace="topp")
        ned = self.cat.get_style("ned", workspace="topp")
        zed = self.cat.get_style("zed", workspace="topp")
        self.assertIsNotNone(ned)
        self.assertIsNotNone(zed)

        lyr = self.cat.get_layer("states")
        lyr.default_style = ned
        lyr.styles = [zed]
        self.cat.save(lyr)
        self.assertEqual("topp:ned", lyr.default_style)
        self.assertEqual([zed], lyr.styles)

        lyr.refresh()
        if lyr.default_style is not None:
            self.assertEqual("topp:ned", lyr.default_style.fqn)
            self.assertEqual([zed.fqn], [s.fqn for s in lyr.styles])

    def testWorkspaceCreate(self):
        ws = self.cat.get_workspace("acme")
        self.assertEqual(None, ws)
        self.cat.create_workspace("acme", "http://example.com/acme")
        ws = self.cat.get_workspace("acme")
        self.assertEqual("acme", ws.name)

    def testWorkspaceDelete(self):
        self.cat.create_workspace("foo", "http://example.com/foo")
        ws = self.cat.get_workspace("foo")
        self.cat.delete(ws)
        ws = self.cat.get_workspace("foo")
        self.assertIsNone(ws)

    def testWorkspaceDefault(self):
        # save orig
        orig = self.cat.get_default_workspace()
        neu = self.cat.create_workspace("neu", "http://example.com/neu")
        try:
            # make sure setting it works
            self.cat.set_default_workspace("neu")
            ws = self.cat.get_default_workspace()
            self.assertEqual('neu', ws.name)
        finally:
            # cleanup and reset to the way things were
            self.cat.delete(neu)
            self.cat.set_default_workspace(orig.name)
            ws = self.cat.get_default_workspace()
            self.assertEqual(orig.name, ws.name)

    def testFeatureTypeDelete(self):
        pass

    def testCoverageDelete(self):
        pass

    def testDataStoreDelete(self):
        states = self.cat.get_store('states_shapefile')
        self.assertTrue(states.enabled)
        states.enabled = False
        self.assertFalse(states.enabled)
        self.cat.save(states)

        states = self.cat.get_store('states_shapefile')
        self.assertFalse(states.enabled)

        states.enabled = True
        self.cat.save(states)

        states = self.cat.get_store('states_shapefile')
        self.assertTrue(states.enabled)

    def testLayerGroupSave(self):
        tas = self.cat.get_layergroup("tasmania")

        self.assertEqual(tas.layers,
                         ['tasmania_state_boundaries', 'tasmania_water_bodies', 'tasmania_roads', 'tasmania_cities'],
                         tas.layers)
        self.assertEqual(tas.styles, [None, None, None, None], tas.styles)

        tas.layers = tas.layers[:-1]
        tas.styles = tas.styles[:-1]

        self.cat.save(tas)

        # this verifies the local state
        self.assertEqual(tas.layers, ['tasmania_state_boundaries', 'tasmania_water_bodies', 'tasmania_roads'],
                         tas.layers)
        self.assertEqual(tas.styles, [None, None, None], tas.styles)

        # force a refresh to check the remote state
        tas.refresh()
        self.assertEqual(tas.layers, ['tasmania_state_boundaries', 'tasmania_water_bodies', 'tasmania_roads'],
                         tas.layers)
        self.assertEqual(tas.styles, [None, None, None], tas.styles)

    def testImageMosaic(self):
        """
            Test case for Issue #110
        """
        # testing the mosaic creation
        name = 'cea_mosaic'
        with open('test/data/mosaic/cea.zip', 'rb') as data:
            self.cat.create_imagemosaic(name, data)

        # get the layer resource back
        self.cat._cache.clear()
        resource = self.cat.get_layer(name).resource

        self.assertIsNotNone(resource)

        # delete granule from mosaic
        coverage = name
        store = self.cat.get_store(name)
        granules = self.cat.list_granules(coverage, store)
        self.assertEqual(1, len(granules['features']))
        granule_id = name + '.1'
        self.cat.mosaic_delete_granule(coverage, store, granule_id)
        granules = self.cat.list_granules(coverage, store)
        self.assertEqual(0, len(granules['features']))

        """
          testing external Image mosaic creation
        """
        name = 'cea_mosaic_external'
        path = 'test/data/mosaic/external'
        self.cat.create_imagemosaic(name, path, workspace='topp')
        self.cat._cache.clear()
        resource = self.cat.get_layer("external").resource
        self.assertIsNotNone(resource)

        # add granule to mosaic
        granule_path = 'test/data/mosaic/granules/cea_20150102.tif'
        self.cat.add_granule(granule_path, name, workspace='topp')
        granules = self.cat.list_granules("external", name, 'topp')
        self.assertEqual(2, len(granules['features']))

        # add external granule to mosaic
        granule_path = os.path.join(os.getcwd(), 'test/data/mosaic/granules/cea_20150103.zip')
        self.cat.add_granule(granule_path, name, workspace='topp')
        granules = self.cat.list_granules("external", name, 'topp')
        self.assertEqual(3, len(granules['features']))

        # Delete store
        store = self.cat.get_store(name)
        self.cat.delete(store, purge=True, recurse=True)
        self.cat._cache.clear()

    def testTimeDimension(self):
        sf = self.cat.get_workspace("sf")
        files = shapefile_and_friends(os.path.join(gisdata.GOOD_DATA, "time", "boxes_with_end_date"))
        self.cat.create_featurestore("boxes_with_end_date", files, sf)

        get_resource = lambda: self.cat._cache.clear() or self.cat.get_layer('boxes_with_end_date').resource  # noqa: E731 E501

        # configure time as LIST
        resource = get_resource()
        timeInfo = DimensionInfo("time", "true", "LIST", None, "ISO8601", None, attribute="date")
        resource.metadata = {'time': timeInfo}
        self.cat.save(resource)
        # and verify
        resource = get_resource()
        timeInfo = resource.metadata['time']
        self.assertEqual("LIST", timeInfo.presentation)
        self.assertEqual(True, timeInfo.enabled)
        self.assertEqual("date", timeInfo.attribute)
        self.assertEqual("ISO8601", timeInfo.units)

        # disable time dimension
        timeInfo = resource.metadata['time']
        timeInfo.enabled = False
        # since this is an xml property, it won't get written unless we modify it
        resource.metadata = {'time': timeInfo}
        self.cat.save(resource)
        # and verify
        resource = get_resource()
        timeInfo = resource.metadata['time']
        self.assertEqual(False, timeInfo.enabled)

        # configure with interval, end_attribute and enable again
        timeInfo.enabled = True
        timeInfo.presentation = 'DISCRETE_INTERVAL'
        timeInfo.resolution = '3 days'
        timeInfo.end_attribute = 'enddate'
        resource.metadata = {'time': timeInfo}
        self.cat.save(resource)
        # and verify
        resource = get_resource()
        timeInfo = resource.metadata['time']
        self.assertEqual(True, timeInfo.enabled)
        self.assertEqual('DISCRETE_INTERVAL', timeInfo.presentation)
        self.assertEqual('3 days', timeInfo.resolution_str())
        self.assertEqual('enddate', timeInfo.end_attribute)


if __name__ == "__main__":
    unittest.main()
